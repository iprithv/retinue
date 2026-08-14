"""Cache-stable context assembly (§31.1).

Segment order: [1] system prompt → [2] tool schemas (none in v0.1) →
[3] pinned blocks → [4] memory → [5] priors/graph → [6] RAG → [7] history →
[8] current turn. Segments 1-3 are byte-identical across every request of a
configuration, so provider prefix caching hits. History is trimmed oldest-first
only (suffix-safe), and the whole function is deterministic: same inputs →
identical bytes (CI-enforced).
"""

from dataclasses import dataclass, field
from typing import Any

import orjson

from retinue.config import ContextSettings
from retinue.core.context_budget import BudgetItem, allocate
from retinue.core.tokens import TokenCounter
from retinue.providers.base import ModelInfo


@dataclass(slots=True)
class HistoryEntry:
    role: str  # user|assistant|system
    text: str


@dataclass(slots=True)
class AssembledContext:
    messages: list[dict[str, Any]]
    max_output: int
    breakdown: dict[str, int] = field(default_factory=dict)
    input_token_estimate: int = 0


def canonical_bytes(value: Any) -> bytes:
    """Canonical serialization for cache-stable segments and determinism tests."""
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def _fractions(cfg: ContextSettings) -> dict[str, float]:
    return {
        "history": cfg.history_frac,
        "rag": cfg.rag_frac,
        "graph": cfg.graph_frac,
        "priors": cfg.priors_frac,
        "schema_cards": cfg.schema_cards_frac,
        "memory": cfg.memory_frac,
        "slack": cfg.slack_frac,
    }


def assemble_context(
    *,
    system_prompt: str | None,
    history: list[HistoryEntry],
    model_info: ModelInfo,
    requested_max_output: int | None,
    counter: TokenCounter,
    context_cfg: ContextSettings,
) -> AssembledContext:
    """history is chronological and ends with the current user turn."""
    max_output = min(
        requested_max_output or context_cfg.default_max_output,
        model_info.max_output_tokens,
    )
    # output shares the window with input on most providers: never reserve more
    # than half the window for generation (floor 256) so input always fits
    max_output = min(max_output, max(256, model_info.context_window // 2))

    # segments 1-3: immutable per configuration; they are the reserve
    reserved = counter.count_message("system", system_prompt) if system_prompt else 0

    # allocator sees newest-first so the kept prefix = most recent turns
    newest_first = list(reversed(history))
    items = [
        BudgetItem(tokens=counter.count_message(entry.role, entry.text), payload=entry)
        for entry in newest_first
    ]

    alloc = allocate(
        context_window=model_info.context_window,
        max_output=max_output,
        reserved=reserved,
        sources={"history": items},
        fractions=_fractions(context_cfg),
        min_keep={"history": context_cfg.min_history_messages},
    )

    kept_chronological = [item.payload for item in reversed(alloc.kept["history"])]
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend({"role": e.role, "content": e.text} for e in kept_chronological)

    breakdown = {"system": reserved, "max_output": max_output, **alloc.breakdown()}
    return AssembledContext(
        messages=messages,
        max_output=max_output,
        breakdown=breakdown,
        input_token_estimate=reserved + alloc.spent.get("history", 0),
    )
