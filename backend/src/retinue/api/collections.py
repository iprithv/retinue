"""RAG collections API (§18): CRUD, file membership, status, search."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

from retinue.api.schemas import (
    CollectionCreate,
    CollectionFilesRequest,
    CollectionFileStatus,
    CollectionOut,
    CollectionStatusOut,
    RagHit,
)
from retinue.core.deps import get_current_user
from retinue.core.errors import NOT_FOUND, AppError
from retinue.core.ids import uuid7
from retinue.core.state import AppState, get_state
from retinue.db.models import Chunk, Collection, CollectionFile, File, User
from retinue.rag.embed import default_embed_model
from retinue.rag.retrieve import retrieve

router = APIRouter()


async def _owned(state: AppState, user: User, collection_id: uuid.UUID) -> Collection:
    async with state.db.read_session() as session:
        collection = await session.get(Collection, collection_id)
    if collection is None or collection.owner_id != user.id:
        raise AppError(NOT_FOUND, "collection not found", status=404)
    return collection


@router.get("/collections")
async def list_collections(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[CollectionOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(Collection)
                    .where(Collection.owner_id == user.id)
                    .order_by(Collection.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return [CollectionOut.model_validate(c) for c in rows]


@router.post("/collections", status_code=201)
async def create_collection(
    body: CollectionCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> CollectionOut:
    state = get_state(request)
    collection = Collection(
        id=uuid7(),
        owner_id=user.id,
        name=body.name,
        description=body.description,
        embed_model=body.embed_model or default_embed_model(state),
        embed_dim=0,  # discovered on first index run
    )
    async with state.db.write_session() as session:
        session.add(collection)
    return CollectionOut.model_validate(collection)


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _owned(state, user, collection_id)
    async with state.db.write_session() as session:
        collection = await session.get(Collection, collection_id)
        if collection is not None:
            await session.delete(collection)  # cascades chunks + memberships


@router.post("/collections/{collection_id}/files", status_code=202)
async def add_files(
    collection_id: uuid.UUID,
    body: CollectionFilesRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> CollectionStatusOut:
    state = get_state(request)
    await _owned(state, user, collection_id)
    async with state.db.write_session() as session:
        files = (
            (await session.execute(select(File).where(File.id.in_(body.file_ids)))).scalars().all()
        )
        by_id = {f.id: f for f in files}
        for file_id in body.file_ids:
            file = by_id.get(file_id)
            if file is None or file.owner_id != user.id or file.status != "ready":
                raise AppError(NOT_FOUND, f"file {file_id} not found or not ready", status=404)
            existing = (
                await session.execute(
                    select(CollectionFile).where(
                        CollectionFile.collection_id == collection_id,
                        CollectionFile.file_id == file_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    CollectionFile(collection_id=collection_id, file_id=file_id, status="pending")
                )
    for file_id in body.file_ids:
        await state.jobs.enqueue(
            "embed_chunks",
            {"collection_id": str(collection_id), "file_id": str(file_id)},
        )
    return await collection_status(collection_id, request, user)


@router.delete("/collections/{collection_id}/files/{file_id}", status_code=204)
async def remove_file(
    collection_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _owned(state, user, collection_id)
    async with state.db.write_session() as session:
        await session.execute(
            sa_delete(Chunk).where(Chunk.collection_id == collection_id, Chunk.file_id == file_id)
        )
        await session.execute(
            sa_delete(CollectionFile).where(
                CollectionFile.collection_id == collection_id,
                CollectionFile.file_id == file_id,
            )
        )


@router.get("/collections/{collection_id}/status")
async def collection_status(
    collection_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> CollectionStatusOut:
    state = get_state(request)
    await _owned(state, user, collection_id)
    async with state.db.read_session() as session:
        rows = (
            await session.execute(
                select(CollectionFile, File.original_name)
                .join(File, File.id == CollectionFile.file_id)
                .where(CollectionFile.collection_id == collection_id)
            )
        ).all()
        count_rows = (
            await session.execute(
                select(Chunk.file_id, func.count())
                .where(Chunk.collection_id == collection_id)
                .group_by(Chunk.file_id)
            )
        ).all()
        counts: dict[uuid.UUID, int] = {row[0]: row[1] for row in count_rows}
    return CollectionStatusOut(
        collection_id=collection_id,
        files=[
            CollectionFileStatus(
                file_id=membership.file_id,
                name=name,
                status=membership.status,
                chunks=counts.get(membership.file_id, 0),
            )
            for membership, name in rows
        ],
    )


@router.get("/collections/{collection_id}/search")
async def search_collection(
    collection_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    q: str = Query(min_length=1, max_length=1000),
    top_k: int = Query(6, ge=1, le=20),
) -> list[RagHit]:
    state = get_state(request)
    await _owned(state, user, collection_id)
    async with state.db.read_session() as session:
        hits = await retrieve(state, session, collection_ids=[collection_id], query=q, top_k=top_k)
    return [
        RagHit(
            chunk_id=h.chunk_id,
            file_id=h.file_id,
            file_name=h.file_name,
            text=h.text,
            score=h.score,
            loc=h.loc,
        )
        for h in hits
    ]
