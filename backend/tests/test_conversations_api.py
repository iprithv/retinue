"""Conversation CRUD, ownership isolation."""

from support import register_user


async def test_crud_roundtrip(app_client):
    client, _ = app_client
    headers = await register_user(client)

    created = await client.post("/api/conversations", headers=headers, json={"title": "My chat"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    listing = await client.get("/api/conversations", headers=headers)
    assert [c["id"] for c in listing.json()] == [conversation_id]

    patched = await client.patch(
        f"/api/conversations/{conversation_id}",
        headers=headers,
        json={"title": "Renamed", "pinned": True},
    )
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["pinned"] is True

    archived = await client.patch(
        f"/api/conversations/{conversation_id}", headers=headers, json={"is_archived": True}
    )
    assert archived.json()["is_archived"] is True
    assert (await client.get("/api/conversations", headers=headers)).json() == []
    assert len((await client.get("/api/conversations?archived=true", headers=headers)).json()) == 1

    deleted = await client.delete(f"/api/conversations/{conversation_id}", headers=headers)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/conversations/{conversation_id}", headers=headers)
    ).status_code == 404


async def test_ownership_isolation(app_client):
    client, _ = app_client
    alice = await register_user(client, "alice@test.dev")
    bob = await register_user(client, "bob@test.dev")

    created = await client.post("/api/conversations", headers=alice, json={"title": "secret"})
    conversation_id = created.json()["id"]

    assert (await client.get("/api/conversations", headers=bob)).json() == []
    for method, url in [
        ("get", f"/api/conversations/{conversation_id}"),
        ("delete", f"/api/conversations/{conversation_id}"),
        ("get", f"/api/conversations/{conversation_id}/messages"),
    ]:
        response = await getattr(client, method)(url, headers=bob)
        assert response.status_code == 404, f"{method} {url} leaked"
