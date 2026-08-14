"""Conversation CRUD + message listing (§18)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from retinue.core.state import AppState, get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import (
    Agent,
    Attachment,
    Conversation,
    File,
    Message,
    MessagePart,
    User,
)

router = APIRouter()


async def _owned(state: AppState, user: User, conversation_id: uuid.UUID) -> Conversation:
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
    agent: Agent | None = None
    if body.agent_id is not None:
        async with state.db.read_session() as session:
            agent = await session.get(Agent, body.agent_id)
        if agent is None or (
            agent.owner_id != user.id and agent.visibility not in ("org", "public")
        ):
            raise AppError(NOT_FOUND, "agent not found", status=404)
    conversation = Conversation(
        id=uuid7(),
        user_id=user.id,
        title=body.title,
        model_override=body.model_override,
        agent_id=agent.id if agent else None,
        agent_version_id=agent.current_version_id if agent else None,  # §9.1 pin
    )
    async with state.db.write_session() as session:
        session.add(conversation)
    return ConversationOut.model_validate(conversation)


@router.post("/conversations/{conversation_id}/repin-agent")
async def repin_agent(
    conversation_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationOut:
    """Move this chat to the agent's current version (the §9.1 banner action)."""
    state = get_state(request)
    conversation = await _owned(state, user, conversation_id)
    if conversation.agent_id is None:
        raise AppError(NOT_FOUND, "conversation has no agent", status=404)
    async with state.db.write_session() as session:
        row = await session.get(Conversation, conversation_id)
        assert row is not None
        agent = await session.get(Agent, row.agent_id)
        if agent is None or agent.current_version_id is None:
            raise AppError(NOT_FOUND, "agent not found", status=404)
        row.agent_version_id = agent.current_version_id
        row.updated_at = now_ms()
        result = row
    return ConversationOut.model_validate(result)


@router.post("/conversations/{conversation_id}/fork", status_code=201)
async def fork_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    at_message_id: uuid.UUID | None = None,
) -> ConversationOut:
    """Fork the visible thread into a new, self-contained conversation (§17):
    the messages and parts on the active branch up to the fork point are
    materialized (with their attachments), and `forked_from_message_id`
    records the provenance. Sibling branches are not copied."""
    state = get_state(request)
    source = await _owned(state, user, conversation_id)

    async with state.db.read_session() as session:
        thread = await load_thread(session, conversation_id, leaf_id=at_message_id)
        attachments_by_message = await _attachments_by_message(
            session, [tm.message.id for tm in thread]
        )
    if not thread:
        raise AppError(NOT_FOUND, "conversation has no messages", status=404)
    fork_point = thread[-1].message

    fork = Conversation(
        id=uuid7(),
        user_id=user.id,
        title=f"Fork: {source.title}" if source.title else None,
        agent_id=source.agent_id,
        agent_version_id=source.agent_version_id,
        model_override=source.model_override,
        params_override=source.params_override,
        forked_from_message_id=fork_point.id,
    )
    async with state.db.write_session() as session:
        session.add(fork)
        await session.flush()
        # materialize the visible path so the fork is self-contained
        prev_id: uuid.UUID | None = None
        for tm in thread:
            new_msg_id = uuid7()
            session.add(
                Message(
                    id=new_msg_id,
                    conversation_id=fork.id,
                    parent_id=prev_id,
                    role=tm.message.role,
                    status=tm.message.status,
                    model=tm.message.model,
                    agent_version_id=tm.message.agent_version_id,
                    created_at=tm.message.created_at,
                )
            )
            await session.flush()
            for part in tm.parts:
                session.add(
                    MessagePart(
                        id=uuid7(),
                        message_id=new_msg_id,
                        idx=part.idx,
                        type=part.type,
                        content=part.content,
                        text_content=part.text_content,
                    )
                )
            # carry attachments so the fork's context (vision, RAG) is intact
            for attachment, _file in attachments_by_message.get(tm.message.id, []):
                session.add(
                    Attachment(
                        message_id=new_msg_id,
                        file_id=attachment.file_id,
                        kind=attachment.kind,
                    )
                )
            prev_id = new_msg_id
        fork.last_message_at = now_ms()
    return ConversationOut.model_validate(fork)


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
            attachments = await _attachments_by_message(session, [m.id for m in rows])
            messages = [
                message_out(m, parts_by_message.get(m.id, []), attachments.get(m.id)) for m in rows
            ]
        else:
            thread = await load_thread(session, conversation_id, leaf_id=branch)
            attachments = await _attachments_by_message(session, [tm.message.id for tm in thread])
            messages = [
                message_out(tm.message, tm.parts, attachments.get(tm.message.id)) for tm in thread
            ]
    return MessageListOut(conversation_id=conversation_id, messages=messages)


async def _attachments_by_message(
    session: AsyncSession, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[Attachment, File | None]]]:
    if not message_ids:
        return {}
    rows = (
        await session.execute(
            select(Attachment, File)
            .outerjoin(File, File.id == Attachment.file_id)
            .where(Attachment.message_id.in_(message_ids))
        )
    ).all()
    out: dict[uuid.UUID, list[tuple[Attachment, File | None]]] = {}
    for attachment, file in rows:
        out.setdefault(attachment.message_id, []).append((attachment, file))
    return out
