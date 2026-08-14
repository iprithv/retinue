"""StreamHub — live SSE streams, decoupled from HTTP requests.

The producer (chat engine) publishes into a stream; any number of subscribers
(the original request, a resumed request after `Last-Event-ID`, an idempotent
retry of the same client message id) consume via per-subscriber queues. A
stream with zero subscribers survives a grace window before being treated as
an implicit client abort (§7.4 rule 3), which is what makes resume work.
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from retinue.core.sse import RingBuffer, encode_sse

log = structlog.get_logger("retinue.streams")

_SENTINEL = None  # queue item marking stream end


class LiveStream:
    def __init__(
        self,
        *,
        message_id: uuid.UUID,
        client_message_id: uuid.UUID | None,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        ring_size: int,
    ) -> None:
        self.message_id = message_id
        self.client_message_id = client_message_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.ring = RingBuffer(ring_size)
        self.next_event_id = 0
        self.subscribers: dict[int, asyncio.Queue[tuple[int, bytes] | None]] = {}
        self._sub_seq = 0
        self.done = False
        self.stop_requested = asyncio.Event()
        self.finished_at: float | None = None
        self.producer_task: asyncio.Task[None] | None = None
        self._orphan_task: asyncio.Task[None] | None = None
        # set by the producer: () -> dict with the full in-memory message state,
        # used to rebuild a subscriber whose Last-Event-ID fell off the ring
        self.snapshot: Callable[[], dict[str, Any]] | None = None


class StreamHub:
    def __init__(
        self, *, ring_size: int = 512, ring_ttl_s: float = 60.0, orphan_grace_s: float = 5.0
    ) -> None:
        self._ring_size = ring_size
        self._ring_ttl_s = ring_ttl_s
        self._orphan_grace_s = orphan_grace_s
        self._by_message: dict[uuid.UUID, LiveStream] = {}
        self._by_client: dict[uuid.UUID, LiveStream] = {}

    # -- lifecycle ------------------------------------------------------------

    def start(
        self,
        *,
        message_id: uuid.UUID,
        client_message_id: uuid.UUID | None,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        producer: Callable[["LiveStream"], Coroutine[Any, Any, None]],
    ) -> LiveStream:
        stream = LiveStream(
            message_id=message_id,
            client_message_id=client_message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            ring_size=self._ring_size,
        )
        self._by_message[message_id] = stream
        if client_message_id is not None:
            self._by_client[client_message_id] = stream
        stream.producer_task = asyncio.create_task(
            producer(stream), name=f"chat-producer-{message_id.hex[:8]}"
        )
        return stream

    def get_by_message(self, message_id: uuid.UUID) -> LiveStream | None:
        return self._by_message.get(message_id)

    def get_by_client(self, client_message_id: uuid.UUID) -> LiveStream | None:
        return self._by_client.get(client_message_id)

    def get_by_conversation(self, conversation_id: uuid.UUID) -> LiveStream | None:
        for stream in self._by_message.values():
            if stream.conversation_id == conversation_id and not stream.done:
                return stream
        return None

    # -- producer side ----------------------------------------------------------

    def publish(self, stream: LiveStream, event: str, data: dict[str, Any]) -> None:
        """Called only from the producer task. No awaits — never on the token path."""
        stream.next_event_id += 1
        frame = encode_sse(stream.next_event_id, event, data)
        stream.ring.push(stream.next_event_id, frame)
        for queue in stream.subscribers.values():
            queue.put_nowait((stream.next_event_id, frame))

    def finish(self, stream: LiveStream) -> None:
        stream.done = True
        stream.finished_at = time.monotonic()
        for queue in stream.subscribers.values():
            queue.put_nowait(_SENTINEL)
        asyncio.get_running_loop().call_later(self._ring_ttl_s, self._cleanup, stream)

    def _cleanup(self, stream: LiveStream) -> None:
        self._by_message.pop(stream.message_id, None)
        if stream.client_message_id is not None:
            self._by_client.pop(stream.client_message_id, None)

    # -- subscriber side ---------------------------------------------------------

    def subscribe(
        self, stream: LiveStream, last_event_id: int = 0
    ) -> tuple[list[bytes], asyncio.Queue[tuple[int, bytes] | None] | None, int, bool]:
        """Returns (replay_frames, live_queue|None, sub_id, missed).

        live_queue is None when the stream already finished — the replay is all
        there is. missed=True means the ring evicted frames the client needs
        (the caller rebuilds from the producer snapshot instead of the replay).
        """
        frames, missed = stream.ring.replay_after(last_event_id)
        if stream.done:
            return frames, None, -1, missed
        stream._sub_seq += 1
        sub_id = stream._sub_seq
        queue: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue()
        stream.subscribers[sub_id] = queue
        if stream._orphan_task is not None:
            stream._orphan_task.cancel()
            stream._orphan_task = None
        return frames, queue, sub_id, missed

    def unsubscribe(self, stream: LiveStream, sub_id: int) -> None:
        stream.subscribers.pop(sub_id, None)
        if not stream.subscribers and not stream.done and stream._orphan_task is None:
            stream._orphan_task = asyncio.create_task(self._orphan_watch(stream))

    async def _orphan_watch(self, stream: LiveStream) -> None:
        """No subscribers: give reconnects a grace window, then abort (§7.4)."""
        try:
            await asyncio.sleep(self._orphan_grace_s)
        except asyncio.CancelledError:
            return
        if not stream.subscribers and not stream.done:
            log.info("stream_orphaned_stop", message_id=str(stream.message_id))
            stream.stop_requested.set()

    # -- control -------------------------------------------------------------------

    def request_stop(self, message_id: uuid.UUID) -> bool:
        stream = self._by_message.get(message_id)
        if stream is None or stream.done:
            return False
        stream.stop_requested.set()
        return True

    async def shutdown(self) -> None:
        """Stop all producers and wait for them to persist their partials."""
        streams = list(self._by_message.values())
        for stream in streams:
            stream.stop_requested.set()
        for stream in streams:
            if stream.producer_task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(stream.producer_task, timeout=10)
            if stream._orphan_task is not None:
                stream._orphan_task.cancel()
