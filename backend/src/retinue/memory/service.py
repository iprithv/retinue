"""Memory retrieval + persistence helpers (§14).

Selection at context-assembly: cosine over stored embeddings when both sides
have vectors (threshold 0.35, top-5), otherwise most-recently-updated actives.
Incognito conversations never reach this module (guarded by the caller).
"""

import uuid
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.db.models import Memory
from retinue.rag.embed import cosine, default_embed_model, embed_texts, unpack_vector

if TYPE_CHECKING:
    from retinue.core.state import AppState

log = structlog.get_logger("retinue.memory")

SIMILARITY_THRESHOLD = 0.35
TOP_K = 5


async def relevant_memories(
    state: "AppState",
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
) -> list[Memory]:
    rows = (
        (
            await session.execute(
                select(Memory)
                .where(Memory.user_id == user_id, Memory.status == "active")
                .order_by(Memory.updated_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    embedded = [m for m in rows if m.embedding]
    if query and embedded:
        try:
            model = default_embed_model(state)
            async with state.db.write_session() as wsession:  # cache writes
                [query_blob] = await embed_texts(
                    state, wsession, user_id=user_id, model=model, texts=[query]
                )
            query_vec = unpack_vector(query_blob)
            scored = [
                (cosine(query_vec, unpack_vector(m.embedding)), m) for m in embedded if m.embedding
            ]
            scored = [(s, m) for s, m in scored if s > SIMILARITY_THRESHOLD]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored:
                return [m for _, m in scored[:TOP_K]]
        except Exception:
            log.warning("memory_similarity_failed_falling_back", exc_info=True)

    return list(rows[:TOP_K])


async def embed_memory(state: "AppState", memory_id: uuid.UUID) -> None:
    """Best-effort embedding of one memory row (own transaction)."""
    try:
        model = default_embed_model(state)
        async with state.db.write_session() as session:
            memory = await session.get(Memory, memory_id)
            if memory is None:
                return
            [blob] = await embed_texts(
                state, session, user_id=memory.user_id, model=model, texts=[memory.content]
            )
            memory.embedding = blob
    except Exception:
        log.warning("memory_embed_failed", memory_id=str(memory_id), exc_info=True)
