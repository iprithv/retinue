"""Share links (§18): tokenized read-only conversation views."""

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from retinue.api.schemas import ShareCreate, SharedThreadOut, ShareOut
from retinue.api.serialize import message_out
from retinue.core.deps import get_current_user, rate_limit
from retinue.core.errors import NOT_FOUND, AppError
from retinue.core.history import load_thread
from retinue.core.ids import uuid7
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import AuditLog, Conversation, Share, User

router = APIRouter()


def _share_out(share: Share) -> ShareOut:
    return ShareOut(
        id=share.id,
        token=share.token,
        url=f"/share/{share.token}",
        mode=share.mode,
        expires_at=share.expires_at,
        created_at=share.created_at,
    )


@router.post("/conversations/{conversation_id}/share", status_code=201)
async def create_share(
    conversation_id: uuid.UUID,
    body: ShareCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ShareOut:
    state = get_state(request)
    async with state.db.read_session() as session:
        conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise AppError(NOT_FOUND, "conversation not found", status=404)

    share = Share(
        id=uuid7(),
        conversation_id=conversation_id,
        token=secrets.token_urlsafe(24),
        expires_at=(now_ms() + body.expires_days * 86_400_000) if body.expires_days else None,
    )
    async with state.db.write_session() as session:
        session.add(share)
        session.add(
            AuditLog(
                id=uuid7(),
                actor_id=user.id,
                action="share.create",
                target=str(conversation_id),
            )
        )
    return _share_out(share)


@router.get("/conversations/{conversation_id}/shares")
async def list_shares(
    conversation_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[ShareOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise AppError(NOT_FOUND, "conversation not found", status=404)
        rows = (
            (await session.execute(select(Share).where(Share.conversation_id == conversation_id)))
            .scalars()
            .all()
        )
    return [_share_out(s) for s in rows]


@router.delete("/shares/{share_id}", status_code=204)
async def delete_share(
    share_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    async with state.db.write_session() as session:
        share = await session.get(Share, share_id)
        if share is None:
            raise AppError(NOT_FOUND, "share not found", status=404)
        conversation = await session.get(Conversation, share.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise AppError(NOT_FOUND, "share not found", status=404)
        await session.delete(share)


@router.get("/share/{token}")
async def read_share(
    token: str,
    request: Request,
    _rl: Annotated[None, Depends(rate_limit("default"))],
) -> SharedThreadOut:
    """Public read-only rendered thread (rate-limited by IP, no auth)."""
    state = get_state(request)
    async with state.db.read_session() as session:
        share = (
            await session.execute(select(Share).where(Share.token == token))
        ).scalar_one_or_none()
        if share is None or (share.expires_at is not None and share.expires_at < now_ms()):
            raise AppError(NOT_FOUND, "share not found or expired", status=404)
        conversation = await session.get(Conversation, share.conversation_id)
        if conversation is None:
            raise AppError(NOT_FOUND, "share not found or expired", status=404)
        thread = await load_thread(session, conversation.id)
    return SharedThreadOut(
        title=conversation.title,
        created_at=conversation.created_at,
        messages=[message_out(tm.message, tm.parts) for tm in thread],
    )
