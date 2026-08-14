"""Chat + message endpoints (§18): the SSE relay entry point.

POST /chat is idempotent on the client-generated user-message id (§31.4a):
retries attach to the live stream (with `Last-Event-ID` replay), retries of a
finished turn replay from the database, and an edited message id with no
children starts a fresh generation from that anchor.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update

from retinue.api.schemas import (
    ChatParams,
    ChatSendRequest,
    MessageEditRequest,
    MessageOut,
    StopResponse,
)
from retinue.api.serialize import message_out
from retinue.core.assembly import assemble_context
from retinue.core.deps import get_current_user, rate_limit
from retinue.core.errors import (
    CONFLICT,
    NO_MODEL_CONFIGURED,
    NOT_FOUND,
    VALIDATION_ERROR,
    AppError,
)
from retinue.core.history import load_thread, to_history
from retinue.core.ids import uuid7
from retinue.core.sse import (
    BLOCK_START,
    DELTA,
    ERROR,
    HEARTBEAT,
    MESSAGE_END,
    MESSAGE_START,
    RESYNC_REQUIRED,
    encode_sse,
)
from retinue.core.state import AppState, get_state
from retinue.core.streams import LiveStream
from retinue.core.timeutil import now_ms
from retinue.db.models import Conversation, Message, MessagePart, User

log = structlog.get_logger("retinue.api.chat")

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id", "")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


# -- SSE plumbing -----------------------------------------------------------


def _attach_response(state: AppState, stream: LiveStream, last_event_id: int) -> StreamingResponse:
    hub = state.hub
    heartbeat_s = state.settings.stream.heartbeat_s
    frames, queue, sub_id, missed = hub.subscribe(stream, last_event_id)

    async def gen() -> AsyncIterator[bytes]:
        try:
            floor = last_event_id
            if missed and queue is not None and stream.snapshot is not None:
                # gap exceeded the ring: rebuild the full message from the
                # producer's in-memory state, then continue live past it
                snap = stream.snapshot()
                floor = int(snap["last_event_id"])
                yield encode_sse(None, MESSAGE_START, snap["start"])
                for block in snap["blocks"]:
                    yield encode_sse(
                        None, BLOCK_START, {"index": block["index"], "type": block["type"]}
                    )
                    if block["text"]:
                        yield encode_sse(
                            None, DELTA, {"index": block["index"], "text": block["text"]}
                        )
            elif missed and queue is None:
                yield encode_sse(None, RESYNC_REQUIRED, {})
                return
            else:
                for frame in frames:
                    yield frame
            if queue is None:
                return
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                except TimeoutError:
                    yield HEARTBEAT
                    continue
                if item is None:
                    return
                event_id, frame = item
                if event_id <= floor:
                    continue
                yield frame
        finally:
            if queue is not None:
                hub.unsubscribe(stream, sub_id)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


def _db_replay_response(message: Message, parts: list[MessagePart]) -> StreamingResponse:
    """Idempotent retry of a finished turn: replay the persisted message."""
    stop_reasons = {"complete": "end", "stopped": "stopped", "error": "error"}

    async def gen() -> AsyncIterator[bytes]:
        yield encode_sse(
            None,
            MESSAGE_START,
            {
                "message_id": str(message.id),
                "conversation_id": str(message.conversation_id),
                "model": message.model,
                "agent_version_id": None,
                "replay": True,
            },
        )
        for part in sorted(parts, key=lambda p: p.idx):
            yield encode_sse(None, BLOCK_START, {"index": part.idx, "type": part.type})
            text = (part.content or {}).get("text") or part.text_content or ""
            if text:
                yield encode_sse(None, DELTA, {"index": part.idx, "text": text})
        if message.error:
            yield encode_sse(None, ERROR, message.error)
        yield encode_sse(
            None,
            MESSAGE_END,
            {
                "stop_reason": stop_reasons.get(message.status, "end"),
                "ttft_ms": None,
                "total_ms": None,
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# -- generation -----------------------------------------------------------------


async def _resolve_model(
    state: AppState, user: User, conversation: Conversation, requested: str | None
) -> str:
    if requested:
        return requested
    if conversation.model_override:
        return conversation.model_override
    async with state.db.read_session() as session:
        model = await state.registry.default_model(session, user.settings or {})
    if not model:
        raise AppError(
            NO_MODEL_CONFIGURED,
            "no model configured — add a provider key in Settings or pass a model",
            status=400,
        )
    return model


async def _start_generation(
    state: AppState,
    user: User,
    conversation: Conversation,
    anchor: Message,
    *,
    model: str | None,
    chat_params: ChatParams | None,
) -> StreamingResponse:
    resolved_model = await _resolve_model(state, user, conversation, model)
    model_info = state.registry.model_info(resolved_model)

    merged: dict[str, Any] = dict(conversation.params_override or {})
    if chat_params is not None:
        merged.update(chat_params.to_provider_params())
    requested_max_output = merged.get("max_tokens")

    async with state.db.read_session() as session:
        thread = await load_thread(session, conversation.id, leaf_id=anchor.id)
        history = to_history(thread)
        assembled = assemble_context(
            system_prompt=state.settings.default_system_prompt,
            history=history,
            model_info=model_info,
            requested_max_output=requested_max_output,
            counter=state.counter,
            context_cfg=state.settings.context,
        )
        call = await state.registry.prepare_call(
            session,
            user_id=user.id,
            model=resolved_model,
            messages=assembled.messages,
            params=merged,
        )

    log.debug("context_assembled", **assembled.breakdown)

    assistant_id = uuid7()
    async with state.db.write_session() as session:
        session.add(
            Message(
                id=assistant_id,
                conversation_id=conversation.id,
                parent_id=anchor.id,
                role="assistant",
                status="streaming",
                model=resolved_model,
            )
        )

    stream = state.engine.start_stream(
        user_id=user.id,
        conversation_id=conversation.id,
        assistant_message_id=assistant_id,
        client_message_id=anchor.id,
        call=call,
        generate_title=conversation.title is None,
    )
    return _attach_response(state, stream, 0)


async def _owned_conversation(
    state: AppState, user: User, conversation_id: uuid.UUID
) -> Conversation:
    async with state.db.read_session() as session:
        conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise AppError(NOT_FOUND, "conversation not found", status=404)
    return conversation


async def _owned_message(state: AppState, user: User, message_id: uuid.UUID) -> Message:
    async with state.db.read_session() as session:
        message = await session.get(Message, message_id)
    if message is None:
        raise AppError(NOT_FOUND, "message not found", status=404)
    await _owned_conversation(state, user, message.conversation_id)
    return message


# -- endpoints ----------------------------------------------------------------------


@router.post("/chat")
async def send_chat(
    body: ChatSendRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("chat"))],
) -> StreamingResponse:
    state = get_state(request)
    last_event_id = _last_event_id(request)

    # 1) live stream for this client message id -> attach (idempotent retry / resume)
    live = state.hub.get_by_client(body.message_id)
    if live is not None and live.user_id == user.id:
        return _attach_response(state, live, last_event_id)

    async with state.db.read_session() as session:
        existing = await session.get(Message, body.message_id)

    if existing is not None:
        # retry after the hub forgot the stream, or edit-then-send
        conversation = await _owned_conversation(state, user, existing.conversation_id)
        if existing.role != "user":
            raise AppError(CONFLICT, "message_id must reference a user message", status=409)
        async with state.db.read_session() as session:
            children = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.parent_id == existing.id)
                        .order_by(Message.created_at, Message.id)
                    )
                )
                .scalars()
                .all()
            )
            newest = children[-1] if children else None
            parts = []
            if newest is not None:
                parts = (
                    (
                        await session.execute(
                            select(MessagePart).where(MessagePart.message_id == newest.id)
                        )
                    )
                    .scalars()
                    .all()
                )
        if newest is not None:
            if newest.status == "streaming":
                live = state.hub.get_by_message(newest.id)
                if live is not None:
                    return _attach_response(state, live, last_event_id)
                # crashed mid-stream before this process restarted: close it out
                async with state.db.write_session() as session:
                    await session.execute(
                        update(Message).where(Message.id == newest.id).values(status="stopped")
                    )
                newest.status = "stopped"
            return _db_replay_response(newest, list(parts))
        return await _start_generation(
            state, user, conversation, existing, model=body.model, chat_params=body.params
        )

    # 2) brand-new turn
    if not body.text:
        raise AppError(VALIDATION_ERROR, "text is required for a new message", status=422)

    if body.conversation_id is not None:
        conversation = await _owned_conversation(state, user, body.conversation_id)
    else:
        conversation = Conversation(id=uuid7(), user_id=user.id, title=None)
        async with state.db.write_session() as session:
            session.add(conversation)

    async with state.db.read_session() as session:
        leaf = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    user_message = Message(
        id=body.message_id,
        conversation_id=conversation.id,
        parent_id=leaf.id if leaf is not None else None,
        role="user",
        status="complete",
    )
    async with state.db.write_session() as session:
        session.add(user_message)
        await session.flush()  # message row before its part (FK)
        session.add(
            MessagePart(
                id=uuid7(),
                message_id=user_message.id,
                idx=0,
                type="text",
                content={"text": body.text},
                text_content=body.text,
            )
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(last_message_at=now_ms(), updated_at=now_ms())
        )

    return await _start_generation(
        state, user, conversation, user_message, model=body.model, chat_params=body.params
    )


@router.post("/messages/{message_id}/stop")
async def stop_message(
    message_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StopResponse:
    state = get_state(request)
    message = await _owned_message(state, user, message_id)
    stopped = state.hub.request_stop(message_id)
    if not stopped and message.status == "streaming":
        # stale row from a crashed process
        async with state.db.write_session() as session:
            await session.execute(
                update(Message).where(Message.id == message_id).values(status="stopped")
            )
        stopped = True
    return StopResponse(stopped=stopped)


@router.post("/messages/{message_id}/regenerate")
async def regenerate_message(
    message_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("chat"))],
    body: ChatSendRequest | None = None,
) -> StreamingResponse:
    state = get_state(request)
    message = await _owned_message(state, user, message_id)
    if message.role != "assistant" or message.parent_id is None:
        raise AppError(CONFLICT, "only assistant replies can be regenerated", status=409)
    if message.status == "streaming" and state.hub.get_by_message(message.id) is not None:
        raise AppError(CONFLICT, "message is still streaming; stop it first", status=409)
    anchor = await _owned_message(state, user, message.parent_id)
    conversation = await _owned_conversation(state, user, message.conversation_id)
    return await _start_generation(
        state,
        user,
        conversation,
        anchor,
        model=body.model if body else None,
        chat_params=body.params if body else None,
    )


@router.patch("/messages/{message_id}")
async def edit_message(
    message_id: uuid.UUID,
    body: MessageEditRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> MessageOut:
    """Edit-creates-branch (§6.6): a sibling user message with the new text.

    The client then POSTs /chat with the returned id to generate the reply.
    """
    state = get_state(request)
    message = await _owned_message(state, user, message_id)
    if message.role != "user":
        raise AppError(CONFLICT, "only user messages can be edited", status=409)

    new_id = body.new_message_id or uuid7()
    async with state.db.read_session() as session:
        if await session.get(Message, new_id) is not None:
            raise AppError(CONFLICT, "new_message_id already exists", status=409)

    sibling = Message(
        id=new_id,
        conversation_id=message.conversation_id,
        parent_id=message.parent_id,
        role="user",
        status="complete",
    )
    part = MessagePart(
        id=uuid7(),
        message_id=new_id,
        idx=0,
        type="text",
        content={"text": body.text},
        text_content=body.text,
    )
    async with state.db.write_session() as session:
        session.add(sibling)
        await session.flush()  # message row before its part (FK)
        session.add(part)
    return message_out(sibling, [part])
