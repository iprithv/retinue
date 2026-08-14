"""Engine catalog (§30.2): 25+ engines, one protocol, lazy-imported drivers.

Wire-compatible engines share adapters: the Postgres family (CockroachDB,
TimescaleDB, QuestDB, Redshift) rides asyncpg; the MySQL family (MariaDB,
TiDB, StarRocks, Doris) rides aiomysql. Managed skins (Supabase/Neon → postgres,
PlanetScale → mysql, MemoryDB → redis, …) map to their engine keys.

Driver packages are optional extras — `available()` reports what is installed
and the exact `pip install retinue[<extra>]` that unlocks the rest.
"""

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any

from retinue.datasources.base import DataSourceError


@dataclass(slots=True, frozen=True)
class ConfigField:
    name: str
    required: bool = False
    default: Any = None
    hint: str = ""


@dataclass(slots=True, frozen=True)
class EngineInfo:
    key: str
    label: str
    category: str  # relational|warehouse|olap|document|kv|wide-column|search|timeseries|graph|file
    module: str  # retinue.datasources.engines.<module>
    cls: str
    driver: str  # importable module that must exist
    extra: str  # pip extra that provides the driver ("" = core install)
    dialect: str | None = None  # sqlglot dialect for the SQL guard
    default_port: int | None = None
    config_fields: tuple[ConfigField, ...] = ()
    secret_fields: tuple[str, ...] = ()
    file_based: bool = False  # reads server-local files → admin-only
    query_language: str = "sql"  # sql|json|command|cql|cypher|flux
    notes: str = ""


def _hostport(*extra: ConfigField) -> tuple[ConfigField, ...]:
    return (
        ConfigField("host", required=True),
        ConfigField("port"),
        ConfigField("database", required=True),
        ConfigField("user", required=True),
        *extra,
    )


_SQL = "retinue.datasources.engines.sql"
_NOSQL = "retinue.datasources.engines.nosql"

ENGINES: dict[str, EngineInfo] = {
    engine.key: engine
    for engine in [
        # -- file-based (admin-only: they read server-local paths) ----------------
        EngineInfo(
            key="sqlite",
            label="SQLite (file)",
            category="file",
            module=_SQL,
            cls="SqliteAdapter",
            driver="aiosqlite",
            extra="",
            dialect="sqlite",
            config_fields=(ConfigField("path", required=True, hint="server-local .db file"),),
            file_based=True,
        ),
        EngineInfo(
            key="duckdb",
            label="DuckDB (file)",
            category="file",
            module=_SQL,
            cls="DuckdbAdapter",
            driver="duckdb",
            extra="duckdb",
            dialect="duckdb",
            config_fields=(ConfigField("path", required=True, hint=".duckdb file or :memory:"),),
            file_based=True,
        ),
        # -- Postgres wire family --------------------------------------------------
        EngineInfo(
            key="postgres",
            label="PostgreSQL",
            category="relational",
            module=_SQL,
            cls="PostgresAdapter",
            driver="asyncpg",
            extra="postgres",
            dialect="postgres",
            default_port=5432,
            config_fields=_hostport(ConfigField("sslmode", default="prefer")),
            secret_fields=("password",),
            notes="also: Supabase, Neon, RDS/Aurora Postgres, AlloyDB",
        ),
        EngineInfo(
            key="cockroachdb",
            label="CockroachDB",
            category="relational",
            module=_SQL,
            cls="PostgresAdapter",
            driver="asyncpg",
            extra="postgres",
            dialect="postgres",
            default_port=26257,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="timescaledb",
            label="TimescaleDB",
            category="timeseries",
            module=_SQL,
            cls="PostgresAdapter",
            driver="asyncpg",
            extra="postgres",
            dialect="postgres",
            default_port=5432,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="questdb",
            label="QuestDB",
            category="timeseries",
            module=_SQL,
            cls="PostgresAdapter",
            driver="asyncpg",
            extra="postgres",
            dialect="postgres",
            default_port=8812,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="redshift",
            label="Amazon Redshift",
            category="warehouse",
            module=_SQL,
            cls="PostgresAdapter",
            driver="asyncpg",
            extra="postgres",
            dialect="redshift",
            default_port=5439,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        # -- MySQL wire family -------------------------------------------------------
        EngineInfo(
            key="mysql",
            label="MySQL",
            category="relational",
            module=_SQL,
            cls="MysqlAdapter",
            driver="aiomysql",
            extra="mysql",
            dialect="mysql",
            default_port=3306,
            config_fields=_hostport(),
            secret_fields=("password",),
            notes="also: PlanetScale, RDS/Aurora MySQL",
        ),
        EngineInfo(
            key="mariadb",
            label="MariaDB",
            category="relational",
            module=_SQL,
            cls="MysqlAdapter",
            driver="aiomysql",
            extra="mysql",
            dialect="mysql",
            default_port=3306,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="tidb",
            label="TiDB",
            category="relational",
            module=_SQL,
            cls="MysqlAdapter",
            driver="aiomysql",
            extra="mysql",
            dialect="mysql",
            default_port=4000,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="starrocks",
            label="StarRocks",
            category="olap",
            module=_SQL,
            cls="MysqlAdapter",
            driver="aiomysql",
            extra="mysql",
            dialect="mysql",
            default_port=9030,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="doris",
            label="Apache Doris",
            category="olap",
            module=_SQL,
            cls="MysqlAdapter",
            driver="aiomysql",
            extra="mysql",
            dialect="mysql",
            default_port=9030,
            config_fields=_hostport(),
            secret_fields=("password",),
        ),
        # -- OLAP / query engines ------------------------------------------------------
        EngineInfo(
            key="clickhouse",
            label="ClickHouse",
            category="olap",
            module=_SQL,
            cls="ClickhouseAdapter",
            driver="clickhouse_connect",
            extra="clickhouse",
            dialect="clickhouse",
            default_port=8123,
            config_fields=_hostport(ConfigField("secure", default=False)),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="trino",
            label="Trino",
            category="olap",
            module=_SQL,
            cls="TrinoAdapter",
            driver="trino",
            extra="trino",
            dialect="trino",
            default_port=8080,
            config_fields=(
                ConfigField("host", required=True),
                ConfigField("port"),
                ConfigField("catalog", required=True),
                ConfigField("schema", required=True),
                ConfigField("user", required=True),
                ConfigField("http_scheme", default="http"),
            ),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="presto",
            label="Presto",
            category="olap",
            module=_SQL,
            cls="TrinoAdapter",
            driver="trino",
            extra="trino",
            dialect="presto",
            default_port=8080,
            config_fields=(
                ConfigField("host", required=True),
                ConfigField("port"),
                ConfigField("catalog", required=True),
                ConfigField("schema", required=True),
                ConfigField("user", required=True),
                ConfigField("http_scheme", default="http"),
            ),
            secret_fields=("password",),
            notes="modern Presto clusters speak the Trino client protocol",
        ),
        # -- big commercial relational --------------------------------------------------
        EngineInfo(
            key="mssql",
            label="SQL Server",
            category="relational",
            module=_SQL,
            cls="MssqlAdapter",
            driver="pymssql",
            extra="mssql",
            dialect="tsql",
            default_port=1433,
            config_fields=_hostport(),
            secret_fields=("password",),
            notes="also: Azure SQL, Synapse dedicated pools",
        ),
        EngineInfo(
            key="oracle",
            label="Oracle",
            category="relational",
            module=_SQL,
            cls="OracleAdapter",
            driver="oracledb",
            extra="oracle",
            dialect="oracle",
            default_port=1521,
            config_fields=(
                ConfigField("host", required=True),
                ConfigField("port"),
                ConfigField("service_name", required=True),
                ConfigField("user", required=True),
            ),
            secret_fields=("password",),
        ),
        # -- cloud warehouses --------------------------------------------------------------
        EngineInfo(
            key="snowflake",
            label="Snowflake",
            category="warehouse",
            module=_SQL,
            cls="SnowflakeAdapter",
            driver="snowflake.connector",
            extra="snowflake",
            dialect="snowflake",
            config_fields=(
                ConfigField("account", required=True, hint="e.g. xy12345.eu-west-1"),
                ConfigField("database", required=True),
                ConfigField("schema", default="PUBLIC"),
                ConfigField("warehouse"),
                ConfigField("user", required=True),
            ),
            secret_fields=("password",),
        ),
        EngineInfo(
            key="bigquery",
            label="Google BigQuery",
            category="warehouse",
            module=_SQL,
            cls="BigQueryAdapter",
            driver="google.cloud.bigquery",
            extra="bigquery",
            dialect="bigquery",
            config_fields=(
                ConfigField("project", required=True),
                ConfigField("dataset", required=True),
            ),
            secret_fields=("service_account_json",),
        ),
        EngineInfo(
            key="databricks",
            label="Databricks SQL",
            category="warehouse",
            module=_SQL,
            cls="DatabricksAdapter",
            driver="databricks.sql",
            extra="databricks",
            dialect="databricks",
            config_fields=(
                ConfigField("server_hostname", required=True),
                ConfigField("http_path", required=True, hint="/sql/1.0/warehouses/…"),
                ConfigField("catalog"),
                ConfigField("schema"),
            ),
            secret_fields=("access_token",),
        ),
        # -- document / KV / wide-column ------------------------------------------------------
        EngineInfo(
            key="mongodb",
            label="MongoDB",
            category="document",
            module=_NOSQL,
            cls="MongoAdapter",
            driver="motor",
            extra="mongodb",
            default_port=27017,
            config_fields=(
                ConfigField("uri", hint="mongodb://… (overrides host/port)"),
                ConfigField("host"),
                ConfigField("port"),
                ConfigField("database", required=True),
                ConfigField("user"),
            ),
            secret_fields=("password",),
            query_language="json",
            notes=(
                'query: {"collection": "users", "filter": {...}} '
                'or {"collection": "...", "pipeline": [...]}'
            ),
        ),
        EngineInfo(
            key="redis",
            label="Redis / Valkey",
            category="kv",
            module=_NOSQL,
            cls="RedisAdapter",
            driver="redis",
            extra="redis",
            default_port=6379,
            config_fields=(
                ConfigField("host", required=True),
                ConfigField("port"),
                ConfigField("db", default=0),
                ConfigField("user"),
            ),
            secret_fields=("password",),
            query_language="command",
            notes=(
                "read-only command allowlist: GET, MGET, KEYS, SCAN, HGETALL, "
                "LRANGE, SMEMBERS, ZRANGE, TYPE, TTL, EXISTS, INFO, ..."
            ),
        ),
        EngineInfo(
            key="cassandra",
            label="Cassandra / ScyllaDB",
            category="wide-column",
            module=_NOSQL,
            cls="CassandraAdapter",
            driver="cassandra",
            extra="cassandra",
            default_port=9042,
            config_fields=(
                ConfigField("host", required=True),
                ConfigField("port"),
                ConfigField("keyspace", required=True),
                ConfigField("user"),
            ),
            secret_fields=("password",),
            query_language="cql",
        ),
        EngineInfo(
            key="dynamodb",
            label="Amazon DynamoDB",
            category="kv",
            module=_NOSQL,
            cls="DynamoAdapter",
            driver="aioboto3",
            extra="dynamodb",
            config_fields=(
                ConfigField("region", required=True),
                ConfigField("endpoint_url", hint="for DynamoDB Local"),
            ),
            secret_fields=("aws_access_key_id", "aws_secret_access_key"),
            query_language="json",
            notes='query: {"table": "...", "operation": "scan"|"query", ...boto3 kwargs}',
        ),
        # -- search --------------------------------------------------------------------------
        EngineInfo(
            key="elasticsearch",
            label="Elasticsearch",
            category="search",
            module=_NOSQL,
            cls="ElasticAdapter",
            driver="elasticsearch",
            extra="elasticsearch",
            default_port=9200,
            config_fields=(
                ConfigField("url", required=True, hint="https://host:9200"),
                ConfigField("user"),
            ),
            secret_fields=("password", "api_key"),
            query_language="json",
            notes='query: {"index": "...", "query": {...}, "size": 10} — search/count only',
        ),
        EngineInfo(
            key="opensearch",
            label="OpenSearch",
            category="search",
            module=_NOSQL,
            cls="OpenSearchAdapter",
            driver="opensearchpy",
            extra="opensearch",
            default_port=9200,
            config_fields=(
                ConfigField("url", required=True),
                ConfigField("user"),
            ),
            secret_fields=("password",),
            query_language="json",
        ),
        # -- graph / timeseries -----------------------------------------------------------------
        EngineInfo(
            key="neo4j",
            label="Neo4j",
            category="graph",
            module=_NOSQL,
            cls="Neo4jAdapter",
            driver="neo4j",
            extra="neo4j",
            default_port=7687,
            config_fields=(
                ConfigField("uri", required=True, hint="bolt://host:7687"),
                ConfigField("database", default="neo4j"),
                ConfigField("user", required=True),
            ),
            secret_fields=("password",),
            query_language="cypher",
            notes="Cypher runs in a read transaction — writes are rejected by the server",
        ),
        EngineInfo(
            key="influxdb",
            label="InfluxDB",
            category="timeseries",
            module=_NOSQL,
            cls="InfluxAdapter",
            driver="influxdb_client",
            extra="influxdb",
            default_port=8086,
            config_fields=(
                ConfigField("url", required=True),
                ConfigField("org", required=True),
            ),
            secret_fields=("token",),
            query_language="flux",
        ),
    ]
}


def engine_info(key: str) -> EngineInfo:
    engine = ENGINES.get(key)
    if engine is None:
        raise DataSourceError(f"unknown engine {key!r}")
    return engine


def driver_available(engine: EngineInfo) -> bool:
    top = engine.driver.split(".")[0]
    return importlib.util.find_spec(top) is not None


def make_adapter(engine: EngineInfo, config: dict[str, Any], secrets: dict[str, Any]) -> Any:
    if not driver_available(engine):
        raise DataSourceError(
            f"the {engine.label} driver is not installed — pip install 'retinue[{engine.extra}]'"
            if engine.extra
            else f"the {engine.label} driver is missing from this install"
        )
    module = importlib.import_module(engine.module)
    adapter_cls = getattr(module, engine.cls)
    return adapter_cls(engine, config, secrets)


def catalog() -> list[dict[str, Any]]:
    """Engine catalog for the API/UI, with live driver availability."""
    return [
        {
            "key": e.key,
            "label": e.label,
            "category": e.category,
            "dialect": e.dialect,
            "query_language": e.query_language,
            "default_port": e.default_port,
            "config_fields": [
                {"name": f.name, "required": f.required, "default": f.default, "hint": f.hint}
                for f in e.config_fields
            ],
            "secret_fields": list(e.secret_fields),
            "file_based": e.file_based,
            "available": driver_available(e),
            "install_extra": e.extra,
            "notes": e.notes,
        }
        for e in ENGINES.values()
    ]
