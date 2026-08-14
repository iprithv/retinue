"""Layered read-only guardrails for SQL sources (§30.3, normative).

Layer 2 of the spec's ladder lives here: AST validation via sqlglot — parse in
the source dialect, reject anything that is not a single SELECT-shaped
statement. Layers 3+ (auto-LIMIT, timeouts, byte caps, allow/deny lists, PII
masking) are applied around every query regardless of engine.

Writes are not gated — they are absent: there is no code path that submits a
non-SELECT statement to a SQL engine.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from retinue.datasources.base import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_TIMEOUT_S,
    HARD_ROW_LIMIT,
    DataSourceError,
    QueryResult,
)

# statement roots we accept: plain SELECT, UNION of selects, CTEs over selects
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


@dataclass(slots=True)
class SourcePolicy:
    """Per-source limits and filters (§30.3.3-5), stored in data_sources.policy."""

    max_rows: int = DEFAULT_ROW_LIMIT
    timeout_s: float = DEFAULT_TIMEOUT_S
    allow_tables: list[str] = field(default_factory=list)  # empty = all
    deny_tables: list[str] = field(default_factory=list)
    mask_patterns: list[str] = field(default_factory=list)  # regexes over string cells

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "SourcePolicy":
        raw = raw or {}
        return cls(
            max_rows=min(int(raw.get("max_rows", DEFAULT_ROW_LIMIT)), HARD_ROW_LIMIT),
            timeout_s=min(float(raw.get("timeout_s", DEFAULT_TIMEOUT_S)), 60.0),
            allow_tables=[str(t).lower() for t in raw.get("allow_tables", [])],
            deny_tables=[str(t).lower() for t in raw.get("deny_tables", [])],
            mask_patterns=[str(p) for p in raw.get("mask_patterns", [])],
        )


def _table_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for table in tree.find_all(exp.Table):
        if table.name:
            names.add(table.name.lower())
    return names


def _assert_tables_allowed(tree: exp.Expression, policy: SourcePolicy) -> None:
    tables = _table_names(tree)
    for name in tables:
        if name in policy.deny_tables:
            raise DataSourceError(f"table {name!r} is denied by this source's policy")
        if policy.allow_tables and name not in policy.allow_tables:
            raise DataSourceError(
                f"table {name!r} is outside this source's allowlist "
                f"({', '.join(sorted(policy.allow_tables))})"
            )


def validate_select(statement: str, dialect: str | None, policy: SourcePolicy) -> exp.Expression:
    """Parse and reject anything that is not exactly one read statement."""
    try:
        trees = sqlglot.parse(statement, read=dialect)
    except sqlglot.errors.ParseError as error:
        raise DataSourceError(f"SQL parse error: {str(error).splitlines()[0][:300]}") from error
    real_trees = [t for t in trees if t is not None]
    if len(real_trees) != 1:
        raise DataSourceError("exactly one statement is allowed (no multi-statement batches)")
    tree = real_trees[0]
    if not isinstance(tree, _READ_ROOTS):
        raise DataSourceError(
            f"only SELECT queries are allowed on data sources "
            f"(got {tree.key.upper()}); this layer is read-only by design"
        )
    # a read root can still smuggle writes: SELECT ... INTO, and writable CTEs
    # (WITH t AS (DELETE ... RETURNING *) SELECT ...) parse with a Select root
    # on Postgres-family dialects. Reject DML anywhere in the tree so the
    # guarantee never depends on a best-effort session flag.
    if next(iter(tree.find_all(exp.Into)), None) is not None:
        raise DataSourceError("SELECT INTO is not allowed (read-only)")
    for node_type in (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop):
        if next(iter(tree.find_all(node_type)), None) is not None:
            raise DataSourceError(
                f"{node_type.__name__.upper()} inside a query is not allowed (read-only)"
            )
    _assert_tables_allowed(tree, policy)
    return tree


def prepare_statement(
    statement: str, dialect: str | None, policy: SourcePolicy, limit: int | None
) -> tuple[str, int]:
    """Validate + inject/clamp LIMIT; returns (final_sql, effective_limit)."""
    effective = min(limit or policy.max_rows, policy.max_rows)
    tree = validate_select(statement, dialect, policy)

    inner = tree.this if isinstance(tree, exp.Union | exp.Intersect | exp.Except) else tree
    existing = tree.args.get("limit") or (
        inner.args.get("limit") if isinstance(inner, exp.Select) else None
    )
    if existing is not None and isinstance(existing, exp.Limit):
        try:
            declared = int(existing.expression.name)
        except (TypeError, ValueError):
            declared = effective
        effective = min(declared, effective)
    limited = tree.limit(effective)  # type: ignore[attr-defined]  # all read roots carry it
    return str(limited.sql(dialect=dialect)), effective


def quote_identifier(name: str, dialect: str | None) -> str:
    """Safe identifier for `sample(table)` — never string-interpolated raw."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", name):
        raise DataSourceError(f"invalid table name {name!r}")
    return exp.to_table(name).sql(dialect=dialect)


def transpile(statement: str, from_dialect: str, to_dialect: str) -> str:
    """Optional dialect translation (§30.5): PG-flavored SQL onto any engine."""
    try:
        out = sqlglot.transpile(statement, read=from_dialect, write=to_dialect)
    except sqlglot.errors.SqlglotError as error:
        raise DataSourceError(f"cannot transpile: {str(error).splitlines()[0][:300]}") from error
    if len(out) != 1:
        raise DataSourceError("exactly one statement is allowed")
    return out[0]


def apply_masking(result: QueryResult, policy: SourcePolicy) -> QueryResult:
    """Regex PII masking over string cells before anything reaches the model."""
    if not policy.mask_patterns:
        return result
    patterns = [re.compile(p) for p in policy.mask_patterns]
    for row in result.rows:
        for i, cell in enumerate(row):
            if isinstance(cell, str):
                for pattern in patterns:
                    cell = pattern.sub("•••", cell)
                row[i] = cell
    return result
