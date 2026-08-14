"""Global search endpoint (§13, §18): ⌘K across messages, conversations,
files, and agents with typed scopes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from retinue.api.schemas import SearchHit, SearchOut
from retinue.core.deps import get_current_user
from retinue.core.state import get_state
from retinue.db.models import User
from retinue.search.fts import SearchService

router = APIRouter()

Scope = Literal["all", "messages", "conversations", "files", "agents"]


@router.get("/search")
async def search(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    q: str = Query(min_length=1, max_length=200),
    scope: Scope = "all",
    limit: int = Query(20, ge=1, le=100),
) -> SearchOut:
    state = get_state(request)
    service = SearchService(state.db.is_sqlite)
    hits = []
    async with state.db.read_session() as session:
        if scope in ("all", "conversations"):
            hits += await service.search_conversations(session, user.id, q, limit)
        if scope in ("all", "messages"):
            hits += await service.search_messages(session, user.id, q, limit)
        if scope in ("all", "files"):
            hits += await service.search_files(session, user.id, q, limit)
        if scope in ("all", "agents"):
            hits += await service.search_agents(session, user.id, q, limit)
    hits.sort(key=lambda h: h.rank)  # bm25: lower = better; non-FTS hits carry 0.0
    return SearchOut(
        query=q,
        hits=[
            SearchHit(
                kind=h.kind,  # type: ignore[arg-type]
                id=h.id,
                conversation_id=h.conversation_id,
                title=h.title,
                snippet=h.snippet,
                created_at=h.created_at,
            )
            for h in hits[:limit]
        ],
    )
