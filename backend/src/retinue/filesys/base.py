"""StorageBackend seam (§11.1, D14). Local FS ships in v1; S3 implements the
same protocol behind the `[s3]` extra without touching callers."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol


class StorageBackend(Protocol):
    name: str

    async def put(self, key: str, source: Path) -> None:
        """Move/copy a fully-written temp file into the store, atomically."""
        ...

    def open_range(self, key: str, start: int = 0, end: int | None = None) -> AsyncIterator[bytes]:
        """Stream bytes [start, end] (end inclusive; None = to EOF).

        Implemented as an async generator: call, then `async for`."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int | None: ...


def shard_key(blake3_hex: str) -> str:
    """files/ab/cd/<blake3> layout (§11.1)."""
    return f"{blake3_hex[:2]}/{blake3_hex[2:4]}/{blake3_hex}"
