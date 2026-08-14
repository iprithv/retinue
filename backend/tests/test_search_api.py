"""FTS5 search (§13): messages, conversations, files, agents; trigger sync."""

import uuid

from tests.support import collect_chat, register_user


async def test_search_across_scopes(app_client):
    client, _ = app_client
    headers = await register_user(client)

    # a conversation whose reply mentions a distinctive word
    events = await collect_chat(
        client,
        headers,
        {"message_id": str(uuid.uuid4()), "text": "tell me about the zephyrium alloy"},
    )
    conversation_id = next(e for e in events if e.event == "message_start").data["conversation_id"]

    # a file containing another distinctive word
    await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("ore.txt", b"the mine yields pure obsidianite ore", "text/plain")},
    )
    # an agent
    await client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": "Zephyrium Expert",
            "system_prompt": "You know alloys.",
            "model": "mock/echo",
        },
    )

    # message search (mock echo repeats the user text back)
    result = (await client.get("/api/search", headers=headers, params={"q": "zephyrium"})).json()
    kinds = {h["kind"] for h in result["hits"]}
    assert "message" in kinds
    assert "agent" in kinds
    message_hit = next(h for h in result["hits"] if h["kind"] == "message")
    assert message_hit["conversation_id"] == conversation_id
    assert "zephyrium" in message_hit["snippet"].lower()

    # file-name + content search (extraction may still be pending → name hit)
    result = (
        await client.get("/api/search", headers=headers, params={"q": "ore", "scope": "files"})
    ).json()
    assert any(h["kind"] == "file" for h in result["hits"])

    # scope filtering
    result = (
        await client.get(
            "/api/search", headers=headers, params={"q": "zephyrium", "scope": "agents"}
        )
    ).json()
    assert {h["kind"] for h in result["hits"]} == {"agent"}


async def test_search_prefix_as_you_type(app_client):
    client, _ = app_client
    headers = await register_user(client)
    await collect_chat(
        client,
        headers,
        {"message_id": str(uuid.uuid4()), "text": "the quokka population is thriving"},
    )
    result = (await client.get("/api/search", headers=headers, params={"q": "quok"})).json()
    assert any(h["kind"] == "message" for h in result["hits"])


async def test_search_ignores_other_users(app_client):
    client, _ = app_client
    headers_a = await register_user(client, email="a@test.dev")
    headers_b = await register_user(client, email="b@test.dev")
    await collect_chat(
        client, headers_a, {"message_id": str(uuid.uuid4()), "text": "supersecretword"}
    )
    result = (
        await client.get("/api/search", headers=headers_b, params={"q": "supersecretword"})
    ).json()
    assert result["hits"] == []


async def test_search_operators_are_neutralized(app_client):
    client, _ = app_client
    headers = await register_user(client)
    # raw FTS operators must not crash the endpoint
    for q in ['"unbalanced', "a AND OR NOT", "col:x", "(paren", "*star"]:
        response = await client.get("/api/search", headers=headers, params={"q": q})
        assert response.status_code == 200, (q, response.text)
