"""Stop, Last-Event-ID resume, and orphan-grace abort (§7.4 rule 3, §19)."""

import asyncio

import orjson

from retinue.core.ids import uuid7
from support import StreamReader, parse_sse, register_user


async def test_stop_mid_stream(live):
    client = live.client
    headers = await register_user(client)
    message_id = str(uuid7())

    async with client.stream(
        "POST",
        "/api/chat",
        headers=headers,
        json={"message_id": message_id, "text": "stop me", "model": "mock/slow"},
    ) as response:
        reader = StreamReader(response)
        await reader.read_deltas(3)
        assistant_id = reader.events[0].data["message_id"]
        stop = await client.post(f"/api/messages/{assistant_id}/stop", headers=headers)
        assert stop.json()["stopped"] is True
        await reader.drain()

    end = next(e for e in reader.events if e.event == "message_end")
    assert end.data["stop_reason"] == "stopped"

    conversation_id = reader.events[0].data["conversation_id"]
    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert messages[1]["status"] == "stopped"
    partial = messages[1]["parts"][0]["content"]["text"]
    assert partial
    assert "zero buffering end to end" not in partial  # genuinely partial


async def test_resume_with_last_event_id(live):
    client = live.client
    headers = await register_user(client)
    message_id = str(uuid7())
    payload = {"message_id": message_id, "text": "resume me", "model": "mock/slow"}

    # first connection: read a few deltas then drop
    async with client.stream("POST", "/api/chat", headers=headers, json=payload) as response:
        reader = StreamReader(response)
        await reader.read_deltas(3)
    first_events = reader.events
    last_id = reader.last_id
    assert last_id > 0
    first_text = "".join(e.data["text"] for e in first_events if e.event == "delta")

    # reconnect immediately with Last-Event-ID: replay + live tail, no gaps
    resumed_headers = {**headers, "Last-Event-ID": str(last_id)}
    async with client.stream(
        "POST", "/api/chat", headers=resumed_headers, json=payload
    ) as response:
        body = await response.aread()
    resumed = parse_sse(body)
    resumed_ids = [e.id for e in resumed if e.id is not None]
    assert resumed_ids and resumed_ids[0] == last_id + 1  # exact continuation
    resumed_text = "".join(e.data["text"] for e in resumed if e.event == "delta")
    assert [e for e in resumed if e.event == "message_end"]

    combined = first_text + resumed_text
    conversation_id = first_events[0].data["conversation_id"]
    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    final_text = messages[1]["parts"][0]["content"]["text"]
    assert combined == final_text  # lossless resume
    assert messages[1]["status"] in ("complete", "stopped")


async def test_orphaned_stream_aborts_after_grace(live):
    client = live.client
    headers = await register_user(client)
    message_id = str(uuid7())

    async with client.stream(
        "POST",
        "/api/chat",
        headers=headers,
        json={"message_id": message_id, "text": "abandon me", "model": "mock/slow"},
    ) as response:
        reader = StreamReader(response)
        await reader.read_deltas(2)
    conversation_id = reader.events[0].data["conversation_id"]

    # nobody re-attaches within orphan_grace_s (1.5s in tests) -> implicit abort
    await asyncio.sleep(3.0)
    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert messages[1]["status"] == "stopped"


async def test_duplicate_post_attaches_to_live_stream(live):
    client = live.client
    headers = await register_user(client)
    message_id = str(uuid7())
    payload = {"message_id": message_id, "text": "shared stream", "model": "mock/slow"}

    async def consume(delay: float):
        await asyncio.sleep(delay)
        async with client.stream("POST", "/api/chat", headers=headers, json=payload) as response:
            return parse_sse(await response.aread())

    # second POST of the same message id arrives mid-stream -> same stream fan-out
    first, second = await asyncio.gather(consume(0), consume(0.6))

    first_full = "".join(e.data["text"] for e in first if e.event == "delta")
    second_full = "".join(e.data["text"] for e in second if e.event == "delta")
    assert first[0].data["message_id"] == second[0].data["message_id"]
    assert first_full == second_full  # replay made the late joiner whole
    assert orjson.dumps(first[-1].data) == orjson.dumps(second[-1].data)
