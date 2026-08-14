"""LiteLLM-as-a-library adapter (D12): 100+ providers, zero extra processes.

litellm is imported lazily — it is the heaviest import in the dependency tree
and must not tax CLI startup (§20 lazy-imports rule).
"""

from collections.abc import AsyncIterator
from typing import Any

import structlog

from retinue.providers.base import ChatCall, NormalizedEvent, ProviderError, Usage

log = structlog.get_logger("retinue.providers.litellm")

# providers on the OpenAI wire format that need explicit opt-in to stream usage
_STREAM_USAGE_PROVIDERS = {"openai", "openrouter", "deepseek", "groq", "xai"}

_STOP_REASONS = {"stop": "end", "length": "length", "content_filter": "filtered"}


def _lazy_litellm() -> Any:
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True  # drop params a provider doesn't support instead of erroring
    return litellm


def _map_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    litellm = _lazy_litellm()
    status = getattr(exc, "status_code", None)
    code = "provider_error"
    retryable = False
    if isinstance(exc, getattr(litellm, "AuthenticationError", ())):
        code = "provider_auth"
    elif isinstance(exc, getattr(litellm, "RateLimitError", ())):
        code, retryable = "provider_rate_limited", True
    elif isinstance(
        exc,
        (
            getattr(litellm, "APIConnectionError", ()),
            getattr(litellm, "Timeout", ()),
            getattr(litellm, "InternalServerError", ()),
            getattr(litellm, "ServiceUnavailableError", ()),
        ),
    ):
        retryable = True
    elif isinstance(status, int) and status in (408, 429, 500, 502, 503, 504):
        retryable = True
    message = str(exc).split("\n", 1)[0][:500]
    return ProviderError(message, code=code, retryable=retryable, status=status)


def _usage_from(raw: Any) -> Usage:
    cached = 0
    details = getattr(raw, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None) or 0
    return Usage(
        input_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        output_tokens=getattr(raw, "completion_tokens", 0) or 0,
        cached_tokens=cached,
    )


class LiteLLMAdapter:
    name = "litellm"

    def _kwargs(self, call: ChatCall) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": call.model, "messages": call.messages, **call.params}
        if call.api_key:
            kwargs["api_key"] = call.api_key
        if call.api_base:
            kwargs["api_base"] = call.api_base
        return kwargs

    async def stream(self, call: ChatCall) -> AsyncIterator[NormalizedEvent]:
        litellm = _lazy_litellm()
        kwargs = self._kwargs(call)
        kwargs["stream"] = True
        provider = call.model.split("/", 1)[0] if "/" in call.model else "openai"
        if provider in _STREAM_USAGE_PROVIDERS:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                raw_usage = getattr(chunk, "usage", None)
                if raw_usage is not None:
                    yield NormalizedEvent(kind="usage", usage=_usage_from(raw_usage))
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    thinking = getattr(delta, "reasoning_content", None)
                    if thinking:
                        yield NormalizedEvent(kind="thinking_delta", text=thinking)
                    content = getattr(delta, "content", None)
                    if content:
                        yield NormalizedEvent(kind="text_delta", text=content)
                finish = getattr(choice, "finish_reason", None)
                if finish:
                    yield NormalizedEvent(kind="stop", stop_reason=_STOP_REASONS.get(finish, "end"))
        except Exception as exc:  # normalize every provider failure at the seam
            raise _map_error(exc) from exc

    async def complete(self, call: ChatCall) -> str:
        litellm = _lazy_litellm()
        try:
            response = await litellm.acompletion(**self._kwargs(call))
            content = response.choices[0].message.content
            return content if isinstance(content, str) else ""
        except Exception as exc:
            raise _map_error(exc) from exc
