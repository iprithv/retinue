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


def _fit_block(text: str, cap: int, counter: TokenCounter) -> tuple[str, int] | None:
    """Fit a context block into `cap` tokens, truncating deterministically.
    Returns None when the cap is too small to carry anything useful."""
    if cap < 32:
        return None
    tokens = counter.count(text)
    if tokens <= cap:
        return text, tokens
    # ~4 chars/token heuristic, then measure once more; deterministic either way
    truncated = text[: cap * 4] + "\n…(truncated)"
    return truncated, counter.count(truncated)


def assemble_context(
    *,
    system_prompt: str | None,
    history: list[HistoryEntry],
    model_info: ModelInfo,
    requested_max_output: int | None,
    counter: TokenCounter,
    context_cfg: ContextSettings,
    memory_block: str | None = None,
    rag_block: str | None = None,
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
    sources: dict[str, list[BudgetItem]] = {"history": items}
    if rag_block:
        sources["rag"] = [BudgetItem(tokens=counter.count(rag_block), payload=rag_block)]
    if memory_block:
        sources["memory"] = [BudgetItem(tokens=counter.count(memory_block), payload=memory_block)]

    alloc = allocate(
        context_window=model_info.context_window,
        max_output=max_output,
        reserved=reserved,
        sources=sources,
        fractions=_fractions(context_cfg),
        min_keep={"history": context_cfg.min_history_messages},
    )

    # blocks that missed their cap outright still get a truncated seat if the
    # cap allows (§31.2: overflow is deterministic, never a provider 400)
    context_blocks: list[str] = []
    for tier, block in (("memory", memory_block), ("rag", rag_block)):
        if not block:
            continue
        if alloc.kept.get(tier):
            context_blocks.append(str(alloc.kept[tier][0].payload))
        else:
            fitted = _fit_block(block, alloc.caps.get(tier, 0), counter)
            if fitted is not None:
                context_blocks.append(fitted[0])
                alloc.spent[tier] = fitted[1]

    kept_chronological = [item.payload for item in reversed(alloc.kept["history"])]
    messages: list[dict[str, Any]] = []
    system_parts = [p for p in [system_prompt, *context_blocks] if p]
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.extend({"role": e.role, "content": e.text} for e in kept_chronological)

    breakdown = {"system": reserved, "max_output": max_output, **alloc.breakdown()}
    return AssembledContext(
        messages=messages,
        max_output=max_output,
        breakdown=breakdown,
        input_token_estimate=reserved + sum(alloc.spent.values()),
    )
