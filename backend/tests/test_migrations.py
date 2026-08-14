"""Migrations: fresh upgrade, idempotent re-run, full downgrade (N-1 policy §24)."""

import sqlite3

from retinue.db.migrate import current_revision_sync, run_migrations, upgrade_sync

EXPECTED_TABLES = {
    "users",
    "oidc_identities",
    "refresh_tokens",
    "api_keys",
    "credentials",
    "model_policies",
    "agents",
    "agent_versions",
    "conversations",
    "messages",
    "message_parts",
    "jobs",
    "job_schedules",
    "usage_events",
    "audit_log",
    "app_settings",
}


def _tables(path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()


async def test_fresh_upgrade_creates_everything(tmp_path):
    db_path = tmp_path / "fresh.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    await run_migrations(url)
    assert EXPECTED_TABLES <= _tables(db_path)
    assert current_revision_sync(url) == "0001"
    # idempotent
    await run_migrations(url)


def test_downgrade_to_base(tmp_path):
    from alembic import command
    from alembic.config import Config

    from retinue.db.migrate import MIGRATIONS_DIR

    db_path = tmp_path / "down.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    upgrade_sync(url)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "base")
    assert _tables(db_path) == {"alembic_version"}


async def test_wal_pragmas_applied(tmp_path):
    from sqlalchemy import text

    from retinue.db.session import Database

    db_path = tmp_path / "pragma.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    await run_migrations(url)
    database = Database(url)
    try:
        async with database.reader.connect() as connection:
            mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar()
            fk = (await connection.execute(text("PRAGMA foreign_keys"))).scalar()
            sync = (await connection.execute(text("PRAGMA synchronous"))).scalar()
        assert mode == "wal"
        assert fk == 1
        assert sync == 1  # NORMAL
    finally:
        await database.dispose()
