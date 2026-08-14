"""Datasource orchestration (§30.7): staged connection testing and guarded
query execution with per-statement audit logging."""

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from retinue.core.ids import uuid7
from retinue.datasources.base import DataSourceError, QueryResult, SchemaModel
from retinue.datasources.guard import (
    SourcePolicy,
    apply_masking,
    prepare_statement,
)
from retinue.datasources.registry import EngineInfo, engine_info, make_adapter
from retinue.db.models import AuditLog, DataSourceRow

if TYPE_CHECKING:
    from retinue.core.state import AppState

log = structlog.get_logger("retinue.datasources")


def decrypt_secrets(state: "AppState", source: DataSourceRow) -> dict[str, Any]:
    if not source.secret_ciphertext or not source.secret_nonce:
        return {}
    try:
        return dict(orjson.loads(state.box.decrypt(source.secret_ciphertext, source.secret_nonce)))
    except Exception:
        log.error("datasource_secret_decrypt_failed", source_id=str(source.id))
        return {}


def adapter_for(state: "AppState", source: DataSourceRow) -> Any:
    engine = engine_info(source.engine)
    return make_adapter(engine, source.config or {}, decrypt_secrets(state, source))


# -- staged connection test (§30.7) ----------------------------------------------------


async def _stage(name: str, coro: Any) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        hint = await coro
        return {
            "stage": name,
            "ok": True,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "detail": hint or "",
        }
    except Exception as error:
        return {
            "stage": name,
            "ok": False,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "detail": str(error)[:300],
        }


async def _dns_stage(host: str) -> str:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return str(infos[0][4][0]) if infos else ""


async def _tcp_stage(host: str, port: int) -> str:
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=8)
        return f"{host}:{port} reachable"
    finally:
        if writer is not None:
            writer.close()


def _target_hostport(engine: EngineInfo, config: dict[str, Any]) -> tuple[str, int] | None:
    """Best-effort host:port for the DNS/TCP rungs; None for file/url engines."""
    host = config.get("host")
    if host:
        port = int(config.get("port") or engine.default_port or 0)
        return (str(host), port) if port else None
    url = config.get("url") or config.get("uri")
    if url:
        from urllib.parse import urlsplit

        parts = urlsplit(str(url))
        if parts.hostname:
            default = 443 if parts.scheme in ("https", "bolt+s", "neo4j+s") else 80
            return parts.hostname, parts.port or engine.default_port or default
    return None


async def staged_test(state: "AppState", source: DataSourceRow) -> dict[str, Any]:
    """DNS → TCP → auth/connect → read probe → introspect capability (§30.7)."""
    engine = engine_info(source.engine)
    stages: list[dict[str, Any]] = []
    config = source.config or {}

    if engine.file_based:
        import os

        path = str(config.get("path", ""))

        def _check_file() -> str:
            if path == ":memory:":
                return "in-memory"
            if not os.path.isfile(path):
                raise DataSourceError(f"file not found: {path}")
            if not os.access(path, os.R_OK):
                raise DataSourceError(f"file not readable: {path}")
            return f"{os.path.getsize(path)} bytes"

        stages.append(await _stage("file", asyncio.to_thread(_check_file)))
    else:
        target = _target_hostport(engine, config)
        if target is not None:
            host, port = target
            stages.append(await _stage("dns", _dns_stage(host)))
            if stages[-1]["ok"]:
                stages.append(await _stage("tcp", _tcp_stage(host, port)))

    if all(s["ok"] for s in stages):
        adapter = adapter_for(state, source)

        async def _probe() -> str:
            await adapter.probe()
            return "read probe ok"

        stages.append(await _stage("auth+probe", _probe()))
        if stages[-1]["ok"]:

            async def _introspect() -> str:
                schema = await adapter.introspect()
                return f"{len(schema.tables)} {schema.note or 'tables'}"

            stages.append(await _stage("introspect", _introspect()))
        await adapter.close()

    ok = all(s["ok"] for s in stages)
    return {"ok": ok, "stages": stages}


# -- guarded execution ----------------------------------------------------------------


async def _audit(
    state: "AppState",
    *,
    user_id: uuid.UUID,
    source: DataSourceRow,
    statement: str,
    ok: bool,
    rows: int,
    elapsed_ms: int,
) -> None:
    try:
        async with state.db.write_session() as session:
            session.add(
                AuditLog(
                    id=uuid7(),
                    actor_id=user_id,
                    action="datasource.query",
                    target=str(source.id),
                    meta={
                        "engine": source.engine,
                        "statement": statement[:500],
                        "ok": ok,
                        "rows": rows,
                        "elapsed_ms": elapsed_ms,
                    },
                )
            )
    except Exception:
        log.exception("datasource_audit_failed")


async def run_query(
    state: "AppState",
    source: DataSourceRow,
    *,
    user_id: uuid.UUID,
    statement: str,
    limit: int | None = None,
) -> QueryResult:
    """Every statement passes the full §30.3 ladder and lands in the audit log —
    including statements the guard rejects (a denied probe is still a probe)."""
    engine = engine_info(source.engine)
    policy = SourcePolicy.from_json(source.policy)

    effective = min(limit or policy.max_rows, policy.max_rows)
    if engine.query_language == "sql":
        try:
            statement, effective = prepare_statement(statement, engine.dialect, policy, limit)
        except DataSourceError:
            await _audit(
                state,
                user_id=user_id,
                source=source,
                statement=statement,
                ok=False,
                rows=0,
                elapsed_ms=0,
            )
            raise

    adapter = adapter_for(state, source)
    ok = False
    result: QueryResult | None = None
    try:
        result = await adapter.query(statement, effective, policy.timeout_s)
        result = apply_masking(result, policy)
        ok = True
        return result
    finally:
        await adapter.close()
        await _audit(
            state,
            user_id=user_id,
            source=source,
            statement=statement,
            ok=ok,
            rows=result.row_count if result else 0,
            elapsed_ms=result.elapsed_ms if result else 0,
        )


async def get_schema(state: "AppState", source: DataSourceRow) -> SchemaModel:
    adapter = adapter_for(state, source)
    try:
        schema: SchemaModel = await adapter.introspect()
        return schema
    finally:
        await adapter.close()


async def get_sample(
    state: "AppState", source: DataSourceRow, *, user_id: uuid.UUID, table: str, n: int
) -> QueryResult:
    policy = SourcePolicy.from_json(source.policy)
    adapter = adapter_for(state, source)
    ok = False
    result: QueryResult | None = None
    try:
        result = await adapter.sample(table, n, policy.timeout_s)
        result = apply_masking(result, policy)
        ok = True
        return result
    finally:
        await adapter.close()
        await _audit(
            state,
            user_id=user_id,
            source=source,
            statement=f"<sample {table}>",
            ok=ok,
            rows=result.row_count if result else 0,
            elapsed_ms=result.elapsed_ms if result else 0,
        )
