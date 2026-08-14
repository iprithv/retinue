"""API keys, usage summary, health/export/proxy."""

import io
import zipfile

from support import register_user


async def test_api_key_lifecycle(app_client):
    client, _ = app_client
    headers = await register_user(client)

    created = await client.post(
        "/api/keys", headers=headers, json={"name": "ci-bot", "expires_days": 30}
    )
    assert created.status_code == 201
    raw_key = created.json()["key"]
    assert raw_key.startswith("rtn_")

    # the raw key authenticates
    me = await client.get("/api/auth/me", headers={"authorization": f"Bearer {raw_key}"})
    assert me.status_code == 200

    # listing never re-exposes the raw key
    listing = (await client.get("/api/keys", headers=headers)).json()
    assert len(listing) == 1
    assert "key" not in listing[0]

    deleted = await client.delete(f"/api/keys/{created.json()['id']}", headers=headers)
    assert deleted.status_code == 204
    revoked = await client.get("/api/auth/me", headers={"authorization": f"Bearer {raw_key}"})
    assert revoked.status_code == 401


async def test_bogus_api_key_rejected(app_client):
    client, _ = app_client
    response = await client.get(
        "/api/auth/me", headers={"authorization": "Bearer rtn_totally-made-up"}
    )
    assert response.status_code == 401


async def test_healthz_and_readyz(app_client):
    client, _ = app_client
    health = await client.get("/api/healthz")
    assert health.status_code == 200
    assert health.json()["name"] == "retinue"
    assert health.json()["version"]

    ready = await client.get("/api/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": True, "data_dir": True}


async def test_security_headers_present(app_client):
    client, _ = app_client
    response = await client.get("/api/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "x-request-id" in response.headers


async def test_export_zip(app_client):
    client, _ = app_client
    headers = await register_user(client)
    await client.post("/api/conversations", headers=headers, json={"title": "exported"})

    response = await client.get("/api/export", headers=headers)
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert {"conversations.jsonl", "agents.jsonl", "memories.jsonl", "manifest.json"} <= names
    assert b"exported" in archive.read("conversations.jsonl")


async def test_image_proxy_blocks_private_targets(app_client):
    client, _ = app_client
    headers = await register_user(client)
    for target in (
        "http://127.0.0.1/x.png",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8000/secret.png",
        "ftp://example.com/x.png",
        "http://example.com:8080/x.png",
    ):
        response = await client.get("/api/proxy/img", headers=headers, params={"url": target})
        assert response.status_code == 400, target
        assert response.json()["error"]["code"] == "egress_denied"


async def test_usage_summary_empty(app_client):
    client, _ = app_client
    headers = await register_user(client)
    summary = (await client.get("/api/usage/summary?days=7", headers=headers)).json()
    assert summary["totals"]["messages"] == 0
    assert summary["by_model"] == []
