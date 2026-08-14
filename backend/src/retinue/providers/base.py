"""ProviderAdapter protocol (§8) — the seam that makes LiteLLM replaceable.

Adapters normalize every provider's wire format into NormalizedEvent. The chat
engine, agents, and UI never see provider-specific shapes.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

EventKind = Literal["text_delta", "thinking_delta", "usage", "stop"]


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(slots=True)
class NormalizedEvent:
    kind: EventKind
    text: str = ""
    usage: Usage | None = None
    stop_reason: str | None = None  # end|length|filtered


@dataclass(slots=True)
class ModelInfo:
    id: str  # "provider/model", e.g. "anthropic/claude-sonnet-4-5"
    provider: str
    display_name: str
    context_window: int = 128_000
    max_output_tokens: int = 4096
    supports_vision: bool = False
    supports_tools: bool = False
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None


@dataclass(slots=True)
class ChatCall:
    model: str
    messages: list[dict[str, Any]]
    params: dict[str, Any] = field(default_factory=dict)
    api_key: str | None = None
    api_base: str | None = None


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.status = status


class ProviderAdapter(Protocol):
    name: str

    def stream(self, call: ChatCall) -> AsyncIterator[NormalizedEvent]:
        """Async generator of normalized events. Raises ProviderError."""
        ...

    async def complete(self, call: ChatCall) -> str:
        """Non-streaming completion for housekeeping tasks (titles etc, §31.3)."""
        ...
