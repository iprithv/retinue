"""SQL engine adapters (§30.2).

Every statement arriving here has already passed the sqlglot read-only guard
(guard.prepare_statement) — adapters only execute and shape results. Sync
drivers run in worker threads; async drivers (asyncpg, aiomysql, aiosqlite)
stay on the loop.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from retinue.datasources.base import (
    SAMPLE_ROWS,
    ColumnInfo,
    DataSourceError,
    QueryResult,
    SchemaModel,
    TableInfo,
    clip_rows,
)
from retinue.datasources.guard import quote_identifier

if TYPE_CHECKING:
    from retinue.datasources.registry import EngineInfo

INTROSPECT_TABLE_CAP = 200


def _lit(value: Any) -> str:
    """Escape a value used as a SQL string literal in fixed introspection
    queries (defense-in-depth; these queries never pass the AST guard)."""
    return str(value).replace("'", "''")


def _result(columns: list[str], raw_rows: list[Any], limit: int, t0: float) -> QueryResult:
    rows, truncated = clip_rows(columns, raw_rows, limit)
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


class _BaseSql:
    """Shared plumbing: sample() is a guarded SELECT over a quoted identifier."""

    def __init__(self, engine: "EngineInfo", config: dict[str, Any], secrets: dict[str, Any]):
        self.engine = engine
        self.config = config
        self.secrets = secrets

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        raise NotImplementedError  # pragma: no cover - every adapter overrides

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        ident = quote_identifier(table, self.engine.dialect)
        n = min(n or SAMPLE_ROWS, 20)
        return await self.query(f"SELECT * FROM {ident} LIMIT {n}", n, timeout_s)

    async def close(self) -> None:  # adapters override when they hold connections
        return


# -- threaded DBAPI adapters ---------------------------------------------------------


class _ThreadedSql(_BaseSql):
    """Generic sync-DBAPI adapter: connect per call in a worker thread.

    Per-call connections keep the Solo bundle simple and leak-proof; the §30.8
    pooling tier is an optimization seam, not a correctness one.
    """

    def _connect(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def _tables_sql(self) -> str:  # information_schema works on most of them
        return (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'sys') "
            f"LIMIT {INTROSPECT_TABLE_CAP}"
        )

    def _columns_sql(self, table: str) -> str:
        return (
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{_lit(table)}' LIMIT 100"
        )

    def _run_sync(self, statement: str, limit: int) -> tuple[list[str], list[Any]]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(statement)
            columns = [d[0] for d in cursor.description or []]
            raw = cursor.fetchmany(limit + 1)
            return columns, list(raw)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        t0 = time.monotonic()
        try:
            columns, raw = await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, statement, limit), timeout=timeout_s
            )
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        return _result(columns, raw, limit, t0)

    async def probe(self) -> None:
        await self.query("SELECT 1", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        tables_result = await self.query(self._tables_sql(), INTROSPECT_TABLE_CAP, 20.0)
        tables: list[TableInfo] = []
        for row in tables_result.rows:
            name = str(row[0])
            try:
                cols = await self.query(self._columns_sql(name), 100, 10.0)
                columns = [ColumnInfo(str(r[0]), str(r[1])) for r in cols.rows]
            except DataSourceError:
                columns = []
            tables.append(TableInfo(name=name, columns=columns))
        return SchemaModel(tables=tables)


# -- file engines --------------------------------------------------------------------


class SqliteAdapter(_BaseSql):
    """SQLite file via aiosqlite (core dependency)."""

    def _path(self) -> str:
        path = str(self.config.get("path", ""))
        if not path:
            raise DataSourceError("sqlite source needs a file path")
        return path

    async def _run(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        import aiosqlite

        t0 = time.monotonic()

        async def _go() -> tuple[list[str], list[Any]]:
            async with aiosqlite.connect(f"file:{self._path()}?mode=ro", uri=True) as db:
                async with db.execute(statement) as cursor:
                    columns = [d[0] for d in cursor.description or []]
                    raw = await cursor.fetchmany(limit + 1)
            return columns, list(raw)

        try:
            columns, raw = await asyncio.wait_for(_go(), timeout=timeout_s)
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        return _result(columns, raw, limit, t0)

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        return await self._run(statement, limit, timeout_s)

    async def probe(self) -> None:
        await self._run("SELECT 1", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        tables_result = await self._run(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            INTROSPECT_TABLE_CAP,
            20.0,
        )
        tables: list[TableInfo] = []
        for row in tables_result.rows:
            name = str(row[0])
            ident = quote_identifier(name, "sqlite")
            cols = await self._run(f"PRAGMA table_info({ident})", 100, 10.0)
            tables.append(
                TableInfo(
                    name=name,
                    columns=[ColumnInfo(str(r[1]), str(r[2])) for r in cols.rows],
                )
            )
        return SchemaModel(tables=tables)


class DuckdbAdapter(_ThreadedSql):
    def _connect(self) -> Any:
        import duckdb

        path = str(self.config.get("path", ":memory:"))
        return duckdb.connect(path, read_only=path != ":memory:")

    def _tables_sql(self) -> str:
        return f"SELECT table_name FROM information_schema.tables LIMIT {INTROSPECT_TABLE_CAP}"


# -- Postgres wire family -------------------------------------------------------------


class PostgresAdapter(_BaseSql):
    """asyncpg; the session is forced read-only (§30.3 layer 1)."""

    async def _run(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        import asyncpg

        t0 = time.monotonic()
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=str(self.config.get("host", "")),
                    port=int(self.config.get("port") or self.engine.default_port or 5432),
                    database=str(self.config.get("database", "")),
                    user=str(self.config.get("user", "")),
                    password=self.secrets.get("password"),
                    timeout=10,
                ),
                timeout=12,
            )
        except Exception as error:
            raise DataSourceError(f"connect failed: {str(error)[:300]}") from error
        try:
            try:
                await conn.execute("SET default_transaction_read_only = on")
            except Exception:
                pass  # not all PG-wire engines support it; the AST guard still holds
            records = await asyncio.wait_for(conn.fetch(statement), timeout=timeout_s)
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            await conn.close()
        columns = list(records[0].keys()) if records else []
        raw = [tuple(r.values()) for r in records]
        return _result(columns, raw, limit, t0)

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        return await self._run(statement, limit, timeout_s)

    async def probe(self) -> None:
        await self._run("SELECT 1", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        result = await self._run(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema NOT IN ('information_schema','pg_catalog') "
            "ORDER BY table_name, ordinal_position LIMIT 2000",
            2000,
            20.0,
        )
        tables: dict[str, TableInfo] = {}
        for table, column, dtype in result.rows:
            entry = tables.setdefault(str(table), TableInfo(name=str(table)))
            entry.columns.append(ColumnInfo(str(column), str(dtype)))
        return SchemaModel(tables=list(tables.values())[:INTROSPECT_TABLE_CAP])


# -- MySQL wire family -----------------------------------------------------------------


class MysqlAdapter(_BaseSql):
    async def _run(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        import aiomysql

        t0 = time.monotonic()
        try:
            conn = await asyncio.wait_for(
                aiomysql.connect(
                    host=str(self.config.get("host", "")),
                    port=int(self.config.get("port") or self.engine.default_port or 3306),
                    db=str(self.config.get("database", "")),
                    user=str(self.config.get("user", "")),
                    password=str(self.secrets.get("password") or ""),
                ),
                timeout=12,
            )
        except Exception as error:
            raise DataSourceError(f"connect failed: {str(error)[:300]}") from error
        try:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute("SET SESSION TRANSACTION READ ONLY")
                except Exception:
                    pass  # TiDB/StarRocks variants may lack it; the AST guard holds
                await asyncio.wait_for(cursor.execute(statement), timeout=timeout_s)
                columns = [d[0] for d in cursor.description or []]
                raw = await cursor.fetchmany(limit + 1)
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            conn.close()
        return _result(columns, list(raw), limit, t0)

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        return await self._run(statement, limit, timeout_s)

    async def probe(self) -> None:
        await self._run("SELECT 1", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        result = await self._run(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = '{_lit(self.config.get('database', ''))}' "
            "ORDER BY table_name, ordinal_position LIMIT 2000",
            2000,
            20.0,
        )
        tables: dict[str, TableInfo] = {}
        for table, column, dtype in result.rows:
            entry = tables.setdefault(str(table), TableInfo(name=str(table)))
            entry.columns.append(ColumnInfo(str(column), str(dtype)))
        return SchemaModel(tables=list(tables.values())[:INTROSPECT_TABLE_CAP])


# -- OLAP -----------------------------------------------------------------------------


class ClickhouseAdapter(_BaseSql):
    def _client(self) -> Any:
        import clickhouse_connect

        return clickhouse_connect.get_client(
            host=str(self.config.get("host", "")),
            port=int(self.config.get("port") or 8123),
            database=str(self.config.get("database", "default")),
            username=str(self.config.get("user", "default")),
            password=str(self.secrets.get("password") or ""),
            secure=bool(self.config.get("secure", False)),
        )

    def _run_sync(self, statement: str) -> tuple[list[str], list[Any]]:
        client = self._client()
        try:
            result = client.query(statement, settings={"readonly": 1})
            return list(result.column_names), list(result.result_rows)
        finally:
            client.close()

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        t0 = time.monotonic()
        try:
            columns, raw = await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, statement), timeout=timeout_s
            )
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        return _result(columns, raw, limit, t0)

    async def probe(self) -> None:
        await self.query("SELECT 1", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        result = await self.query(
            "SELECT table, name, type FROM system.columns "
            f"WHERE database = '{_lit(self.config.get('database', 'default'))}' LIMIT 2000",
            2000,
            20.0,
        )
        tables: dict[str, TableInfo] = {}
        for table, column, dtype in result.rows:
            entry = tables.setdefault(str(table), TableInfo(name=str(table)))
            entry.columns.append(ColumnInfo(str(column), str(dtype)))
        return SchemaModel(tables=list(tables.values())[:INTROSPECT_TABLE_CAP])


class TrinoAdapter(_ThreadedSql):
    def _connect(self) -> Any:
        import trino

        auth = None
        if self.secrets.get("password"):
            auth = trino.auth.BasicAuthentication(
                str(self.config.get("user", "")), str(self.secrets["password"])
            )
        return trino.dbapi.connect(
            host=str(self.config.get("host", "")),
            port=int(self.config.get("port") or 8080),
            catalog=str(self.config.get("catalog", "")),
            schema=str(self.config.get("schema", "")),
            user=str(self.config.get("user", "")),
            http_scheme=str(self.config.get("http_scheme", "http")),
            auth=auth,
        )


class MssqlAdapter(_ThreadedSql):
    def _connect(self) -> Any:
        import pymssql

        return pymssql.connect(
            server=str(self.config.get("host", "")),
            port=str(self.config.get("port") or 1433),
            database=str(self.config.get("database", "")),
            user=str(self.config.get("user", "")),
            password=str(self.secrets.get("password") or ""),
            login_timeout=10,
        )


class OracleAdapter(_ThreadedSql):
    def _connect(self) -> Any:
        import oracledb

        return oracledb.connect(
            host=str(self.config.get("host", "")),
            port=int(self.config.get("port") or 1521),
            service_name=str(self.config.get("service_name", "")),
            user=str(self.config.get("user", "")),
            password=str(self.secrets.get("password") or ""),
        )

    def _tables_sql(self) -> str:
        return f"SELECT table_name FROM user_tables FETCH FIRST {INTROSPECT_TABLE_CAP} ROWS ONLY"

    def _columns_sql(self, table: str) -> str:
        return (
            "SELECT column_name, data_type FROM user_tab_columns "
            f"WHERE table_name = '{_lit(table)}' FETCH FIRST 100 ROWS ONLY"
        )


# -- warehouses ---------------------------------------------------------------------------


class SnowflakeAdapter(_ThreadedSql):
    def _connect(self) -> Any:
        import snowflake.connector

        return snowflake.connector.connect(
            account=str(self.config.get("account", "")),
            database=str(self.config.get("database", "")),
            schema=str(self.config.get("schema", "PUBLIC")),
            warehouse=self.config.get("warehouse"),
            user=str(self.config.get("user", "")),
            password=str(self.secrets.get("password") or ""),
            login_timeout=15,
        )


class DatabricksAdapter(_ThreadedSql):
    def _connect(self) -> Any:
        from databricks import sql as dbsql

        return dbsql.connect(
            server_hostname=str(self.config.get("server_hostname", "")),
            http_path=str(self.config.get("http_path", "")),
            access_token=str(self.secrets.get("access_token") or ""),
            catalog=self.config.get("catalog"),
            schema=self.config.get("schema"),
        )


class BigQueryAdapter(_BaseSql):
    def _client(self) -> Any:
        import orjson
        from google.cloud import bigquery
        from google.oauth2 import service_account

        raw = self.secrets.get("service_account_json")
        if raw:
            info = orjson.loads(raw if isinstance(raw, (str, bytes)) else orjson.dumps(raw))
            credentials = service_account.Credentials.from_service_account_info(info)
            return bigquery.Client(
                project=str(self.config.get("project", "")), credentials=credentials
            )
        return bigquery.Client(project=str(self.config.get("project", "")))

    def _run_sync(self, statement: str, limit: int) -> tuple[list[str], list[Any]]:
        client = self._client()
        job = client.query(statement)
        rows_iter = job.result(max_results=limit + 1)
        columns = [f.name for f in rows_iter.schema]
        raw = [tuple(row.values()) for row in rows_iter]
        return columns, raw

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        t0 = time.monotonic()
        try:
            columns, raw = await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, statement, limit), timeout=timeout_s
            )
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        return _result(columns, raw, limit, t0)

    async def probe(self) -> None:
        await self.query("SELECT 1", 1, 15.0)

    async def introspect(self) -> SchemaModel:
        dataset = str(self.config.get("dataset", "")).replace("`", "")
        result = await self.query(
            f"SELECT table_name, column_name, data_type "
            f"FROM `{dataset}`.INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY table_name, ordinal_position LIMIT 2000",
            2000,
            30.0,
        )
        tables: dict[str, TableInfo] = {}
        for table, column, dtype in result.rows:
            entry = tables.setdefault(str(table), TableInfo(name=str(table)))
            entry.columns.append(ColumnInfo(str(column), str(dtype)))
        return SchemaModel(tables=list(tables.values())[:INTROSPECT_TABLE_CAP])
