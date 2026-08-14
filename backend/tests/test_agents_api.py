"""Agents: versioning, pinning, revert, export/import, test bench (§9)."""

import uuid

from tests.support import collect_chat, parse_sse, register_user

AGENT_BODY = {
    "name": "Code Detective",
    "description": "finds bugs",
    "system_prompt": "You are a meticulous code detective.",
    "model": "mock/echo",
    "params": {"temperature": 0.2},
    "starters": ["Find the bug in..."],
}


async def _create_agent(client, headers, **overrides):
    response = await client.post("/api/agents", headers=headers, json={**AGENT_BODY, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def test_agent_create_and_versioning(app_client):
    client, _ = app_client
    headers = await register_user(client)

    agent = await _create_agent(client, headers)
    assert agent["slug"] == "code-detective"
    assert agent["current_version"]["version"] == 1
    assert agent["current_version"]["model"] == "mock/echo"

    # editing behavior creates v2 and moves the pointer
    response = await client.post(
        f"/api/agents/{agent['id']}/versions",
        headers=headers,
        json={**AGENT_BODY, "system_prompt": "v2 prompt", "changelog": "tighten prompt"},
    )
    assert response.status_code == 201
    assert response.json()["version"] == 2

    detail = (await client.get(f"/api/agents/{agent['id']}", headers=headers)).json()
    assert detail["current_version"]["version"] == 2
    assert detail["current_version"]["system_prompt"] == "v2 prompt"

    versions = (await client.get(f"/api/agents/{agent['id']}/versions", headers=headers)).json()
    assert [v["version"] for v in versions] == [2, 1]

    # revert = new version copying the old one (history stays linear)
    response = await client.post(f"/api/agents/{agent['id']}/revert/1", headers=headers)
    assert response.status_code == 201
    reverted = response.json()
    assert reverted["version"] == 3
    assert reverted["system_prompt"] == AGENT_BODY["system_prompt"]


async def test_agent_slug_uniqueness(app_client):
    client, _ = app_client
    headers = await register_user(client)
    first = await _create_agent(client, headers)
    second = await _create_agent(client, headers)
    assert first["slug"] == "code-detective"
    assert second["slug"] == "code-detective-2"


async def test_agent_export_import_roundtrip(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = await _create_agent(client, headers)

    exported = (await client.get(f"/api/agents/{agent['id']}/export", headers=headers)).json()
    assert exported["schema"] == "retinue.agent/1"
    assert exported["system_prompt"] == AGENT_BODY["system_prompt"]
    assert "id" not in exported

    response = await client.post("/api/agents/import", headers=headers, json=exported)
    assert response.status_code == 201
    imported = response.json()
    assert imported["id"] != agent["id"]
    assert imported["current_version"]["system_prompt"] == AGENT_BODY["system_prompt"]

    bad = {**exported, "schema": "other/9"}
    response = await client.post("/api/agents/import", headers=headers, json=bad)
    assert response.status_code == 422


async def test_conversation_pins_agent_version(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = await _create_agent(client, headers)
    v1_id = agent["current_version"]["id"]

    events = await collect_chat(
        client,
        headers,
        {"message_id": str(uuid.uuid4()), "text": "hello", "agent_id": agent["id"]},
    )
    start = next(e for e in events if e.event == "message_start")
    assert start.data["agent_version_id"] == v1_id
    assert start.data["model"] == "mock/echo"
    conversation_id = start.data["conversation_id"]

    # new agent version must NOT affect the pinned conversation
    await client.post(
        f"/api/agents/{agent['id']}/versions",
        headers=headers,
        json={**AGENT_BODY, "model": "mock/slow", "system_prompt": "changed"},
    )
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "text": "again",
        },
    )
    start = next(e for e in events if e.event == "message_start")
    assert start.data["agent_version_id"] == v1_id
    assert start.data["model"] == "mock/echo"

    # the banner action moves the pin
    response = await client.post(
        f"/api/conversations/{conversation_id}/repin-agent", headers=headers
    )
    assert response.status_code == 200
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "text": "third",
        },
    )
    start = next(e for e in events if e.event == "message_start")
    assert start.data["agent_version_id"] != v1_id
    assert start.data["model"] == "mock/slow"


async def test_agent_test_bench_is_ephemeral(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = await _create_agent(client, headers)

    async with client.stream(
        "POST",
        f"/api/agents/{agent['id']}/test",
        headers=headers,
        json={"messages": [{"role": "user", "content": "ping"}]},
    ) as response:
        body = await response.aread()
    assert response.status_code == 200
    events = parse_sse(body)
    kinds = [e.event for e in events]
    assert "message_start" in kinds and "message_end" in kinds
    assert any(e.event == "delta" for e in events)
    assert events[0].data.get("ephemeral") is True

    # nothing persisted
    conversations = (await client.get("/api/conversations", headers=headers)).json()
    assert conversations == []


async def test_agent_delete_archives_when_in_use(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = await _create_agent(client, headers)

    await collect_chat(
        client,
        headers,
        {"message_id": str(uuid.uuid4()), "text": "hi", "agent_id": agent["id"]},
    )
    response = await client.delete(f"/api/agents/{agent['id']}", headers=headers)
    assert response.status_code == 204
    detail = (await client.get(f"/api/agents/{agent['id']}", headers=headers)).json()
    assert detail["is_archived"] is True

    unused = await _create_agent(client, headers, name="Unused")
    response = await client.delete(f"/api/agents/{unused['id']}", headers=headers)
    assert response.status_code == 204
    response = await client.get(f"/api/agents/{unused['id']}", headers=headers)
    assert response.status_code == 404


async def test_fork_conversation(app_client):
    client, _ = app_client
    headers = await register_user(client)
    events = await collect_chat(client, headers, {"message_id": str(uuid.uuid4()), "text": "one"})
    conversation_id = next(e for e in events if e.event == "message_start").data["conversation_id"]

    response = await client.post(f"/api/conversations/{conversation_id}/fork", headers=headers)
    assert response.status_code == 201
    fork = response.json()
    assert fork["id"] != conversation_id
    assert fork["forked_from_message_id"] is not None

    messages = (
        await client.get(f"/api/conversations/{fork['id']}/messages", headers=headers)
    ).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
