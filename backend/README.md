# Retinue

**Your AI retinue.** Self-hosted, single-process, multi-provider AI chat platform —
`pip install retinue && retinue serve`.

- One process, one SQLite file, zero external services.
- Streams from 100+ model providers (OpenAI, Anthropic, Google, Ollama, …) over a
  zero-buffer SSE relay with resumable streams.
- Argon2id + Ed25519-JWT auth, rotating refresh tokens with reuse detection,
  AES-256-GCM encrypted provider keys.
- Prompt-cache-stable context assembly with a deterministic token-budget allocator.

This directory is the Python package of the [Retinue monorepo](https://github.com/retinue/retinue).
See the repository root for the full architecture specification, frontend source, and
deployment assets.

## Quickstart

```bash
pip install retinue      # or: uvx retinue
retinue serve            # http://127.0.0.1:8000
```

Add a provider key in Settings (or export `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
`GEMINI_API_KEY` before starting) and chat.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
pytest
ruff check src tests && ruff format --check src tests
```

Licensed under MIT.
