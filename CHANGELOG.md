# Changelog

All notable changes to Retinue are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) (pre-1.0: minor versions
may contain breaking changes; the API surface is additive by policy, §18).

## [Unreleased]

### Security & fixes (external bug report)

- **Privilege**: stdio MCP servers (arbitrary host commands) and stdio
  connector installs now require admin — parity with file datasources.
  Org-global MCP servers are mutable only by admins. Only an owner can grant
  the owner role, and the last owner cannot be demoted.
- **SSRF**: HTTP MCP server URLs, OpenAPI-action host allowlists, and connector
  installs are validated by a single egress classifier — link-local/metadata
  addresses are refused for everyone; private/loopback/unresolvable targets
  are admin-only ("admin-expandable", §9.4).
- **Tenant isolation**: an agent version may only pin collections its creator
  owns or that are shared org/public-wide (no cross-user RAG read); agent
  preflight only probes MCP servers and collections the caller can access.
- **Datasource**: db_sample / the sample endpoint now enforce the table
  allow/deny policy (previously only db_query did) and audit denials.
- **Robustness**: a chat send with a bad file id returns 404 instead of 500,
  and the idempotency IntegrityError path can no longer recurse forever;
  concurrent resumable-upload PATCHes on one session are serialized (offset
  race); fork now copies message attachments; the runtime registration toggle
  (admin setting) is honored by /auth/register.
- **CI**: the workflow provisions a real interpreter (actions/setup-python)
  before `uv pip install --system`, so the pipeline installs successfully.
- Versions synced across pyproject / frontend / changelog (0.7.0).


## [0.1.0]

### Added

- Chat over a zero-buffer SSE relay: write-behind persistence, lossless
  `Last-Event-ID` resume, idempotent sends, stop/regenerate, heartbeats,
  orphan-grace aborts, crash recovery.
- LiteLLM provider layer behind the `ProviderAdapter` seam; encrypted
  per-user/org credentials; model catalog with policies; cost accounting.
- Auth: Argon2id (peppered), Ed25519 JWTs, rotating refresh tokens with
  reuse detection, API keys, rate limits, audit log.
- Cache-stable context assembly with a deterministic token-budget allocator
  (§31.1–.2).
- React 19 SPA: rAF-coalesced streaming store, incremental markdown,
  worker-based highlighting, dark mode.
- SQLite/WAL storage with Alembic migrations; Postgres support via
  `[postgres]`; packaging pipeline (wheel with embedded SPA, Docker, CI).
