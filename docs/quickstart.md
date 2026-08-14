# Quickstart

## Install & run

```bash
pip install retinue        # or: uvx retinue / uv tool install retinue
retinue serve              # http://127.0.0.1:8000
```

One process, one SQLite file under `~/.retinue/data/`, zero external services.

1. Open http://127.0.0.1:8000 and create the first account (it becomes the
   **owner**).
2. Add a provider API key in **Settings → Providers** (encrypted at rest), or
   export one before starting the server:

   ```bash
   export ANTHROPIC_API_KEY=sk-ant-…   # or OPENAI_API_KEY, GEMINI_API_KEY,
   retinue serve                        # GROQ_API_KEY, OPENROUTER_API_KEY, …
   ```

   For local models, point Ollama at it: `export OLLAMA_API_BASE=http://localhost:11434`.
3. Pick a model in the chat header and go.

`retinue doctor` verifies the environment; `retinue --help` lists everything.

## Configuration

Precedence: CLI flags > `RETINUE_*` env vars > `~/.retinue/config.toml` > defaults.
Nested keys use `__` in env names:

| Setting | Example |
|---|---|
| Bind address/port | `retinue serve --host 0.0.0.0 --port 9000` |
| Default model | `RETINUE_MODELS__DEFAULT=anthropic/claude-sonnet-4-5` |
| Housekeeping model (titles) | `RETINUE_MODELS__HOUSEKEEPING=openai/gpt-4o-mini` |
| System prompt | `RETINUE_DEFAULT_SYSTEM_PROMPT="You are …"` |
| Master secret (prod) | `RETINUE_SECRET=…` (else auto-generated to `~/.retinue/secret`) |
| Database | `RETINUE_DATABASE_URL=postgresql+asyncpg://…` (`pip install retinue[postgres]`) |
| Disable signups | `RETINUE_AUTH__REGISTRATION_ENABLED=false` (first user always allowed) |

The same table works as TOML in `~/.retinue/config.toml`:

```toml
[models]
default = "anthropic/claude-sonnet-4-5"
housekeeping = "openai/gpt-4o-mini"
```

## Programmatic access

Create an API key in **Settings → API keys**, then:

```bash
curl -N http://127.0.0.1:8000/api/chat \
  -H "Authorization: Bearer rtn_…" -H "Accept: text/event-stream" \
  -d '{"message_id":"<uuid7>","text":"hello","model":"anthropic/claude-sonnet-4-5"}'
```

The response is the SSE stream documented in `ARCHITECTURE.md` §19. Retries
with the same `message_id` attach to the live stream (send `Last-Event-ID` to
resume losslessly). Interactive API docs live at `/api/docs`.

## Docker

```bash
docker compose -f deploy/docker-compose.yml up
```

## Backups

```bash
retinue db backup            # consistent snapshot via VACUUM INTO
```

## A five-minute tour

- **Agents** — sidebar → *Agents* → *New agent*. Give it a prompt, pick a
  model, toggle tools (web search, page fetch, files, databases, images,
  code). Test it live in the right pane; **Save as vN** publishes a new
  immutable version. Chats pin the version they start with.
- **Files & knowledge** — sidebar → *Files*. Drop files (identical content is
  stored once), create a collection, add files to it, then pin the collection
  to an agent — its answers will cite sources as `[n]`.
- **Databases** — Settings → *Data sources*. Pick an engine, fill in the
  connection, hit *test* to watch the DNS → TCP → auth → probe ladder, then
  give an agent the `db_query` tool. Queries are read-only, row-limited, and
  audit-logged no matter what the model asks for.
- **Connectors** — Settings → *Connectors*. One-click Slack, GitHub, Jira,
  Grafana, PagerDuty, and 17 more; each becomes an MCP server or API action
  you attach to agents in the studio.
- **Search** — press `⌘K` anywhere.
- **Memory** — Settings → *Memory*. Retinue proposes durable facts after
  conversations (review mode by default); approve, edit, disable, or turn it
  off entirely. Incognito chats never touch it.
