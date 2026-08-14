"""Typer CLI (§7.2): serve, doctor, admin, db. Imports are lazy (§20) so
`retinue --version` stays fast."""

import os
from typing import Annotated

import typer

app = typer.Typer(
    name="retinue",
    help="Your AI retinue — self-hosted, multi-provider AI chat. One command, zero services.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
admin_app = typer.Typer(help="Administrative commands.")
db_app = typer.Typer(help="Database maintenance.")
app.add_typer(admin_app, name="admin")
app.add_typer(db_app, name="db")


def _version_callback(value: bool) -> None:
    if value:
        import retinue

        typer.echo(f"retinue {retinue.__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Retinue — assemble your retinue."""


def _export_env(**pairs: object) -> None:
    for key, value in pairs.items():
        if value is not None:
            os.environ[f"RETINUE_{key.upper()}"] = str(value)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port.")] = None,
    server: Annotated[str, typer.Option(help="ASGI server: granian|uvicorn.")] = "granian",
    data_dir: Annotated[str | None, typer.Option(help="Data directory.")] = None,
    database_url: Annotated[str | None, typer.Option(help="SQLAlchemy database URL.")] = None,
    no_migrate: Annotated[
        bool, typer.Option("--no-migrate", help="Skip startup migrations.")
    ] = False,
    log_level: Annotated[str | None, typer.Option(help="debug|info|warning|error.")] = None,
    log_format: Annotated[str | None, typer.Option(help="console|json.")] = None,
) -> None:
    """Start the Retinue server (Granian by default, uvicorn fallback)."""
    _export_env(
        data_dir=data_dir,
        database_url=database_url,
        log_level=log_level,
        log_format=log_format,
    )
    if no_migrate:
        os.environ["RETINUE_AUTO_MIGRATE"] = "false"

    from retinue.config import Settings

    settings = Settings()
    bind_host = host or settings.server.host
    bind_port = port or settings.server.port

    if server == "granian":
        try:
            from granian import Granian
            from granian.constants import Interfaces

            typer.echo(f"retinue serving on http://{bind_host}:{bind_port} (granian)")
            Granian(
                "retinue.app:create_app",
                address=bind_host,
                port=bind_port,
                interface=Interfaces.ASGI,
                factory=True,
                workers=1,
            ).serve()
            return
        except Exception as exc:  # granian unavailable/incompatible on this platform
            typer.echo(f"granian unavailable ({exc!r}); falling back to uvicorn", err=True)

    import uvicorn

    typer.echo(f"retinue serving on http://{bind_host}:{bind_port} (uvicorn)")
    uvicorn.run(
        "retinue.app:create_app",
        factory=True,
        host=bind_host,
        port=bind_port,
        log_level=(log_level or settings.log_level).lower(),
    )


@app.command()
def doctor() -> None:
    """Verify the environment: data dir, WAL, secret, migrations, providers, port."""
    import socket
    import sqlite3

    from retinue.config import Settings
    from retinue.db.migrate import current_revision_sync
    from retinue.providers.registry import ENV_PROVIDER_KEYS

    settings = Settings()
    failures = 0

    def check(name: str, ok: bool, note: str = "") -> None:
        nonlocal failures
        mark = "✓" if ok else "✗"
        if not ok:
            failures += 1
        typer.echo(f"  {mark} {name}{f' — {note}' if note else ''}")

    typer.echo("retinue doctor")

    try:
        settings.ensure_dirs()
        probe = settings.resolved_data_dir / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
        check("data dir writable", True, str(settings.resolved_data_dir))
    except Exception as exc:
        check("data dir writable", False, str(exc))

    try:
        conn = sqlite3.connect(settings.resolved_data_dir / "doctor.db")
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        conn.close()
        (settings.resolved_data_dir / "doctor.db").unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            (settings.resolved_data_dir / f"doctor.db{suffix}").unlink(missing_ok=True)
        check("SQLite WAL support", mode == "wal", f"journal_mode={mode}")
    except Exception as exc:
        check("SQLite WAL support", False, str(exc))

    source = settings.secret_source()
    settings.master_secret()
    check(
        "master secret",
        True,
        {
            "env": "from RETINUE_SECRET",
            "file": f"persisted at {settings.secret_file}",
            "generated": f"generated to {settings.secret_file}",
        }[source],
    )

    db_file = settings.resolved_data_dir / "app.db"
    if settings.is_sqlite and not db_file.exists():
        # probing would create the file; a fresh install is healthy by definition
        check("database migrations", True, "fresh install — schema is created on first start")
    else:
        revision = current_revision_sync(settings.effective_database_url)
        if revision is not None:
            check("database migrations", True, f"revision {revision}")
        else:
            check("database migrations", False, "database exists but has no migration state")

    configured = [p for env, p in ENV_PROVIDER_KEYS.items() if os.environ.get(env)]
    check(
        "provider keys in env",
        True,
        ", ".join(sorted(set(configured))) if configured else "none (add in Settings after start)",
    )

    try:
        sock = socket.socket()
        sock.bind((settings.server.host, settings.server.port))
        sock.close()
        check("port available", True, f"{settings.server.host}:{settings.server.port}")
    except OSError:
        check("port available", False, f"{settings.server.host}:{settings.server.port} is in use")

    if failures:
        typer.echo(f"{failures} check(s) failed")
        raise typer.Exit(code=1)
    typer.echo("all checks passed")


@admin_app.command("create-user")
def admin_create_user(
    email: Annotated[str, typer.Option(prompt=True)],
    password: Annotated[str, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)],
    role: Annotated[str, typer.Option(help="owner|admin|member|viewer")] = "member",
    name: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create a user directly in the database (bootstrap/recovery)."""
    import asyncio

    from sqlalchemy import select

    from retinue.config import Settings
    from retinue.core.ids import uuid7
    from retinue.core.security import PasswordService
    from retinue.db.migrate import run_migrations
    from retinue.db.models import User
    from retinue.db.session import Database

    if role not in ("owner", "admin", "member", "viewer"):
        typer.echo(f"invalid role {role!r}")
        raise typer.Exit(code=2)

    settings = Settings()
    settings.ensure_dirs()

    async def run() -> None:
        await run_migrations(settings.effective_database_url)
        db = Database(settings.effective_database_url)
        passwords = PasswordService(settings.master_secret())
        try:
            async with db.write_session() as session:
                existing = (
                    await session.execute(select(User).where(User.email == email.lower()))
                ).scalar_one_or_none()
                if existing is not None:
                    typer.echo(f"user {email} already exists")
                    raise typer.Exit(code=2)
                session.add(
                    User(
                        id=uuid7(),
                        email=email.lower(),
                        name=name,
                        role=role,
                        password_hash=passwords.hash(password),
                    )
                )
        finally:
            await db.dispose()

    asyncio.run(run())
    typer.echo(f"created {role} {email}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Run pending migrations."""
    from retinue.config import Settings
    from retinue.db.migrate import upgrade_sync

    settings = Settings()
    settings.ensure_dirs()
    upgrade_sync(settings.effective_database_url)
    typer.echo("database is up to date")


@db_app.command("backup")
def db_backup(
    out: Annotated[str | None, typer.Option(help="Output file path.")] = None,
) -> None:
    """Consistent SQLite snapshot via VACUUM INTO (§21)."""
    import sqlite3
    import time as _time

    from retinue.config import Settings

    settings = Settings()
    if not settings.is_sqlite:
        typer.echo("db backup currently supports the SQLite bundle; use pg_dump for Postgres")
        raise typer.Exit(code=2)
    src = settings.resolved_data_dir / "app.db"
    if not src.is_file():
        typer.echo(f"no database at {src}")
        raise typer.Exit(code=2)
    backups = settings.resolved_data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = out or str(backups / f"app-{_time.strftime('%Y%m%d-%H%M%S')}.db")
    conn = sqlite3.connect(src)
    try:
        conn.execute("VACUUM INTO ?", (target,))
    finally:
        conn.close()
    typer.echo(f"backup written to {target}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
