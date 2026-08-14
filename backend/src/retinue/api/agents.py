"""Agents API (§18): CRUD, versions, revert, export/import, test bench SSE.

Editing behavior always creates version N+1 (§9.1); conversations pin the
version they started with and never silently change.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.agents.service import (
    create_version,
    export_agent,
    get_version,
    unique_slug,
    validate_import,
)
from retinue.api.schemas import (
    AgentBehavior,
    AgentCreate,
    AgentOut,
    AgentPatch,
    AgentTestRequest,
    AgentVersionOut,
)
from retinue.core.deps import get_current_user, rate_limit
from retinue.core.errors import CONFLICT, NOT_FOUND, AppError
from retinue.core.ids import uuid7
from retinue.core.sse import BLOCK_START, DELTA, ERROR, MESSAGE_END, MESSAGE_START, encode_sse
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import Agent, AgentVersion, Conversation, User
from retinue.providers.base import ProviderError, aclose_events

log = structlog.get_logger("retinue.api.agents")

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _agent_out(agent: Agent, version: AgentVersion | None, *, user: User) -> AgentOut:
    return AgentOut(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        avatar=agent.avatar,
        visibility=agent.visibility,
        is_archived=agent.is_archived,
        owned=agent.owner_id == user.id,
        current_version=AgentVersionOut.model_validate(version) if version else None,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def _visible_agent(
    session: AsyncSession, user: User, agent_id: uuid.UUID, *, write: bool = False
) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise AppError(NOT_FOUND, "agent not found", status=404)
    owned = agent.owner_id == user.id
    if write and not owned and user.role not in ("owner", "admin"):
        raise AppError(NOT_FOUND, "agent not found", status=404)
    if not write and not owned and agent.visibility not in ("org", "public"):
        raise AppError(NOT_FOUND, "agent not found", status=404)
    return agent


async def _current_version(session: AsyncSession, agent: Agent) -> AgentVersion | None:
    if agent.current_version_id is None:
        return None
    return await session.get(AgentVersion, agent.current_version_id)


# -- CRUD ---------------------------------------------------------------------


@router.get("/agents")
async def list_agents(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    archived: bool = False,
) -> list[AgentOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        agents = (
            (
                await session.execute(
                    select(Agent)
                    .where(
                        Agent.is_archived == archived,
                        (Agent.owner_id == user.id) | (Agent.visibility.in_(("org", "public"))),
                    )
                    .order_by(Agent.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        version_ids = [a.current_version_id for a in agents if a.current_version_id]
        versions = {}
        if version_ids:
            rows = (
                (
                    await session.execute(
                        select(AgentVersion).where(AgentVersion.id.in_(version_ids))
                    )
                )
                .scalars()
                .all()
            )
            versions = {v.id: v for v in rows}
    return [
        _agent_out(
            a,
            versions.get(a.current_version_id) if a.current_version_id else None,
            user=user,
        )
        for a in agents
    ]


@router.post("/agents", status_code=201)
async def create_agent(
    body: AgentCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> AgentOut:
    state = get_state(request)
    async with state.db.write_session() as session:
        agent = Agent(
            id=uuid7(),
            owner_id=user.id,
            slug=await unique_slug(session, body.name),
            name=body.name,
            description=body.description,
            avatar=body.avatar,
            visibility=body.visibility,
        )
        session.add(agent)
        await session.flush()
        version = await create_version(
            session,
            agent,
            created_by=user.id,
            system_prompt=body.system_prompt,
            model=body.model,
            params=body.params,
            tools=body.tools,
            mcp_servers=body.mcp_servers,
            collection_ids=body.collection_ids,
            starters=body.starters,
            changelog=body.changelog or "initial version",
        )
    return _agent_out(agent, version, user=user)


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> AgentOut:
    state = get_state(request)
    async with state.db.read_session() as session:
        agent = await _visible_agent(session, user, agent_id)
        version = await _current_version(session, agent)
    return _agent_out(agent, version, user=user)


@router.patch("/agents/{agent_id}")
async def patch_agent(
    agent_id: uuid.UUID,
    body: AgentPatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> AgentOut:
    state = get_state(request)
    updates = body.model_dump(exclude_unset=True)
    async with state.db.write_session() as session:
        agent = await _visible_agent(session, user, agent_id, write=True)
        for field, value in updates.items():
            setattr(agent, field, value)
        agent.updated_at = now_ms()
        version = await _current_version(session, agent)
    return _agent_out(agent, version, user=user)


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Hard-delete when no conversation pins it; otherwise archive (pinned
    history must stay resolvable, §9.1)."""
    state = get_state(request)
    async with state.db.write_session() as session:
        agent = await _visible_agent(session, user, agent_id, write=True)
        in_use = (
            await session.execute(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.agent_id == agent.id)
            )
        ).scalar_one()
        if in_use:
            agent.is_archived = True
            agent.updated_at = now_ms()
        else:
            await session.delete(agent)  # cascade removes versions


# -- versions -------------------------------------------------------------------


@router.get("/agents/{agent_id}/versions")
async def list_versions(
    agent_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[AgentVersionOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        await _visible_agent(session, user, agent_id)
        rows = (
            (
                await session.execute(
                    select(AgentVersion)
                    .where(AgentVersion.agent_id == agent_id)
                    .order_by(AgentVersion.version.desc())
                )
            )
            .scalars()
            .all()
        )
    return [AgentVersionOut.model_validate(v) for v in rows]


@router.post("/agents/{agent_id}/versions", status_code=201)
async def new_version(
    agent_id: uuid.UUID,
    body: AgentBehavior,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> AgentVersionOut:
    state = get_state(request)
    async with state.db.write_session() as session:
        agent = await _visible_agent(session, user, agent_id, write=True)
        version = await create_version(
            session,
            agent,
            created_by=user.id,
            system_prompt=body.system_prompt,
            model=body.model,
            params=body.params,
            tools=body.tools,
            mcp_servers=body.mcp_servers,
            collection_ids=body.collection_ids,
            starters=body.starters,
            changelog=body.changelog,
        )
    return AgentVersionOut.model_validate(version)


@router.post("/agents/{agent_id}/revert/{version}", status_code=201)
async def revert_version(
    agent_id: uuid.UUID,
    version: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> AgentVersionOut:
    state = get_state(request)
    async with state.db.write_session() as session:
        agent = await _visible_agent(session, user, agent_id, write=True)
        source = await get_version(session, agent_id, version)
        created = await create_version(
            session,
            agent,
            created_by=user.id,
            system_prompt=source.system_prompt,
            model=source.model,
            params=source.params,
            tools=source.tools,
            mcp_servers=source.mcp_servers,
            collection_ids=source.collection_ids,
            starters=source.starters,
            changelog=f"revert to v{version}",
        )
    return AgentVersionOut.model_validate(created)


# -- export / import ----------------------------------------------------------------


@router.get("/agents/{agent_id}/export")
async def export_agent_json(
    agent_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    state = get_state(request)
    async with state.db.read_session() as session:
        agent = await _visible_agent(session, user, agent_id)
        version = await _current_version(session, agent)
    if version is None:
        raise AppError(CONFLICT, "agent has no versions", status=409)
    return export_agent(agent, version)


@router.post("/agents/import", status_code=201)
async def import_agent_json(
    payload: dict[str, Any],
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> AgentOut:
    state = get_state(request)
    data = validate_import(payload)
    async with state.db.write_session() as session:
        agent = Agent(
            id=uuid7(),
            owner_id=user.id,
            slug=await unique_slug(session, str(data.get("slug") or data["name"])),
            name=str(data["name"]),
            description=data.get("description"),
            avatar=data.get("avatar"),
            visibility="private",
        )
        session.add(agent)
        await session.flush()
        version = await create_version(
            session,
            agent,
            created_by=user.id,
            system_prompt=str(data["system_prompt"]),
            model=str(data["model"]),
            params=dict(data.get("params") or {}),
            tools=list(data.get("tools") or []),
            mcp_servers=[],
            collection_ids=[],
            starters=list(data.get("starters") or []),
            changelog="imported",
        )
    return _agent_out(agent, version, user=user)


# -- test bench (§6.6 studio split-view) ------------------------------------------------


@router.post("/agents/{agent_id}/test")
async def test_agent(
    agent_id: uuid.UUID,
    body: AgentTestRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("chat"))],
) -> StreamingResponse:
    """Ephemeral SSE turn against the agent's config — nothing is persisted."""
    state = get_state(request)
    async with state.db.read_session() as session:
        agent = await _visible_agent(session, user, agent_id)
        version = await _current_version(session, agent)

    if body.behavior is not None:
        system_prompt = body.behavior.system_prompt
        model = body.behavior.model
        params: dict[str, Any] = dict(body.behavior.params)
    elif version is not None:
        system_prompt = version.system_prompt
        model = version.model
        params = dict(version.params or {})
    else:
        raise AppError(CONFLICT, "agent has no versions", status=409)

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for m in body.messages[-32:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})

    async with state.db.read_session() as session:
        call = await state.registry.prepare_call(
            session, user_id=user.id, model=model, messages=messages, params=params
        )

    adapter = state.registry.adapter_for(model)
    message_id = uuid7()

    async def gen() -> AsyncIterator[bytes]:
        yield encode_sse(
            None,
            MESSAGE_START,
            {
                "message_id": str(message_id),
                "conversation_id": None,
                "model": model,
                "agent_version_id": str(version.id) if version else None,
                "ephemeral": True,
            },
        )
        block_open = False
        try:
            agen = adapter.stream(call)
            try:
                async for event in agen:
                    if event.kind in ("text_delta", "thinking_delta") and event.text:
                        if not block_open:
                            yield encode_sse(None, BLOCK_START, {"index": 0, "type": "text"})
                            block_open = True
                        yield encode_sse(None, DELTA, {"index": 0, "text": event.text})
            finally:
                await aclose_events(agen)
            yield encode_sse(
                None, MESSAGE_END, {"stop_reason": "end", "ttft_ms": None, "total_ms": None}
            )
        except ProviderError as exc:
            yield encode_sse(
                None,
                ERROR,
                {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
            )
            yield encode_sse(
                None, MESSAGE_END, {"stop_reason": "error", "ttft_ms": None, "total_ms": None}
            )

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)
