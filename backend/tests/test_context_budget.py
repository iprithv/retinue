"""§31.2 allocator + §31.1 cache-stable assembly determinism (CI-enforced)."""

import pytest

from retinue.config import ContextSettings
from retinue.core.assembly import HistoryEntry, assemble_context, canonical_bytes
from retinue.core.context_budget import BudgetItem, allocate
from retinue.core.errors import AppError
from retinue.core.tokens import TokenCounter
from retinue.providers.base import ModelInfo

FRACTIONS = {
    "history": 0.45,
    "rag": 0.20,
    "graph": 0.10,
    "priors": 0.08,
    "schema_cards": 0.08,
    "memory": 0.04,
    "slack": 0.05,
}


def items(*token_counts: int) -> list[BudgetItem]:
    return [BudgetItem(tokens=t, payload=i) for i, t in enumerate(token_counts)]


class TestAllocator:
    def test_within_cap_keeps_all(self):
        alloc = allocate(
            context_window=10_000,
            max_output=1_000,
            reserved=500,
            sources={"history": items(100, 100, 100)},
            fractions=FRACTIONS,
        )
        assert len(alloc.kept["history"]) == 3
        assert alloc.spent["history"] == 300
        assert alloc.available == 8_500

    def test_trims_to_cap_prefix_order(self):
        # cap = 45% of 1000 = 450 -> keeps first two 200-token items only
        alloc = allocate(
            context_window=1_500,
            max_output=400,
            reserved=100,
            sources={"history": items(200, 200, 200, 200)},
            fractions=FRACTIONS,
        )
        assert [i.payload for i in alloc.kept["history"]] == [0, 1]

    def test_unused_allocation_cascades(self):
        # history uses nothing -> its 45% cascades into rag's budget
        alloc = allocate(
            context_window=1_100,
            max_output=0,
            reserved=100,
            sources={"history": [], "rag": items(500, 100)},
            fractions=FRACTIONS,
        )
        # rag cap alone = 200; with 450 carry = 650 -> both kept
        assert len(alloc.kept["rag"]) == 2

    def test_min_keep_forces_current_exchange(self):
        alloc = allocate(
            context_window=1_000,
            max_output=500,
            reserved=400,  # available = 100, cap = 45
            sources={"history": items(80, 80)},
            fractions=FRACTIONS,
            min_keep={"history": 1},
        )
        assert len(alloc.kept["history"]) == 1  # forced past its cap

    def test_impossible_window_raises(self):
        with pytest.raises(AppError) as excinfo:
            allocate(
                context_window=1_000,
                max_output=900,
                reserved=200,
                sources={},
                fractions=FRACTIONS,
            )
        assert excinfo.value.code == "context_overflow"

    def test_deterministic(self):
        kwargs = {
            "context_window": 5_000,
            "max_output": 1_000,
            "reserved": 200,
            "sources": {"history": items(10, 400, 300, 900, 50)},
            "fractions": FRACTIONS,
        }
        first = allocate(**kwargs)
        second = allocate(**kwargs)
        assert first.spent == second.spent
        assert [i.payload for i in first.kept["history"]] == [
            i.payload for i in second.kept["history"]
        ]


class TestAssembly:
    def setup_method(self):
        self.counter = TokenCounter(force_fallback=True)  # deterministic, offline
        self.cfg = ContextSettings()
        self.model = ModelInfo(
            id="mock/echo", provider="mock", display_name="e", context_window=4_000
        )

    def history(self, n: int = 4) -> list[HistoryEntry]:
        entries = []
        for i in range(n):
            entries.append(HistoryEntry(role="user", text=f"question {i} " + "x" * 40))
            entries.append(HistoryEntry(role="assistant", text=f"answer {i} " + "y" * 40))
        entries.append(HistoryEntry(role="user", text="current question"))
        return entries

    def test_deterministic_bytes(self):
        """§31.5: same inputs -> byte-identical assembled context."""
        kwargs = {
            "system_prompt": "You are Retinue.",
            "history": self.history(),
            "model_info": self.model,
            "requested_max_output": None,
            "counter": self.counter,
            "context_cfg": self.cfg,
        }
        first = assemble_context(**kwargs)
        second = assemble_context(**kwargs)
        assert canonical_bytes(first.messages) == canonical_bytes(second.messages)

    def test_system_prompt_leads(self):
        result = assemble_context(
            system_prompt="You are Retinue.",
            history=self.history(1),
            model_info=self.model,
            requested_max_output=None,
            counter=self.counter,
            context_cfg=self.cfg,
        )
        assert result.messages[0] == {"role": "system", "content": "You are Retinue."}
        assert result.messages[-1]["content"] == "current question"

    def test_trims_oldest_first(self):
        tiny_model = ModelInfo(
            id="mock/echo",
            provider="mock",
            display_name="e",
            context_window=300,
            max_output_tokens=100,
        )
        result = assemble_context(
            system_prompt=None,
            history=self.history(10),
            model_info=tiny_model,
            requested_max_output=64,
            counter=self.counter,
            context_cfg=self.cfg,
        )
        contents = [m["content"] for m in result.messages]
        assert contents[-1] == "current question"  # newest always survives
        assert "question 0" not in " ".join(contents)  # oldest trimmed
        assert result.input_token_estimate <= 300 - 64

    def test_max_output_clamped_to_model(self):
        result = assemble_context(
            system_prompt=None,
            history=self.history(1),
            model_info=ModelInfo(
                id="m/x",
                provider="m",
                display_name="x",
                context_window=8_000,
                max_output_tokens=512,
            ),
            requested_max_output=99_999,
            counter=self.counter,
            context_cfg=self.cfg,
        )
        assert result.max_output == 512
