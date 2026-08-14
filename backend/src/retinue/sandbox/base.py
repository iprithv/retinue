"""ExecutionBackend protocol (§12, D16). The model-visible tool contract is
identical across backends; capability differences surface in `describe()`."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class ExecLimits:
    wall_s: float = 5.0
    memory_mb: int = 256
    output_cap: int = 64_000  # chars of stdout+stderr kept


@dataclass(slots=True)
class ExecResult:
    status: str  # ok|error|timeout|unavailable
    stdout: str = ""
    stderr: str = ""
    artifacts: list[dict[str, str]] = field(default_factory=list)


class ExecutionBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def describe(self) -> str:
        """One-line capability statement shown to users and the model."""
        ...

    async def run(self, code: str, lang: str, limits: ExecLimits) -> ExecResult: ...
