# ⚜️ retinue

**Your AI retinue.** Self-hosted AI chat & agents for every model — versioned
custom agents, MCP, RAG, autonomous runs. One command, zero services.

```bash
pip install retinue && retinue serve     # or: uvx retinue
```

One OS process. One SQLite file. A React 19 UI served from the same port.
Streams from 100+ providers (OpenAI, Anthropic, Google, Groq, Mistral,
DeepSeek, OpenRouter, Ollama, …) through a zero-buffer SSE relay.

> **Status: v0.1** — the first slice of the [locked architecture](ARCHITECTURE.md)
> (§31.6 roadmap): chat + SSE relay, LiteLLM provider layer, SQLite/WAL,
> auth, cache-stable context assembly, packaging pipeline. Agents, files/RAG,
> MCP, and the rest land wave by wave.

## What's in v0.1

- **Chat over SSE, engineered to spec (§7.4/§19)** — no DB writes on the token
  path (write-behind batching), per-stream ring buffers with `Last-Event-ID`
  resume that is *lossless* (ring replay → in-memory snapshot rebuild → DB
  replay), idempotent sends (retrying a `POST /chat` attaches to the live
  stream instead of double-generating), stop/regenerate/edit-as-branch,
  heartbeats, orphan-grace aborts, crash recovery.
- **Provider layer (D12)** — LiteLLM as a library behind a ~100-line
  `ProviderAdapter` seam; per-user → org → env credential resolution;
  provider keys encrypted with AES-256-GCM envelope encryption; model catalog
  with admin allow/deny policies; cost accounting per usage event.
- **Auth (§16)** — Argon2id (peppered) passwords, Ed25519 JWTs (15 min),
  rotating refresh tokens with family-revoking reuse detection, httpOnly
  cookie + CSRF double-submit for the browser, `rtn_` API keys for scripts,
  token-bucket rate limits, audit log.
- **Context engine (§31.1–.2)** — cache-stable prefix ordering for provider
  prompt caching, deterministic tiered token-budget allocator
  (byte-identical assembly is CI-tested), tokenizer loading that never blocks
  a request.
- **React 19 UI (§6)** — streaming store where a token re-renders exactly one
  component (rAF-coalesced), incremental markdown with content-hash-memoized
  stable blocks, Shiki highlighting in a worker, strict CSP, DOMPurify
  sanitization, dark mode, drafts, conversation management. Initial JS ≤250 KB
  gz (budget-checked: currently ~208 KB).
- **Ops** — `retinue doctor`, `retinue db backup` (VACUUM INTO), structured
  JSON logs with request ids, `/api/healthz` + `/api/readyz`, data export as
  a zip, background jobs with retry/backoff (conversation titles today).

Verified in this tree: **83 backend tests** (real-server SSE streaming, resume,
stop, reuse detection, migrations up/down, determinism), 10 frontend unit
tests, ruff + tsc strict clean, wheel-install smoke test (cold start ~1.3 s,
first token overhead ~27 ms with the mock provider).

## Quickstart

```bash
pip install retinue
retinue serve                # http://127.0.0.1:8000
```

Create the first account (it becomes the owner), add a provider key in
Settings — or export `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`
before starting — and chat. Details: [docs/quickstart.md](docs/quickstart.md).

Docker: `docker compose -f deploy/docker-compose.yml up`.

## Repository layout

```
backend/    Python package (FastAPI + SQLAlchemy + LiteLLM; `retinue` CLI)
frontend/   React 19 SPA (Vite, Tailwind v4, TanStack Query, zustand)
deploy/     Dockerfile, compose
scripts/    build_frontend_into_wheel.py, gen_ts_client.py
proto/      sandbox.proto — pre-cut gRPC seam (unused in v1 by design, §3.1)
docs/       quickstart, development guide
```

`ARCHITECTURE.md` (repo root) is the locked specification this implements;
deviations shipped in v0.1 are called out in [docs/development.md](docs/development.md)
and inline where they live.

## Development

```bash
just setup && just test     # see docs/development.md
```

MIT licensed. Contributions welcome — DCO sign-off required, security reports
via [SECURITY.md](SECURITY.md).
