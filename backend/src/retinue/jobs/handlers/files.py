"""File post-processing jobs (§11.6): text extraction into `file_texts`
(drives file search and chat-about-this-file), plus collection indexing
hand-off when the file belongs to a collection."""

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from retinue.core.timeutil import now_ms
from retinue.db.models import CollectionFile, File, FileText
from retinue.filesys.base import shard_key
from retinue.rag.extract import ExtractionError, extract_text

log = structlog.get_logger("retinue.jobs.files")


async def extract_file(ctx: Any, payload: dict[str, Any]) -> None:
    file_id = uuid.UUID(payload["file_id"])
    async with ctx.db.read_session() as session:
        file = await session.get(File, file_id)
    if file is None or file.status != "ready" or not file.blake3:
        return

    # local backend only in v1: read straight from the sharded path
    path = ctx.settings.resolved_data_dir / "files" / shard_key(file.blake3)
    if not path.is_file():
        log.warning("extract_blob_missing", file_id=str(file_id))
        return

    try:
        text, extractor = await extract_text(path, file.mime, file.original_name)
    except ExtractionError as exc:
        async with ctx.db.write_session() as session:
            row = await session.get(File, file_id)
            if row is not None:
                row.meta = {**(row.meta or {}), "extraction": str(exc)}
        return

    async with ctx.db.write_session() as session:
        existing = await session.get(FileText, file_id)
        if existing is None:
            session.add(
                FileText(file_id=file_id, text=text, extracted_at=now_ms(), extractor=extractor)
            )
        else:
            existing.text = text
            existing.extracted_at = now_ms()
            existing.extractor = extractor
        row = await session.get(File, file_id)
        if row is not None:
            row.meta = {**(row.meta or {}), "text_chars": len(text), "extractor": extractor}

    # if the file is pinned to collections, (re)index its chunks
    async with ctx.db.read_session() as session:
        memberships = (
            (await session.execute(select(CollectionFile).where(CollectionFile.file_id == file_id)))
            .scalars()
            .all()
        )
    for membership in memberships:
        await ctx.jobs.enqueue(
            "embed_chunks",
            {
                "collection_id": str(membership.collection_id),
                "file_id": str(file_id),
            },
        )
