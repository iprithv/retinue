"""Shares (§18), admin surface, and data export/import round-trip."""

import io
import uuid
import zipfile

from tests.support import collect_chat, register_user


async def _seed_conversation(client, headers, text="share me"):
    events = await collect_chat(client, headers, {"message_id": str(uuid.uuid4()), "text": text})
    return next(e for e in events if e.event == "message_start").data["conversation_id"]


async def test_share_lifecycle(app_client):
    client, _ = app_client
    headers = await register_user(client)
    conversation_id = await _seed_conversation(client, headers)

    response = await client.post(
        f"/api/conversations/{conversation_id}/share", headers=headers, json={}
    )
    assert response.status_code == 201
    share = response.json()

    # public read requires no auth
    thread = (await client.get(f"/api/share/{share['token']}")).json()
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]
    assert "share me" in thread["messages"][0]["parts"][0]["content"]["text"]

    # revoke → 404
    response = await client.delete(f"/api/shares/{share['id']}", headers=headers)
    assert response.status_code == 204
    assert (await client.get(f"/api/share/{share['token']}")).status_code == 404


async def test_share_other_users_cannot_revoke(app_client):
    client, _ = app_client
    headers_a = await register_user(client, email="a@test.dev")
    headers_b = await register_user(client, email="b@test.dev")
    conversation_id = await _seed_conversation(client, headers_a)
    share = (
        await client.post(f"/api/conversations/{conversation_id}/share", headers=headers_a, json={})
    ).json()
    response = await client.delete(f"/api/shares/{share['id']}", headers=headers_b)
    assert response.status_code == 404


async def test_admin_surface(app_client):
    client, _ = app_client
    admin_headers = await register_user(client, email="admin@test.dev")  # first user = owner
    member_headers = await register_user(client, email="member@test.dev")

    users = (await client.get("/api/admin/users", headers=admin_headers)).json()
    assert len(users) == 2
    assert users[0]["role"] == "owner"
    member = next(u for u in users if u["email"] == "member@test.dev")

    # member cannot reach admin routes
    assert (await client.get("/api/admin/users", headers=member_headers)).status_code == 403

    # promote member to admin, then deactivate (forces global logout)
    response = await client.patch(
        f"/api/admin/users/{member['id']}", headers=admin_headers, json={"role": "admin"}
    )
    assert response.json()["role"] == "admin"
    response = await client.patch(
        f"/api/admin/users/{member['id']}", headers=admin_headers, json={"is_active": False}
    )
    assert response.json()["is_active"] is False
    assert (await client.get("/api/auth/me", headers=member_headers)).status_code == 401

    # settings store round-trip
    response = await client.patch(
        "/api/admin/settings",
        headers=admin_headers,
        json={"settings": {"registration_enabled": {"value": False}}},
    )
    assert response.json()["settings"]["registration_enabled"] == {"value": False}

    # jobs listing (title job from seeding may be present) + audit
    await _seed_conversation(client, admin_headers)
    jobs = (await client.get("/api/admin/jobs", headers=admin_headers)).json()
    assert isinstance(jobs, list)
    audit = (await client.get("/api/admin/audit", headers=admin_headers)).json()
    assert isinstance(audit, list)

    usage = (await client.get("/api/admin/usage", headers=admin_headers)).json()
    assert any(row["email"] == "admin@test.dev" for row in usage["by_user"])


async def test_export_import_roundtrip(app_client):
    client, _ = app_client
    headers = await register_user(client)
    await _seed_conversation(client, headers, "the exported saga")
    await client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Exported Agent", "system_prompt": "hi", "model": "mock/echo"},
    )
    await client.post("/api/memories", headers=headers, json={"content": "likes trains"})

    response = await client.get("/api/export", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert {"manifest.json", "conversations.jsonl", "agents.jsonl", "memories.jsonl"} <= set(
        archive.namelist()
    )

    # import into a fresh account
    headers2 = await register_user(client, email="second@test.dev")
    response = await client.post(
        "/api/import",
        headers=headers2,
        files={"file": ("export.zip", response.content, "application/zip")},
    )
    assert response.status_code == 200, response.text
    counts = response.json()
    assert counts == {"conversations": 1, "agents": 1, "memories": 1}

    conversations = (await client.get("/api/conversations", headers=headers2)).json()
    assert len(conversations) == 1
    messages = (
        await client.get(f"/api/conversations/{conversations[0]['id']}/messages", headers=headers2)
    ).json()["messages"]
    assert "the exported saga" in messages[0]["parts"][0]["content"]["text"]

    agents = (await client.get("/api/agents", headers=headers2)).json()
    assert agents[0]["name"] == "Exported Agent"
    memories = (await client.get("/api/memories", headers=headers2)).json()
    assert memories[0]["content"] == "likes trains"

    # garbage archive is rejected
    response = await client.post(
        "/api/import",
        headers=headers2,
        files={"file": ("junk.zip", b"not a zip", "application/zip")},
    )
    assert response.status_code == 422
