"""Token-bucket rate limits (§16)."""

import httpx

from conftest import make_settings
from retinue.core.ratelimit import RateLimiter


def test_bucket_allows_burst_then_blocks():
    limiter = RateLimiter()
    rate, burst = 5, 5
    allowed = sum(1 for _ in range(10) if limiter.check("k", rate, burst) is None)
    assert allowed == 5
    retry_after = limiter.check("k", rate, burst)
    assert retry_after is not None and retry_after > 0


def test_disabled_limiter_never_blocks():
    limiter = RateLimiter(enabled=False)
    assert all(limiter.check("k", 1, 1) is None for _ in range(100))


def test_keys_are_independent():
    limiter = RateLimiter()
    assert limiter.check("a", 1, 1) is None
    assert limiter.check("a", 1, 1) is not None
    assert limiter.check("b", 1, 1) is None


async def test_auth_route_class_enforced(tmp_path):
    from retinue.app import create_app

    settings = make_settings(
        tmp_path,
        ratelimit={"enabled": True, "auth_per_min": 3, "burst_factor": 1.0},
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            statuses = []
            for i in range(5):
                response = await client.post(
                    "/api/auth/login",
                    json={"email": f"u{i}@test.dev", "password": "irrelevant1"},
                )
                statuses.append(response.status_code)
            assert statuses[:3] == [401, 401, 401]
            assert statuses[3] == 429
            limited = await client.post(
                "/api/auth/login", json={"email": "x@test.dev", "password": "irrelevant1"}
            )
            assert limited.headers.get("retry-after") is not None
            assert limited.json()["error"]["retryable"] is True
