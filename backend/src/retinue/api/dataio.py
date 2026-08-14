"""Data export/import (§18): the user's conversations, agents, and memories
as a JSONL zip. Leaving must always be as easy as arriving."""

import io
import uuid
import zipfile
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from retinue.core.deps import get_current_user, rate_limit
from retinue.core.errors import VALIDATION_ERROR, AppError
from retinue.core.ids import uuid7
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import (
    Agent,
    AgentVersion,
    Conversation,
    Memory,
    Message,
    MessagePart,
    User,
)

router = APIRouter()

EXPORT_SCHEMA = "retinue.export/1"
MAX_IMPORT_BYTES = 200 * 1024 * 1024


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"\n".join(orjson.dumps(r) for r in rows)


@router.get("/export")
async def export_data(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("uploads"))],
) -> Response:
    state = get_state(request)
    async with state.db.read_session() as session:
        conversations = (
            (await session.execute(select(Conversation).where(Conversation.user_id == user.id)))
            .scalars()
            .all()
        )
        conv_ids = [c.id for c in conversations]
        messages: list[Message] = []
        parts_by_message: dict[uuid.UUID, list[MessagePart]] = {}
        if conv_ids:
            messages = list(
                (
                    await session.execute(
                        select(Message).where(Message.conversation_id.in_(conv_ids))
                    )
                )
                .scalars()
                .all()
            )
            part_rows = (
                (
                    await session.execute(
                        select(MessagePart).where(
                            MessagePart.message_id.in_([m.id for m in messages])
                        )
                    )
                )
                .scalars()
                .all()
            )
            for part in part_rows:
                parts_by_message.setdefault(part.message_id, []).append(part)
        agents = (
            (await session.execute(select(Agent).where(Agent.owner_id == user.id))).scalars().all()
        )
        versions: list[AgentVersion] = []
        if agents:
            versions = list(
                (
                    await session.execute(
                        select(AgentVersion).where(
                            AgentVersion.agent_id.in_([a.id for a in agents])
                        )
                    )
                )
                .scalars()
                .all()
            )
        memories = (
            (await session.execute(select(Memory).where(Memory.user_id == user.id))).scalars().all()
        )

    conv_rows = [
        {
            "id": str(c.id),
            "title": c.title,
            "created_at": c.created_at,
            "is_incognito": c.is_incognito,
            "messages": [
                {
                    "id": str(m.id),
                    "parent_id": str(m.parent_id) if m.parent_id else None,
                    "role": m.role,
                    "status": m.status,
                    "model": m.model,
                    "created_at": m.created_at,
                    "parts": [
                        {"idx": p.idx, "type": p.type, "content": p.content}
                        for p in sorted(parts_by_message.get(m.id, []), key=lambda p: p.idx)
                    ],
                }
                for m in sorted(
                    [m for m in messages if m.conversation_id == c.id],
                    key=lambda m: m.created_at,
                )
            ],
        }
        for c in conversations
    ]
    versions_by_agent: dict[uuid.UUID, list[AgentVersion]] = {}
    for v in versions:
        versions_by_agent.setdefault(v.agent_id, []).append(v)
    agent_rows = [
        {
            "name": a.name,
            "slug": a.slug,
            "description": a.description,
            "versions": [
                {
                    "version": v.version,
                    "system_prompt": v.system_prompt,
                    "model": v.model,
                    "params": v.params,
                    "tools": v.tools,
                    "starters": v.starters,
                    "changelog": v.changelog,
                }
                for v in sorted(versions_by_agent.get(a.id, []), key=lambda v: v.version)
            ],
        }
        for a in agents
    ]
    memory_rows = [
        {"content": m.content, "status": m.status, "created_at": m.created_at} for m in memories
    ]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            orjson.dumps(
                {
                    "schema": EXPORT_SCHEMA,
                    "exported_at": now_ms(),
                    "email": user.email,
                    "counts": {
                        "conversations": len(conv_rows),
                        "agents": len(agent_rows),
                        "memories": len(memory_rows),
                    },
                }
            ),
        )
        archive.writestr("conversations.jsonl", _jsonl(conv_rows))
        archive.writestr("agents.jsonl", _jsonl(agent_rows))
        archive.writestr("memories.jsonl", _jsonl(memory_rows))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="retinue-export.zip"'},
    )


@router.post("/import")
async def import_data(
    file: UploadFile,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("uploads"))],
) -> dict[str, int]:
    state = get_state(request)
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise AppError(VALIDATION_ERROR, "import archive too large", status=413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        manifest = orjson.loads(archive.read("manifest.json"))
    except Exception as exc:
        raise AppError(VALIDATION_ERROR, "not a retinue export archive", status=422) from exc
    if manifest.get("schema") != EXPORT_SCHEMA:
        raise AppError(
            VALIDATION_ERROR, f"unsupported export schema {manifest.get('schema')!r}", status=422
        )

    def _rows(name: str) -> list[dict[str, Any]]:
        try:
            data = archive.read(name)
        except KeyError:
            return []
        return [orjson.loads(line) for line in data.splitlines() if line.strip()]

    imported = {"conversations": 0, "agents": 0, "memories": 0}
    from retinue.agents.service import create_version, unique_slug

    async with state.db.write_session() as session:
        for row in _rows("conversations.jsonl")[:5000]:
            conversation = Conversation(
                id=uuid7(),
                user_id=user.id,
                title=(row.get("title") or "Imported")[:300],
                created_at=int(row.get("created_at") or now_ms()),
            )
            session.add(conversation)
            await session.flush()
            id_map: dict[str, uuid.UUID] = {}
            for msg in row.get("messages", [])[:2000]:
                new_id = uuid7()
                id_map[str(msg.get("id"))] = new_id
                parent = id_map.get(str(msg.get("parent_id")))
                session.add(
                    Message(
                        id=new_id,
                        conversation_id=conversation.id,
                        parent_id=parent,
                        role=str(msg.get("role", "user"))[:16],
                        status="complete",
                        model=msg.get("model"),
                        created_at=int(msg.get("created_at") or now_ms()),
                    )
                )
                await session.flush()
                for part in msg.get("parts", [])[:64]:
                    content = part.get("content") or {}
                    text = content.get("text") if isinstance(content, dict) else None
                    session.add(
                        MessagePart(
                            id=uuid7(),
                            message_id=new_id,
                            idx=int(part.get("idx", 0)),
                            type=str(part.get("type", "text"))[:24],
                            content=content if isinstance(content, dict) else {},
                            text_content=text if isinstance(text, str) else None,
                        )
                    )
            conversation.last_message_at = conversation.created_at
            imported["conversations"] += 1

        for row in _rows("agents.jsonl")[:200]:
            agent_versions = row.get("versions") or []
            if not agent_versions:
                continue
            agent = Agent(
                id=uuid7(),
                owner_id=user.id,
                slug=await unique_slug(session, str(row.get("slug") or row.get("name", "agent"))),
                name=str(row.get("name", "Imported agent"))[:200],
                description=row.get("description"),
            )
            session.add(agent)
            await session.flush()
            for v in agent_versions[:100]:
                await create_version(
                    session,
                    agent,
                    created_by=user.id,
                    system_prompt=str(v.get("system_prompt", "")),
                    model=str(v.get("model", "")) or "openai/gpt-4o-mini",
                    params=dict(v.get("params") or {}),
                    tools=list(v.get("tools") or []),
                    mcp_servers=[],
                    collection_ids=[],
                    starters=list(v.get("starters") or []),
                    changelog=v.get("changelog"),
                )
            imported["agents"] += 1

        for row in _rows("memories.jsonl")[:1000]:
            content = str(row.get("content", "")).strip()
            if not content:
                continue
            session.add(
                Memory(
                    id=uuid7(),
                    user_id=user.id,
                    content=content[:2000],
                    status="active" if row.get("status") == "active" else "disabled",
                )
            )
            imported["memories"] += 1

    return imported
