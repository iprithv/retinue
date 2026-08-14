"""RAG indexing job (§10): chunk a file's extracted text into a collection
and embed the chunks (embed-cache backed, §31.3)."""

import uuid
from typing import Any

import structlog
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from retinue.core.ids import uuid7
from retinue.db.models import Chunk, Collection, CollectionFile, File, FileText
from retinue.rag.chunk import chunk_text
from retinue.rag.embed import embed_texts

log = structlog.get_logger("retinue.jobs.rag")


async def _set_status(ctx: Any, collection_id: uuid.UUID, file_id: uuid.UUID, status: str) -> None:
    async with ctx.db.write_session() as session:
        membership = (
            await session.execute(
                select(CollectionFile).where(
                    CollectionFile.collection_id == collection_id,
                    CollectionFile.file_id == file_id,
                )
            )
        ).scalar_one_or_none()
        if membership is not None:
            membership.status = status


async def embed_chunks(ctx: Any, payload: dict[str, Any]) -> None:
    collection_id = uuid.UUID(payload["collection_id"])
    file_id = uuid.UUID(payload["file_id"])

    async with ctx.db.read_session() as session:
        collection = await session.get(Collection, collection_id)
        file = await session.get(File, file_id)
        file_text = await session.get(FileText, file_id)
    if collection is None or file is None:
        return
    if file_text is None or not file_text.text.strip():
        await _set_status(ctx, collection_id, file_id, "failed")
        log.info("embed_chunks_no_text", file_id=str(file_id))
        return

    rag_cfg = ctx.settings.rag
    chunks = chunk_text(
        file_text.text,
        ctx.counter,
        target_tokens=rag_cfg.chunk_target_tokens,
        overlap_frac=rag_cfg.chunk_overlap_frac,
    )

    async with ctx.db.write_session() as session:
        vectors = await embed_texts(
            ctx.state,
            session,
            user_id=collection.owner_id,
            model=collection.embed_model,
            texts=[c.text for c in chunks],
        )
        # replace previous index for this (collection, file)
        await session.execute(
            sa_delete(Chunk).where(Chunk.collection_id == collection_id, Chunk.file_id == file_id)
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            session.add(
                Chunk(
                    id=uuid7(),
                    collection_id=collection_id,
                    file_id=file_id,
                    idx=i,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    loc=chunk.loc,
                    embedding=vector,
                )
            )
        # record the discovered dimension on first successful index
        row = await session.get(Collection, collection_id)
        if row is not None and vectors and row.embed_dim == 0:
            row.embed_dim = len(vectors[0]) // 4

    await _set_status(ctx, collection_id, file_id, "indexed")
    log.info(
        "embed_chunks_done",
        collection=str(collection_id),
        file=str(file_id),
        chunks=len(chunks),
    )
