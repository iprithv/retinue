"""SSE wire protocol (§19).

Every event: `id: <monotonic-int>\\nevent: <type>\\ndata: <orjson>\\n\\n`.
Heartbeat is the comment frame `: ping`. The per-stream ring buffer supports
`Last-Event-ID` resume; a miss triggers `resync_required`.
"""

from collections import deque
from typing import Any

import orjson

HEARTBEAT = b": ping\n\n"

# §19 event types
MESSAGE_START = "message_start"
BLOCK_START = "block_start"
DELTA = "delta"
USAGE = "usage"
CITATION = "citation"
TITLE = "title"
MESSAGE_END = "message_end"
ERROR = "error"
RESYNC_REQUIRED = "resync_required"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
APPROVAL_REQUIRED = "approval_required"


def encode_sse(event_id: int | None, event: str, data: dict[str, Any]) -> bytes:
    payload = orjson.dumps(data)
    if event_id is None:
        return b"event: %b\ndata: %b\n\n" % (event.encode("ascii"), payload)
    return b"id: %d\nevent: %b\ndata: %b\n\n" % (event_id, event.encode("ascii"), payload)


class RingBuffer:
    """Bounded replay buffer of (event_id, encoded_frame)."""

    def __init__(self, size: int = 512) -> None:
        self._items: deque[tuple[int, bytes]] = deque(maxlen=size)

    def push(self, event_id: int, frame: bytes) -> None:
        self._items.append((event_id, frame))

    def replay_after(self, last_event_id: int) -> tuple[list[bytes], bool]:
        """Frames with id > last_event_id. missed=True if evicted frames were skipped."""
        if not self._items:
            return [], last_event_id > 0
        oldest_id = self._items[0][0]
        missed = last_event_id + 1 < oldest_id
        frames = [frame for event_id, frame in self._items if event_id > last_event_id]
        return frames, missed

    @property
    def last_id(self) -> int:
        return self._items[-1][0] if self._items else 0

    def __len__(self) -> int:
        return len(self._items)
