"""SearchService (§13).

SQLite path: FTS5 external-content tables (populated by triggers from
migration 0002) — BM25 ranking, porter + unicode61 tokenizer, prefix indexes
for search-as-you-type, `snippet()` highlighting. Postgres path: ILIKE
fallback behind the same interface until the tsvector tier lands.

FTS queries are built from sanitized user input: each token is double-quoted
(neutralizing MATCH operators) and the final token gets a `*` for
as-you-type prefix matching.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TOKEN_RE = re.compile(r"[^\s\"'()*:^]+")

SNIPPET_TOKENS = 14


@dataclass(slots=True)
class Hit:
    kind: str
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    title: str | None
    snippet: str
    created_at: int | None
    rank: float


def fts_query(raw: str) -> str | None:
    tokens = _TOKEN_RE.findall(raw)[:12]
    if not tokens:
        return None
    quoted = [f'"{t}"' for t in tokens]
    quoted[-1] = f"{quoted[-1]} *" if len(tokens[-1]) >= 2 else quoted[-1]
    return " ".join(quoted)


def _like_pattern(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, bytes):
        return uuid.UUID(bytes=value)
    return uuid.UUID(str(value))


class SearchService:
    def __init__(self, is_sqlite: bool) -> None:
        self.is_sqlite = is_sqlite

    # -- messages ------------------------------------------------------------

    async def search_messages(
        self, session: AsyncSession, user_id: uuid.UUID, query: str, limit: int
    ) -> list[Hit]:
        if self.is_sqlite:
            match = fts_query(query)
            if match is None:
                return []
            sql = text(
                f"""
                SELECT m.id AS message_id, m.conversation_id, c.title,
                       snippet(part_fts, 0, '[', ']', '…', {SNIPPET_TOKENS}) AS snip,
                       m.created_at, bm25(part_fts) AS rank
                FROM part_fts
                JOIN message_parts mp ON mp.rowid = part_fts.rowid
                JOIN messages m ON m.id = mp.message_id
                JOIN conversations c ON c.id = m.conversation_id
                WHERE part_fts MATCH :match AND c.user_id = :user_id
                  AND c.is_incognito = 0
                ORDER BY rank LIMIT :limit
                """
            )
            rows = (
                await session.execute(
                    sql, {"match": match, "user_id": user_id.bytes, "limit": limit}
                )
            ).all()
        else:
            sql = text(
                """
                SELECT m.id AS message_id, m.conversation_id, c.title,
                       substring(mp.text_content for 240) AS snip,
                       m.created_at, 1.0 AS rank
                FROM message_parts mp
                JOIN messages m ON m.id = mp.message_id
                JOIN conversations c ON c.id = m.conversation_id
                WHERE mp.text_content ILIKE :pattern AND c.user_id = :user_id
                  AND c.is_incognito = false
                ORDER BY m.created_at DESC LIMIT :limit
                """
            )
            rows = (
                await session.execute(
                    sql, {"pattern": _like_pattern(query), "user_id": user_id, "limit": limit}
                )
            ).all()
        return [
            Hit(
                kind="message",
                id=_uuid(r.message_id),
                conversation_id=_uuid(r.conversation_id),
                title=r.title,
                snippet=r.snip or "",
                created_at=r.created_at,
                rank=float(r.rank),
            )
            for r in rows
        ]

    # -- conversations ---------------------------------------------------------

    async def search_conversations(
        self, session: AsyncSession, user_id: uuid.UUID, query: str, limit: int
    ) -> list[Hit]:
        if self.is_sqlite:
            match = fts_query(query)
            if match is None:
                return []
            sql = text(
                """
                SELECT c.id, c.title, c.created_at, bm25(conv_fts) AS rank
                FROM conv_fts
                JOIN conversations c ON c.rowid = conv_fts.rowid
                WHERE conv_fts MATCH :match AND c.user_id = :user_id
                  AND c.is_incognito = 0
                ORDER BY rank LIMIT :limit
                """
            )
            rows = (
                await session.execute(
                    sql, {"match": match, "user_id": user_id.bytes, "limit": limit}
                )
            ).all()
        else:
            sql = text(
                """
                SELECT c.id, c.title, c.created_at, 1.0 AS rank
                FROM conversations c
                WHERE c.title ILIKE :pattern AND c.user_id = :user_id
                  AND c.is_incognito = false
                ORDER BY c.last_message_at DESC NULLS LAST LIMIT :limit
                """
            )
            rows = (
                await session.execute(
                    sql, {"pattern": _like_pattern(query), "user_id": user_id, "limit": limit}
                )
            ).all()
        return [
            Hit(
                kind="conversation",
                id=_uuid(r.id),
                conversation_id=_uuid(r.id),
                title=r.title,
                snippet=r.title or "",
                created_at=r.created_at,
                rank=float(r.rank),
            )
            for r in rows
        ]

    # -- files -----------------------------------------------------------------

    async def search_files(
        self, session: AsyncSession, user_id: uuid.UUID, query: str, limit: int
    ) -> list[Hit]:
        hits: list[Hit] = []
        if self.is_sqlite:
            match = fts_query(query)
            if match is not None:
                sql = text(
                    f"""
                    SELECT f.id, f.original_name, f.created_at,
                           snippet(filetext_fts, 0, '[', ']', '…', {SNIPPET_TOKENS}) AS snip,
                           bm25(filetext_fts) AS rank
                    FROM filetext_fts
                    JOIN file_texts ft ON ft.rowid = filetext_fts.rowid
                    JOIN files f ON f.id = ft.file_id
                    WHERE filetext_fts MATCH :match AND f.owner_id = :user_id
                    ORDER BY rank LIMIT :limit
                    """
                )
                rows = (
                    await session.execute(
                        sql, {"match": match, "user_id": user_id.bytes, "limit": limit}
                    )
                ).all()
                hits = [
                    Hit(
                        kind="file",
                        id=_uuid(r.id),
                        conversation_id=None,
                        title=r.original_name,
                        snippet=r.snip or "",
                        created_at=r.created_at,
                        rank=float(r.rank),
                    )
                    for r in rows
                ]
            name_sql = text(
                """
                SELECT f.id, f.original_name, f.created_at
                FROM files f
                WHERE f.owner_id = :user_id AND f.original_name LIKE :pattern ESCAPE '\\'
                ORDER BY f.created_at DESC LIMIT :limit
                """
            )
            params: dict[str, Any] = {
                "user_id": user_id.bytes,
                "pattern": _like_pattern(query),
                "limit": limit,
            }
        else:
            name_sql = text(
                """
                SELECT f.id, f.original_name, f.created_at
                FROM files f
                WHERE f.owner_id = :user_id AND f.original_name ILIKE :pattern
                ORDER BY f.created_at DESC LIMIT :limit
                """
            )
            params = {"user_id": user_id, "pattern": _like_pattern(query), "limit": limit}
        seen = {h.id for h in hits}
        for r in (await session.execute(name_sql, params)).all():
            rid = _uuid(r.id)
            if rid not in seen:
                hits.append(
                    Hit(
                        kind="file",
                        id=rid,
                        conversation_id=None,
                        title=r.original_name,
                        snippet=r.original_name,
                        created_at=r.created_at,
                        rank=0.0,
                    )
                )
        return hits[:limit]

    # -- agents ------------------------------------------------------------------

    async def search_agents(
        self, session: AsyncSession, user_id: uuid.UUID, query: str, limit: int
    ) -> list[Hit]:
        op = "LIKE" if self.is_sqlite else "ILIKE"
        escape = " ESCAPE '\\'" if self.is_sqlite else ""
        sql = text(
            f"""
            SELECT a.id, a.name, a.description, a.created_at
            FROM agents a
            WHERE (a.owner_id = :user_id OR a.visibility IN ('org', 'public'))
              AND a.is_archived = {"0" if self.is_sqlite else "false"}
              AND (a.name {op} :pattern{escape} OR a.description {op} :pattern{escape})
            ORDER BY a.updated_at DESC LIMIT :limit
            """
        )
        rows = (
            await session.execute(
                sql,
                {
                    "user_id": user_id.bytes if self.is_sqlite else user_id,
                    "pattern": _like_pattern(query),
                    "limit": limit,
                },
            )
        ).all()
        return [
            Hit(
                kind="agent",
                id=_uuid(r.id),
                conversation_id=None,
                title=r.name,
                snippet=r.description or r.name,
                created_at=r.created_at,
                rank=0.0,
            )
            for r in rows
        ]
