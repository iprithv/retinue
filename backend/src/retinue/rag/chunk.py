"""Structure-aware chunking (§10): headings, code fences, and page markers
stay intact; ~800-token targets with 15% overlap; every chunk carries
`{page, heading_path}` for citations."""

import re
from dataclasses import dataclass, field
from typing import Any

from retinue.core.tokens import TokenCounter

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_RE = re.compile(r"^\[page (\d+)\]$")
_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(slots=True)
class ChunkOut:
    text: str
    token_count: int
    loc: dict[str, Any]


@dataclass(slots=True)
class _Block:
    text: str
    tokens: int
    heading_path: list[str] = field(default_factory=list)
    page: int | None = None


def _blocks(text: str, counter: TokenCounter) -> list[_Block]:
    """Split into indivisible blocks: paragraphs, whole code fences, headings."""
    lines = text.splitlines()
    blocks: list[_Block] = []
    heading_stack: list[tuple[int, str]] = []
    page: int | None = None
    buf: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        nonlocal buf
        body = "\n".join(buf).strip("\n")
        if body.strip():
            blocks.append(
                _Block(
                    text=body,
                    tokens=counter.count(body),
                    heading_path=[h for _, h in heading_stack],
                    page=page,
                )
            )
        buf = []

    for line in lines:
        if in_fence:
            buf.append(line)
            if line.startswith(fence_marker):
                in_fence = False
                flush()
            continue
        fence = _FENCE_RE.match(line)
        if fence:
            flush()
            in_fence = True
            fence_marker = fence.group(1)
            buf.append(line)
            continue
        page_match = _PAGE_RE.match(line.strip())
        if page_match:
            flush()
            page = int(page_match.group(1))
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading.group(2).strip()))
            buf.append(line)
            flush()
            continue
        if not line.strip() and buf:
            flush()
            continue
        if line.strip():
            buf.append(line)
    flush()
    return blocks


def _split_oversized(block: _Block, target: int, counter: TokenCounter) -> list[_Block]:
    if block.tokens <= target * 2:
        return [block]
    # split on sentence-ish boundaries, fall back to fixed windows
    pieces = re.split(r"(?<=[.!?])\s+", block.text)
    out: list[_Block] = []
    current: list[str] = []
    tokens = 0
    for piece in pieces:
        piece_tokens = counter.count(piece)
        if tokens + piece_tokens > target and current:
            body = " ".join(current)
            out.append(_Block(body, counter.count(body), block.heading_path, block.page))
            current, tokens = [], 0
        current.append(piece)
        tokens += piece_tokens
    if current:
        body = " ".join(current)
        out.append(_Block(body, counter.count(body), block.heading_path, block.page))
    return out


def chunk_text(
    text: str,
    counter: TokenCounter,
    *,
    target_tokens: int = 800,
    overlap_frac: float = 0.15,
) -> list[ChunkOut]:
    raw_blocks: list[_Block] = []
    for block in _blocks(text, counter):
        raw_blocks.extend(_split_oversized(block, target_tokens, counter))
    if not raw_blocks:
        return []

    chunks: list[ChunkOut] = []
    current: list[_Block] = []
    tokens = 0

    def emit() -> None:
        nonlocal current, tokens
        if not current:
            return
        body = "\n\n".join(b.text for b in current)
        first = current[0]
        loc: dict[str, Any] = {}
        if first.heading_path:
            loc["heading_path"] = first.heading_path
        if first.page is not None:
            loc["page"] = first.page
        chunks.append(ChunkOut(text=body, token_count=counter.count(body), loc=loc))
        # overlap: keep the trailing blocks worth ~overlap_frac of the target
        overlap_budget = int(target_tokens * overlap_frac)
        kept: list[_Block] = []
        kept_tokens = 0
        for b in reversed(current):
            if kept_tokens + b.tokens > overlap_budget:
                break
            kept.insert(0, b)
            kept_tokens += b.tokens
        current = kept
        tokens = kept_tokens

    for block in raw_blocks:
        if tokens + block.tokens > target_tokens and current:
            emit()
        current.append(block)
        tokens += block.tokens
    if current:
        body = "\n\n".join(b.text for b in current)
        # avoid emitting an overlap-only tail that duplicates the previous chunk
        if not chunks or body not in chunks[-1].text:
            first = current[0]
            loc: dict[str, Any] = {}
            if first.heading_path:
                loc["heading_path"] = first.heading_path
            if first.page is not None:
                loc["page"] = first.page
            chunks.append(ChunkOut(text=body, token_count=counter.count(body), loc=loc))
    return chunks
