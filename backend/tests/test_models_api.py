"""Model catalog, credentials (encrypted at rest), policies."""

from sqlalchemy import select

from retinue.core.ids import uuid7
from retinue.db.models import Credential, ModelPolicy
from support import register_user


async def test_mock_models_listed(app_client):
    client, _ = app_client
    headers = await register_user(client)
    models = (await client.get("/api/models", headers=headers)).json()
    ids = [m["id"] for m in models]
    assert "mock/echo" in ids
    echo = next(m for m in models if m["id"] == "mock/echo")
    assert echo["context_window"] > 0


async def test_credentials_encrypted_roundtrip(app_client):
    client, app = app_client
    headers = await register_user(client)

    created = await client.post(
        "/api/providers/credentials",
        headers=headers,
        json={"provider": "OpenAI", "api_key": "sk-test-1234abcd"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["provider"] == "openai"  # normalized
    assert body["key_hint"] == "…abcd"
    assert "sk-test" not in created.text.replace("…abcd", "")

    # at rest: ciphertext only
    state = app.state.retinue
    async with state.db.read_session() as session:
        row = (await session.execute(select(Credential))).scalar_one()
    assert b"sk-test-1234abcd" not in row.data_ciphertext
    assert (
        state.box.decrypt(row.data_ciphertext, row.data_nonce) == b'{"api_key":"sk-test-1234abcd"}'
    )

    listing = (await client.get("/api/providers/credentials", headers=headers)).json()
    assert listing[0]["key_hint"] == "…abcd"
    assert listing[0]["org"] is False

    deleted = await client.delete(f"/api/providers/credentials/{body['id']}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get("/api/providers/credentials", headers=headers)).json() == []


async def test_org_credentials_require_admin(app_client):
    client, _ = app_client
    owner = await register_user(client, "owner@test.dev")  # first user = owner
    member = await register_user(client, "member@test.dev")

    denied = await client.post(
        "/api/providers/credentials",
        headers=member,
        json={"provider": "openai", "api_key": "sk-nope", "org": True},
    )
    assert denied.status_code == 403

    allowed = await client.post(
        "/api/providers/credentials",
        headers=owner,
        json={"provider": "openai", "api_key": "sk-org-key-9999", "org": True},
    )
    assert allowed.status_code == 201
    assert allowed.json()["org"] is True

    # org credentials are visible to members (redacted)
    listing = (await client.get("/api/providers/credentials", headers=member)).json()
    assert len(listing) == 1 and listing[0]["org"] is True
    # ...but not deletable by them
    assert (
        await client.delete(f"/api/providers/credentials/{listing[0]['id']}", headers=member)
    ).status_code == 403


async def test_model_policy_deny_filters_catalog(app_client):
    client, app = app_client
    headers = await register_user(client)
    state = app.state.retinue

    async with state.db.write_session() as session:
        session.add(ModelPolicy(id=uuid7(), pattern="mock/slow", allow=False, note="test"))
    state.registry.invalidate_models_cache()

    ids = [m["id"] for m in (await client.get("/api/models", headers=headers)).json()]
    assert "mock/echo" in ids
    assert "mock/slow" not in ids


async def test_models_refresh_admin_only(app_client):
    client, _ = app_client
    owner = await register_user(client, "own@test.dev")
    member = await register_user(client, "mem@test.dev")
    assert (await client.post("/api/models/refresh", headers=member)).status_code == 403
    assert (await client.post("/api/models/refresh", headers=owner)).status_code == 200
