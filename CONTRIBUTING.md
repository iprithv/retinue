# Contributing to Retinue

Thanks for wanting to help. Retinue aims for a rare combination — full-platform
feature depth with a one-command install — and the way we keep that promise is
a short list of non-negotiables. Read this once and everything else is normal
open-source flow.

## Ground rules

1. **`main` is always releasable.** Every PR must pass the full CI matrix
   (lint, types, tests on Linux/macOS/Windows, OpenAPI drift, wheel smoke).
2. **No new always-on services.** SQLite, local files, and in-process workers
   are the default story; anything heavier goes behind an existing seam
   (`ProviderAdapter`, `StorageBackend`, `ExecutionBackend`, `SearchService`,
   `DataSource`) as an optional tier.
3. **No optimization PR without a before/after number.** `just bench` or a
   reproducible measurement in the PR description.
4. **Wire changes are deliberate.** Anything that alters the HTTP/SSE surface
   must regenerate the snapshot (`python scripts/gen_ts_client.py`) — CI fails
   on drift — and stay additive (§18 of the architecture).
5. **Tool/RAG/MCP output is untrusted data, always.** Never interpret it as
   instructions; never widen an SSRF allowlist implicitly.

## Getting started

```bash
git clone https://github.com/retinue/retinue && cd retinue
just setup          # uv venv + backend deps + pnpm install
just dev-api        # backend on :8000 with the mock provider enabled
just dev-web        # vite dev server on :5173
```

No API key is needed for development: `RETINUE_MODELS__MOCK_ENABLED=1` (set by
`just dev-api`) provides deterministic models — `mock/echo`, `mock/slow`,
`mock/fail-mid`, `mock/tool` (drive the tool loop with `use:<tool> {...}` in
your message), `mock/vision`, and `mock/embed`.

## Before you open a PR

```bash
just test           # backend (pytest) + frontend (vitest)
just lint           # ruff check + format, tsc
cd backend && ../.venv/bin/python -m mypy src/retinue   # strict, must be clean
```

- **Tests are not optional.** New behavior ships with tests; bug fixes ship
  with a regression test. SSE behavior is tested against a real server
  (`live` fixture), not mocks.
- **Migrations** are additive-first, generated with Alembic against
  `retinue.db.models`, and must apply on both SQLite and Postgres. Never let
  autogenerate touch the FTS5 shadow tables — write those by hand (see 0002).
- **Style is enforced, not debated:** Ruff (line length 100) and
  `mypy --strict` for Python; `tsc` strict + `noUncheckedIndexedAccess` for
  TypeScript. Comments explain constraints the code can't, referencing the
  architecture section (e.g. `(§7.4)`) where one applies.

## Commit & PR conventions

- Sign your commits (DCO): `git commit -s`.
- Keep PRs focused; a PR that mixes a refactor with a feature will be asked
  to split.
- PR descriptions state *what changed and why*, link the architecture section
  they implement, and call out any deliberate deviation (deviations are
  documented in `docs/development.md`, honestly).

## Adding common things

- **A datasource engine:** add an `EngineInfo` to
  `backend/src/retinue/datasources/registry.py`, an adapter in
  `engines/sql.py` or `engines/nosql.py` (lazy driver import, read-only
  enforcement that does not rely on the driver), a pip extra in
  `backend/pyproject.toml`, and a registry-integrity assertion. If the engine
  can run in CI (file-based or a service container), add an end-to-end test.
- **A connector:** add a pinned recipe to
  `backend/src/retinue/agents/connectors.py` (MCP command/URL or a *minimal*
  OpenAPI subset — never a full API mirror), declare its secrets/params, and
  extend the catalog tests.
- **A builtin tool:** schema in `agents/tools/builtin.py`, dispatch in
  `agents/runtime.py`, a `mock/tool`-driven end-to-end test, and an entry in
  the studio's tool list (`frontend/src/app/AgentStudioPage.tsx`).

## Reporting security issues

Privately, please — see [SECURITY.md](SECURITY.md). Do not open a public
issue for a vulnerability.

## Code of conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
