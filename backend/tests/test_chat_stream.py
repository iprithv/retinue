"""The SSE relay end to end over a real server (§7.4, §19): event grammar,
persistence, idempotent retries, branching, error semantics, titles."""

import asyncio

from retinue.core.ids import uuid7
from support import collect_chat, delta_text, event_types, register_user


async def test_happy_path_stream_and_persistence(live):
    client = live.client
    headers = await register_user(client)

    message_id = str(uuid7())
    events = await collect_chat(
        client, headers, {"message_id": message_id, "text": "hello retinue", "model": "mock/echo"}
    )

    types = event_types(events)
    assert types[0] == "message_start"
    assert types[1] == "block_start"
    assert types[-2:] == ["usage", "message_end"]
    assert types.count("delta") > 5

    start = events[0].data
    assert start["model"] == "mock/echo"
    end = events[-1].data
    assert end["stop_reason"] == "end"
    assert end["ttft_ms"] is not None and end["total_ms"] is not None
    usage = next(e for e in events if e.event == "usage").data
    assert usage["output_tokens"] > 0

    # event ids are monotonic from 1
    ids = [e.id for e in events if e.id is not None]
    assert ids == list(range(1, len(ids) + 1))

    streamed_text = delta_text(events)
    assert "hello retinue" in streamed_text

    # persisted rows match the streamed bytes exactly
    conversations = (await client.get("/api/conversations", headers=headers)).json()
    assert len(conversations) == 1
    messages = (
        await client.get(f"/api/conversations/{conversations[0]['id']}/messages", headers=headers)
    ).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["id"] == message_id
    assert messages[1]["status"] == "complete"
    assert messages[1]["parts"][0]["content"]["text"] == streamed_text

    # usage event recorded
    summary = (await client.get("/api/usage/summary?days=1", headers=headers)).json()
    assert summary["totals"]["messages"] == 1
    assert summary["totals"]["output_tokens"] == usage["output_tokens"]


async def test_multi_turn_uses_same_conversation(live):
    client = live.client
    headers = await register_user(client)

    first = await collect_chat(
        client, headers, {"message_id": str(uuid7()), "text": "turn one", "model": "mock/echo"}
    )
    conversation_id = first[0].data["conversation_id"]

    second = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid7()),
            "conversation_id": conversation_id,
            "text": "turn two",
            "model": "mock/echo",
        },
    )
    assert second[0].data["conversation_id"] == conversation_id

    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    # thread is parent-linked
    assert messages[1]["parent_id"] == messages[0]["id"]
    assert messages[2]["parent_id"] == messages[1]["id"]


async def test_idempotent_retry_replays_finished_turn(live):
    client = live.client
    headers = await register_user(client)
    message_id = str(uuid7())
    payload = {"message_id": message_id, "text": "replay me", "model": "mock/echo"}

    original = await collect_chat(client, headers, payload)
    original_text = delta_text(original)

    # stream is finished & evicted or not — the retry must NOT create a new turn
    await asyncio.sleep(0.1)
    replay = await collect_chat(client, headers, payload)
    assert delta_text(replay) == original_text
    assert replay[0].data["message_id"] == original[0].data["message_id"]

    conversation_id = original[0].data["conversation_id"]
    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert len(messages) == 2  # no duplicates


async def test_regenerate_creates_sibling_branch(live):
    client = live.client
    headers = await register_user(client)

    first = await collect_chat(
        client, headers, {"message_id": str(uuid7()), "text": "branch here", "model": "mock/echo"}
    )
    conversation_id = first[0].data["conversation_id"]
    assistant_id = first[0].data["message_id"]

    regen = await collect_chat(
        client,
        headers,
        {"message_id": str(uuid7())},
        path=f"/api/messages/{assistant_id}/regenerate",
    )
    assert regen[0].data["message_id"] != assistant_id
    assert event_types(regen)[-1] == "message_end"

    active = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert len(active) == 2  # user + newest assistant only on the active branch
    assert active[1]["id"] == regen[0].data["message_id"]

    everything = (
        await client.get(f"/api/conversations/{conversation_id}/messages?all=true", headers=headers)
    ).json()["messages"]
    assert len(everything) == 3  # both assistant siblings exist


async def test_edit_creates_branch_then_generates(live):
    client = live.client
    headers = await register_user(client)

    first = await collect_chat(
        client, headers, {"message_id": str(uuid7()), "text": "original text", "model": "mock/echo"}
    )
    conversation_id = first[0].data["conversation_id"]
    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    user_message_id = messages[0]["id"]

    new_id = str(uuid7())
    edited = await client.patch(
        f"/api/messages/{user_message_id}",
        headers=headers,
        json={"text": "edited text", "new_message_id": new_id},
    )
    assert edited.status_code == 200
    assert edited.json()["id"] == new_id

    # sending the edited id generates from the new branch
    events = await collect_chat(client, headers, {"message_id": new_id, "model": "mock/echo"})
    assert "edited text" in delta_text(events)

    active = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert next(m["id"] for m in active) == new_id  # edited branch is now active


async def test_provider_auth_error_before_first_token(live):
    client = live.client
    headers = await register_user(client)
    events = await collect_chat(
        client, headers, {"message_id": str(uuid7()), "text": "x", "model": "mock/fail-auth"}
    )
    types = event_types(events)
    assert "error" in types
    error = next(e for e in events if e.event == "error").data
    assert error["code"] == "provider_auth"
    assert error["retryable"] is False
    assert events[-1].data["stop_reason"] == "error"


async def test_mid_stream_failure_keeps_partial(live):
    client = live.client
    headers = await register_user(client)
    events = await collect_chat(
        client, headers, {"message_id": str(uuid7()), "text": "x", "model": "mock/fail-mid"}
    )
    partial = delta_text(events)
    assert partial  # §7.4 rule 4: partial content kept
    error = next(e for e in events if e.event == "error").data
    assert error["retryable"] is True

    conversation_id = events[0].data["conversation_id"]
    messages = (
        await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    ).json()["messages"]
    assert messages[1]["status"] == "error"
    assert messages[1]["parts"][0]["content"]["text"] == partial
    assert messages[1]["error"]["retryable"] is True


async def test_no_model_configured(tmp_path):
    from conftest import make_settings, run_live_server

    settings = make_settings(tmp_path, models={"mock_enabled": False})
    async with run_live_server(settings) as live:
        headers = await register_user(live.client)
        response = await live.client.post(
            "/api/chat",
            headers=headers,
            json={"message_id": str(uuid7()), "text": "anyone there?"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "no_model_configured"


async def test_title_generated_in_background(live):
    client = live.client
    headers = await register_user(client)
    events = await collect_chat(
        client,
        headers,
        {"message_id": str(uuid7()), "text": "explain rust lifetimes", "model": "mock/echo"},
    )
    conversation_id = events[0].data["conversation_id"]

    title = None
    for _ in range(60):
        await asyncio.sleep(0.1)
        rows = (await client.get("/api/conversations", headers=headers)).json()
        if rows and rows[0]["title"]:
            title = rows[0]["title"]
            break
    assert title == "explain rust lifetimes"
    # a user rename is never clobbered by a late job
    await client.patch(
        f"/api/conversations/{conversation_id}", headers=headers, json={"title": "Mine"}
    )
    rows = (await client.get("/api/conversations", headers=headers)).json()
    assert rows[0]["title"] == "Mine"
