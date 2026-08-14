"""Local filesystem storage (§11.1 default): sharded content-addressed layout,
atomic tmp+rename, fsync on complete."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

READ_CHUNK = 1024 * 1024


class LocalStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    async def put(self, key: str, source: Path) -> None:
        def _move() -> None:
            dest = self._path(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():  # dedupe hit: identical content already stored
                source.unlink(missing_ok=True)
                return
            os.replace(source, dest)
            fd = os.open(dest, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

        await asyncio.to_thread(_move)

    async def open_range(
        self, key: str, start: int = 0, end: int | None = None
    ) -> AsyncIterator[bytes]:
        path = self._path(key)
        remaining = (end - start + 1) if end is not None else None

        def _read(offset: int, count: int) -> bytes:
            with open(path, "rb") as f:
                f.seek(offset)
                return f.read(count)

        offset = start
        while True:
            count = READ_CHUNK if remaining is None else min(READ_CHUNK, remaining)
            if count <= 0:
                return
            data = await asyncio.to_thread(_read, offset, count)
            if not data:
                return
            yield data
            offset += len(data)
            if remaining is not None:
                remaining -= len(data)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    def size(self, key: str) -> int | None:
        try:
            return self._path(key).stat().st_size
        except OSError:
            return None
