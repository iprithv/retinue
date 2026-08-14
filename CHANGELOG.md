# Changelog

All notable changes to Retinue are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) (pre-1.0: minor versions
may contain breaking changes; the API surface is additive by policy, §18).

## [Unreleased]

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
