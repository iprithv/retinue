"""Agent versioning, slugging, export/import (§9.1).

Editing always creates version N+1 and moves `agents.current_version_id`;
revert copies an old version forward so history stays linear and auditable.
Export is the canonical `.agent.json`: schema-versioned, server-local ids and
secrets stripped; import remaps everything onto fresh ids.
"""

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.core.errors import NOT_FOUND, VALIDATION_ERROR, AppError
from retinue.core.ids import uuid7
from retinue.core.timeutil import now_ms
from retinue.db.models import Agent, AgentVersion

AGENT_EXPORT_SCHEMA = "retinue.agent/1"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:64] or "agent"


async def unique_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug = base
    n = 1
    while True:
        exists = (
            await session.execute(select(Agent.id).where(Agent.slug == slug))
        ).scalar_one_or_none()
        if exists is None:
            return slug
        n += 1
        slug = f"{base}-{n}"


def _clean_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Validate the §9.1 tools shape: [{type, ref, config}]."""
    cleaned: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise AppError(VALIDATION_ERROR, "each tool must be an object", status=422)
        tool_type = tool.get("type")
        if tool_type not in ("builtin", "mcp", "action"):
            raise AppError(VALIDATION_ERROR, f"unknown tool type {tool_type!r}", status=422)
        ref = tool.get("ref")
        if not isinstance(ref, str) or not ref:
            raise AppError(VALIDATION_ERROR, "tool.ref is required", status=422)
        config = tool.get("config") or {}
        mode = config.get("mode", "auto")
        if mode not in ("auto", "ask_user", "disabled"):
            raise AppError(VALIDATION_ERROR, f"unknown tool mode {mode!r}", status=422)
        cleaned.append({"type": tool_type, "ref": ref, "config": {**config, "mode": mode}})
    return cleaned


async def create_version(
    session: AsyncSession,
    agent: Agent,
    *,
    created_by: uuid.UUID,
    system_prompt: str,
    model: str,
    params: dict[str, Any],
    tools: list[Any],
    mcp_servers: list[Any],
    collection_ids: list[Any],
    starters: list[Any],
    changelog: str | None = None,
) -> AgentVersion:
    """Insert version N+1 and repoint the agent. Caller owns the transaction."""
    latest = (
        await session.execute(
            select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id)
        )
    ).scalar_one()
    version = AgentVersion(
        id=uuid7(),
        agent_id=agent.id,
        version=(latest or 0) + 1,
        system_prompt=system_prompt,
        model=model,
        params=params or {},
        tools=_clean_tools(tools or []),
        mcp_servers=mcp_servers or [],
        collection_ids=[str(c) for c in (collection_ids or [])],
        starters=starters or [],
        changelog=changelog,
        created_by=created_by,
    )
    session.add(version)
    await session.flush()  # version row exists before the agent points at it
    agent.current_version_id = version.id
    agent.updated_at = now_ms()
    return version


async def get_version(session: AsyncSession, agent_id: uuid.UUID, version: int) -> AgentVersion:
    row = (
        await session.execute(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent_id, AgentVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(NOT_FOUND, f"version {version} not found", status=404)
    return row


def export_agent(agent: Agent, version: AgentVersion) -> dict[str, Any]:
    """Canonical .agent.json. Secrets and server-local ids never leave (§9.1):
    mcp server attachments and collection pins are reduced to names the
    importer can re-map or drop."""
    return {
        "schema": AGENT_EXPORT_SCHEMA,
        "name": agent.name,
        "slug": agent.slug,
        "description": agent.description,
        "avatar": agent.avatar,
        "version": version.version,
        "system_prompt": version.system_prompt,
        "model": version.model,
        "params": version.params,
        "tools": [t for t in (version.tools or []) if t.get("type") == "builtin"],
        "starters": version.starters,
    }


def validate_import(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != AGENT_EXPORT_SCHEMA:
        raise AppError(
            VALIDATION_ERROR,
            f"unsupported agent schema {payload.get('schema')!r} (expected {AGENT_EXPORT_SCHEMA})",
            status=422,
        )
    name = payload.get("name")
    system_prompt = payload.get("system_prompt")
    model = payload.get("model")
    if not isinstance(name, str) or not name.strip():
        raise AppError(VALIDATION_ERROR, "agent name is required", status=422)
    if not isinstance(system_prompt, str):
        raise AppError(VALIDATION_ERROR, "system_prompt is required", status=422)
    if not isinstance(model, str) or not model:
        raise AppError(VALIDATION_ERROR, "model is required", status=422)
    return payload
