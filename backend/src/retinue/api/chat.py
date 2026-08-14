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
from sqlalchemy.exc import IntegrityError

from retinue.api.schemas import (
    ApprovalRequest,
    ChatParams,
    ChatSendRequest,
    MessageEditRequest,
    MessageOut,
    StopResponse,
)
from retinue.api.serialize import message_out
from retinue.core.assembly import assemble_context
from retinue.core.context_extras import gather_context_extras
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
    APPROVAL_REQUIRED,
    BLOCK_START,
    CITATION,
    DELTA,
    ERROR,
    HEARTBEAT,
    MESSAGE_END,
    MESSAGE_START,
    RESYNC_REQUIRED,
    TOOL_CALL,
    TOOL_RESULT,
    encode_sse,
)
from retinue.core.state import AppState, get_state
from retinue.core.streams import LiveStream
from retinue.core.timeutil import now_ms
from retinue.db.models import (
    Agent,
    AgentVersion,
    Attachment,
    Conversation,
    File,
    Message,
    MessagePart,
    User,
)

log = structlog.get_logger("retinue.api.chat")

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _block_frames(
    index: int, block_type: str, content: dict[str, Any] | None, text: str | None
) -> list[bytes]:
    """Rebuild one block as SSE frames — JSON blocks replay as their typed
    events so tool activity and citations survive a ring-miss resume (§19)."""
    frames = [encode_sse(None, BLOCK_START, {"index": index, "type": block_type})]
    if block_type == "tool_call" and content:
        frames.append(encode_sse(None, TOOL_CALL, {"index": index, **content}))
    elif block_type == "tool_result" and content:
        frames.append(encode_sse(None, TOOL_RESULT, {"index": index, **content}))
    elif block_type == "citation" and content:
        frames.append(encode_sse(None, CITATION, {"index": index, **content}))
    elif text:
        frames.append(encode_sse(None, DELTA, {"index": index, "text": text}))
    return frames


def _approval_payload(blocks: list[dict[str, Any]], call_id: str) -> dict[str, Any] | None:
    for block in blocks:
        content = block.get("content") or {}
        if block.get("type") == "tool_call" and content.get("call_id") == call_id:
            return {
                "call_id": call_id,
                "name": content.get("name"),
                "args": content.get("args") or {},
            }
    return None


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
                    for frame_bytes in _block_frames(
                        block["index"], block["type"], block.get("content"), block.get("text")
                    ):
                        yield frame_bytes
                # a producer parked on an approval must re-ask the rebuilt
                # client, or the turn dead-waits until the approval timeout
                for call_id, _future in stream.pending_approvals.items():
                    payload = _approval_payload(snap["blocks"], call_id)
                    if payload is not None:
                        yield encode_sse(None, APPROVAL_REQUIRED, payload)
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
            text = (part.content or {}).get("text") or part.text_content or ""
            for frame_bytes in _block_frames(part.idx, part.type, part.content, text):
                yield frame_bytes
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
    state: AppState,
    user: User,
    conversation: Conversation,
    requested: str | None,
    agent_version: AgentVersion | None = None,
) -> str:
    if requested:
        return requested
    if conversation.model_override:
        return conversation.model_override
    if agent_version is not None:
        return agent_version.model
    async with state.db.read_session() as session:
        model = await state.registry.default_model(session, user.settings or {})
    if not model:
        raise AppError(
            NO_MODEL_CONFIGURED,
            "no model configured — add a provider key in Settings or pass a model",
            status=400,
        )
    return model


async def _pinned_agent_version(state: AppState, conversation: Conversation) -> AgentVersion | None:
    if conversation.agent_version_id is None:
        return None
    async with state.db.read_session() as session:
        return await session.get(AgentVersion, conversation.agent_version_id)


async def _start_generation(
    state: AppState,
    user: User,
    conversation: Conversation,
    anchor: Message,
    *,
    model: str | None,
    chat_params: ChatParams | None,
) -> StreamingResponse:
    agent_version = await _pinned_agent_version(state, conversation)
    resolved_model = await _resolve_model(state, user, conversation, model, agent_version)
    model_info = state.registry.model_info(resolved_model)

    # params precedence: agent version < conversation override < request (§9.1)
    merged: dict[str, Any] = {}
    if agent_version is not None:
        merged.update(agent_version.params or {})
    merged.update(conversation.params_override or {})
    if chat_params is not None:
        merged.update(chat_params.to_provider_params())
    requested_max_output = merged.get("max_tokens")

    system_prompt = (
        agent_version.system_prompt
        if agent_version is not None and agent_version.system_prompt
        else state.settings.default_system_prompt
    )

    async with state.db.read_session() as session:
        thread = await load_thread(session, conversation.id, leaf_id=anchor.id)
        history = to_history(thread)
        extras = await gather_context_extras(
            state, session, user=user, conversation=conversation, thread=thread
        )
        assembled = assemble_context(
            system_prompt=system_prompt,
            history=history,
            model_info=model_info,
            requested_max_output=requested_max_output,
            counter=state.counter,
            context_cfg=state.settings.context,
            memory_block=extras.memory_block,
            rag_block=extras.rag_block,
        )
        call = await state.registry.prepare_call(
            session,
            user_id=user.id,
            model=resolved_model,
            messages=assembled.messages,
            params=merged,
        )

    if model_info.supports_vision:
        await _attach_vision_images(state, anchor.id, call.messages)

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
                agent_version_id=conversation.agent_version_id,
            )
        )

    tools = None
    if agent_version is not None and (agent_version.tools or agent_version.mcp_servers):
        from retinue.agents.runtime import ToolExecutor

        tools = ToolExecutor(state, user, agent_version)

    stream = state.engine.start_stream(
        user_id=user.id,
        conversation_id=conversation.id,
        assistant_message_id=assistant_id,
        client_message_id=anchor.id,
        call=call,
        agent_version=agent_version,
        citations=extras.citations,
        generate_title=conversation.title is None,
        extract_memory=not conversation.is_incognito,
        tools=tools,
    )
    return _attach_response(state, stream, 0)


_VISION_MAX_IMAGES = 4
_VISION_MAX_BYTES = 5 * 1024 * 1024


async def _attach_vision_images(
    state: AppState, anchor_id: uuid.UUID, messages: list[dict[str, Any]]
) -> None:
    """Vision input (§11.7): image attachments on the current user turn become
    base64 data-URL parts on the final user message (OpenAI shape; LiteLLM
    translates per provider). Text-only turns are left untouched."""
    import base64

    from retinue.filesys.base import shard_key

    async with state.db.read_session() as session:
        rows = (
            await session.execute(
                select(File)
                .join(Attachment, Attachment.file_id == File.id)
                .where(Attachment.message_id == anchor_id, Attachment.kind == "image")
                .order_by(File.created_at)
            )
        ).all()
    images: list[str] = []
    for (file,) in rows:
        if len(images) >= _VISION_MAX_IMAGES:
            break
        if file.status != "ready" or not file.blake3 or file.size > _VISION_MAX_BYTES:
            continue
        chunks: list[bytes] = []
        async for chunk in state.storage.open_range(shard_key(file.blake3), 0, None):
            chunks.append(chunk)
        payload = base64.b64encode(b"".join(chunks)).decode()
        images.append(f"data:{file.mime};base64,{payload}")
    if not images:
        return
    for message in reversed(messages):
        if message.get("role") == "user":
            text = message.get("content") or ""
            message["content"] = [
                {"type": "text", "text": text},
                *({"type": "image_url", "image_url": {"url": url}} for url in images),
            ]
            return


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

    # a concurrent duplicate POST (network retry) may be mid-setup: reserve
    # the id; the loser waits for the winner's stream instead of generating
    # a second sibling (§31.4a)
    if not state.hub.reserve_client(body.message_id):
        for _ in range(100):
            await asyncio.sleep(0.05)
            live = state.hub.get_by_client(body.message_id)
            if live is not None and live.user_id == user.id:
                return _attach_response(state, live, last_event_id)
        raise AppError(
            CONFLICT, "a duplicate send is still starting; retry", status=409, retryable=True
        )
    try:
        return await _send_chat_inner(state, body, user, last_event_id)
    finally:
        state.hub.release_client(body.message_id)


async def _send_chat_inner(
    state: AppState, body: ChatSendRequest, user: User, last_event_id: int, *, retried: bool = False
) -> StreamingResponse:
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
            parts: list[MessagePart] = []
            if newest is not None:
                parts = list(
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
            title=None,
            agent_id=agent.id if agent else None,
            agent_version_id=agent.current_version_id if agent else None,  # §9.1 pin
        )
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

    attach_files: list[File] = []
    if body.file_ids:
        async with state.db.read_session() as session:
            rows = (
                (await session.execute(select(File).where(File.id.in_(body.file_ids))))
                .scalars()
                .all()
            )
        by_id = {f.id: f for f in rows}
        for file_id in body.file_ids:
            f = by_id.get(file_id)
            if f is None or f.owner_id != user.id or f.status != "ready":
                raise AppError(NOT_FOUND, f"file {file_id} not found or not ready", status=404)
            attach_files.append(f)

    user_message = Message(
        id=body.message_id,
        conversation_id=conversation.id,
        parent_id=leaf.id if leaf is not None else None,
        role="user",
        status="complete",
    )
    try:
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
            for f in attach_files:
                kind = "image" if f.mime.startswith("image/") else "document"
                session.add(Attachment(message_id=user_message.id, file_id=f.id, kind=kind))
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(last_message_at=now_ms(), updated_at=now_ms())
            )
    except IntegrityError as error:
        # a concurrent writer committed this exact message id first (the §31.4a
        # race): re-enter the idempotent existing-message path exactly once.
        # Any other integrity failure (e.g. a bad FK) is a real error, not a
        # race — surface it instead of recursing forever.
        async with state.db.read_session() as session:
            now_exists = await session.get(Message, body.message_id)
        if now_exists is not None and not retried:
            return await _send_chat_inner(state, body, user, last_event_id, retried=True)
        raise AppError(
            CONFLICT, "could not persist the message; retry", status=409, retryable=True
        ) from error

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


@router.post("/messages/{message_id}/approve")
async def approve_tool_call(
    message_id: uuid.UUID,
    body: ApprovalRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StopResponse:
    """Resolve an `approval_required` tool gate (§9.2 ask_user mode)."""
    state = get_state(request)
    await _owned_message(state, user, message_id)
    stream = state.hub.get_by_message(message_id)
    if stream is None or stream.done:
        raise AppError(NOT_FOUND, "no live stream awaiting approval", status=404)
    future = stream.pending_approvals.get(body.call_id)
    if future is None or future.done():
        raise AppError(NOT_FOUND, "no pending approval for this call_id", status=404)
    future.set_result(body.approve)
    return StopResponse(stopped=True)


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
