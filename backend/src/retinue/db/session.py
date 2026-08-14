"""Engine & session management (§17).

SQLite: WAL pragmas on every connection, one writer connection (all writes are
serialized through it — SQLite's law) plus a small read pool. Postgres: one
pooled engine serves both roles.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import orjson
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SQLITE_PRAGMAS = (
    "journal_mode=WAL",
    "synchronous=NORMAL",
    "foreign_keys=ON",
    "busy_timeout=5000",
    "cache_size=-64000",
    "temp_store=MEMORY",
    "mmap_size=268435456",
    "wal_autocheckpoint=1000",
)


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    for pragma in SQLITE_PRAGMAS:
        cursor.execute(f"PRAGMA {pragma}")
    cursor.close()


def _json_serializer(value: Any) -> str:
    return orjson.dumps(value).decode("utf-8")


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        kwargs: dict[str, Any] = {
            "echo": echo,
            "json_serializer": _json_serializer,
            "json_deserializer": orjson.loads,
        }
        if self.is_sqlite:
            self.writer: AsyncEngine = create_async_engine(
                url, pool_size=1, max_overflow=0, **kwargs
            )
            self.reader: AsyncEngine = create_async_engine(
                url, pool_size=4, max_overflow=2, **kwargs
            )
            event.listen(self.writer.sync_engine, "connect", _set_sqlite_pragmas)
            event.listen(self.reader.sync_engine, "connect", _set_sqlite_pragmas)
        else:
            self.writer = create_async_engine(url, pool_size=10, max_overflow=10, **kwargs)
            self.reader = self.writer

        self._write_sessions = async_sessionmaker(self.writer, expire_on_commit=False)
        self._read_sessions = async_sessionmaker(self.reader, expire_on_commit=False)

    @asynccontextmanager
    async def write_session(self) -> AsyncIterator[AsyncSession]:
        """Commit on success, rollback on error."""
        async with self._write_sessions() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def read_session(self) -> AsyncIterator[AsyncSession]:
        async with self._read_sessions() as session:
            yield session

    async def ping(self) -> bool:
        async with self.reader.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.writer.dispose()
        if self.reader is not self.writer:
            await self.reader.dispose()
