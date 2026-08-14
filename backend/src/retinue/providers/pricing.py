"""Cost accounting (§8): LiteLLM's in-package price table (versioned by pin)
plus an operator override file at {data_dir}/pricing_overrides.json:
    {"anthropic/claude-sonnet-4-5": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}}
"""

from pathlib import Path
from typing import Any

import orjson
import structlog

from retinue.providers.base import Usage

log = structlog.get_logger("retinue.pricing")


class PricingTable:
    def __init__(self, override_path: Path | None = None) -> None:
        self._overrides: dict[str, dict[str, float]] = {}
        if override_path and override_path.is_file():
            try:
                self._overrides = orjson.loads(override_path.read_bytes())
            except Exception as exc:
                log.warning("pricing_overrides_invalid", path=str(override_path), error=str(exc))

    def _litellm_entry(self, model: str) -> dict[str, Any] | None:
        try:
            import litellm

            cost_map = litellm.model_cost
        except Exception:
            return None
        bare = model.split("/", 1)[1] if "/" in model else model
        for key in (model, bare):
            entry = cost_map.get(key)
            if entry:
                return dict(entry)
        return None

    def cost_usd(self, model: str, usage: Usage) -> float | None:
        bare = model.split("/", 1)[1] if "/" in model else model
        override = self._overrides.get(model) or self._overrides.get(bare)
        if override:
            input_rate = float(override.get("input_per_mtok", 0.0)) / 1_000_000
            output_rate = float(override.get("output_per_mtok", 0.0)) / 1_000_000
            cached_rate = float(override.get("cached_per_mtok", 0.0)) / 1_000_000
            return round(
                (usage.input_tokens - usage.cached_tokens) * input_rate
                + usage.cached_tokens * (cached_rate or input_rate)
                + usage.output_tokens * output_rate,
                8,
            )

        entry = self._litellm_entry(model)
        if not entry:
            return None
        input_rate = entry.get("input_cost_per_token") or 0.0
        output_rate = entry.get("output_cost_per_token") or 0.0
        cached_rate = entry.get("cache_read_input_token_cost") or input_rate
        return round(
            (usage.input_tokens - usage.cached_tokens) * input_rate
            + usage.cached_tokens * cached_rate
            + usage.output_tokens * output_rate,
            8,
        )
