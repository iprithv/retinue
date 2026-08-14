"""Auth flows (§16): register, login, refresh rotation + reuse detection,
session-version bump on password change."""

from support import register_user


async def test_register_and_me(app_client):
    client, _ = app_client
    response = await client.post(
        "/api/auth/register",
        json={"email": "First@Example.com", "password": "hunter2secret", "name": "First"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "first@example.com"  # normalized
    assert body["user"]["role"] == "owner"  # first user
    assert body["refresh_token"].startswith("rtr_")
    assert "retinue_refresh=" in response.headers.get("set-cookie", "")

    me = await client.get(
        "/api/auth/me", headers={"authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "first@example.com"

    second = await client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "hunter2secret"}
    )
    assert second.json()["user"]["role"] == "member"


async def test_duplicate_email_conflict(app_client):
    client, _ = app_client
    await register_user(client, "dup@test.dev")
    response = await client.post(
        "/api/auth/register", json={"email": "dup@test.dev", "password": "hunter2secret"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "validation_error"


async def test_login_wrong_password(app_client):
    client, _ = app_client
    await register_user(client, "login@test.dev")
    response = await client.post(
        "/api/auth/login", json={"email": "login@test.dev", "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    # unknown email answers identically
    response = await client.post(
        "/api/auth/login", json={"email": "nobody@test.dev", "password": "whatever123"}
    )
    assert response.status_code == 401


async def test_unauthenticated_401_envelope(app_client):
    client, _ = app_client
    response = await client.get("/api/conversations")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_refresh_rotation_and_reuse_detection(app_client):
    client, _ = app_client
    reg = await client.post(
        "/api/auth/register", json={"email": "rotate@test.dev", "password": "hunter2secret"}
    )
    first_refresh = reg.json()["refresh_token"]

    # rotate: old -> new
    rotated = await client.post("/api/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh and second_refresh != first_refresh

    # replaying the old token = theft signal -> whole family revoked
    reused = await client.post("/api/auth/refresh", json={"refresh_token": first_refresh})
    assert reused.status_code == 401
    assert "reuse" in reused.json()["error"]["message"]

    # the rotated descendant is dead too
    after = await client.post("/api/auth/refresh", json={"refresh_token": second_refresh})
    assert after.status_code == 401


async def test_cookie_refresh_requires_csrf(app_client):
    client, _ = app_client
    await register_user(client, "csrf@test.dev")  # cookies now set on the client jar
    no_header = await client.post("/api/auth/refresh")
    assert no_header.status_code == 403
    assert no_header.json()["error"]["code"] == "csrf_failed"

    csrf = client.cookies.get("retinue_csrf")
    assert csrf
    ok = await client.post("/api/auth/refresh", headers={"x-csrf-token": csrf})
    assert ok.status_code == 200


async def test_password_change_revokes_sessions(app_client):
    client, _ = app_client
    headers = await register_user(client, "pw@test.dev")
    old_access = headers["authorization"]

    response = await client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": "hunter2secret", "new_password": "even-more-secret9"},
    )
    assert response.status_code == 200
    new_access = response.json()["access_token"]

    # old access token carries a stale session_version
    stale = await client.get("/api/auth/me", headers={"authorization": old_access})
    assert stale.status_code == 401
    fresh = await client.get("/api/auth/me", headers={"authorization": f"Bearer {new_access}"})
    assert fresh.status_code == 200

    login = await client.post(
        "/api/auth/login", json={"email": "pw@test.dev", "password": "even-more-secret9"}
    )
    assert login.status_code == 200


async def test_registration_disabled_after_first_user(tmp_path):
    import httpx

    from conftest import make_settings
    from retinue.app import create_app

    settings = make_settings(tmp_path, auth={"registration_enabled": False})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # bootstrap: the very first user is always allowed
            first = await client.post(
                "/api/auth/register", json={"email": "boot@test.dev", "password": "hunter2secret"}
            )
            assert first.status_code == 200
            blocked = await client.post(
                "/api/auth/register", json={"email": "later@test.dev", "password": "hunter2secret"}
            )
            assert blocked.status_code == 403
            assert blocked.json()["error"]["code"] == "registration_disabled"


async def test_patch_me_merges_settings(app_client):
    client, _ = app_client
    headers = await register_user(client, "patch@test.dev")
    await client.patch(
        "/api/auth/me", headers=headers, json={"settings": {"default_model": "mock/echo"}}
    )
    response = await client.patch(
        "/api/auth/me", headers=headers, json={"settings": {"theme": "dark"}, "name": "Renamed"}
    )
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["settings"]["default_model"] == "mock/echo"  # merge, not replace
    assert body["settings"]["theme"] == "dark"
