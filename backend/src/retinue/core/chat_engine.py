"""Chat engine — the zero-buffer relay (§7.4, normative).

Rules enforced here:
1. no `await db` between a provider chunk and its SSE publish (WriteBehind
   feeds are synchronous appends; flushing happens on its own task),
2. heartbeats are per-subscriber (in the API layer),
3. stop/abort cancels the provider request and persists the partial as
   status='stopped',
4. provider error mid-stream emits `error {retryable}` and keeps the partial,
5. usage + message_end publish after the final DB flush so a client that
   refetches on message_end always sees consistent rows.
"""

import asyncio
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import update

from retinue.config import Settings
from retinue.core.ids import uuid7
from retinue.core.sse import BLOCK_START, DELTA, ERROR, MESSAGE_END, MESSAGE_START, USAGE
from retinue.core.streams import LiveStream, StreamHub
from retinue.core.timeutil import now_ms
from retinue.core.tokens import TokenCounter
from retinue.db.models import Conversation, Message, MessagePart, UsageEvent
from retinue.db.session import Database
from retinue.jobs.queue import JobQueue
from retinue.providers.base import ChatCall, ProviderError, Usage
from retinue.providers.pricing import PricingTable
from retinue.providers.registry import ProviderRegistry, provider_of

log = structlog.get_logger("retinue.chat")


class _BlockState:
    __slots__ = ("idx", "inserted", "row_id", "text", "type")

    def __init__(self, idx: int, block_type: str) -> None:
        self.idx = idx
        self.type = block_type
        self.text = ""
        self.row_id = uuid7()
        self.inserted = False


class WriteBehind:
    """Batches message-part persistence off the token path (§7.4).

    feed() is synchronous; a background task flushes dirty blocks every
    `interval_ms`. finalize() does the last flush plus the message/conversation
    row updates and the usage event, in one transaction.
    """

    def __init__(self, db: Database, message_id: uuid.UUID, interval_ms: int) -> None:
        self._db = db
        self._message_id = message_id
        self._interval_s = max(interval_ms, 10) / 1000
        self._blocks: dict[int, _BlockState] = {}
        self._dirty: set[int] = set()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(
            self._loop(), name=f"write-behind-{self._message_id.hex[:8]}"
        )

    def ensure_block(self, idx: int, block_type: str) -> None:
        if idx not in self._blocks:
            self._blocks[idx] = _BlockState(idx, block_type)
            self._dirty.add(idx)

    def feed(self, idx: int, text_delta: str) -> None:
        self._blocks[idx].text += text_delta
        self._dirty.add(idx)

    @property
    def full_text(self) -> str:
        return "".join(
            b.text for b in sorted(self._blocks.values(), key=lambda b: b.idx) if b.type == "text"
        )

    def blocks_snapshot(self) -> list[dict[str, Any]]:
        return [
            {"index": b.idx, "type": b.type, "text": b.text}
            for b in sorted(self._blocks.values(), key=lambda b: b.idx)
        ]

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return
            except TimeoutError:
                try:
                    await self._flush()
                except Exception:
                    log.exception("write_behind_flush_failed", message_id=str(self._message_id))

    async def _flush(self) -> None:
        if not self._dirty:
            return
        dirty = [self._blocks[i] for i in sorted(self._dirty)]
        self._dirty.clear()
        async with self._db.write_session() as session:
            for block in dirty:
                await self._upsert(session, block)

    async def _upsert(self, session: Any, block: _BlockState) -> None:
        text_content = block.text if block.type == "text" else None
        if not block.inserted:
            session.add(
                MessagePart(
                    id=block.row_id,
                    message_id=self._message_id,
                    idx=block.idx,
                    type=block.type,
                    content={"text": block.text},
                    text_content=text_content,
                )
            )
            block.inserted = True
        else:
            await session.execute(
                update(MessagePart)
                .where(MessagePart.id == block.row_id)
                .values(content={"text": block.text}, text_content=text_content)
            )

    async def finalize(
        self,
        *,
        status: str,
        model: str,
        error: dict[str, Any] | None,
        conversation_id: uuid.UUID,
        usage_event: UsageEvent | None,
    ) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        ts = now_ms()
        async with self._db.write_session() as session:
            for block in sorted(self._blocks.values(), key=lambda b: b.idx):
                await self._upsert(session, block)
            await session.execute(
                update(Message)
                .where(Message.id == self._message_id)
                .values(status=status, model=model, error=error)
            )
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=ts, last_message_at=ts)
            )
            if usage_event is not None:
                session.add(usage_event)


class ChatEngine:
    def __init__(
        self,
        *,
        db: Database,
        hub: StreamHub,
        registry: ProviderRegistry,
        pricing: PricingTable,
        jobs: JobQueue,
        settings: Settings,
        counter: TokenCounter,
    ) -> None:
        self.db = db
        self.hub = hub
        self.registry = registry
        self.pricing = pricing
        self.jobs = jobs
        self.settings = settings
        self.counter = counter

    def start_stream(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        client_message_id: uuid.UUID | None,
        call: ChatCall,
        agent_version_id: uuid.UUID | None = None,
        generate_title: bool = False,
    ) -> LiveStream:
        async def producer(stream: LiveStream) -> None:
            await self._produce(
                stream,
                call=call,
                agent_version_id=agent_version_id,
                generate_title=generate_title,
            )

        return self.hub.start(
            message_id=assistant_message_id,
            client_message_id=client_message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            producer=producer,
        )

    async def _produce(
        self,
        stream: LiveStream,
        *,
        call: ChatCall,
        agent_version_id: uuid.UUID | None,
        generate_title: bool,
    ) -> None:
        hub = self.hub
        wb = WriteBehind(self.db, stream.message_id, self.settings.stream.write_behind_ms)
        wb.start()

        start_payload = {
            "message_id": str(stream.message_id),
            "conversation_id": str(stream.conversation_id),
            "model": call.model,
            "agent_version_id": str(agent_version_id) if agent_version_id else None,
        }
        hub.publish(stream, MESSAGE_START, start_payload)

        def snapshot() -> dict[str, Any]:
            # read by re-attaching subscribers whose Last-Event-ID fell off the
            # ring; same event loop, no await between read and use
            return {
                "start": start_payload,
                "last_event_id": stream.next_event_id,
                "blocks": wb.blocks_snapshot(),
            }

        stream.snapshot = snapshot

        t0 = time.monotonic()
        ttft_ms: int | None = None
        usage: Usage | None = None
        stop_reason: str | None = None
        status = "complete"
        error_payload: dict[str, Any] | None = None
        block_idx = -1
        block_type: str | None = None

        adapter = self.registry.adapter_for(call.model)
        try:
            agen = adapter.stream(call)
            try:
                async for event in agen:
                    if stream.stop_requested.is_set():
                        status = "stopped"
                        stop_reason = "stopped"
                        break
                    if event.kind in ("text_delta", "thinking_delta"):
                        btype = "text" if event.kind == "text_delta" else "thinking"
                        if block_type != btype:
                            block_idx += 1
                            block_type = btype
                            wb.ensure_block(block_idx, btype)
                            hub.publish(stream, BLOCK_START, {"index": block_idx, "type": btype})
                        if ttft_ms is None:
                            ttft_ms = int((time.monotonic() - t0) * 1000)
                        wb.feed(block_idx, event.text)
                        hub.publish(stream, DELTA, {"index": block_idx, "text": event.text})
                    elif event.kind == "usage" and event.usage is not None:
                        usage = event.usage
                    elif event.kind == "stop":
                        stop_reason = event.stop_reason or "end"
            finally:
                await agen.aclose()  # cancels the provider request on early exit
        except ProviderError as exc:
            status = "error"
            stop_reason = "error"
            error_payload = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
            hub.publish(stream, ERROR, error_payload)
            log.warning(
                "provider_error",
                model=call.model,
                code=exc.code,
                retryable=exc.retryable,
                message_id=str(stream.message_id),
            )
        except Exception:
            status = "error"
            stop_reason = "error"
            error_payload = {
                "code": "internal_error",
                "message": "unexpected relay failure",
                "retryable": True,
            }
            hub.publish(stream, ERROR, error_payload)
            log.exception("relay_failure", message_id=str(stream.message_id))

        total_ms = int((time.monotonic() - t0) * 1000)
        if usage is None:
            input_estimate = sum(
                self.counter.count_message(str(m.get("role", "")), str(m.get("content", "")))
                for m in call.messages
            )
            usage = Usage(
                input_tokens=input_estimate,
                output_tokens=self.counter.count(wb.full_text),
            )
        cost = self.pricing.cost_usd(call.model, usage)

        usage_event = UsageEvent(
            id=uuid7(),
            user_id=stream.user_id,
            conversation_id=stream.conversation_id,
            message_id=stream.message_id,
            provider=provider_of(call.model),
            model=call.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            cost_usd=cost,
            ttft_ms=ttft_ms,
            total_ms=total_ms,
        )

        try:
            await wb.finalize(
                status=status,
                model=call.model,
                error=error_payload,
                conversation_id=stream.conversation_id,
                usage_event=usage_event,
            )
        except Exception:
            log.exception("finalize_failed", message_id=str(stream.message_id))

        hub.publish(
            stream,
            USAGE,
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_tokens": usage.cached_tokens,
                "cost_usd": cost,
            },
        )
        hub.publish(
            stream,
            MESSAGE_END,
            {"stop_reason": stop_reason or "end", "ttft_ms": ttft_ms, "total_ms": total_ms},
        )
        hub.finish(stream)

        if generate_title and status in ("complete", "stopped"):
            try:
                await self.jobs.enqueue(
                    "generate_title", {"conversation_id": str(stream.conversation_id)}
                )
            except Exception:
                log.exception("title_enqueue_failed")
