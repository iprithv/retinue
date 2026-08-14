"""Programmatic Alembic runner — migrations run at startup (§7.3, opt-out flag)."""

import asyncio
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _make_config(database_url: str) -> "object":
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade_sync(database_url: str, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(_make_config(database_url), revision)  # type: ignore[arg-type]


async def run_migrations(database_url: str) -> None:
    """The Alembic env is async-aware; run it in a thread so it can own a loop."""
    await asyncio.to_thread(upgrade_sync, database_url)


def current_revision_sync(database_url: str) -> str | None:
    """Best-effort current revision (doctor)."""
    import sqlalchemy as sa

    sync_url = database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT version_num FROM alembic_version")).first()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        engine.dispose()
