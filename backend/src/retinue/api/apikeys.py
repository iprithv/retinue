"""API key management — programmatic access with scopes (§16)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from retinue.api.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from retinue.core.audit import audit
from retinue.core.crypto import hash_token
from retinue.core.deps import client_ip, get_current_user
from retinue.core.errors import NOT_FOUND, AppError
from retinue.core.ids import uuid7
from retinue.core.security import new_api_key
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import ApiKey, User

router = APIRouter()


@router.get("")
async def list_keys(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> list[ApiKeyOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [ApiKeyOut.model_validate(row) for row in rows]


@router.post("", status_code=201)
async def create_key(
    body: ApiKeyCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ApiKeyCreated:
    state = get_state(request)
    raw = new_api_key()
    row = ApiKey(
        id=uuid7(),
        user_id=user.id,
        name=body.name,
        key_hash=hash_token(raw),
        scopes=body.scopes,
        expires_at=now_ms() + body.expires_days * 86_400_000 if body.expires_days else None,
    )
    async with state.db.write_session() as session:
        session.add(row)
        audit(
            session,
            action="apikey.create",
            actor_id=user.id,
            target=body.name,
            ip=client_ip(request),
        )
    return ApiKeyCreated(
        id=row.id,
        name=row.name,
        scopes=list(row.scopes),
        last_used_at=None,
        expires_at=row.expires_at,
        created_at=row.created_at,
        key=raw,
    )


@router.delete("/{key_id}", status_code=204)
async def delete_key(
    key_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    async with state.db.write_session() as session:
        row = await session.get(ApiKey, key_id)
        if row is None or row.user_id != user.id:
            raise AppError(NOT_FOUND, "API key not found", status=404)
        await session.delete(row)
        audit(
            session,
            action="apikey.delete",
            actor_id=user.id,
            target=row.name,
            ip=client_ip(request),
        )
