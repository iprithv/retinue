"""Memory API (§14): full CRUD + review-mode approval. Explicit and
inspectable — the UI's "why does it know this?" reads from here."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from retinue.api.schemas import MemoryCreate, MemoryOut, MemoryPatch
from retinue.core.deps import get_current_user
from retinue.core.errors import CONFLICT, NOT_FOUND, AppError
from retinue.core.ids import uuid7
from retinue.core.state import AppState, get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import Memory, User
from retinue.memory.service import embed_memory

router = APIRouter()


async def _owned(state: AppState, user: User, memory_id: uuid.UUID) -> Memory:
    async with state.db.read_session() as session:
        memory = await session.get(Memory, memory_id)
    if memory is None or memory.user_id != user.id:
        raise AppError(NOT_FOUND, "memory not found", status=404)
    return memory


@router.get("/memories")
async def list_memories(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    status: Literal["all", "active", "proposed", "disabled"] = Query("all"),
) -> list[MemoryOut]:
    state = get_state(request)
    query = select(Memory).where(Memory.user_id == user.id)
    if status != "all":
        query = query.where(Memory.status == status)
    async with state.db.read_session() as session:
        rows = (await session.execute(query.order_by(Memory.updated_at.desc()))).scalars().all()
    return [MemoryOut.model_validate(m) for m in rows]


@router.post("/memories", status_code=201)
async def create_memory(
    body: MemoryCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> MemoryOut:
    state = get_state(request)
    memory = Memory(id=uuid7(), user_id=user.id, content=body.content, status="active")
    async with state.db.write_session() as session:
        session.add(memory)
    await embed_memory(state, memory.id)
    return MemoryOut.model_validate(memory)


@router.patch("/memories/{memory_id}")
async def patch_memory(
    memory_id: uuid.UUID,
    body: MemoryPatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> MemoryOut:
    state = get_state(request)
    await _owned(state, user, memory_id)
    updates = body.model_dump(exclude_unset=True)
    async with state.db.write_session() as session:
        memory = await session.get(Memory, memory_id)
        assert memory is not None
        for field, value in updates.items():
            setattr(memory, field, value)
        memory.updated_at = now_ms()
        if "content" in updates:
            memory.embedding = None
        result = memory
    if "content" in updates:
        await embed_memory(state, memory_id)
    return MemoryOut.model_validate(result)


@router.post("/memories/{memory_id}/approve")
async def approve_memory(
    memory_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> MemoryOut:
    state = get_state(request)
    memory = await _owned(state, user, memory_id)
    if memory.status != "proposed":
        raise AppError(CONFLICT, "only proposed memories can be approved", status=409)
    async with state.db.write_session() as session:
        row = await session.get(Memory, memory_id)
        assert row is not None
        row.status = "active"
        row.updated_at = now_ms()
        result = row
    await embed_memory(state, memory_id)
    return MemoryOut.model_validate(result)


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _owned(state, user, memory_id)
    async with state.db.write_session() as session:
        memory = await session.get(Memory, memory_id)
        if memory is not None:
            await session.delete(memory)
