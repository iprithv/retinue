"""Deterministic in-process provider for development and tests.

Enabled via `RETINUE_MODELS__MOCK_ENABLED=1`. Never registered otherwise.
Variants: echo (fast), slow (streams ~8 tok/s for resume/stop testing),
fail-mid (errors after a few tokens), fail-auth (errors before first token).
"""

import asyncio
import re
from collections.abc import AsyncIterator

from retinue.providers.base import (
    ChatCall,
    ModelInfo,
    NormalizedEvent,
    ProviderError,
    ToolUse,
    Usage,
)

MOCK_MODELS = [
    ModelInfo(id="mock/echo", provider="mock", display_name="Mock Echo"),
    ModelInfo(id="mock/slow", provider="mock", display_name="Mock Slow"),
    ModelInfo(id="mock/fail-mid", provider="mock", display_name="Mock Fail Mid-stream"),
    ModelInfo(id="mock/fail-auth", provider="mock", display_name="Mock Fail Auth"),
    ModelInfo(
        id="mock/tool", provider="mock", display_name="Mock Tool Caller", supports_tools=True
    ),
    ModelInfo(id="mock/vision", provider="mock", display_name="Mock Vision", supports_vision=True),
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

        if variant == "tool":
            async for event in self._stream_tool(call):
                yield event
            return

        if variant == "vision":
            last = next((m for m in reversed(call.messages) if m.get("role") == "user"), {})
            content = last.get("content")
            images = 0
            text = ""
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "image_url":
                        images += 1
                    elif part.get("type") == "text":
                        text += str(part.get("text", ""))
            else:
                text = str(content or "")
            reply = f"I can see {images} image(s). You said: {text}"
            for chunk in re.findall(r"\S+\s*", reply):
                yield NormalizedEvent(kind="text_delta", text=chunk)
                await asyncio.sleep(0.002)
            yield NormalizedEvent(kind="usage", usage=Usage(input_tokens=30, output_tokens=12))
            yield NormalizedEvent(kind="stop", stop_reason="end")
            return

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

    async def _stream_tool(self, call: ChatCall) -> AsyncIterator[NormalizedEvent]:
        """Deterministic tool-calling model for tests and keyless dev.

        Turn 1 (no tool result in context yet): calls the tool named in the
        user text as `use:<tool>` (default: first tool), with args parsed from
        a JSON object embedded in the user text (default {}).
        Turn 2 (tool result present): streams a summary quoting the result.
        """
        import orjson

        last_tool_result = next(
            (m for m in reversed(call.messages) if m.get("role") == "tool"), None
        )
        if last_tool_result is not None or not call.tools:
            content = str(last_tool_result.get("content", "")) if last_tool_result else ""
            reply = f"Tool result received: {content[:400] or '(empty)'}"
            for chunk in re.findall(r"\S+\s*", reply):
                yield NormalizedEvent(kind="text_delta", text=chunk)
                await asyncio.sleep(0.002)
            yield NormalizedEvent(kind="usage", usage=Usage(input_tokens=20, output_tokens=10))
            yield NormalizedEvent(kind="stop", stop_reason="end")
            return

        user_text = _last_user_text(call)
        wanted = None
        for token in user_text.split():
            if token.startswith("use:"):
                wanted = token[4:]
        names = [t["function"]["name"] for t in call.tools]
        name = wanted if wanted and wanted in names else str(names[0])
        args: dict[str, object] = {}
        start, end = user_text.find("{"), user_text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = orjson.loads(user_text[start : end + 1])
                if isinstance(parsed, dict):
                    args = parsed
            except orjson.JSONDecodeError:
                pass
        yield NormalizedEvent(kind="text_delta", text="Let me check that. ")
        yield NormalizedEvent(
            kind="tool_use",
            tool_use=ToolUse(call_id=f"mockcall_{name}", name=name, args=args),
        )
        yield NormalizedEvent(kind="stop", stop_reason="tool_use")

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
