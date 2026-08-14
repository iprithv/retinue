<div align="center">

# ⚜️ Retinue

**Your AI retinue.** Self-hosted AI chat and agents for every model —
versioned custom agents, tools, RAG, and search in a single process.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](backend/pyproject.toml)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](frontend/package.json)

```bash
pip install retinue && retinue serve
```

One OS process. One SQLite file. Zero external services.

</div>

---

Retinue is what you get when a full AI platform refuses to become a stack of
containers: chat, agents, tools, files, RAG, and search served by a single
`pip install`-able process with a React UI on the same port. It streams from
100+ providers — OpenAI, Anthropic, Google, Groq, Mistral, DeepSeek,
OpenRouter, Ollama, and any OpenAI-compatible endpoint.

## Features

- **Streaming chat, engineered end to end** — a zero-buffer SSE relay with no
  database writes on the token path, lossless `Last-Event-ID` resume after
  network drops, idempotent sends, stop/regenerate, and crash recovery.
- **Versioned agents** — build agents in a studio with a live test bench.
  Every edit creates an immutable new version; conversations pin the version
  they started with and never silently change behavior. Revert, export, and
  import agents as portable `.agent.json` files.
- **Tools** — agents can search the web (SearXNG, Tavily, Brave, Serper, or
  Jina), fetch and read web pages, query your files, generate images, and run
  code in a sandbox. Attach any [MCP](https://modelcontextprotocol.io) server
  (stdio or HTTP) or paste an OpenAPI spec to turn its operations into tools,
  guarded by a strict SSRF policy. Sensitive tools can require per-call user
  approval, streamed live into the chat.
- **Databases as agent sources** — connect 25+ engines (PostgreSQL, MySQL,
  MariaDB, SQL Server, Oracle, Snowflake, BigQuery, Redshift, Databricks,
  ClickHouse, DuckDB, Trino/Presto, SQLite, CockroachDB, TiDB, StarRocks,
  TimescaleDB, QuestDB, MongoDB, Redis, Cassandra/Scylla, DynamoDB,
  Elasticsearch, OpenSearch, Neo4j, InfluxDB, …) and let agents query them —
  **read-only by construction**: single-SELECT AST validation, automatic row
  limits, table allow/deny lists, PII masking, per-statement audit logging,
  and a staged connection tester (DNS → TCP → auth → probe → introspect) so
  "connection failed" is never a mystery.
- **One-click connectors** — a curated gallery (Slack, GitHub, GitLab,
  Jira/Confluence, Linear, Notion, Google Drive, Grafana, Prometheus,
  Datadog, Sentry, New Relic, Splunk, PagerDuty, Opsgenie, Kubernetes, AWS,
  Zendesk, ServiceNow, Intercom, Discord, …) that installs pinned MCP servers
  or minimal API actions with encrypted credentials. Anything not in the
  gallery still plugs in through raw MCP or OpenAPI.
- **Files & knowledge** — drag-and-drop uploads with resumable transfers and
  BLAKE3 content addressing (identical files are stored once — and never
  re-uploaded). Text is extracted, chunked structure-aware, embedded with a
  content-hash cache, and retrieved with hybrid BM25 + vector search. Answers
  cite their sources.
- **Search everything** — `⌘K` full-text search across messages,
  conversations, files, and agents, powered by SQLite FTS5 with
  as-you-type prefix matching.
- **Memory you can inspect** — Retinue proposes durable facts for your
  approval (or extracts them automatically, or never — your choice), shows
  you exactly what it knows, and injects only what's relevant. Incognito
  conversations are always excluded.
- **Branching conversations** — edit any message or regenerate any reply to
  create a branch, and switch between alternatives inline.
- **Vision & images** — attach images and vision-capable models see them;
  agents can generate images that land in your file library.
- **Multi-user ready** — accounts with roles, per-user encrypted provider
  keys (AES-256-GCM), rotating refresh tokens with reuse detection, API keys,
  rate limits, an audit log, and an admin panel with usage and cost rollups.
- **Your data is yours** — share conversations by link, export everything as
  a zip, import it elsewhere. No telemetry.

## Quickstart

```bash
pip install retinue        # or: uvx retinue
retinue serve              # http://127.0.0.1:8000
```

Open the URL, create the first account (it becomes the owner), and add a
provider API key in **Settings** — or export one before starting:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, GEMINI_API_KEY, ...
retinue serve
```

More detail in [docs/quickstart.md](docs/quickstart.md).

### Docker

```bash
docker compose -f deploy/docker-compose.yml up
```

## Configuration

Everything is configurable via `RETINUE_*` environment variables or
`~/.retinue/config.toml` — CLI flags > env > file > defaults. A few common
knobs:

| Setting | Purpose |
|---|---|
| `RETINUE_SERVER__PORT` | listen port (default 8000) |
| `RETINUE_MODELS__DEFAULT` | default model, e.g. `anthropic/claude-sonnet-4-5` |
| `RETINUE_DATABASE_URL` | switch SQLite → PostgreSQL |
| `RETINUE_SECRET` | master secret for encryption at rest (auto-generated otherwise) |
| `RETINUE_TOOLS__WEB_SEARCH__PROVIDER` | `searxng`, `tavily`, `brave`, `serper`, or `jina` |
| `RETINUE_TOOLS__IMAGE_GEN_MODEL` | e.g. `openai/dall-e-3` to enable image generation |

Optional extras: `retinue[extract]` (PDF/DOCX/XLSX text extraction),
`retinue[sandbox-wasm]` (WASM code sandbox), `retinue[postgres]`, and one
extra per datasource driver family (`retinue[mysql]`, `retinue[snowflake]`,
`retinue[mongodb]`, … or `retinue[datasources]` for the common set).

`retinue doctor` diagnoses a deployment; `retinue db backup` takes a
consistent snapshot.

## How it's built

The design doctrine: *Python and TypeScript are the ergonomic shells; Rust
and C do the computing.* FastAPI + SQLAlchemy over SQLite/WAL (or Postgres),
LiteLLM as a library behind a thin provider seam, orjson/blake3/tiktoken on
the hot paths, and a React 19 SPA where an arriving token re-renders exactly
one component. The complete specification lives in
[ARCHITECTURE.md](ARCHITECTURE.md); deliberate deviations are documented in
[docs/development.md](docs/development.md).

```
backend/    Python package (FastAPI + SQLAlchemy + LiteLLM; `retinue` CLI)
frontend/   React 19 SPA (Vite, Tailwind v4, TanStack Query, zustand)
deploy/     Dockerfile, compose
scripts/    build/codegen helpers
docs/       quickstart, development guide
```

## Development

```bash
just setup     # venv + backend deps + frontend deps
just dev-api   # backend with reload (mock provider enabled)
just dev-web   # vite dev server
just test      # backend + frontend suites
```

See [docs/development.md](docs/development.md) for the full loop, test
architecture, and conventions.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
ground rules, dev setup (no API key needed: a deterministic mock provider
drives everything, including the tool loop), and how to add engines,
connectors, and tools. Participation is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md); releases are tracked in the
[CHANGELOG](CHANGELOG.md).

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).
Provider keys and tool credentials are encrypted at rest; nothing ever leaves
your server except the model calls you configure.

## License

[MIT](LICENSE)
