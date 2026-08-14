"""Memory system (§14): CRUD, review-mode approval, context injection,
incognito exclusion, extraction parsing."""

import uuid

from tests.support import collect_chat, register_user

from retinue.jobs.handlers.memory import parse_memory_response


def test_parse_memory_response_shapes():
    assert parse_memory_response('["a", "b"]') == ["a", "b"]
    assert parse_memory_response('Here you go: ["x"] thanks') == ["x"]
    assert parse_memory_response("- likes tea\n- vegetarian\ntext") == [
        "likes tea",
        "vegetarian",
    ]
    assert parse_memory_response("[]") == []
    assert parse_memory_response("nothing here") == []
    # cap at 3
    assert len(parse_memory_response('["1","2","3","4","5"]')) == 3


async def test_memory_crud_and_approval(app_client):
    client, _ = app_client
    headers = await register_user(client)

    created = (
        await client.post(
            "/api/memories", headers=headers, json={"content": "prefers metric units"}
        )
    ).json()
    assert created["status"] == "active"

    listed = (await client.get("/api/memories", headers=headers)).json()
    assert len(listed) == 1

    patched = (
        await client.patch(
            f"/api/memories/{created['id']}",
            headers=headers,
            json={"status": "disabled"},
        )
    ).json()
    assert patched["status"] == "disabled"

    # approve only works on proposed
    response = await client.post(f"/api/memories/{created['id']}/approve", headers=headers)
    assert response.status_code == 409

    response = await client.delete(f"/api/memories/{created['id']}", headers=headers)
    assert response.status_code == 204
    assert (await client.get("/api/memories", headers=headers)).json() == []


async def test_active_memory_reaches_model_context(app_client):
    client, app = app_client
    headers = await register_user(client)
    await client.post(
        "/api/memories",
        headers=headers,
        json={"content": "the user's dog is named Biscuit"},
    )

    captured: dict = {}
    state = app.state.retinue
    adapter = state.registry.adapter_for("mock/echo")
    original = adapter.stream

    def spy(call):
        captured["messages"] = call.messages
        return original(call)

    adapter.stream = spy
    try:
        await collect_chat(
            client,
            headers,
            {"message_id": str(uuid.uuid4()), "text": "what is my dog called?"},
        )
    finally:
        adapter.stream = original

    system = next((m["content"] for m in captured["messages"] if m["role"] == "system"), "")
    assert "Biscuit" in system
    assert "## Memory" in system


async def test_incognito_conversation_skips_memory(app_client):
    client, app = app_client
    headers = await register_user(client)
    await client.post("/api/memories", headers=headers, json={"content": "secret preference"})

    conversation = (await client.post("/api/conversations", headers=headers, json={})).json()
    # flip incognito directly (creation API exposes it via patch in the UI)
    state = app.state.retinue
    from retinue.db.models import Conversation

    async with state.db.write_session() as session:
        row = await session.get(Conversation, uuid.UUID(conversation["id"]))
        row.is_incognito = True

    captured: dict = {}
    adapter = state.registry.adapter_for("mock/echo")
    original = adapter.stream

    def spy(call):
        captured["messages"] = call.messages
        return original(call)

    adapter.stream = spy
    try:
        await collect_chat(
            client,
            headers,
            {
                "message_id": str(uuid.uuid4()),
                "conversation_id": conversation["id"],
                "text": "hello",
            },
        )
    finally:
        adapter.stream = original

    system = next((m["content"] for m in captured["messages"] if m["role"] == "system"), "")
    assert "secret preference" not in system
