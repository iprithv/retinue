"""Regression tests for the security-review bug fixes (authz, SSRF, races)."""

import uuid

import pytest
from tests.support import register_user

# -- Bug 1: stdio MCP is admin-only (host RCE) --------------------------------------


async def test_stdio_mcp_requires_admin(app_client):
    client, _ = app_client
    owner = await register_user(client, email="owner@test.dev")  # first = owner
    member = await register_user(client, email="member@test.dev")

    response = await client.post(
        "/api/mcp/servers",
        headers=member,
        json={"name": "evil", "transport": "stdio", "command": "/bin/sh", "args": ["-c", "id"]},
    )
    assert response.status_code == 403
    assert "admin" in response.json()["error"]["message"].lower()

    # the owner may create it
    response = await client.post(
        "/api/mcp/servers",
        headers=owner,
        json={"name": "ok", "transport": "stdio", "command": "echo", "args": ["hi"]},
    )
    assert response.status_code == 201


# -- Bug 2: SSRF via MCP HTTP url and OpenAPI allowlist ------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",  # cloud metadata, blocked for all
        "http://127.0.0.1:9000/",  # loopback, member-forbidden
        "http://10.0.0.5/mcp",  # private, member-forbidden
    ],
)
async def test_http_mcp_url_egress_guarded(app_client, url):
    client, _ = app_client
    await register_user(client, email="owner@test.dev")
    member = await register_user(client, email="member@test.dev")
    response = await client.post(
        "/api/mcp/servers",
        headers=member,
        json={"name": "x", "transport": "http", "url": url},
    )
    assert response.status_code == 400  # EGRESS_DENIED


async def test_action_allowlist_metadata_blocked_even_for_admin(app_client):
    client, _ = app_client
    owner = await register_user(client)  # owner
    response = await client.post(
        "/api/actions",
        headers=owner,
        json={
            "name": "meta",
            "spec": {
                "openapi": "3.0.0",
                "info": {"title": "x", "version": "1"},
                "servers": [{"url": "http://example.com"}],
                "paths": {"/x": {"get": {"operationId": "getX"}}},
            },
            "host_allowlist": ["169.254.169.254"],
        },
    )
    assert response.status_code == 400  # metadata host never permitted


async def test_action_allowlist_private_requires_admin(app_client):
    client, _ = app_client
    await register_user(client, email="owner@test.dev")
    member = await register_user(client, email="member@test.dev")
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x", "version": "1"},
        "servers": [{"url": "http://example.com"}],
        "paths": {"/x": {"get": {"operationId": "getX"}}},
    }
    # member cannot allowlist a private host
    response = await client.post(
        "/api/actions",
        headers=member,
        json={"name": "p", "spec": spec, "host_allowlist": ["10.1.2.3"]},
    )
    assert response.status_code == 400
    # a public host is fine for members
    response = await client.post(
        "/api/actions",
        headers=member,
        json={"name": "pub", "spec": spec, "host_allowlist": ["example.com"]},
    )
    assert response.status_code == 201


# -- Bug 3: members cannot mutate org-global MCP servers ----------------------------


async def test_member_cannot_mutate_org_server(app_client):
    client, _ = app_client
    owner = await register_user(client, email="owner@test.dev")
    member = await register_user(client, email="member@test.dev")

    org = (
        await client.post(
            "/api/mcp/servers",
            headers=owner,
            json={
                "name": "shared",
                "transport": "http",
                "url": "https://example.com/mcp",
                "org": True,
            },
        )
    ).json()
    assert org["org"] is True

    # visible to the member (read)
    servers = (await client.get("/api/mcp/servers", headers=member)).json()
    assert any(s["id"] == org["id"] for s in servers)

    # but not patchable/deletable by the member
    response = await client.patch(
        f"/api/mcp/servers/{org['id']}", headers=member, json={"enabled": False}
    )
    assert response.status_code == 403
    response = await client.delete(f"/api/mcp/servers/{org['id']}", headers=member)
    assert response.status_code == 403


# -- Bug 4: cross-user RAG collection leak ------------------------------------------


async def test_agent_cannot_pin_foreign_collection(app_client):
    client, _ = app_client
    owner = await register_user(client, email="owner@test.dev")
    victim_collection = (
        await client.post("/api/collections", headers=owner, json={"name": "private KB"})
    ).json()

    attacker = await register_user(client, email="attacker@test.dev")
    response = await client.post(
        "/api/agents",
        headers=attacker,
        json={
            "name": "thief",
            "system_prompt": "x",
            "model": "mock/echo",
            "collection_ids": [victim_collection["id"]],
        },
    )
    assert response.status_code == 404  # not found or not accessible


# -- Bug 5: preflight cannot probe a foreign MCP server -----------------------------


async def test_preflight_skips_inaccessible_mcp(app_client):
    client, _ = app_client
    owner = await register_user(client, email="owner@test.dev")
    victim_server = (
        await client.post(
            "/api/mcp/servers",
            headers=owner,
            json={"name": "victim", "transport": "http", "url": "https://example.com/mcp"},
        )
    ).json()

    attacker = await register_user(client, email="attacker@test.dev")
    agent = (
        await client.post(
            "/api/agents",
            headers=attacker,
            json={
                "name": "probe",
                "system_prompt": "x",
                "model": "mock/echo",
                "mcp_servers": [{"server_id": victim_server["id"]}],
            },
        )
    ).json()
    report = (
        await client.post(f"/api/agents/{agent['id']}/preflight", headers=attacker, json={})
    ).json()
    mcp_item = next(i for i in report["items"] if i["check"] == "mcp")
    assert mcp_item["ok"] is False
    assert "not found or not accessible" in mcp_item["detail"]


# -- Bug 6: db_sample honors the table deny list ------------------------------------


async def test_sample_enforces_table_policy(app_client, tmp_path):
    import sqlite3

    db_path = tmp_path / "s.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE public_t (id INT); INSERT INTO public_t VALUES (1);"
        "CREATE TABLE secrets (id INT); INSERT INTO secrets VALUES (9);"
    )
    conn.commit()
    conn.close()

    client, _ = app_client
    owner = await register_user(client)
    source = (
        await client.post(
            "/api/datasources",
            headers=owner,
            json={
                "name": "s",
                "engine": "sqlite",
                "config": {"path": str(db_path)},
                "policy": {"deny_tables": ["secrets"]},
            },
        )
    ).json()

    ok = await client.get(
        f"/api/datasources/{source['id']}/sample",
        headers=owner,
        params={"table": "public_t"},
    )
    assert ok.status_code == 200
    denied = await client.get(
        f"/api/datasources/{source['id']}/sample",
        headers=owner,
        params={"table": "secrets"},
    )
    assert denied.status_code == 422
    assert "denied" in denied.json()["error"]["message"]


# -- Bug 7: bad file_ids do not 500 or recurse forever ------------------------------


async def test_chat_with_bad_file_id_returns_4xx(app_client):
    client, _ = app_client
    headers = await register_user(client)
    response = await client.post(
        "/api/chat",
        headers=headers,
        json={
            "message_id": str(uuid.uuid4()),
            "text": "hi",
            "file_ids": [str(uuid.uuid4())],  # nonexistent
        },
    )
    assert response.status_code == 404  # graceful, not 500


# -- Bug 10: admins cannot promote to owner -----------------------------------------


async def test_admin_cannot_grant_owner(app_client):
    client, _ = app_client
    owner = await register_user(client, email="owner@test.dev")
    await register_user(client, email="admin@test.dev")
    users = (await client.get("/api/admin/users", headers=owner)).json()
    admin_id = next(u["id"] for u in users if u["email"] == "admin@test.dev")
    await client.patch(f"/api/admin/users/{admin_id}", headers=owner, json={"role": "admin"})

    # re-login as the admin to pick up the new role
    login = await client.post(
        "/api/auth/login", json={"email": "admin@test.dev", "password": "hunter2secret"}
    )
    admin_headers = {"authorization": f"Bearer {login.json()['access_token']}"}

    # admin promotes a member — allowed
    await register_user(client, email="m@test.dev")
    member_id = next(
        u["id"]
        for u in (await client.get("/api/admin/users", headers=admin_headers)).json()
        if u["email"] == "m@test.dev"
    )
    ok = await client.patch(
        f"/api/admin/users/{member_id}", headers=admin_headers, json={"role": "member"}
    )
    assert ok.status_code == 200
    # admin tries to grant owner — forbidden
    escalate = await client.patch(
        f"/api/admin/users/{member_id}", headers=admin_headers, json={"role": "owner"}
    )
    assert escalate.status_code == 403
    # admin tries to self-promote to owner — forbidden
    self_escalate = await client.patch(
        f"/api/admin/users/{admin_id}", headers=admin_headers, json={"role": "owner"}
    )
    assert self_escalate.status_code == 403


# -- registration_enabled admin setting is honored ----------------------------------


async def test_registration_toggle_via_admin_setting(app_client):
    client, _ = app_client
    owner = await register_user(client, email="owner@test.dev")
    response = await client.patch(
        "/api/admin/settings",
        headers=owner,
        json={"settings": {"registration_enabled": {"value": False}}},
    )
    assert response.status_code == 200
    # a new registration is now refused at runtime
    blocked = await client.post(
        "/api/auth/register", json={"email": "late@test.dev", "password": "hunter2secret"}
    )
    assert blocked.status_code == 403


# -- fork carries attachments -------------------------------------------------------


async def test_fork_copies_attachments(app_client):
    from tests.support import collect_chat

    client, _ = app_client
    headers = await register_user(client)
    upload = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    file_id = upload.json()["id"]
    events = await collect_chat(
        client,
        headers,
        {"message_id": str(uuid.uuid4()), "text": "look", "file_ids": [file_id]},
    )
    conversation_id = next(e for e in events if e.event == "message_start").data["conversation_id"]

    fork = (await client.post(f"/api/conversations/{conversation_id}/fork", headers=headers)).json()
    messages = (
        await client.get(f"/api/conversations/{fork['id']}/messages", headers=headers)
    ).json()["messages"]
    user_msg = messages[0]
    assert user_msg["attachments"], "fork dropped the attachment"
    assert user_msg["attachments"][0]["file_id"] == file_id
