"""Data sources API (§30): engine catalog, CRUD, staged connection tests,
schema browsing, and a guarded query console.

File-based engines (SQLite/DuckDB paths) read server-local files, so creating
them requires an admin role (§31.4b posture). Every query is audit-logged.
"""

import uuid
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from retinue.api.schemas import (
    DataSourceCreate,
    DataSourceOut,
    DataSourcePatch,
    DataSourceQueryRequest,
    QueryResultOut,
)
from retinue.core.deps import get_current_user, rate_limit
from retinue.core.errors import FORBIDDEN, NOT_FOUND, VALIDATION_ERROR, AppError
from retinue.core.ids import uuid7
from retinue.core.state import AppState, get_state
from retinue.core.timeutil import now_ms
from retinue.datasources.base import DataSourceError
from retinue.datasources.registry import ENGINES, catalog, engine_info
from retinue.datasources.service import get_sample, get_schema, run_query, staged_test
from retinue.db.models import DataSourceRow, User

router = APIRouter()


def _source_out(source: DataSourceRow) -> DataSourceOut:
    engine = ENGINES.get(source.engine)
    return DataSourceOut(
        id=source.id,
        name=source.name,
        engine=source.engine,
        engine_label=engine.label if engine else source.engine,
        config=source.config or {},
        has_secrets=source.secret_ciphertext is not None,
        policy=source.policy or {},
        status=source.status,
        last_test=source.last_test,
        created_at=source.created_at,
    )


async def _owned(state: AppState, user: User, source_id: uuid.UUID) -> DataSourceRow:
    async with state.db.read_session() as session:
        source = await session.get(DataSourceRow, source_id)
    if source is None or source.owner_id != user.id:
        raise AppError(NOT_FOUND, "data source not found", status=404)
    return source


def _sanitize_url_credentials(
    config: dict[str, Any], secrets: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Credentials embedded in uri/url config values (mongodb://u:p@host, …)
    must never sit in the plaintext config column or echo back from the API:
    the full value moves into the encrypted secret blob and the stored config
    keeps a redacted copy for display (§16 secrets-at-rest posture)."""
    from urllib.parse import urlsplit, urlunsplit

    config = dict(config)
    secrets = dict(secrets)
    for key in ("uri", "url"):
        value = config.get(key)
        if not isinstance(value, str) or "@" not in value:
            continue
        try:
            parts = urlsplit(value)
        except ValueError:
            continue
        if parts.username is None and parts.password is None:
            continue
        secrets[key] = value  # full credentialed form, encrypted at rest
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        config[key] = urlunsplit((parts.scheme, host, parts.path, parts.query, ""))
    return config, secrets


def _validate_engine(user: User, engine_key: str, config: dict[str, Any]) -> None:
    engine = engine_info(engine_key)  # raises DataSourceError for unknown keys
    if engine.file_based and user.role not in ("owner", "admin"):
        raise AppError(
            FORBIDDEN,
            "file-based sources read server-local files — admin role required",
            status=403,
        )
    missing = [f.name for f in engine.config_fields if f.required and not config.get(f.name)]
    if missing:
        raise AppError(
            VALIDATION_ERROR,
            f"missing required config for {engine.label}: {', '.join(missing)}",
            status=422,
        )


@router.get("/datasources/engines")
async def list_engines(
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    return catalog()


@router.get("/datasources")
async def list_sources(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[DataSourceOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(DataSourceRow)
                    .where(DataSourceRow.owner_id == user.id)
                    .order_by(DataSourceRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return [_source_out(s) for s in rows]


@router.post("/datasources", status_code=201)
async def create_source(
    body: DataSourceCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> DataSourceOut:
    state = get_state(request)
    try:
        _validate_engine(user, body.engine, body.config)
    except DataSourceError as error:
        raise AppError(VALIDATION_ERROR, str(error), status=422) from error

    config, secrets = _sanitize_url_credentials(body.config, body.secrets)
    ciphertext = nonce = None
    if secrets:
        ciphertext, nonce = state.box.encrypt(orjson.dumps(secrets))
    source = DataSourceRow(
        id=uuid7(),
        owner_id=user.id,
        name=body.name,
        engine=body.engine,
        config=config,
        secret_ciphertext=ciphertext,
        secret_nonce=nonce,
        policy=body.policy,
    )
    async with state.db.write_session() as session:
        session.add(source)
    return _source_out(source)


@router.patch("/datasources/{source_id}")
async def patch_source(
    source_id: uuid.UUID,
    body: DataSourcePatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> DataSourceOut:
    state = get_state(request)
    await _owned(state, user, source_id)
    async with state.db.write_session() as session:
        source = await session.get(DataSourceRow, source_id)
        assert source is not None
        if body.name is not None:
            source.name = body.name
        if body.config is not None or body.secrets is not None:
            new_config = body.config if body.config is not None else (source.config or {})
            _validate_engine(user, source.engine, new_config)
            new_secrets = body.secrets if body.secrets is not None else {}
            new_config, new_secrets = _sanitize_url_credentials(new_config, new_secrets)
            source.config = new_config
            if new_secrets or body.secrets is not None:
                ciphertext, nonce = state.box.encrypt(orjson.dumps(new_secrets))
                source.secret_ciphertext = ciphertext
                source.secret_nonce = nonce
            source.status = "unverified"
        if body.policy is not None:
            source.policy = body.policy
        result = source
    return _source_out(result)


@router.delete("/datasources/{source_id}", status_code=204)
async def delete_source(
    source_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _owned(state, user, source_id)
    async with state.db.write_session() as session:
        source = await session.get(DataSourceRow, source_id)
        if source is not None:
            await session.delete(source)


@router.post("/datasources/{source_id}/test")
async def test_source(
    source_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("default"))],
) -> dict[str, Any]:
    state = get_state(request)
    source = await _owned(state, user, source_id)
    result = await staged_test(state, source)
    async with state.db.write_session() as session:
        row = await session.get(DataSourceRow, source_id)
        if row is not None:
            row.status = "ok" if result["ok"] else "failed"
            row.last_test = {**result, "at": now_ms()}
    return result


@router.get("/datasources/{source_id}/schema")
async def source_schema(
    source_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    state = get_state(request)
    source = await _owned(state, user, source_id)
    try:
        schema = await get_schema(state, source)
    except DataSourceError as error:
        raise AppError(VALIDATION_ERROR, str(error), status=502) from error
    return {
        "note": schema.note,
        "tables": [
            {
                "name": t.name,
                "row_estimate": t.row_estimate,
                "columns": [{"name": c.name, "type": c.type} for c in t.columns],
            }
            for t in schema.tables
        ],
    }


@router.post("/datasources/{source_id}/query")
async def query_source(
    source_id: uuid.UUID,
    body: DataSourceQueryRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("default"))],
) -> QueryResultOut:
    state = get_state(request)
    source = await _owned(state, user, source_id)
    try:
        result = await run_query(
            state, source, user_id=user.id, statement=body.statement, limit=body.limit
        )
    except DataSourceError as error:
        raise AppError(VALIDATION_ERROR, str(error), status=422) from error
    return QueryResultOut(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
        note=result.note,
    )


@router.get("/datasources/{source_id}/sample")
async def sample_source(
    source_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    table: str = Query(min_length=1, max_length=300),
    n: int = Query(3, ge=1, le=20),
) -> QueryResultOut:
    state = get_state(request)
    source = await _owned(state, user, source_id)
    try:
        result = await get_sample(state, source, user_id=user.id, table=table, n=n)
    except DataSourceError as error:
        raise AppError(VALIDATION_ERROR, str(error), status=422) from error
    return QueryResultOut(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
        note=result.note,
    )
