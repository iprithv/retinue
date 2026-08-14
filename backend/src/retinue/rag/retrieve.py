"""Hybrid retrieval (§10): FTS5 BM25 top-N union vector cosine top-N →
Reciprocal Rank Fusion (k=60) → top-k hits with citation locators."""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.db.models import Chunk, Collection, File
from retinue.rag.embed import cosine, embed_texts, unpack_vector
from retinue.search.fts import fts_query

if TYPE_CHECKING:
    from retinue.core.state import AppState

log = structlog.get_logger("retinue.rag.retrieve")


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    file_name: str
    text: str
    score: float
    loc: dict[str, Any]


async def _fts_candidates(
    session: AsyncSession,
    collection_ids: list[uuid.UUID],
    query: str,
    top_n: int,
    is_sqlite: bool,
) -> list[uuid.UUID]:
    if not is_sqlite:
        rows = (
            await session.execute(
                select(Chunk.id)
                .where(
                    Chunk.collection_id.in_(collection_ids),
                    Chunk.text.ilike(f"%{query[:80]}%"),
                )
                .limit(top_n)
            )
        ).all()
        return [r[0] for r in rows]
    match = fts_query(query)
    if match is None:
        return []
    placeholders = ",".join(f":c{i}" for i in range(len(collection_ids)))
    sql = text(
        f"""
        SELECT ch.id FROM chunk_fts
        JOIN chunks ch ON ch.rowid = chunk_fts.rowid
        WHERE chunk_fts MATCH :match AND ch.collection_id IN ({placeholders})
        ORDER BY bm25(chunk_fts) LIMIT :limit
        """
    )
    params: dict[str, Any] = {"match": match, "limit": top_n}
    for i, cid in enumerate(collection_ids):
        params[f"c{i}"] = cid.bytes
    rows = (await session.execute(sql, params)).all()
    return [uuid.UUID(bytes=r[0]) if isinstance(r[0], bytes) else r[0] for r in rows]


async def _vector_candidates(
    state: "AppState",
    session: AsyncSession,
    collections: list[Collection],
    query: str,
    top_n: int,
) -> list[uuid.UUID]:
    """Brute-force cosine per collection (dimensions may differ across
    collections, so scoring never mixes embedding spaces)."""
    scored: list[tuple[float, uuid.UUID]] = []
    for collection in collections:
        try:
            async with state.db.write_session() as wsession:  # embed cache writes
                [query_blob] = await embed_texts(
                    state,
                    wsession,
                    user_id=collection.owner_id,
                    model=collection.embed_model,
                    texts=[query],
                )
        except Exception:
            log.warning("query_embed_failed", collection=str(collection.id), exc_info=True)
            continue
        query_vec = unpack_vector(query_blob)
        rows = (
            await session.execute(
                select(Chunk.id, Chunk.embedding).where(
                    Chunk.collection_id == collection.id, Chunk.embedding.is_not(None)
                )
            )
        ).all()
        for chunk_id, blob in rows:
            score = cosine(query_vec, unpack_vector(blob))
            scored.append((score, chunk_id))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk_id for _, chunk_id in scored[:top_n]]


def _rrf(ranked_lists: list[list[uuid.UUID]], k: int) -> dict[uuid.UUID, float]:
    fused: dict[uuid.UUID, float] = {}
    for ranking in ranked_lists:
        for rank, chunk_id in enumerate(ranking):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return fused


async def retrieve(
    state: "AppState",
    session: AsyncSession,
    *,
    collection_ids: list[uuid.UUID],
    query: str,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    cfg = state.settings.rag
    top_k = top_k or cfg.top_k
    collections = (
        (await session.execute(select(Collection).where(Collection.id.in_(collection_ids))))
        .scalars()
        .all()
    )
    if not collections:
        return []
    ids = [c.id for c in collections]

    fts_ranking = await _fts_candidates(session, ids, query, cfg.fts_top_n, state.db.is_sqlite)
    vec_ranking = await _vector_candidates(
        state, session, list(collections), query, cfg.vector_top_n
    )
    fused = _rrf([fts_ranking, vec_ranking], cfg.rrf_k)
    if not fused:
        return []
    best = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:top_k]
    best_ids = [chunk_id for chunk_id, _ in best]

    rows = (
        await session.execute(
            select(Chunk, File.original_name)
            .join(File, File.id == Chunk.file_id)
            .where(Chunk.id.in_(best_ids))
        )
    ).all()
    by_id = {chunk.id: (chunk, name) for chunk, name in rows}
    out: list[RetrievedChunk] = []
    for chunk_id, score in best:
        entry = by_id.get(chunk_id)
        if entry is None:
            continue
        chunk, name = entry
        out.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                file_id=chunk.file_id,
                file_name=name,
                text=chunk.text,
                score=score,
                loc=chunk.loc or {},
            )
        )
    return out
