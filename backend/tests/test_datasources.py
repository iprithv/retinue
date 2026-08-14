"""Universal Data Layer (§30): guardrails, engines, staged tests, agent tools."""

import sqlite3
import uuid

import pytest
from tests.support import collect_chat, register_user

from retinue.datasources.base import DataSourceError, QueryResult
from retinue.datasources.guard import (
    SourcePolicy,
    apply_masking,
    prepare_statement,
    transpile,
    validate_select,
)
from retinue.datasources.registry import ENGINES, catalog, driver_available, engine_info

POLICY = SourcePolicy()


# -- guard unit tests (§30.3 layer 2-5) --------------------------------------------


class TestSqlGuard:
    def test_select_passes(self):
        validate_select("SELECT * FROM users WHERE id = 1", "postgres", POLICY)
        validate_select("WITH x AS (SELECT 1) SELECT * FROM x", "postgres", POLICY)
        validate_select("SELECT a FROM t1 UNION SELECT b FROM t2", "mysql", POLICY)

    @pytest.mark.parametrize(
        "statement",
        [
            "DROP TABLE users",
            "DELETE FROM users",
            "UPDATE users SET role = 'admin'",
            "INSERT INTO users VALUES (1)",
            "CREATE TABLE x (a int)",
            "ALTER TABLE users ADD COLUMN x int",
            "TRUNCATE TABLE users",
            "GRANT ALL ON users TO public",
        ],
    )
    def test_writes_rejected(self, statement):
        with pytest.raises(DataSourceError, match=r"read-only|SELECT"):
            validate_select(statement, "postgres", POLICY)

    def test_multi_statement_rejected(self):
        with pytest.raises(DataSourceError, match="one statement"):
            validate_select("SELECT 1; DROP TABLE users", "postgres", POLICY)

    def test_select_into_rejected(self):
        with pytest.raises(DataSourceError, match="INTO"):
            validate_select("SELECT * INTO backup FROM users", "tsql", POLICY)

    def test_limit_injected(self):
        sql, effective = prepare_statement("SELECT * FROM t", "postgres", POLICY, None)
        assert "LIMIT 1000" in sql
        assert effective == 1000

    def test_existing_limit_clamped(self):
        sql, effective = prepare_statement("SELECT * FROM t LIMIT 999999", "postgres", POLICY, None)
        assert effective == 1000
        assert "999999" not in sql

    def test_smaller_limit_kept(self):
        _sql, effective = prepare_statement("SELECT * FROM t LIMIT 5", "postgres", POLICY, None)
        assert effective == 5

    def test_table_deny(self):
        policy = SourcePolicy(deny_tables=["secrets"])
        with pytest.raises(DataSourceError, match="denied"):
            validate_select("SELECT * FROM secrets", "postgres", policy)

    def test_table_allowlist(self):
        policy = SourcePolicy(allow_tables=["orders"])
        validate_select("SELECT * FROM orders", "postgres", policy)
        with pytest.raises(DataSourceError, match="allowlist"):
            validate_select("SELECT * FROM users", "postgres", policy)
        # joins can't smuggle a denied table either
        with pytest.raises(DataSourceError, match="allowlist"):
            validate_select(
                "SELECT * FROM orders JOIN users ON users.id = orders.user_id",
                "postgres",
                policy,
            )

    def test_masking(self):
        result = QueryResult(columns=["email"], rows=[["write to alice@corp.com now"]], row_count=1)
        policy = SourcePolicy(mask_patterns=[r"[\w.+-]+@[\w-]+\.[\w.]+"])
        masked = apply_masking(result, policy)
        assert masked.rows[0][0] == "write to ••• now"

    def test_transpile(self):
        out = transpile("SELECT NOW()", "postgres", "mysql")
        assert "NOW" in out.upper() or "CURRENT_TIMESTAMP" in out.upper()


# -- registry integrity ---------------------------------------------------------------


def test_registry_has_20_plus_engines():
    assert len(ENGINES) >= 25
    for engine in ENGINES.values():
        assert engine.key and engine.label and engine.category
        assert engine.module and engine.cls
        # SQL engines must carry a sqlglot dialect for the guard
        if engine.query_language == "sql":
            assert engine.dialect, engine.key

    entries = catalog()
    assert len(entries) == len(ENGINES)
    # locally installed drivers are detected; heavyweight ones honestly absent
    available = {e["key"] for e in entries if e["available"]}
    assert {"sqlite", "duckdb"} <= available
    for entry in entries:
        if not entry["available"]:
            assert entry["install_extra"], entry["key"]


def test_missing_driver_is_clean_error(app_client):
    engine = engine_info("snowflake")
    assert not driver_available(engine)  # not installed in the test env
    from retinue.datasources.registry import make_adapter

    with pytest.raises(DataSourceError, match="pip install 'retinue\\[snowflake\\]'"):
        make_adapter(engine, {}, {})


# -- end-to-end over real engines (sqlite file + duckdb) --------------------------------


def _seed_sqlite(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
        INSERT INTO customers VALUES
            (1, 'Ada', 'ada@example.com'),
            (2, 'Grace', 'grace@example.com'),
            (3, 'Edsger', 'edsger@example.com');
        CREATE TABLE secrets (id INTEGER PRIMARY KEY, value TEXT);
        INSERT INTO secrets VALUES (1, 'do not read');
        """
    )
    connection.commit()
    connection.close()


async def _create_source(client, headers, tmp_path, **overrides):
    db_path = tmp_path / "external.db"
    if not db_path.exists():
        _seed_sqlite(db_path)
    body = {
        "name": "Ext SQLite",
        "engine": "sqlite",
        "config": {"path": str(db_path)},
        **overrides,
    }
    response = await client.post("/api/datasources", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_sqlite_source_lifecycle(app_client, tmp_path):
    client, _ = app_client
    headers = await register_user(client)  # first user = owner → file engines allowed
    source = await _create_source(client, headers, tmp_path)

    # staged connection test (§30.7): file → probe → introspect, all green
    result = (await client.post(f"/api/datasources/{source['id']}/test", headers=headers)).json()
    assert result["ok"] is True
    assert [s["stage"] for s in result["stages"]] == ["file", "auth+probe", "introspect"]
    detail = (await client.get("/api/datasources", headers=headers)).json()[0]
    assert detail["status"] == "ok"

    # schema
    schema = (await client.get(f"/api/datasources/{source['id']}/schema", headers=headers)).json()
    names = {t["name"] for t in schema["tables"]}
    assert {"customers", "secrets"} <= names
    customers = next(t for t in schema["tables"] if t["name"] == "customers")
    assert {c["name"] for c in customers["columns"]} == {"id", "name", "email"}

    # guarded query console
    response = await client.post(
        f"/api/datasources/{source['id']}/query",
        headers=headers,
        json={"statement": "SELECT name FROM customers ORDER BY id"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["columns"] == ["name"]
    assert [r[0] for r in result["rows"]] == ["Ada", "Grace", "Edsger"]

    # writes rejected at the API too
    response = await client.post(
        f"/api/datasources/{source['id']}/query",
        headers=headers,
        json={"statement": "DELETE FROM customers"},
    )
    assert response.status_code == 422
    assert "read-only" in response.json()["error"]["message"]

    # sample endpoint
    sample = (
        await client.get(
            f"/api/datasources/{source['id']}/sample",
            headers=headers,
            params={"table": "customers", "n": 2},
        )
    ).json()
    assert sample["row_count"] == 2


async def test_policy_enforcement_and_audit(app_client, tmp_path):
    client, app = app_client
    headers = await register_user(client)
    source = await _create_source(
        client,
        headers,
        tmp_path,
        policy={
            "deny_tables": ["secrets"],
            "max_rows": 2,
            "mask_patterns": [r"[\w.+-]+@[\w-]+\.[\w.]+"],
        },
    )

    # deny list
    response = await client.post(
        f"/api/datasources/{source['id']}/query",
        headers=headers,
        json={"statement": "SELECT * FROM secrets"},
    )
    assert response.status_code == 422
    assert "denied" in response.json()["error"]["message"]

    # row cap + masking
    result = (
        await client.post(
            f"/api/datasources/{source['id']}/query",
            headers=headers,
            json={"statement": "SELECT email FROM customers"},
        )
    ).json()
    assert result["row_count"] == 2  # policy max_rows clamps below the table size
    assert all(row[0] == "•••" for row in result["rows"])

    # every statement audited (§30.3.6)
    from sqlalchemy import select

    from retinue.db.models import AuditLog

    state = app.state.retinue
    async with state.db.read_session() as session:
        entries = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "datasource.query")))
            .scalars()
            .all()
        )
    assert len(entries) >= 2
    assert any(e.meta.get("ok") is False for e in entries)  # the denied one too


async def test_duckdb_engine(app_client, tmp_path):
    import duckdb as duckdb_mod

    db_path = tmp_path / "analytics.duckdb"
    connection = duckdb_mod.connect(str(db_path))
    connection.execute("CREATE TABLE events (day VARCHAR, clicks INTEGER)")
    connection.execute("INSERT INTO events VALUES ('mon', 10), ('tue', 30)")
    connection.close()

    client, _ = app_client
    headers = await register_user(client)
    response = await client.post(
        "/api/datasources",
        headers=headers,
        json={"name": "Analytics", "engine": "duckdb", "config": {"path": str(db_path)}},
    )
    assert response.status_code == 201, response.text
    source = response.json()

    result = (await client.post(f"/api/datasources/{source['id']}/test", headers=headers)).json()
    assert result["ok"] is True

    query = (
        await client.post(
            f"/api/datasources/{source['id']}/query",
            headers=headers,
            json={"statement": "SELECT SUM(clicks) AS total FROM events"},
        )
    ).json()
    assert query["rows"][0][0] == 40


async def test_file_engines_require_admin(app_client, tmp_path):
    client, _ = app_client
    await register_user(client, email="owner@test.dev")  # first user = owner
    member_headers = await register_user(client, email="member@test.dev")
    response = await client.post(
        "/api/datasources",
        headers=member_headers,
        json={"name": "x", "engine": "sqlite", "config": {"path": "/etc/passwd"}},
    )
    assert response.status_code == 403


async def test_agent_queries_datasource_through_chat(app_client, tmp_path):
    """The full §30.5 loop: agent → db_query tool → guarded engine → cited answer."""
    client, _ = app_client
    headers = await register_user(client)
    source = await _create_source(client, headers, tmp_path)

    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Data Analyst",
                "system_prompt": "Query the data.",
                "model": "mock/tool",
                "tools": [
                    {"type": "builtin", "ref": "db_sources"},
                    {"type": "builtin", "ref": "db_query"},
                ],
            },
        )
    ).json()

    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": (
                'use:db_query {"source_id": "' + source["id"] + '", '
                '"statement": "SELECT name FROM customers ORDER BY id"}'
            ),
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "ok"
    assert "Ada" in result.data["summary"] and "Grace" in result.data["summary"]

    # db_sources tool lists it
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": "use:db_sources {}",
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert "Ext SQLite" in result.data["summary"]
    assert source["id"] in result.data["summary"]
