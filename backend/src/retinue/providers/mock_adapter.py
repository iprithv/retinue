"""Deterministic in-process provider for development and tests.

Enabled via `RETINUE_MODELS__MOCK_ENABLED=1`. Never registered otherwise.
Variants: echo (fast), slow (streams ~8 tok/s for resume/stop testing),
fail-mid (errors after a few tokens), fail-auth (errors before first token).
"""

import asyncio
import re
from collections.abc import AsyncIterator

from retinue.providers.base import ChatCall, ModelInfo, NormalizedEvent, ProviderError, Usage

MOCK_MODELS = [
    ModelInfo(id="mock/echo", provider="mock", display_name="Mock Echo"),
    ModelInfo(id="mock/slow", provider="mock", display_name="Mock Slow"),
    ModelInfo(id="mock/fail-mid", provider="mock", display_name="Mock Fail Mid-stream"),
    ModelInfo(id="mock/fail-auth", provider="mock", display_name="Mock Fail Auth"),
]

_REPLY_TEMPLATE = """You said: {user}

A little **markdown** to exercise the renderer:

```python
print("hello from retinue")
```

- streamed over SSE
- zero buffering end to end
"""


def _last_user_text(call: ChatCall) -> str:
    for message in reversed(call.messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            return content if isinstance(content, str) else ""
    return ""


class MockAdapter:
    name = "mock"

    def stream(self, call: ChatCall) -> AsyncIterator[NormalizedEvent]:
        return self._stream(call)

    async def _stream(self, call: ChatCall) -> AsyncIterator[NormalizedEvent]:
        variant = call.model.split("/", 1)[1] if "/" in call.model else "echo"
        if variant == "fail-auth":
            raise ProviderError("mock: invalid API key", code="provider_auth", retryable=False)

        reply = _REPLY_TEMPLATE.format(user=_last_user_text(call) or "(nothing)")
        chunks = re.findall(r"\S+\s*", reply)
        delay = 0.12 if variant == "slow" else 0.004
        for i, chunk in enumerate(chunks):
            if variant == "fail-mid" and i == 6:
                raise ProviderError("mock: provider dropped mid-stream", retryable=True)
            yield NormalizedEvent(kind="text_delta", text=chunk)
            await asyncio.sleep(delay)
        input_tokens = sum(len(str(m.get("content", ""))) // 4 + 4 for m in call.messages)
        yield NormalizedEvent(
            kind="usage",
            usage=Usage(input_tokens=input_tokens, output_tokens=len(chunks)),
        )
        yield NormalizedEvent(kind="stop", stop_reason="end")

    async def complete(self, call: ChatCall) -> str:
        if "fail" in call.model:
            raise ProviderError("mock: completion failed", retryable=False)
        text = _last_user_text(call)
        # housekeeping prompts embed the conversation as "User: ..." — answer
        # from that content, not the instructions around it
        if "\nUser: " in text:
            text = text.split("\nUser: ", 1)[1].split("\n\nAssistant:", 1)[0]
        words = " ".join(text.split()[:4]).strip()
        return words or "Mock chat"
