"""Context budget allocator (§31.2) — deterministic, never a provider 400.

available = context_window - max_output - reserved(system + tools).
The remainder is split by tiered caps; each source trims to its cap in its own
relevance order; unused allocation cascades down the tier list. Trimming is
suffix-safe: it never touches the cache-stable prefix segments (§31.1).
"""

from dataclasses import dataclass, field
from typing import Any

from retinue.core.errors import CONTEXT_OVERFLOW, AppError

# tier order is normative (§31.2)
TIER_ORDER = ("history", "rag", "graph", "priors", "schema_cards", "memory", "slack")


@dataclass(slots=True)
class BudgetItem:
    """One allocatable unit (a message, a chunk, a card). Items arrive ordered
    by relevance, most important first; the allocator keeps a prefix."""

    tokens: int
    payload: Any = None


@dataclass(slots=True)
class Allocation:
    available: int
    kept: dict[str, list[BudgetItem]] = field(default_factory=dict)
    spent: dict[str, int] = field(default_factory=dict)
    caps: dict[str, int] = field(default_factory=dict)

    def breakdown(self) -> dict[str, int]:
        return {"available": self.available, **self.spent}


def allocate(
    *,
    context_window: int,
    max_output: int,
    reserved: int,
    sources: dict[str, list[BudgetItem]],
    fractions: dict[str, float],
    min_keep: dict[str, int] | None = None,
) -> Allocation:
    """Deterministic tiered allocation.

    min_keep forces at least N leading items of a source (e.g. the current
    exchange) even when the cap says otherwise — overflow then raises instead
    of silently dropping the current turn.
    """
    available = context_window - max_output - reserved
    if available <= 0:
        raise AppError(
            CONTEXT_OVERFLOW,
            "system prompt and reserved output exceed the model context window",
            status=400,
            details={
                "context_window": context_window,
                "max_output": max_output,
                "reserved": reserved,
            },
        )

    min_keep = min_keep or {}
    alloc = Allocation(available=available)
    carry = 0
    for tier in TIER_ORDER:
        cap = int(available * fractions.get(tier, 0.0)) + carry
        alloc.caps[tier] = cap
        items = sources.get(tier, [])
        kept: list[BudgetItem] = []
        spent = 0
        forced = min_keep.get(tier, 0)
        for i, item in enumerate(items):
            if i < forced or spent + item.tokens <= cap:
                kept.append(item)
                spent += item.tokens
            else:
                break  # relevance-ordered prefix; first miss ends the tier
        alloc.kept[tier] = kept
        alloc.spent[tier] = spent
        carry = max(0, cap - spent)

    total = sum(alloc.spent.values())
    if total > available:
        raise AppError(
            CONTEXT_OVERFLOW,
            "context does not fit the model window even after trimming",
            status=400,
            details={"needed": total, "available": available},
        )
    return alloc
