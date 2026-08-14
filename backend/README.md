# ⚜️ Retinue

**Your AI retinue.** Self-hosted AI chat and agents for every model —
versioned custom agents, tools, RAG, and search in a single process.

```bash
pip install retinue && retinue serve     # http://127.0.0.1:8000
```

One OS process. One SQLite file. Zero external services. A React UI served
from the same port, streaming from 100+ providers (OpenAI, Anthropic, Google,
Ollama, and any OpenAI-compatible endpoint).

- **Versioned agents** with a studio, live test bench, and portable
  `.agent.json` export — conversations pin the agent version they started with.
- **Tools**: web search (5 providers), page fetching, file query, image
  generation, sandboxed code, any MCP server, and OpenAPI specs as tools,
  with optional per-call user approval.
- **Databases as agent sources**: 25+ engines (Postgres, MySQL, Snowflake,
  BigQuery, ClickHouse, MongoDB, Redis, Elasticsearch, …) behind read-only
  AST-validated queries, row limits, masking, and audit logging.
- **One-click connectors**: Slack, GitHub, Jira, Grafana, Prometheus,
  Datadog, Sentry, PagerDuty, Kubernetes, AWS, and more via pinned MCP/API
  recipes with encrypted credentials.
- **Files & RAG**: resumable deduplicated uploads, hybrid BM25 + vector
  retrieval, cited answers. **Full-text search** across everything.
- **Inspectable memory**, branching conversations, share links, full data
  export, multi-user accounts with encrypted keys and an admin panel.

Add a provider key in Settings, or export `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / `GEMINI_API_KEY` before starting.

Optional extras: `retinue[extract]` (PDF/DOCX/XLSX extraction),
`retinue[sandbox-wasm]` (code sandbox), `retinue[postgres]`.

This is the Python package of the [Retinue monorepo](https://github.com/retinue/retinue) —
see the repository for the full documentation, architecture specification,
frontend source, and deployment assets. MIT licensed.
