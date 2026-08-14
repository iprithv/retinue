"""Model catalog + provider credentials (§18)."""

import dataclasses
import uuid
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from retinue.api.schemas import CredentialCreate, CredentialOut, ModelOut
from retinue.core.audit import audit
from retinue.core.deps import client_ip, get_admin_user, get_current_user
from retinue.core.errors import FORBIDDEN, NOT_FOUND, AppError
from retinue.core.ids import uuid7
from retinue.core.state import get_state
from retinue.db.models import Credential, User

router = APIRouter()


@router.get("/models")
async def list_models(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> list[ModelOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        models = await state.registry.list_models(session)
    return [ModelOut(**dataclasses.asdict(model)) for model in models]


@router.post("/models/refresh")
async def refresh_models(
    request: Request, user: Annotated[User, Depends(get_admin_user)]
) -> dict[str, bool]:
    get_state(request).registry.invalidate_models_cache()
    return {"refreshed": True}


def _credential_out(row: Credential, key_hint: str) -> CredentialOut:
    return CredentialOut(
        id=row.id,
        provider=row.provider,
        label=row.label,
        base_url=row.base_url,
        org=row.user_id is None,
        key_hint=key_hint,
        created_at=row.created_at,
    )


@router.get("/providers/credentials")
async def list_credentials(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> list[CredentialOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(Credential).where(
                        Credential.kind == "llm",
                        (Credential.user_id == user.id) | (Credential.user_id.is_(None)),
                    )
                )
            )
            .scalars()
            .all()
        )
    out: list[CredentialOut] = []
    for row in rows:
        hint = "****"
        try:
            payload = orjson.loads(state.box.decrypt(row.data_ciphertext, row.data_nonce))
            api_key = str(payload.get("api_key", ""))
            if len(api_key) >= 4:
                hint = f"…{api_key[-4:]}"
        except Exception:
            hint = "decrypt-failed"
        out.append(_credential_out(row, hint))
    return out


@router.post("/providers/credentials", status_code=201)
async def create_credential(
    body: CredentialCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> CredentialOut:
    state = get_state(request)
    if body.org and user.role not in ("owner", "admin"):
        raise AppError(FORBIDDEN, "admin role required for org-global credentials", status=403)
    ciphertext, nonce = state.box.encrypt(orjson.dumps({"api_key": body.api_key}))
    row = Credential(
        id=uuid7(),
        user_id=None if body.org else user.id,
        kind="llm",
        provider=body.provider.strip().lower(),
        label=body.label,
        base_url=body.base_url,
        data_ciphertext=ciphertext,
        data_nonce=nonce,
    )
    async with state.db.write_session() as session:
        session.add(row)
        audit(
            session,
            action="credential.create",
            actor_id=user.id,
            target=row.provider,
            meta={"org": body.org},
            ip=client_ip(request),
        )
    state.registry.invalidate_models_cache()
    return _credential_out(row, f"…{body.api_key[-4:]}" if len(body.api_key) >= 4 else "****")


@router.delete("/providers/credentials/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    async with state.db.write_session() as session:
        row = await session.get(Credential, credential_id)
        if row is None or row.kind != "llm":
            raise AppError(NOT_FOUND, "credential not found", status=404)
        if row.user_id is None:
            if user.role not in ("owner", "admin"):
                raise AppError(FORBIDDEN, "admin role required", status=403)
        elif row.user_id != user.id:
            raise AppError(NOT_FOUND, "credential not found", status=404)
        await session.delete(row)
        audit(
            session,
            action="credential.delete",
            actor_id=user.id,
            target=row.provider,
            ip=client_ip(request),
        )
    state.registry.invalidate_models_cache()
