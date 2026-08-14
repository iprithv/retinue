"""Chat engine — the zero-buffer relay (§7.4, normative).

Rules enforced here:
1. no `await db` between a provider chunk and its SSE publish (WriteBehind
   feeds are synchronous appends; flushing happens on its own task),
2. heartbeats are per-subscriber (in the API layer),
3. stop/abort cancels the provider request and persists the partial as
   status='stopped',
4. provider error mid-stream emits `error {retryable}` and keeps the partial,
5. usage + message_end publish after the final DB flush so a client that
   refetches on message_end always sees consistent rows.
"""

import asyncio
import time
import uuid
from typing import Any

import orjson
import structlog
from sqlalchemy import update

from retinue.config import Settings
from retinue.core.ids import uuid7
from retinue.core.sse import (
    APPROVAL_REQUIRED,
    BLOCK_START,
    CITATION,
    DELTA,
    ERROR,
    MESSAGE_END,
    MESSAGE_START,
    TOOL_CALL,
    TOOL_RESULT,
    USAGE,
)
from retinue.core.streams import LiveStream, StreamHub
from retinue.core.timeutil import now_ms
from retinue.core.tokens import TokenCounter
from retinue.db.models import Conversation, Message, MessagePart, UsageEvent
from retinue.db.session import Database
from retinue.jobs.queue import JobQueue
from retinue.providers.base import ChatCall, ProviderError, Usage, aclose_events
from retinue.providers.pricing import PricingTable
from retinue.providers.registry import ProviderRegistry, provider_of

log = structlog.get_logger("retinue.chat")


class _BlockState:
    __slots__ = ("content", "idx", "inserted", "row_id", "text", "type")

    def __init__(self, idx: int, block_type: str, content: dict[str, Any] | None = None) -> None:
        self.idx = idx
        self.type = block_type
        self.text = ""
        self.content = content  # JSON blocks (tool_call/tool_result/citation)
        self.row_id = uuid7()
        self.inserted = False


class WriteBehind:
    """Batches message-part persistence off the token path (§7.4).

    feed() is synchronous; a background task flushes dirty blocks every
    `interval_ms`. finalize() does the last flush plus the message/conversation
    row updates and the usage event, in one transaction.
    """

    def __init__(self, db: Database, message_id: uuid.UUID, interval_ms: int) -> None:
        self._db = db
        self._message_id = message_id
        self._interval_s = max(interval_ms, 10) / 1000
        self._blocks: dict[int, _BlockState] = {}
        self._dirty: set[int] = set()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(
            self._loop(), name=f"write-behind-{self._message_id.hex[:8]}"
        )

    def ensure_block(self, idx: int, block_type: str) -> None:
        if idx not in self._blocks:
            self._blocks[idx] = _BlockState(idx, block_type)
            self._dirty.add(idx)

    def add_json_block(self, block_type: str, content: dict[str, Any]) -> int:
        """Append a JSON part (tool_call/tool_result/citation) after existing blocks."""
        idx = max(self._blocks) + 1 if self._blocks else 0
        self._blocks[idx] = _BlockState(idx, block_type, content=content)
        self._dirty.add(idx)
        return idx

    def set_json_content(self, idx: int, content: dict[str, Any]) -> None:
        self._blocks[idx].content = content
        self._dirty.add(idx)

    @property
    def next_block_idx(self) -> int:
        return max(self._blocks) + 1 if self._blocks else 0

    def feed(self, idx: int, text_delta: str) -> None:
        self._blocks[idx].text += text_delta
        self._dirty.add(idx)

    @property
    def full_text(self) -> str:
        return "".join(
            b.text for b in sorted(self._blocks.values(), key=lambda b: b.idx) if b.type == "text"
        )

    def blocks_snapshot(self) -> list[dict[str, Any]]:
        """Full in-memory state for ring-miss resume — JSON blocks included,
        so tool calls/results/citations survive a reconnect (§19)."""
        return [
            {"index": b.idx, "type": b.type, "text": b.text, "content": b.content}
            for b in sorted(self._blocks.values(), key=lambda b: b.idx)
        ]

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return
            except TimeoutError:
                try:
                    await self._flush()
                except Exception:
                    log.exception("write_behind_flush_failed", message_id=str(self._message_id))

    async def _flush(self) -> None:
        if not self._dirty:
            return
        dirty_ids = sorted(self._dirty)
        self._dirty.clear()
        blocks = [self._blocks[i] for i in dirty_ids]
        try:
            async with self._db.write_session() as session:
                for block in blocks:
                    await self._upsert(session, block)
        except BaseException:
            # the transaction rolled back: nothing was written — requeue so the
            # next flush (or finalize) retries instead of silently losing parts
            self._dirty.update(dirty_ids)
            raise
        # only after a successful commit do inserts become updates
        for block in blocks:
            block.inserted = True

    async def _upsert(self, session: Any, block: _BlockState) -> None:
        text_content = block.text if block.type == "text" else None
        content = block.content if block.content is not None else {"text": block.text}
        if not block.inserted:
            session.add(
                MessagePart(
                    id=block.row_id,
                    message_id=self._message_id,
                    idx=block.idx,
                    type=block.type,
                    content=content,
                    text_content=text_content,
                )
            )
        else:
            await session.execute(
                update(MessagePart)
                .where(MessagePart.id == block.row_id)
                .values(content=content, text_content=text_content)
            )

    async def finalize(
        self,
        *,
        status: str,
        model: str,
        error: dict[str, Any] | None,
        conversation_id: uuid.UUID,
        usage_event: UsageEvent | None,
    ) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        ts = now_ms()
        blocks = sorted(self._blocks.values(), key=lambda b: b.idx)
        async with self._db.write_session() as session:
            for block in blocks:
                await self._upsert(session, block)
            await session.execute(
                update(Message)
                .where(Message.id == self._message_id)
                .values(status=status, model=model, error=error)
            )
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=ts, last_message_at=ts)
            )
            if usage_event is not None:
                session.add(usage_event)
        for block in blocks:
            block.inserted = True


class ChatEngine:
    def __init__(
        self,
        *,
        db: Database,
        hub: StreamHub,
        registry: ProviderRegistry,
        pricing: PricingTable,
        jobs: JobQueue,
        settings: Settings,
        counter: TokenCounter,
    ) -> None:
        self.db = db
        self.hub = hub
        self.registry = registry
        self.pricing = pricing
        self.jobs = jobs
        self.settings = settings
        self.counter = counter

    def start_stream(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        client_message_id: uuid.UUID | None,
        call: ChatCall,
        agent_version: Any | None = None,
        citations: list[dict[str, Any]] | None = None,
        generate_title: bool = False,
        extract_memory: bool = False,
        tools: Any | None = None,  # agents.runtime.ToolExecutor
    ) -> LiveStream:
        async def producer(stream: LiveStream) -> None:
            await self._produce(
                stream,
                call=call,
                agent_version=agent_version,
                citations=citations or [],
                generate_title=generate_title,
                extract_memory=extract_memory,
                tools=tools,
            )

        return self.hub.start(
            message_id=assistant_message_id,
            client_message_id=client_message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            producer=producer,
        )

    async def _race_stop(
        self, stream: LiveStream, awaitable: Any, timeout_s: float
    ) -> tuple[bool, Any]:
        """Await `awaitable` unless the user stops the turn first.
        Returns (stopped, result); raises TimeoutError on timeout."""
        wait_task = asyncio.ensure_future(awaitable)
        stop_task = asyncio.ensure_future(stream.stop_requested.wait())
        try:
            done, _pending = await asyncio.wait(
                {wait_task, stop_task},
                timeout=timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wait_task in done:
                return False, wait_task.result()
            if stop_task in done:
                return True, None
            raise TimeoutError
        finally:
            for task in (wait_task, stop_task):
                if not task.done():
                    task.cancel()

    async def _run_tool_call(
        self,
        stream: LiveStream,
        wb: WriteBehind,
        tools: Any,
        tool_use: Any,
    ) -> tuple[str, str]:
        """One tool invocation: approval gate → dispatch under timeout, both
        interruptible by stop (§7.4 rule 3 extends to tool waits)."""
        hub = self.hub
        cfg = self.settings.tools
        mode = tools.mode(tool_use.name)

        if mode == "ask_user":
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            stream.pending_approvals[tool_use.call_id] = future
            hub.publish(
                stream,
                APPROVAL_REQUIRED,
                {"call_id": tool_use.call_id, "name": tool_use.name, "args": tool_use.args},
            )
            try:
                stopped, approved = await self._race_stop(
                    stream, asyncio.shield(future), cfg.approval_timeout_s
                )
            except TimeoutError:
                return "timeout", "the user did not respond to the approval request"
            finally:
                stream.pending_approvals.pop(tool_use.call_id, None)
                if not future.done():
                    future.cancel()
            if stopped:
                return "denied", "generation was stopped before the tool was approved"
            if not approved:
                return "denied", "the user denied this tool call"

        try:
            stopped, outcome = await self._race_stop(
                stream, tools.dispatch(tool_use.name, tool_use.args), cfg.per_tool_timeout_s
            )
        except TimeoutError:
            return "timeout", f"tool {tool_use.name!r} exceeded {cfg.per_tool_timeout_s}s"
        if stopped:
            return "denied", "generation was stopped while the tool was running"
        return outcome.status, outcome.content

    async def _produce(
        self,
        stream: LiveStream,
        *,
        call: ChatCall,
        agent_version: Any | None,
        citations: list[dict[str, Any]],
        generate_title: bool,
        extract_memory: bool,
        tools: Any | None,
    ) -> None:
        hub = self.hub
        wb = WriteBehind(self.db, stream.message_id, self.settings.stream.write_behind_ms)
        wb.start()

        start_payload = {
            "message_id": str(stream.message_id),
            "conversation_id": str(stream.conversation_id),
            "model": call.model,
            "agent_version_id": str(agent_version.id) if agent_version is not None else None,
        }
        hub.publish(stream, MESSAGE_START, start_payload)

        def snapshot() -> dict[str, Any]:
            # read by re-attaching subscribers whose Last-Event-ID fell off the
            # ring; same event loop, no await between read and use
            return {
                "start": start_payload,
                "last_event_id": stream.next_event_id,
                "blocks": wb.blocks_snapshot(),
            }

        stream.snapshot = snapshot

        t0 = time.monotonic()
        ttft_ms: int | None = None
        usage: Usage | None = None
        stop_reason: str | None = None
        status = "complete"
        error_payload: dict[str, Any] | None = None

        if tools is not None:
            try:
                await tools.prepare()
                call.tools = tools.tool_defs()
            except Exception:
                log.exception("tool_prepare_failed", message_id=str(stream.message_id))

        adapter = self.registry.adapter_for(call.model)
        messages = list(call.messages)
        cfg = self.settings.tools
        hop = 0
        # cumulative cap on tool results fed back to the provider (~50k tokens)
        tool_budget = 200_000
        tool_chars_used = 0
        tool_budget_hit = False
        try:
            while True:
                hop_call = ChatCall(
                    model=call.model,
                    messages=messages,
                    params=call.params,
                    api_key=call.api_key,
                    api_base=call.api_base,
                    tools=call.tools,
                )
                tool_uses: list[Any] = []
                hop_text: list[str] = []
                block_idx = -1
                block_type: str | None = None
                agen = adapter.stream(hop_call)
                try:
                    async for event in agen:
                        if stream.stop_requested.is_set():
                            status = "stopped"
                            stop_reason = "stopped"
                            break
                        if event.kind in ("text_delta", "thinking_delta"):
                            btype = "text" if event.kind == "text_delta" else "thinking"
                            if block_type != btype:
                                block_idx = wb.next_block_idx
                                block_type = btype
                                wb.ensure_block(block_idx, btype)
                                hub.publish(
                                    stream, BLOCK_START, {"index": block_idx, "type": btype}
                                )
                            if ttft_ms is None:
                                ttft_ms = int((time.monotonic() - t0) * 1000)
                            wb.feed(block_idx, event.text)
                            if btype == "text":
                                hop_text.append(event.text)
                            hub.publish(stream, DELTA, {"index": block_idx, "text": event.text})
                        elif event.kind == "tool_use" and event.tool_use is not None:
                            tool_uses.append(event.tool_use)
                        elif event.kind == "usage" and event.usage is not None:
                            if usage is None:
                                usage = event.usage
                            else:  # accumulate across hops
                                usage = Usage(
                                    input_tokens=usage.input_tokens + event.usage.input_tokens,
                                    output_tokens=usage.output_tokens + event.usage.output_tokens,
                                    cached_tokens=usage.cached_tokens + event.usage.cached_tokens,
                                )
                        elif event.kind == "stop":
                            stop_reason = event.stop_reason or "end"
                finally:
                    await aclose_events(agen)  # cancels the provider request on early exit

                if status != "complete" or not tool_uses or tools is None:
                    break
                hop += 1
                if hop >= cfg.max_iterations:
                    stop_reason = "tool_limit"
                    break
                if time.monotonic() - t0 > cfg.wall_clock_s:
                    stop_reason = "tool_limit"
                    break

                # some OpenAI-compatible backends emit colliding call ids in a
                # single hop; de-duplicate so approval futures and tool_call_id
                # pairing stay 1:1
                seen_ids: set[str] = set()
                for tu in tool_uses:
                    while tu.call_id in seen_ids:
                        tu.call_id = f"{tu.call_id}_2"
                    seen_ids.add(tu.call_id)

                # record the assistant hop for the next provider call
                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(hop_text) or None,
                        "tool_calls": [
                            {
                                "id": tu.call_id,
                                "type": "function",
                                "function": {
                                    "name": tu.name,
                                    "arguments": orjson.dumps(tu.args).decode(),
                                },
                            }
                            for tu in tool_uses
                        ],
                    }
                )

                # publish + persist the calls, then run them concurrently (§7.4 r5)
                for tu in tool_uses:
                    call_block = wb.add_json_block(
                        "tool_call",
                        {"call_id": tu.call_id, "name": tu.name, "args": tu.args},
                    )
                    hub.publish(
                        stream,
                        TOOL_CALL,
                        {
                            "index": call_block,
                            "call_id": tu.call_id,
                            "name": tu.name,
                            "args": tu.args,
                        },
                    )
                results = await asyncio.gather(
                    *(self._run_tool_call(stream, wb, tools, tu) for tu in tool_uses)
                )
                for tu, (tool_status, content) in zip(tool_uses, results, strict=True):
                    result_block = wb.add_json_block(
                        "tool_result",
                        {
                            "call_id": tu.call_id,
                            "name": tu.name,
                            "status": tool_status,
                            "summary": content[:2000],
                        },
                    )
                    hub.publish(
                        stream,
                        TOOL_RESULT,
                        {
                            "index": result_block,
                            "call_id": tu.call_id,
                            "status": tool_status,
                            "summary": content[:2000],
                        },
                    )
                    # §31.5: hop context is budgeted — a runaway tool loop must
                    # degrade to tool_limit, never to a provider 400
                    remaining = tool_budget - tool_chars_used
                    if len(content) > remaining:
                        content = content[: max(remaining, 0)] + "\n…(tool budget exhausted)"
                        tool_budget_hit = True
                    tool_chars_used += len(content)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tu.call_id,
                            "content": content,
                        }
                    )
                if tool_budget_hit:
                    stop_reason = "tool_limit"
                    break
                if stream.stop_requested.is_set():
                    status = "stopped"
                    stop_reason = "stopped"
                    break
        except ProviderError as exc:
            status = "error"
            stop_reason = "error"
            error_payload = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
            hub.publish(stream, ERROR, error_payload)
            log.warning(
                "provider_error",
                model=call.model,
                code=exc.code,
                retryable=exc.retryable,
                message_id=str(stream.message_id),
            )
        except Exception:
            status = "error"
            stop_reason = "error"
            error_payload = {
                "code": "internal_error",
                "message": "unexpected relay failure",
                "retryable": True,
            }
            hub.publish(stream, ERROR, error_payload)
            log.exception("relay_failure", message_id=str(stream.message_id))

        if citations and status == "complete":
            for citation in citations:
                idx = wb.add_json_block("citation", citation)
                hub.publish(
                    stream,
                    CITATION,
                    {
                        "index": idx,
                        "n": citation.get("n"),
                        "file_id": citation.get("file_id"),
                        "file_name": citation.get("file_name"),
                        "loc": citation.get("loc") or {},
                    },
                )

        total_ms = int((time.monotonic() - t0) * 1000)
        if usage is None:
            input_estimate = sum(
                self.counter.count_message(str(m.get("role", "")), str(m.get("content", "")))
                for m in call.messages
            )
            usage = Usage(
                input_tokens=input_estimate,
                output_tokens=self.counter.count(wb.full_text),
            )
        cost = self.pricing.cost_usd(call.model, usage)

        usage_event = UsageEvent(
            id=uuid7(),
            user_id=stream.user_id,
            conversation_id=stream.conversation_id,
            message_id=stream.message_id,
            provider=provider_of(call.model),
            model=call.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            cost_usd=cost,
            ttft_ms=ttft_ms,
            total_ms=total_ms,
        )

        try:
            await wb.finalize(
                status=status,
                model=call.model,
                error=error_payload,
                conversation_id=stream.conversation_id,
                usage_event=usage_event,
            )
        except Exception:
            log.exception("finalize_failed", message_id=str(stream.message_id))

        hub.publish(
            stream,
            USAGE,
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_tokens": usage.cached_tokens,
                "cost_usd": cost,
            },
        )
        hub.publish(
            stream,
            MESSAGE_END,
            {"stop_reason": stop_reason or "end", "ttft_ms": ttft_ms, "total_ms": total_ms},
        )
        hub.finish(stream)

        if generate_title and status in ("complete", "stopped"):
            try:
                await self.jobs.enqueue(
                    "generate_title", {"conversation_id": str(stream.conversation_id)}
                )
            except Exception:
                log.exception("title_enqueue_failed")
        if extract_memory and status == "complete":
            try:
                await self.jobs.enqueue(
                    "memory_extract", {"conversation_id": str(stream.conversation_id)}
                )
            except Exception:
                log.exception("memory_enqueue_failed")
