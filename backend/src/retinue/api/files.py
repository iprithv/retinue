"""Files API (§11): direct multipart (path A), tus-style resumable (path B),
dedupe, Range/ETag downloads, refcounted delete. Path C (presigned S3) lands
with the `[s3]` extra behind the same endpoints.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, BinaryIO

import structlog
from blake3 import blake3
from fastapi import APIRouter, Depends, Header, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from retinue.api.schemas import (
    FileOut,
    UploadCompleteRequest,
    UploadCreateRequest,
    UploadCreateResponse,
)
from retinue.core.deps import client_ip, get_current_user, rate_limit
from retinue.core.errors import CONFLICT, NOT_FOUND, VALIDATION_ERROR, AppError
from retinue.core.ids import uuid7
from retinue.core.state import AppState, get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import AuditLog, File, UploadSession, User
from retinue.filesys.base import shard_key
from retinue.filesys.service import link_blob, mark_ready, release_file, sniff_mime

log = structlog.get_logger("retinue.api.files")

router = APIRouter()

READ_CHUNK = 1024 * 1024  # §11.2: RAM stays O(1 MB)

# rendered inline in the browser; everything else downloads as attachment (§16)
_INLINE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}


def _open_append(path: Path) -> BinaryIO:  # append mode creates on first chunk
    return open(path, "ab")


def _open_write(path: Path) -> BinaryIO:
    return open(path, "wb")


def _uploads_dir(state: AppState) -> Path:
    path = state.settings.resolved_data_dir / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_policy(state: AppState, mime: str, size: int) -> None:
    cfg = state.settings.files
    if size > cfg.max_file_mb * 1024 * 1024:
        raise AppError(
            VALIDATION_ERROR,
            f"file exceeds the {cfg.max_file_mb} MB limit",
            status=413,
        )
    for denied in cfg.mime_deny:
        if mime == denied or (denied.endswith("/*") and mime.startswith(denied[:-1])):
            raise AppError(VALIDATION_ERROR, f"file type {mime} is not allowed", status=422)


async def _owned_file(state: AppState, user: User, file_id: uuid.UUID) -> File:
    async with state.db.read_session() as session:
        file = await session.get(File, file_id)
    if file is None or file.owner_id != user.id:
        raise AppError(NOT_FOUND, "file not found", status=404)
    return file


async def _enqueue_extraction(state: AppState, file_id: uuid.UUID) -> None:
    await state.jobs.enqueue("extract_file", {"file_id": str(file_id)})


def _audit_download(state: AppState, user: User, file: File, ip: str) -> None:
    async def _write() -> None:
        try:
            async with state.db.write_session() as session:
                session.add(
                    AuditLog(
                        id=uuid7(),
                        actor_id=user.id,
                        action="file.download",
                        target=str(file.id),
                        meta={"name": file.original_name, "size": file.size},
                        ip=ip,
                    )
                )
        except Exception:
            log.exception("audit_write_failed")

    task = asyncio.get_running_loop().create_task(_write())
    task.add_done_callback(lambda _t: None)


# -- path A: direct multipart (small files) -------------------------------------


@router.post("/files/direct", status_code=201)
async def upload_direct(
    file: UploadFile,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("uploads"))],
) -> FileOut:
    state = get_state(request)
    cfg = state.settings.files
    limit = cfg.direct_max_mb * 1024 * 1024

    tmp = _uploads_dir(state) / f"direct-{uuid7().hex}.part"
    hasher = blake3()
    size = 0
    head = b""
    try:
        out = await asyncio.to_thread(_open_write, tmp)
        try:
            while chunk := await file.read(READ_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise AppError(
                        VALIDATION_ERROR,
                        f"direct uploads are capped at {cfg.direct_max_mb} MB — "
                        "use the resumable path (POST /api/files)",
                        status=413,
                    )
                if len(head) < 512:
                    head += chunk[: 512 - len(head)]
                hasher.update(chunk)
                await asyncio.to_thread(out.write, chunk)
        finally:
            await asyncio.to_thread(out.close)
        if size == 0:
            raise AppError(VALIDATION_ERROR, "empty upload", status=422)

        name = file.filename or "upload.bin"
        mime = sniff_mime(head, name)
        _check_policy(state, mime, size)
        digest = hasher.hexdigest()

        await state.storage.put(shard_key(digest), tmp)
        file_id = uuid7()
        async with state.db.write_session() as session:
            await link_blob(session, blake3_hex=digest, size=size, backend=state.storage)
            row = File(
                id=file_id,
                owner_id=user.id,
                blake3=digest,
                original_name=name,
                mime=mime,
                size=size,
                status="ready",
            )
            session.add(row)
        await _enqueue_extraction(state, file_id)
        return FileOut.model_validate(row)
    finally:
        tmp.unlink(missing_ok=True)


# -- path B: resumable sessions (large files) ------------------------------------


@router.post("/files", status_code=201)
async def create_upload(
    body: UploadCreateRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _rl: Annotated[None, Depends(rate_limit("uploads"))],
) -> UploadCreateResponse:
    state = get_state(request)
    cfg = state.settings.files
    mime_guess = body.mime or "application/octet-stream"
    _check_policy(state, mime_guess, body.size)
    chunk_size = cfg.chunk_size_mib * 1024 * 1024
    expires = now_ms() + cfg.session_ttl_h * 3600 * 1000

    # §11.3 instant dedupe: a pre-hashed file that already exists never uploads
    if body.blake3:
        async with state.db.write_session() as session:
            blob = (
                await session.execute(select(File).where(File.blake3 == body.blake3).limit(1))
            ).scalar_one_or_none()
            if blob is not None and await state.storage.exists(shard_key(body.blake3)):
                await link_blob(
                    session, blake3_hex=body.blake3, size=body.size, backend=state.storage
                )
                row = File(
                    id=uuid7(),
                    owner_id=user.id,
                    blake3=body.blake3,
                    original_name=body.name,
                    mime=blob.mime,
                    size=blob.size,
                    status="ready",
                )
                session.add(row)
                file_id = row.id
                deduped = True
            else:
                deduped = False
        if deduped:
            await _enqueue_extraction(state, file_id)
            return UploadCreateResponse(
                file_id=file_id,
                upload_id=None,
                chunk_size=chunk_size,
                expires_at=expires,
                already_exists=True,
            )

    file_id = uuid7()
    upload_id = uuid7()
    async with state.db.write_session() as session:
        session.add(
            File(
                id=file_id,
                owner_id=user.id,
                original_name=body.name,
                mime=mime_guess,
                size=body.size,
                status="uploading",
            )
        )
        await session.flush()
        session.add(
            UploadSession(
                id=upload_id,
                file_id=file_id,
                received_bytes=0,
                total_bytes=body.size,
                chunk_size=chunk_size,
                expires_at=expires,
            )
        )
        # opportunistic purge of expired sessions (§11.6 retention)
        expired = (
            (
                await session.execute(
                    select(UploadSession).where(UploadSession.expires_at < now_ms())
                )
            )
            .scalars()
            .all()
        )
        for stale in expired:
            (_uploads_dir(state) / f"{stale.id.hex}.part").unlink(missing_ok=True)
            await session.execute(sa_delete(File).where(File.id == stale.file_id))
    return UploadCreateResponse(
        file_id=file_id, upload_id=upload_id, chunk_size=chunk_size, expires_at=expires
    )


async def _session_or_404(state: AppState, user: User, upload_id: uuid.UUID) -> UploadSession:
    async with state.db.read_session() as session:
        upload = await session.get(UploadSession, upload_id)
        file = await session.get(File, upload.file_id) if upload else None
    if upload is None or file is None or file.owner_id != user.id:
        raise AppError(NOT_FOUND, "upload session not found", status=404)
    if upload.expires_at < now_ms():
        raise AppError(NOT_FOUND, "upload session expired", status=404)
    return upload


@router.head("/uploads/{upload_id}")
async def upload_offset(
    upload_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    state = get_state(request)
    upload = await _session_or_404(state, user, upload_id)
    return Response(
        status_code=200,
        headers={
            "Upload-Offset": str(upload.received_bytes),
            "Upload-Length": str(upload.total_bytes),
            "Cache-Control": "no-store",
        },
    )


@router.patch("/uploads/{upload_id}")
async def upload_chunk(
    upload_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    upload_offset: Annotated[int, Header(alias="Upload-Offset")] = 0,
) -> Response:
    state = get_state(request)
    upload = await _session_or_404(state, user, upload_id)
    if upload_offset != upload.received_bytes:
        raise AppError(
            CONFLICT,
            "offset mismatch",
            status=409,
            details={"expected": upload.received_bytes},
        )

    part = _uploads_dir(state) / f"{upload.id.hex}.part"
    received = upload.received_bytes
    hard_cap = upload.total_bytes

    out = await asyncio.to_thread(_open_append, part)
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            received += len(chunk)
            if received > hard_cap:
                raise AppError(VALIDATION_ERROR, "more bytes than declared size", status=413)
            await asyncio.to_thread(out.write, chunk)
        await asyncio.to_thread(out.flush)
        import os

        await asyncio.to_thread(os.fsync, out.fileno())
    finally:
        await asyncio.to_thread(out.close)

    async with state.db.write_session() as session:
        row = await session.get(UploadSession, upload.id)
        assert row is not None
        row.received_bytes = received
    return Response(status_code=204, headers={"Upload-Offset": str(received)})


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(
    upload_id: uuid.UUID,
    body: UploadCompleteRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> FileOut:
    state = get_state(request)
    upload = await _session_or_404(state, user, upload_id)
    if upload.received_bytes != upload.total_bytes:
        raise AppError(
            CONFLICT,
            "upload incomplete",
            status=409,
            details={"received": upload.received_bytes, "expected": upload.total_bytes},
        )
    part = _uploads_dir(state) / f"{upload.id.hex}.part"
    if not part.is_file():
        raise AppError(NOT_FOUND, "upload data missing", status=404)

    def _hash_and_head() -> tuple[str, bytes]:
        hasher = blake3()
        head = b""
        with open(part, "rb") as f:
            while chunk := f.read(READ_CHUNK):
                if len(head) < 512:
                    head += chunk[: 512 - len(head)]
                hasher.update(chunk)
        return hasher.hexdigest(), head

    digest, head = await asyncio.to_thread(_hash_and_head)
    if digest != body.blake3:
        part.unlink(missing_ok=True)
        async with state.db.write_session() as session:
            file = await session.get(File, upload.file_id)
            if file is not None:
                file.status = "failed"
        raise AppError(
            VALIDATION_ERROR,
            "hash mismatch — upload corrupted, retry from scratch",
            status=422,
            details={"server": digest},
        )

    async with state.db.read_session() as session:
        file = await session.get(File, upload.file_id)
    assert file is not None
    mime = sniff_mime(head, file.original_name)
    _check_policy(state, mime, upload.total_bytes)

    await state.storage.put(shard_key(digest), part)
    async with state.db.write_session() as session:
        await link_blob(session, blake3_hex=digest, size=upload.total_bytes, backend=state.storage)
        await mark_ready(session, file.id, blake3_hex=digest, size=upload.total_bytes, mime=mime)
        await session.execute(sa_delete(UploadSession).where(UploadSession.id == upload.id))
        refreshed = await session.get(File, file.id)
    await _enqueue_extraction(state, file.id)
    return FileOut.model_validate(refreshed)


# -- listing / download / delete ---------------------------------------------------


@router.get("/files")
async def list_files(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(200, ge=1, le=500),
) -> list[FileOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(File)
                    .where(File.owner_id == user.id)
                    .order_by(File.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [FileOut.model_validate(f) for f in rows]


@router.get("/files/{file_id}")
async def get_file(
    file_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> FileOut:
    state = get_state(request)
    return FileOut.model_validate(await _owned_file(state, user, file_id))


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header or not header.startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        else:  # suffix range: last N bytes
            length = int(end_s)
            start, end = max(0, size - length), size - 1
    except ValueError:
        return None
    if start > end or start >= size:
        return None
    return start, min(end, size - 1)


@router.get("/files/{file_id}/content")
async def download_file(
    file_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    state = get_state(request)
    file = await _owned_file(state, user, file_id)
    if file.status != "ready" or not file.blake3:
        raise AppError(CONFLICT, "file is not ready", status=409)

    key = shard_key(file.blake3)
    etag = f'"{file.blake3}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})  # type: ignore[return-value]

    inline = file.mime in _INLINE_MIME
    disposition = "inline" if inline else "attachment"
    safe_name = file.original_name.replace('"', "")
    headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
    }
    _audit_download(state, user, file, client_ip(request))

    byte_range = _parse_range(request.headers.get("range"), file.size)
    if byte_range is not None:
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end}/{file.size}"
        headers["Content-Length"] = str(end - start + 1)

        async def ranged() -> AsyncIterator[bytes]:
            async for chunk in state.storage.open_range(key, start, end):
                yield chunk

        return StreamingResponse(ranged(), status_code=206, media_type=file.mime, headers=headers)

    headers["Content-Length"] = str(file.size)

    async def full() -> AsyncIterator[bytes]:
        async for chunk in state.storage.open_range(key, 0, None):
            yield chunk

    return StreamingResponse(full(), media_type=file.mime, headers=headers)


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _owned_file(state, user, file_id)
    async with state.db.write_session() as session:
        file = await session.get(File, file_id)
        if file is not None:
            await release_file(session, state.storage, file)
