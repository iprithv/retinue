"""Shared test helpers."""

from dataclasses import dataclass
from typing import Any

import httpx
import orjson


async def register_user(
    client: httpx.AsyncClient,
    email: str = "user@test.dev",
    password: str = "hunter2secret",
) -> dict[str, str]:
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"authorization": f"Bearer {response.json()['access_token']}"}


@dataclass
class SseEvent:
    id: int | None
    event: str
    data: Any


def parse_sse(raw: bytes) -> list[SseEvent]:
    events: list[SseEvent] = []
    for frame in raw.split(b"\n\n"):
        if not frame.strip() or frame.startswith(b":"):
            continue
        event_id: int | None = None
        event_type = "message"
        data: Any = None
        for line in frame.split(b"\n"):
            if line.startswith(b"id: "):
                event_id = int(line[4:])
            elif line.startswith(b"event: "):
                event_type = line[7:].decode()
            elif line.startswith(b"data: "):
                data = orjson.loads(line[6:])
        events.append(SseEvent(id=event_id, event=event_type, data=data))
    return events


async def collect_chat(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    last_event_id: int | None = None,
    path: str = "/api/chat",
) -> list[SseEvent]:
    send_headers = dict(headers)
    if last_event_id is not None:
        send_headers["Last-Event-ID"] = str(last_event_id)
    async with client.stream("POST", path, headers=send_headers, json=payload) as response:
        body = await response.aread()
        assert response.status_code == 200, body
        assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(body)


def delta_text(events: list[SseEvent]) -> str:
    return "".join(e.data["text"] for e in events if e.event == "delta")


def event_types(events: list[SseEvent]) -> list[str]:
    return [e.event for e in events]


class StreamReader:
    """Incremental SSE reader over one httpx streaming response.

    httpx allows a single body iterator, so mid-stream control tests must keep
    pulling from the same one.
    """

    def __init__(self, response: httpx.Response) -> None:
        self._iter = response.aiter_bytes()
        self._buffer = b""
        self.events: list[SseEvent] = []

    async def next_event(self) -> SseEvent | None:
        while True:
            while b"\n\n" in self._buffer:
                frame, self._buffer = self._buffer.split(b"\n\n", 1)
                parsed = parse_sse(frame + b"\n\n")
                if parsed:
                    self.events.append(parsed[0])
                    return parsed[0]
            try:
                self._buffer += await self._iter.__anext__()
            except StopAsyncIteration:
                return None

    async def read_deltas(self, count: int) -> None:
        seen = 0
        while seen < count:
            event = await self.next_event()
            assert event is not None, "stream ended before enough deltas arrived"
            if event.event == "delta":
                seen += 1

    async def drain(self) -> None:
        while await self.next_event() is not None:
            pass

    @property
    def last_id(self) -> int:
        return max((e.id for e in self.events if e.id is not None), default=0)
