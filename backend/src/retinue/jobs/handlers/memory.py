"""Post-conversation memory extraction (§14): the housekeeping model proposes
stable user facts/preferences; user setting `memory_mode` decides their fate —
`auto` → active, `review` (default) → proposed badge, `off` → skip."""

import uuid
from typing import Any

import orjson
import structlog
from sqlalchemy import select

from retinue.core.history import load_thread, to_history
from retinue.core.ids import uuid7
from retinue.db.models import Conversation, Memory, User

log = structlog.get_logger("retinue.jobs.memory")

MAX_NEW_MEMORIES = 3

_PROMPT = (
    "Extract stable, durable facts about the user from this exchange: "
    "preferences, role, constraints, or personal context that would still be "
    "true next month. Ignore one-off requests and anything about the current "
    "task. Reply with a JSON array of strings (max 3); reply [] when nothing "
    "qualifies.\n\nUser: {user}\n\nAssistant: {assistant}"
)


def parse_memory_response(raw: str) -> list[str]:
    """Tolerant parse: JSON array preferred, '- ' bullet lines accepted."""
    raw = raw.strip()
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        try:
            items = orjson.loads(raw[start : end + 1])
            if isinstance(items, list):
                return [str(i).strip() for i in items if str(i).strip()][:MAX_NEW_MEMORIES]
        except orjson.JSONDecodeError:
            pass
    bullets = [
        line[2:].strip()
        for line in raw.splitlines()
        if line.strip().startswith("- ") and len(line.strip()) > 4
    ]
    return bullets[:MAX_NEW_MEMORIES]


async def memory_extract(ctx: Any, payload: dict[str, Any]) -> None:
    conversation_id = uuid.UUID(payload["conversation_id"])
    async with ctx.db.read_session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.is_incognito:
            return
        user = await session.get(User, conversation.user_id)
        if user is None:
            return
        mode = (user.settings or {}).get("memory_mode", "review")
        if mode == "off":
            return
        thread = await load_thread(session, conversation_id)
        history = to_history(thread)
        existing = (
            (
                await session.execute(
                    select(Memory.content).where(Memory.user_id == user.id).limit(500)
                )
            )
            .scalars()
            .all()
        )

    last_user = next((e.text for e in reversed(history) if e.role == "user"), "")
    last_assistant = next((e.text for e in reversed(history) if e.role == "assistant"), "")
    if not last_user:
        return

    model = ctx.settings.models.housekeeping or ctx.settings.models.default
    if not model:
        return

    try:
        async with ctx.db.read_session() as session:
            call = await ctx.registry.prepare_call(
                session,
                user_id=user.id,
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": _PROMPT.format(
                            user=last_user[:4000], assistant=last_assistant[:4000]
                        ),
                    }
                ],
                params={"max_tokens": 300, "temperature": 0.0},
            )
        raw = await ctx.registry.adapter_for(model).complete(call)
    except Exception:
        log.info("memory_extract_model_failed", exc_info=True)
        return

    candidates = parse_memory_response(raw)
    known = {c.strip().lower() for c in existing}
    fresh = [c for c in candidates if c.strip().lower() not in known]
    if not fresh:
        return

    status = "active" if mode == "auto" else "proposed"
    created: list[uuid.UUID] = []
    async with ctx.db.write_session() as session:
        for content in fresh:
            memory = Memory(
                id=uuid7(),
                user_id=user.id,
                content=content[:2000],
                source_conversation_id=conversation_id,
                status=status,
            )
            session.add(memory)
            created.append(memory.id)

    if status == "active" and ctx.state is not None:
        from retinue.memory.service import embed_memory

        for memory_id in created:
            await embed_memory(ctx.state, memory_id)
