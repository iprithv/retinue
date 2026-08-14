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
