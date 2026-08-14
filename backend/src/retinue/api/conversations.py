"""Conversation CRUD + message listing (§18)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from retinue.api.schemas import (
    ConversationCreate,
    ConversationOut,
    ConversationPatch,
    MessageListOut,
    MessageOut,
)
from retinue.api.serialize import message_out
from retinue.core.deps import get_current_user
from retinue.core.errors import NOT_FOUND, AppError
from retinue.core.history import load_thread
from retinue.core.ids import uuid7
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import Conversation, Message, MessagePart, User

router = APIRouter()


async def _owned(state, user: User, conversation_id: uuid.UUID) -> Conversation:
    async with state.db.read_session() as session:
        conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise AppError(NOT_FOUND, "conversation not found", status=404)
    return conversation


@router.get("/conversations")
async def list_conversations(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    archived: bool = False,
    limit: int = Query(200, ge=1, le=500),
) -> list[ConversationOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(Conversation)
                    .where(
                        Conversation.user_id == user.id,
                        Conversation.is_archived == archived,
                    )
                    .order_by(
                        Conversation.pinned.desc(),
                        Conversation.last_message_at.desc().nulls_last(),
                        Conversation.created_at.desc(),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationOut:
    state = get_state(request)
    conversation = Conversation(
        id=uuid7(),
        user_id=user.id,
        title=body.title,
        model_override=body.model_override,
    )
    async with state.db.write_session() as session:
        session.add(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationOut:
    state = get_state(request)
    conversation = await _owned(state, user, conversation_id)
    return ConversationOut.model_validate(conversation)


@router.patch("/conversations/{conversation_id}")
async def patch_conversation(
    conversation_id: uuid.UUID,
    body: ConversationPatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationOut:
    state = get_state(request)
    await _owned(state, user, conversation_id)
    updates = body.model_dump(exclude_unset=True)
    async with state.db.write_session() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        for field, value in updates.items():
            setattr(conversation, field, value)
        conversation.updated_at = now_ms()
        result = conversation
    return ConversationOut.model_validate(result)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _owned(state, user, conversation_id)
    async with state.db.write_session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is not None:
            await session.delete(conversation)  # FK cascade removes messages/parts


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    all_branches: bool = Query(False, alias="all"),
    branch: uuid.UUID | None = None,
) -> MessageListOut:
    state = get_state(request)
    await _owned(state, user, conversation_id)
    messages: list[MessageOut] = []
    async with state.db.read_session() as session:
        if all_branches:
            rows = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == conversation_id)
                        .order_by(Message.created_at, Message.id)
                    )
                )
                .scalars()
                .all()
            )
            part_rows = (
                (
                    await session.execute(
                        select(MessagePart).where(MessagePart.message_id.in_([m.id for m in rows]))
                    )
                )
                .scalars()
                .all()
            )
            parts_by_message: dict[uuid.UUID, list[MessagePart]] = {}
            for part in part_rows:
                parts_by_message.setdefault(part.message_id, []).append(part)
            messages = [message_out(m, parts_by_message.get(m.id, [])) for m in rows]
        else:
            thread = await load_thread(session, conversation_id, leaf_id=branch)
            messages = [message_out(tm.message, tm.parts) for tm in thread]
    return MessageListOut(conversation_id=conversation_id, messages=messages)
