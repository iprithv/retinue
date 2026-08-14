## What & why

<!-- One or two sentences. Link the architecture section this implements
     (e.g. §9.2) if one applies. -->

## Checklist

- [ ] `just test` and `just lint` pass locally; `mypy --strict` is clean
- [ ] New behavior has tests (bug fixes include a regression test)
- [ ] Wire changes regenerated the OpenAPI snapshot (`python scripts/gen_ts_client.py`)
- [ ] Migrations (if any) are additive-first and apply on SQLite and Postgres
- [ ] Performance claims come with a before/after number
- [ ] Deliberate spec deviations are documented in `docs/development.md`
- [ ] Commits are signed off (DCO, `git commit -s`)
