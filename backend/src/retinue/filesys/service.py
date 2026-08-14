"""File service (§11): dedupe bookkeeping, MIME sniffing, refcounted GC.

Content addressing: the BLAKE3 of the bytes is the storage key, so identical
uploads store once; `files` rows are per-user references onto `blobs`.
"""

import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.core.timeutil import now_ms
from retinue.db.models import Blob, File
from retinue.filesys.base import StorageBackend, shard_key

log = structlog.get_logger("retinue.files")

# magic-byte MIME sniffing (§11.2: never trust the client type)
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
    (b"ID3", "audio/mpeg"),
]

_ZIP_OFFICE = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_TEXT_EXT = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/x-typescript",
    ".css": "text/css",
}


def sniff_mime(head: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            if mime == "application/zip" and ext in _ZIP_OFFICE:
                return _ZIP_OFFICE[ext]
            if head[8:12] == b"WEBP":
                return "image/webp"
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    # text heuristic: valid utf-8 with no NUL bytes
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return _TEXT_EXT.get(ext, "text/plain")
    return _TEXT_EXT.get(ext, "application/octet-stream")


async def link_blob(
    session: AsyncSession,
    *,
    blake3_hex: str,
    size: int,
    backend: StorageBackend,
) -> Blob:
    """Get-or-create the blob row and bump its refcount. Caller stores bytes
    first (put is idempotent by content addressing)."""
    blob = await session.get(Blob, blake3_hex)
    if blob is None:
        blob = Blob(
            blake3=blake3_hex,
            size=size,
            storage_backend=backend.name,
            storage_key=shard_key(blake3_hex),
            refcount=1,
            created_at=now_ms(),
        )
        session.add(blob)
    else:
        blob.refcount += 1
    return blob


async def release_file(session: AsyncSession, backend: StorageBackend, file: File) -> None:
    """Delete a file reference; GC the blob when the last reference drops (§11.6)."""
    blake3_hex = file.blake3
    await session.delete(file)
    await session.flush()  # the reference must go before the blob (FK)
    if blake3_hex:
        blob = await session.get(Blob, blake3_hex)
        if blob is not None:
            blob.refcount -= 1
            if blob.refcount <= 0:
                await backend.delete(blob.storage_key)
                await session.delete(blob)


async def find_ready_by_hash(session: AsyncSession, blake3_hex: str) -> Blob | None:
    return (
        await session.execute(select(Blob).where(Blob.blake3 == blake3_hex))
    ).scalar_one_or_none()


async def mark_ready(
    session: AsyncSession,
    file_id: uuid.UUID,
    *,
    blake3_hex: str,
    size: int,
    mime: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {"blake3": blake3_hex, "size": size, "status": "ready"}
    if mime:
        values["mime"] = mime
    if meta:
        values["meta"] = meta
    await session.execute(update(File).where(File.id == file_id).values(**values))
