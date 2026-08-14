# Development

The repo is a monorepo (§5): `backend/` is the Python package, `frontend/` the
React SPA, `scripts/` the build glue. `ARCHITECTURE.md` is the locked spec —
read it before changing any seam.

## Setup

```bash
just setup           # uv venv + backend[dev] + pnpm install
# or manually:
uv venv .venv --python 3.12
uv pip install -p .venv/bin/python -e "./backend[dev]"
cd frontend && corepack pnpm install
```

## Daily loop

```bash
just dev-api         # uvicorn --reload on :8000 (mock provider enabled)
just dev-web         # vite dev server on :5173, /api proxied to :8000
just test            # backend pytest + frontend vitest
just lint            # ruff check/format + tsc
just build           # SPA -> wheel in ./dist
```

The mock provider (`RETINUE_MODELS__MOCK_ENABLED=1`) serves `mock/echo`,
`mock/slow`, `mock/fail-mid`, `mock/fail-auth` — deterministic streams for
UI work and tests without any API key.

## Tests worth knowing about

- `backend/tests/test_chat_stream.py` / `test_chat_stop_resume.py` run a real
  uvicorn on a loopback port: genuine SSE streaming, stop, lossless
  `Last-Event-ID` resume, idempotent retries, orphan-grace aborts.
- `backend/tests/test_context_budget.py` enforces §31.1/§31.2: byte-identical
  assembly for identical inputs, deterministic tiered trimming.
- `backend/tests/test_sse.py` property-tests the encoder against a parser
  (hypothesis).
- Wire changes must regenerate `frontend/src/lib/api/openapi.json` via
  `python scripts/gen_ts_client.py` — CI fails on drift (D24).

## Conventions

- Migrations: additive-first; `alembic revision --autogenerate` against
  `retinue.db.models`; both SQLite and Postgres must apply.
- No optimization PR without a before/after number (§20).
- Contributions require DCO sign-off (`git commit -s`).

## Spec deviations shipped in v0.6 (each has a seam back to spec)

- **Vectors**: inline float32 BLOBs + brute-force cosine instead of sqlite-vec
  virtual tables — correct at Solo-bundle scale; `rag/embed.py`/`rag/retrieve.py`
  are the swap seam for sqlite-vec/pgvector (§27.4 ladder).
- **Sandbox**: the WASM backend needs `pip install retinue[sandbox-wasm]` plus a
  WASI CPython at `~/.retinue/sandbox/python.wasm`; absent that, `code_exec`
  returns an honest "sandbox unavailable" tool error (§27.5 floor). Docker
  backend not yet wired.
- **Web search**: SearchProvider seam configured via
  `RETINUE_TOOLS__WEB_SEARCH__*` (searxng/tavily/brave); off by default.
- **Extraction extras**: PDF/DOCX/XLSX/HTML need `retinue[extract]`; text-family
  formats extract with the core wheel. No thumbnails/OCR yet (§11.6 partial).
- **Postgres search**: ILIKE fallback until the tsvector tier (§13); the FTS5
  path is SQLite-only and CI-tested.
- Still deferred from v0.1: OIDC login, generated TS client (schema-snapshot
  drift check instead), TanStack Virtual in the message list, importers for
  third-party chat exports, budgets middleware, `migrate-to-postgres`.

## v0.7: Universal Data Layer & connectors — honest notes

- **28 datasource engines** are registered; SQLite and DuckDB are exercised
  end-to-end in CI. The Postgres/MySQL wire families share those adapters'
  code paths; the remaining engines (Snowflake, BigQuery, Mongo, …) have
  small adapters over their official clients that are *structurally* tested
  (registry integrity, clean missing-driver errors) but need a live server to
  verify — they are honest first implementations, not battle-tested ones.
  Read-only enforcement does not depend on the engine: the sqlglot AST guard
  and the NoSQL operation allowlists run before any driver code.
- **Connectors** are pinned recipes over the MCP/OpenAPI bus, not bespoke
  clients. stdio recipes need their runtime (npx/uvx/binary) on the host —
  the install response says so. Bundled API subsets (Prometheus, PagerDuty,
  Opsgenie, New Relic, Splunk, Zendesk, ServiceNow, Intercom, webhooks) are
  minimal read/notify surfaces, deliberately not full API mirrors.

### Feature-parity notes vs. other self-hosted chat platforms

Where we are ahead: versioned agents with pinning, resumable deduplicated
uploads, lossless SSE resume, per-call tool approvals, read-only database
layer with staged connection tests, whole-account export/import, mypy-strict
backend. At parity: multi-provider chat, MCP, RAG with citations, memory,
branching/forking, share links, search, vision input, image generation,
temporary chats, multi-user with admin. Honest gaps that remain: OIDC/LDAP
social login, speech (STT/TTS), a live artifacts preview panel, prompt
library, i18n, and importers for other platforms' chat exports.
