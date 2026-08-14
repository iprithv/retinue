import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from retinue.config import Settings
from retinue.providers.registry import ENV_PROVIDER_KEYS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Isolate tests from the developer's environment and ~/.retinue config."""
    for key in list(os.environ):
        if key.startswith("RETINUE_"):
            monkeypatch.delenv(key, raising=False)
    for key in ENV_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RETINUE_CONFIG_FILE", str(tmp_path / "no-config.toml"))


def make_settings(tmp_path, **overrides) -> Settings:
    base: dict = {
        "home_dir": tmp_path / "retinue-home",
        "secret": "test-secret-000000000000000000000000",
        "log_level": "warning",
        "log_format": "console",
        "models": {"mock_enabled": True},
        "ratelimit": {"enabled": False},
        "stream": {"heartbeat_s": 5.0, "orphan_grace_s": 1.5, "write_behind_ms": 20},
        "server": {"cors_origins": []},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return Settings(**base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def app_client(settings) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    """In-process ASGI client with a running lifespan (non-SSE tests)."""
    from retinue.app import create_app

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


class LiveServer:
    def __init__(self, client: httpx.AsyncClient, app: FastAPI, base_url: str) -> None:
        self.client = client
        self.app = app
        self.base_url = base_url


@contextlib.asynccontextmanager
async def run_live_server(settings) -> AsyncIterator[LiveServer]:
    """Real uvicorn on a loopback port — genuine streaming for SSE tests."""
    from retinue.app import create_app

    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            yield LiveServer(client, app, base_url)
    finally:
        server.should_exit = True
        await task


@pytest.fixture
async def live(settings) -> AsyncIterator[LiveServer]:
    async with run_live_server(settings) as server:
        yield server
