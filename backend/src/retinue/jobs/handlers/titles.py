"""Conversation title generation — the housekeeping model class (§31.3),
falling back to a heuristic so titles work with zero provider config."""

import uuid
from typing import Any

import structlog
from sqlalchemy import select, update

from retinue.core.history import load_thread, to_history
from retinue.db.models import Conversation
from retinue.providers.base import ProviderError

log = structlog.get_logger("retinue.jobs.titles")

MAX_TITLE_CHARS = 64

_PROMPT = (
    "Write a title for this conversation: 3 to 6 words, no quotes, no trailing "
    "punctuation, same language as the conversation. Reply with the title only.\n\n"
    "User: {user}\n\nAssistant: {assistant}"
)


def _heuristic_title(text: str) -> str:
    words = text.strip().split()
    title = " ".join(words[:8])[:MAX_TITLE_CHARS].strip()
    return title or "New conversation"


def _clean(title: str) -> str:
    title = title.strip().strip("\"'").splitlines()[0].strip() if title.strip() else ""
    return title[:MAX_TITLE_CHARS].rstrip(".:,;")


async def generate_title(ctx: Any, payload: dict[str, Any]) -> None:
    conversation_id = uuid.UUID(payload["conversation_id"])
    async with ctx.db.read_session() as session:
        conversation = (
            await session.execute(select(Conversation).where(Conversation.id == conversation_id))
        ).scalar_one_or_none()
        if conversation is None or conversation.title:
            return
        thread = await load_thread(session, conversation_id)
        history = to_history(thread)
        first_user = next((e.text for e in history if e.role == "user"), "")
        first_assistant = next((e.text for e in history if e.role == "assistant"), "")
        assistant_model = next(
            (
                tm.message.model
                for tm in thread
                if tm.message.role == "assistant" and tm.message.model
            ),
            None,
        )

    if not first_user:
        return

    title = ""
    model = ctx.settings.models.housekeeping or assistant_model
    if model:
        try:
            async with ctx.db.read_session() as session:
                call = await ctx.registry.prepare_call(
                    session,
                    user_id=conversation.user_id,
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": _PROMPT.format(
                                user=first_user[:2000], assistant=first_assistant[:2000]
                            ),
                        }
                    ],
                    params={"max_tokens": 24, "temperature": 0.2},
                )
            title = _clean(await ctx.registry.adapter_for(model).complete(call))
        except ProviderError as exc:
            log.info("title_model_failed_falling_back", model=model, error=exc.message)
        except Exception:
            log.exception("title_model_failed_falling_back", model=model)

    if not title:
        title = _heuristic_title(first_user)

    async with ctx.db.write_session() as session:
        # only fill if still untitled — never clobber a user rename
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.title.is_(None))
            .values(title=title)
        )
