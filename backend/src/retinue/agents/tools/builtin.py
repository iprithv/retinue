"""Built-in tool implementations (§9.2).

Each returns plain text for the model. Failures return readable error text —
the model self-corrects — and never raise into the relay.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from retinue.db.models import File, FileText, User
from retinue.sandbox.base import ExecLimits
from retinue.search.fts import SearchService

if TYPE_CHECKING:
    from retinue.core.state import AppState
    from retinue.datasources.base import QueryResult
    from retinue.db.models import DataSourceRow

log = structlog.get_logger("retinue.tools.builtin")

BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "web_search": {
        "description": "Search the web. Returns titles, URLs, and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
    },
    "file_read": {
        "description": "Read the extracted text of one of the user's files by id.",
        "parameters": {
            "type": "object",
            "properties": {"file_id": {"type": "string", "description": "file UUID"}},
            "required": ["file_id"],
        },
    },
    "file_search": {
        "description": "Full-text search across the user's uploaded files.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    "db_sources": {
        "description": "List the user's connected data sources (databases) with their engines.",
        "parameters": {"type": "object", "properties": {}},
    },
    "db_schema": {
        "description": "Get the schema (tables and columns) of a connected data source.",
        "parameters": {
            "type": "object",
            "properties": {"source_id": {"type": "string", "description": "data source UUID"}},
            "required": ["source_id"],
        },
    },
    "db_query": {
        "description": (
            "Run a read-only query against a connected data source. SQL engines take "
            "a single SELECT; document/search engines take a JSON query (see db_schema "
            "output for the engine's query language). Results are row-limited."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "statement": {
                    "type": "string",
                    "description": "SELECT / JSON query / read command",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["source_id", "statement"],
        },
    },
    "db_sample": {
        "description": "Fetch a few sample rows from a table/collection of a data source.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "table": {"type": "string"},
            },
            "required": ["source_id", "table"],
        },
    },
    "web_fetch": {
        "description": (
            "Fetch a public web page and return its readable text (SSRF-guarded: "
            "private addresses are blocked)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "http(s) URL"}},
            "required": ["url"],
        },
    },
    "image_gen": {
        "description": "Generate an image from a text prompt; returns a file id + markdown link.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "size": {"type": "string", "enum": ["1024x1024", "1792x1024", "1024x1792"]},
            },
            "required": ["prompt"],
        },
    },
    "code_exec": {
        "description": (
            "Execute code in the sandbox and return stdout/stderr. "
            "No network access; keep runs under the CPU/memory caps."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "language": {"type": "string", "enum": ["python"]},
            },
            "required": ["code"],
        },
    },
}


async def web_search(state: "AppState", args: dict[str, Any]) -> str:
    cfg = state.settings.tools.web_search
    query = str(args.get("query", ""))[:500]
    limit = min(int(args.get("max_results") or cfg.max_results), 10)
    if cfg.provider == "none":
        return (
            "error: web search is not configured. An admin can set "
            "RETINUE_TOOLS__WEB_SEARCH__PROVIDER to searxng, tavily, brave, "
            "serper, or jina."
        )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if cfg.provider == "searxng":
                response = await client.get(
                    f"{(cfg.url or '').rstrip('/')}/search",
                    params={"q": query, "format": "json"},
                )
                response.raise_for_status()
                results = response.json().get("results", [])[:limit]
                rows = [
                    f"- {r.get('title', '?')} — {r.get('url', '')}\n  {r.get('content', '')[:300]}"
                    for r in results
                ]
            elif cfg.provider == "tavily":
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": cfg.api_key, "query": query, "max_results": limit},
                )
                response.raise_for_status()
                rows = [
                    f"- {r.get('title', '?')} — {r.get('url', '')}\n  {r.get('content', '')[:300]}"
                    for r in response.json().get("results", [])[:limit]
                ]
            elif cfg.provider == "serper":
                response = await client.post(
                    "https://google.serper.dev/search",
                    json={"q": query, "num": limit},
                    headers={"X-API-KEY": cfg.api_key or ""},
                )
                response.raise_for_status()
                rows = [
                    f"- {r.get('title', '?')} — {r.get('link', '')}\n  {r.get('snippet', '')[:300]}"
                    for r in response.json().get("organic", [])[:limit]
                ]
            elif cfg.provider == "jina":
                response = await client.get(
                    f"https://s.jina.ai/{query}",
                    headers={
                        "Authorization": f"Bearer {cfg.api_key or ''}",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                rows = [
                    f"- {r.get('title', '?')} — {r.get('url', '')}\n"
                    f"  {r.get('description', r.get('content', ''))[:300]}"
                    for r in response.json().get("data", [])[:limit]
                ]
            else:  # brave
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": limit},
                    headers={"X-Subscription-Token": cfg.api_key or ""},
                )
                response.raise_for_status()
                rows = [
                    f"- {r.get('title', '?')} — {r.get('url', '')}\n"
                    f"  {r.get('description', '')[:300]}"
                    for r in response.json().get("web", {}).get("results", [])[:limit]
                ]
    except httpx.HTTPError as exc:
        return f"error: web search failed ({exc})"
    return "\n".join(rows) if rows else "no results"


async def file_read(state: "AppState", user: User, args: dict[str, Any]) -> str:
    try:
        file_id = uuid.UUID(str(args.get("file_id", "")))
    except ValueError:
        return "error: file_id must be a UUID"
    async with state.db.read_session() as session:
        file = await session.get(File, file_id)
        if file is None or file.owner_id != user.id:
            return "error: file not found"
        text = await session.get(FileText, file_id)
    if text is None or not text.text.strip():
        return f"error: no extracted text for {file.original_name!r} (status: {file.status})"
    body = text.text[:32_000]
    return f"[{file.original_name}]\n{body}"


async def file_search(state: "AppState", user: User, args: dict[str, Any]) -> str:
    query = str(args.get("query", ""))[:200]
    service = SearchService(state.db.is_sqlite)
    async with state.db.read_session() as session:
        hits = await service.search_files(session, user.id, query, 8)
    if not hits:
        return "no matching files"
    return "\n".join(f"- {h.title} (file_id: {h.id})\n  {h.snippet[:200]}" for h in hits)


async def code_exec(state: "AppState", args: dict[str, Any]) -> str:
    code = str(args.get("code", ""))[:100_000]
    language = str(args.get("language") or "python")
    result = await state.sandbox.run(code, language, ExecLimits())
    if result.status == "unavailable":
        return f"error: {result.stderr}"
    parts = [f"status: {result.status}"]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    return "\n".join(parts)


# -- data source tools (§30.5) ---------------------------------------------------------


def _render_result(result: "QueryResult") -> str:
    from retinue.datasources.base import MODEL_CHAR_CAP

    lines = ["\t".join(result.columns)] if result.columns else []
    for row in result.rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    body = "\n".join(lines)
    if len(body) > MODEL_CHAR_CAP:
        body = body[:MODEL_CHAR_CAP] + "\n…(truncated)"
    tail = " — truncated" if result.truncated else ""
    suffix = f"\n({result.row_count} rows{tail}, {result.elapsed_ms}ms)"
    return (body or "(no rows)") + suffix


async def _owned_source(state: "AppState", user: User, raw_id: str) -> "DataSourceRow | str":
    import uuid as uuid_mod

    from retinue.db.models import DataSourceRow

    try:
        source_id = uuid_mod.UUID(str(raw_id))
    except ValueError:
        return "error: source_id must be a UUID (use db_sources to list them)"
    async with state.db.read_session() as session:
        source = await session.get(DataSourceRow, source_id)
    if source is None or source.owner_id != user.id:
        return "error: data source not found (use db_sources to list them)"
    return source


async def db_sources(state: "AppState", user: User) -> str:
    from sqlalchemy import select as sa_select

    from retinue.datasources.registry import ENGINES
    from retinue.db.models import DataSourceRow

    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    sa_select(DataSourceRow).where(DataSourceRow.owner_id == user.id)
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        return "no data sources connected — the user can add one in Settings"
    lines = []
    for source in rows:
        engine = ENGINES.get(source.engine)
        language = engine.query_language if engine else "sql"
        lines.append(
            f"- {source.name} (source_id: {source.id}, engine: {source.engine}, "
            f"query language: {language}, status: {source.status})"
        )
    return "\n".join(lines)


async def db_schema(state: "AppState", user: User, args: dict[str, Any]) -> str:
    from retinue.datasources.base import DataSourceError
    from retinue.datasources.registry import ENGINES
    from retinue.datasources.service import get_schema

    source = await _owned_source(state, user, args.get("source_id", ""))
    if isinstance(source, str):
        return source
    try:
        schema = await get_schema(state, source)
    except DataSourceError as error:
        return f"error: {error}"
    engine = ENGINES.get(source.engine)
    language = engine.query_language if engine else "sql"
    lines = [f"engine: {source.engine} (query language: {language})"]
    if engine and engine.notes:
        lines.append(f"notes: {engine.notes}")
    for table in schema.tables[:100]:
        columns = ", ".join(f"{c.name} {c.type}" for c in table.columns[:40])
        lines.append(f"- {table.name}({columns})" if columns else f"- {table.name}")
    return "\n".join(lines) or "no tables found"


async def db_query(state: "AppState", user: User, args: dict[str, Any]) -> str:
    from retinue.datasources.base import DataSourceError
    from retinue.datasources.service import run_query

    source = await _owned_source(state, user, args.get("source_id", ""))
    if isinstance(source, str):
        return source
    try:
        limit = int(args["limit"]) if str(args.get("limit", "")).isdigit() else None
        result = await run_query(
            state,
            source,
            user_id=user.id,
            statement=str(args.get("statement", "")),
            limit=limit,
        )
    except DataSourceError as error:
        return f"error: {error}"
    return _render_result(result)


async def db_sample(state: "AppState", user: User, args: dict[str, Any]) -> str:
    from retinue.datasources.base import DataSourceError
    from retinue.datasources.service import get_sample

    source = await _owned_source(state, user, args.get("source_id", ""))
    if isinstance(source, str):
        return source
    try:
        result = await get_sample(
            state, source, user_id=user.id, table=str(args.get("table", "")), n=3
        )
    except DataSourceError as error:
        return f"error: {error}"
    return _render_result(result)


async def web_fetch(state: "AppState", args: dict[str, Any]) -> str:
    """Guarded page fetch → readable text (the scrape half of web research)."""
    from retinue.core.egress import fetch_guarded
    from retinue.core.errors import AppError
    from retinue.rag.extract import _extract_html  # shared readability path

    url = str(args.get("url", ""))[:2000]
    try:
        body, content_type = await fetch_guarded(url, max_bytes=2 * 1024 * 1024)
    except AppError as error:
        return f"error: {error.message}"
    if "html" in content_type:
        text, _ = _extract_html(body)
    else:
        text = body.decode("utf-8", errors="replace")
    text = text.strip()
    cap = state.settings.tools.web_fetch_max_chars
    if len(text) > cap:
        text = text[:cap] + "\n…(truncated)"
    return f"[{url}]\n{text}" if text else f"error: no readable text at {url}"


def _mock_png() -> bytes:
    import base64

    # 1x1 opaque indigo pixel — deterministic bytes for keyless dev/tests
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg+M9QDwADgQF/e5IkGQAAAABJRU5ErkJggg=="
    )


async def image_gen(state: "AppState", user: User, args: dict[str, Any]) -> str:
    """Generate an image and land it in the files subsystem (§11 artifacts)."""
    import base64

    from blake3 import blake3 as blake3_hash

    from retinue.core.ids import uuid7
    from retinue.db.models import File as FileRow
    from retinue.filesys.base import shard_key
    from retinue.filesys.service import link_blob

    model = state.settings.tools.image_gen_model
    if model is None and state.settings.models.mock_enabled:
        model = "mock/image"
    if not model:
        return (
            "error: image generation is not configured. An admin can set "
            "RETINUE_TOOLS__IMAGE_GEN_MODEL (e.g. openai/dall-e-3)."
        )
    prompt = str(args.get("prompt", ""))[:2000]
    size = str(args.get("size") or "1024x1024")

    if model == "mock/image":
        png = _mock_png()
    else:
        import litellm

        provider = model.split("/", 1)[0] if "/" in model else "openai"
        async with state.db.read_session() as session:
            api_key, api_base = await state.registry.resolve_credential(session, user.id, provider)
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        try:
            response = await litellm.aimage_generation(**kwargs)
            entry = response.data[0]
            b64 = entry["b64_json"] if isinstance(entry, dict) else entry.b64_json
            png = base64.b64decode(b64)
        except Exception as error:
            return f"error: image generation failed ({str(error)[:300]})"

    digest = blake3_hash(png).hexdigest()
    import tempfile
    from pathlib import Path as _Path

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as handle:
        handle.write(png)
        tmp_path = _Path(handle.name)
    try:
        await state.storage.put(shard_key(digest), tmp_path)  # moves the file
    finally:
        await asyncio.to_thread(tmp_path.unlink, True)  # survives only if put() failed
    file_id = uuid7()
    async with state.db.write_session() as session:
        await link_blob(session, blake3_hex=digest, size=len(png), backend=state.storage)
        session.add(
            FileRow(
                id=file_id,
                owner_id=user.id,
                blake3=digest,
                original_name=f"generated-{file_id.hex[:8]}.png",
                mime="image/png",
                size=len(png),
                status="ready",
                meta={"generated": True, "prompt": prompt[:500], "model": model},
            )
        )
    return (
        f"image generated (file_id: {file_id}). "
        f"Embed it with: ![{prompt[:60]}](/api/files/{file_id}/content)"
    )
