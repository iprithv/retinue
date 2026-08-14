"""create_app() factory, lifespan wiring, middleware (§7.3, §4).

Middlewares are pure ASGI (no BaseHTTPMiddleware) so the SSE token path takes
zero extra buffering hops. Response compression is deliberately absent in v0.1:
static assets ship pre-compressed and never-compress-SSE is a hard rule (§3.3);
API-JSON brotli belongs to the reverse proxy at T2+.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.types import Message as ASGIMessage

import retinue
from retinue.api import build_api_router
from retinue.config import Settings
from retinue.core.chat_engine import ChatEngine
from retinue.core.crypto import SecretBox
from retinue.core.errors import install_exception_handlers
from retinue.core.ids import uuid7
from retinue.core.logging import configure_logging
from retinue.core.ratelimit import RateLimiter
from retinue.core.security import PasswordService, TokenService
from retinue.core.state import AppState
from retinue.core.streams import StreamHub
from retinue.core.tokens import TokenCounter
from retinue.db.migrate import run_migrations
from retinue.db.models import Message
from retinue.db.session import Database
from retinue.jobs.handlers import builtin_handlers
from retinue.jobs.queue import JobQueue
from retinue.jobs.worker import JobContext, JobWorker
from retinue.providers.pricing import PricingTable
from retinue.providers.registry import ProviderRegistry

log = structlog.get_logger("retinue.app")

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: ASGIMessage) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                headers.append((b"x-frame-options", b"DENY"))
                content_type = b""
                for name, value in headers:
                    if name.lower() == b"content-type":
                        content_type = value
                        break
                if content_type.startswith(b"text/html"):
                    headers.append((b"content-security-policy", CSP.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        import time

        request_id = uuid7().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.monotonic()
        status_holder = {"status": 0}

        async def send_wrapper(message: ASGIMessage) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in ("/api/healthz", "/api/readyz"):
                log.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_holder["status"],
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            structlog.contextvars.clear_contextvars()


class SPAStaticFiles(StaticFiles):
    """Serve the SPA with client-side-routing fallback to index.html."""

    async def get_response(self, path: str, scope: Scope) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    configure_logging(settings.log_level, settings.log_format)

    tiktoken_cache = settings.home_dir / "cache" / "tiktoken"
    tiktoken_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(tiktoken_cache))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_url = settings.effective_database_url
        if settings.auto_migrate:
            await run_migrations(database_url)
        db = Database(database_url)

        # crash recovery: a previous process may have died mid-stream
        async with db.write_session() as session:
            await session.execute(
                update(Message).where(Message.status == "streaming").values(status="stopped")
            )

        master = settings.master_secret()
        box = SecretBox(master)
        passwords = PasswordService(master)
        tokens = TokenService(
            settings.home_dir / "keys" / "jwt_ed25519.pem", settings.auth.access_ttl_s
        )
        hub = StreamHub(
            ring_size=settings.stream.ring_size,
            ring_ttl_s=settings.stream.ring_ttl_s,
            orphan_grace_s=settings.stream.orphan_grace_s,
        )
        registry = ProviderRegistry(settings, box)
        pricing = PricingTable(settings.resolved_data_dir / "pricing_overrides.json")
        counter = TokenCounter()
        jobs = JobQueue(db)
        worker = JobWorker(
            db=db,
            queue=jobs,
            ctx=JobContext(db=db, settings=settings, registry=registry, hub=hub, counter=counter),
            handlers=builtin_handlers(),
        )
        engine = ChatEngine(
            db=db,
            hub=hub,
            registry=registry,
            pricing=pricing,
            jobs=jobs,
            settings=settings,
            counter=counter,
        )
        app.state.retinue = AppState(
            settings=settings,
            db=db,
            hub=hub,
            registry=registry,
            pricing=pricing,
            jobs=jobs,
            worker=worker,
            engine=engine,
            tokens=tokens,
            passwords=passwords,
            box=box,
            limiter=RateLimiter(settings.ratelimit.enabled),
            counter=counter,
        )
        worker.start()
        counter.warm()  # tiktoken loads off-thread; counts are heuristic until then
        if settings.models.mock_enabled:
            log.warning("mock_provider_enabled", note="dev/test only — mock/* models are live")
        log.info(
            "retinue_started",
            version=retinue.__version__,
            data_dir=str(settings.resolved_data_dir),
            database=database_url.split("://")[0],
            secret_source=settings.secret_source(),
        )
        try:
            yield
        finally:
            await hub.shutdown()
            await worker.stop()
            await db.dispose()
            log.info("retinue_stopped")

    docs_enabled = settings.server.enable_docs
    # No default_response_class: FastAPI ≥0.141 serializes response models to
    # JSON bytes directly through pydantic-core (Rust) — the modern equivalent
    # of the D3 orjson requirement. orjson still encodes SSE frames and DB JSON.
    app = FastAPI(
        title="Retinue",
        version=retinue.__version__,
        lifespan=lifespan,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
    )
    install_exception_handlers(app)

    if settings.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-request-id"],
        )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware)

    app.include_router(build_api_router(), prefix="/api")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isfile(os.path.join(static_dir, "index.html")):
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="spa")
    else:
        # source install without a bundled SPA: keep / informative
        from starlette.responses import HTMLResponse

        @app.get("/", include_in_schema=False)
        async def _no_spa() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><title>Retinue</title>"
                "<body style='font-family:system-ui;padding:3rem;max-width:40rem;margin:auto'>"
                "<h1>⚜️ Retinue API is running</h1>"
                "<p>This install has no bundled web UI (source checkout). "
                "Build it with <code>python scripts/build_frontend_into_wheel.py</code> "
                "or install the release wheel: <code>pip install retinue</code>.</p>"
                "<p>API docs: <a href='/api/docs'>/api/docs</a> · "
                "health: <a href='/api/healthz'>/api/healthz</a></p></body>"
            )

    return app
