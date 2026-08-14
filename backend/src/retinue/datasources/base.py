"""DataSource protocol and result shapes (§30.1).

Results are JSON-safe previews sized for model context; long operations honor
the per-source timeout. Every adapter is read-only by construction — the SQL
guard rejects anything but SELECT before an adapter ever sees it, and NoSQL
adapters expose only read operations.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_ROW_LIMIT = 1000
HARD_ROW_LIMIT = 10_000
DEFAULT_TIMEOUT_S = 10.0
RESULT_BYTE_CAP = 1_000_000  # preview cap before masking/serialization
MODEL_CHAR_CAP = 16_000  # what a tool result hands the model
SAMPLE_ROWS = 3


class DataSourceError(Exception):
    """User-facing failure: bad statement, unreachable host, missing driver."""


@dataclass(slots=True)
class ColumnInfo:
    name: str
    type: str


@dataclass(slots=True)
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_estimate: int | None = None


@dataclass(slots=True)
class SchemaModel:
    tables: list[TableInfo] = field(default_factory=list)
    note: str | None = None  # e.g. "collections" for document stores


@dataclass(slots=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: int = 0
    note: str | None = None


class EngineAdapter(Protocol):
    """One live connection surface to an external engine."""

    async def probe(self) -> None:
        """Cheapest possible auth + read check (SELECT 1 / ping)."""
        ...

    async def introspect(self) -> SchemaModel: ...

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult: ...

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult: ...

    async def close(self) -> None: ...


def jsonable(value: Any) -> Any:
    """Coerce driver-native cells into JSON-safe values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def clip_rows(columns: list[str], raw_rows: list[Any], limit: int) -> tuple[list[list[Any]], bool]:
    """Apply the row limit and the byte cap; report truncation honestly."""
    rows: list[list[Any]] = []
    budget = RESULT_BYTE_CAP
    truncated = len(raw_rows) > limit
    for raw in raw_rows[:limit]:
        row = [jsonable(v) for v in raw]
        budget -= sum(len(str(v)) for v in row) + 8
        if budget < 0:
            truncated = True
            break
        rows.append(row)
    return rows, truncated
