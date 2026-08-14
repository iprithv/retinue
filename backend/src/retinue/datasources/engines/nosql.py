"""NoSQL engine adapters (§30.3 NoSQL equivalents).

No SQL guard applies here, so each adapter enforces its own read-only
operation allowlist: find/aggregate/count for document stores, a read-command
whitelist for Redis, search/count for the search engines, read transactions
for Neo4j, SELECT-shaped CQL for Cassandra. Anything else is rejected before
it touches the driver.
"""

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any, ClassVar

import orjson

from retinue.datasources.base import (
    ColumnInfo,
    DataSourceError,
    QueryResult,
    SchemaModel,
    TableInfo,
    clip_rows,
    jsonable,
)

if TYPE_CHECKING:
    from retinue.datasources.registry import EngineInfo


def _parse_json_query(statement: str) -> dict[str, Any]:
    try:
        parsed = orjson.loads(statement)
    except orjson.JSONDecodeError as error:
        raise DataSourceError(
            "this engine takes a JSON query document, not SQL — see the engine notes"
        ) from error
    if not isinstance(parsed, dict):
        raise DataSourceError("the query must be a JSON object")
    return parsed


def _docs_to_result(docs: list[dict[str, Any]], limit: int, t0: float) -> QueryResult:
    columns: list[str] = []
    for doc in docs:
        for key in doc:
            if key not in columns:
                columns.append(key)
    columns = columns[:40]
    raw = [tuple(doc.get(c) for c in columns) for doc in docs]
    rows, truncated = clip_rows(columns, raw, limit)
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated or len(docs) > limit,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


class _Base:
    def __init__(self, engine: "EngineInfo", config: dict[str, Any], secrets: dict[str, Any]):
        self.engine = engine
        self.config = config
        self.secrets = secrets

    async def close(self) -> None:
        return


# -- MongoDB ---------------------------------------------------------------------------


class MongoAdapter(_Base):
    _FORBIDDEN_STAGES: ClassVar[set[str]] = {"$out", "$merge"}

    def _client(self) -> Any:
        from motor.motor_asyncio import AsyncIOMotorClient

        uri = self.secrets.get("uri") or self.config.get("uri")
        if uri:
            return AsyncIOMotorClient(str(uri), serverSelectionTimeoutMS=8000)
        host = str(self.config.get("host", "localhost"))
        port = int(self.config.get("port") or 27017)
        user = self.config.get("user")
        password = self.secrets.get("password")
        if user:
            return AsyncIOMotorClient(
                host=host,
                port=port,
                username=str(user),
                password=str(password or ""),
                serverSelectionTimeoutMS=8000,
            )
        return AsyncIOMotorClient(host=host, port=port, serverSelectionTimeoutMS=8000)

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        spec = _parse_json_query(statement)
        collection_name = spec.get("collection")
        if not isinstance(collection_name, str):
            raise DataSourceError('the query needs a "collection" key')
        t0 = time.monotonic()
        client = self._client()
        try:
            db = client[str(self.config.get("database", ""))]
            coll = db[collection_name]
            if "pipeline" in spec:
                pipeline = spec["pipeline"]
                if not isinstance(pipeline, list):
                    raise DataSourceError('"pipeline" must be a list of stages')
                for stage in pipeline:
                    if isinstance(stage, dict) and self._FORBIDDEN_STAGES & set(stage):
                        raise DataSourceError("$out/$merge stages are not allowed (read-only)")
                cursor = coll.aggregate(pipeline)
                docs = await asyncio.wait_for(cursor.to_list(length=limit + 1), timeout=timeout_s)
            elif spec.get("count"):
                n = await asyncio.wait_for(
                    coll.count_documents(spec.get("filter") or {}), timeout=timeout_s
                )
                return QueryResult(columns=["count"], rows=[[n]], row_count=1)
            else:
                cursor = coll.find(spec.get("filter") or {}, spec.get("projection"))
                docs = await asyncio.wait_for(cursor.to_list(length=limit + 1), timeout=timeout_s)
            docs = [dict(jsonable(d)) for d in docs]
            return _docs_to_result(docs, limit, t0)
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            client.close()

    async def probe(self) -> None:
        client = self._client()
        try:
            await asyncio.wait_for(client.admin.command("ping"), timeout=10)
        except Exception as error:
            raise DataSourceError(f"ping failed: {str(error)[:300]}") from error
        finally:
            client.close()

    async def introspect(self) -> SchemaModel:
        client = self._client()
        try:
            db = client[str(self.config.get("database", ""))]
            names = await asyncio.wait_for(db.list_collection_names(), timeout=15)
            return SchemaModel(
                tables=[TableInfo(name=n) for n in sorted(names)[:200]], note="collections"
            )
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            client.close()

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        return await self.query(
            orjson.dumps({"collection": table, "filter": {}}).decode(), n or 3, timeout_s
        )


# -- Redis ------------------------------------------------------------------------------


class RedisAdapter(_Base):
    READ_COMMANDS: ClassVar[set[str]] = {
        "GET", "MGET", "KEYS", "SCAN", "HGETALL", "HGET", "HKEYS", "LRANGE",
        "SMEMBERS", "ZRANGE", "ZRANGEBYSCORE", "TYPE", "TTL", "EXISTS",
        "STRLEN", "LLEN", "SCARD", "ZCARD", "HLEN", "INFO", "DBSIZE", "PING",
    }  # fmt: skip

    def _client(self) -> Any:
        import redis.asyncio as aioredis

        return aioredis.Redis(
            host=str(self.config.get("host", "localhost")),
            port=int(self.config.get("port") or 6379),
            db=int(self.config.get("db") or 0),
            username=self.config.get("user"),
            password=self.secrets.get("password"),
            socket_timeout=8,
            decode_responses=True,
        )

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        parts = statement.strip().split()
        if not parts:
            raise DataSourceError("empty command")
        command = parts[0].upper()
        if command not in self.READ_COMMANDS:
            raise DataSourceError(
                f"command {command} is not on the read-only allowlist "
                f"({', '.join(sorted(self.READ_COMMANDS))})"
            )
        t0 = time.monotonic()
        client = self._client()
        try:
            result = await asyncio.wait_for(client.execute_command(*parts), timeout=timeout_s)
        except TimeoutError as error:
            raise DataSourceError(f"command exceeded {timeout_s}s") from error
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            await client.aclose()
        value = jsonable(result)
        if isinstance(value, dict):
            rows = [[k, v] for k, v in list(value.items())[:limit]]
            return QueryResult(
                columns=["field", "value"],
                rows=rows,
                row_count=len(rows),
                truncated=len(value) > limit,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        if isinstance(value, list):
            rows = [[v] for v in value[:limit]]
            return QueryResult(
                columns=["value"],
                rows=rows,
                row_count=len(rows),
                truncated=len(value) > limit,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        return QueryResult(
            columns=["value"],
            rows=[[value]],
            row_count=1,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    async def probe(self) -> None:
        await self.query("PING", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        result = await self.query("SCAN 0 COUNT 100", 100, 10.0)
        keys = result.rows[1][0] if len(result.rows) > 1 else []
        names = keys if isinstance(keys, list) else []
        return SchemaModel(
            tables=[TableInfo(name=str(k)) for k in names[:100]], note="key sample (SCAN)"
        )

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        return await self.query(f"GET {table}", 1, timeout_s)


# -- Cassandra / ScyllaDB ------------------------------------------------------------------


class CassandraAdapter(_Base):
    _SELECT_RE = re.compile(r"^\s*SELECT\s", re.IGNORECASE)

    def _run_sync(self, statement: str, limit: int) -> tuple[list[str], list[Any]]:
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster

        auth = None
        if self.config.get("user"):
            auth = PlainTextAuthProvider(
                username=str(self.config["user"]),
                password=str(self.secrets.get("password") or ""),
            )
        cluster = Cluster(
            [str(self.config.get("host", ""))],
            port=int(self.config.get("port") or 9042),
            auth_provider=auth,
        )
        try:
            session = cluster.connect(str(self.config.get("keyspace", "")))
            rows = list(session.execute(statement, timeout=20))[: limit + 1]
            columns = list(rows[0]._fields) if rows else []
            return columns, [tuple(r) for r in rows]
        finally:
            cluster.shutdown()

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        if ";" in statement.strip().rstrip(";"):
            raise DataSourceError("exactly one statement is allowed")
        if not self._SELECT_RE.match(statement):
            raise DataSourceError("only SELECT is allowed on this source (read-only)")
        t0 = time.monotonic()
        try:
            columns, raw = await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, statement, limit), timeout=timeout_s + 15
            )
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        rows, truncated = clip_rows(columns, raw, limit)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    async def probe(self) -> None:
        await self.query("SELECT release_version FROM system.local", 1, 15.0)

    async def introspect(self) -> SchemaModel:
        keyspace = str(self.config.get("keyspace", ""))
        result = await self.query(
            "SELECT table_name, column_name, type FROM system_schema.columns "
            f"WHERE keyspace_name = '{keyspace.replace(chr(39), '')}'",
            2000,
            20.0,
        )
        tables: dict[str, TableInfo] = {}
        for table, column, dtype in result.rows:
            entry = tables.setdefault(str(table), TableInfo(name=str(table)))
            entry.columns.append(ColumnInfo(str(column), str(dtype)))
        return SchemaModel(tables=list(tables.values())[:200])

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise DataSourceError(f"invalid table name {table!r}")
        return await self.query(f"SELECT * FROM {table} LIMIT {min(n or 3, 20)}", n or 3, timeout_s)


# -- DynamoDB -------------------------------------------------------------------------------


class DynamoAdapter(_Base):
    _ALLOWED: ClassVar[set[str]] = {"scan", "query", "get_item", "describe_table"}

    def _session(self) -> Any:
        import aioboto3

        return aioboto3.Session(
            aws_access_key_id=self.secrets.get("aws_access_key_id"),
            aws_secret_access_key=self.secrets.get("aws_secret_access_key"),
            region_name=str(self.config.get("region", "us-east-1")),
        )

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.config.get("endpoint_url"):
            kwargs["endpoint_url"] = str(self.config["endpoint_url"])
        return kwargs

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        spec = _parse_json_query(statement)
        operation = str(spec.pop("operation", "scan")).lower()
        table = spec.pop("table", None)
        if operation not in self._ALLOWED:
            raise DataSourceError(
                f"operation {operation!r} is not allowed (read-only: {sorted(self._ALLOWED)})"
            )
        if not table:
            raise DataSourceError('the query needs a "table" key')
        t0 = time.monotonic()
        session = self._session()
        try:
            async with session.client("dynamodb", **self._client_kwargs()) as client:
                method = getattr(client, operation)
                spec["TableName"] = str(table)
                if operation in ("scan", "query"):
                    spec.setdefault("Limit", min(limit, 100))
                response = await asyncio.wait_for(method(**spec), timeout=timeout_s)
        except TimeoutError as error:
            raise DataSourceError(f"operation exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        items = response.get("Items") or ([response["Item"]] if response.get("Item") else [])
        docs = [dict(jsonable(i)) for i in items]
        return _docs_to_result(docs, limit, t0)

    async def probe(self) -> None:
        session = self._session()
        try:
            async with session.client("dynamodb", **self._client_kwargs()) as client:
                await asyncio.wait_for(client.list_tables(Limit=1), timeout=10)
        except Exception as error:
            raise DataSourceError(f"list_tables failed: {str(error)[:300]}") from error

    async def introspect(self) -> SchemaModel:
        session = self._session()
        try:
            async with session.client("dynamodb", **self._client_kwargs()) as client:
                response = await asyncio.wait_for(client.list_tables(Limit=100), timeout=15)
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        return SchemaModel(tables=[TableInfo(name=n) for n in response.get("TableNames", [])])

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        return await self.query(
            orjson.dumps({"table": table, "operation": "scan", "Limit": min(n or 3, 20)}).decode(),
            n or 3,
            timeout_s,
        )


# -- Elasticsearch / OpenSearch ---------------------------------------------------------------


class ElasticAdapter(_Base):
    def _client(self) -> Any:
        from elasticsearch import AsyncElasticsearch

        kwargs: dict[str, Any] = {"request_timeout": 15}
        if self.secrets.get("api_key"):
            kwargs["api_key"] = str(self.secrets["api_key"])
        elif self.config.get("user"):
            kwargs["basic_auth"] = (
                str(self.config["user"]),
                str(self.secrets.get("password") or ""),
            )
        return AsyncElasticsearch(
            str(self.secrets.get("url") or self.config.get("url", "")), **kwargs
        )

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        spec = _parse_json_query(statement)
        index = spec.get("index")
        if not isinstance(index, str):
            raise DataSourceError('the query needs an "index" key')
        t0 = time.monotonic()
        client = self._client()
        try:
            if spec.get("count"):
                response = await asyncio.wait_for(
                    client.count(index=index, query=spec.get("query")), timeout=timeout_s
                )
                return QueryResult(columns=["count"], rows=[[response["count"]]], row_count=1)
            response = await asyncio.wait_for(
                client.search(
                    index=index,
                    query=spec.get("query") or {"match_all": {}},
                    size=min(int(spec.get("size", limit)), limit),
                ),
                timeout=timeout_s,
            )
        except TimeoutError as error:
            raise DataSourceError(f"search exceeded {timeout_s}s") from error
        except DataSourceError:
            raise
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            await client.close()
        docs = [
            {"_id": h.get("_id"), "_score": h.get("_score"), **(h.get("_source") or {})}
            for h in response["hits"]["hits"]
        ]
        return _docs_to_result([dict(jsonable(d)) for d in docs], limit, t0)

    async def probe(self) -> None:
        client = self._client()
        try:
            await asyncio.wait_for(client.info(), timeout=10)
        except Exception as error:
            raise DataSourceError(f"info failed: {str(error)[:300]}") from error
        finally:
            await client.close()

    async def introspect(self) -> SchemaModel:
        client = self._client()
        try:
            indices = await asyncio.wait_for(client.cat.indices(format="json"), timeout=15)
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            await client.close()
        return SchemaModel(
            tables=[
                TableInfo(
                    name=str(i.get("index")),
                    row_estimate=int(i.get("docs.count") or 0),
                )
                for i in indices
                if not str(i.get("index", "")).startswith(".")
            ][:200],
            note="indices",
        )

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        return await self.query(
            orjson.dumps({"index": table, "size": min(n or 3, 20)}).decode(), n or 3, timeout_s
        )


class OpenSearchAdapter(ElasticAdapter):
    def _client(self) -> Any:
        from opensearchpy import AsyncOpenSearch

        kwargs: dict[str, Any] = {"timeout": 15}
        if self.config.get("user"):
            kwargs["http_auth"] = (
                str(self.config["user"]),
                str(self.secrets.get("password") or ""),
            )
        return AsyncOpenSearch(
            hosts=[str(self.secrets.get("url") or self.config.get("url", ""))], **kwargs
        )


# -- Neo4j -----------------------------------------------------------------------------------


class Neo4jAdapter(_Base):
    def _driver(self) -> Any:
        from neo4j import AsyncGraphDatabase

        return AsyncGraphDatabase.driver(
            str(self.secrets.get("uri") or self.config.get("uri", "")),
            auth=(str(self.config.get("user", "")), str(self.secrets.get("password") or "")),
        )

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        t0 = time.monotonic()
        driver = self._driver()
        try:
            async with driver.session(
                database=str(self.config.get("database", "neo4j"))
            ) as session:

                async def _read(tx: Any) -> tuple[list[str], list[Any]]:
                    result = await tx.run(statement)
                    records = [rec async for rec in result]
                    records = records[: limit + 1]
                    columns = list(records[0].keys()) if records else []
                    return columns, [tuple(rec.values()) for rec in records]

                # execute_read: the server rejects writes inside a read transaction
                columns, raw = await asyncio.wait_for(
                    session.execute_read(_read), timeout=timeout_s
                )
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        finally:
            await driver.close()
        rows, truncated = clip_rows(columns, raw, limit)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    async def probe(self) -> None:
        await self.query("RETURN 1", 1, 10.0)

    async def introspect(self) -> SchemaModel:
        labels = await self.query("CALL db.labels()", 200, 15.0)
        return SchemaModel(
            tables=[TableInfo(name=str(r[0])) for r in labels.rows], note="node labels"
        )

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise DataSourceError(f"invalid label {table!r}")
        return await self.query(
            f"MATCH (n:{table}) RETURN n LIMIT {min(n or 3, 20)}", n or 3, timeout_s
        )


# -- InfluxDB ----------------------------------------------------------------------------------


class InfluxAdapter(_Base):
    _WRITE_RE = re.compile(r"(?:\bto\s*\(|experimental\.to|\bdelete\s*\()", re.IGNORECASE)

    def _assert_read_only(self, statement: str) -> None:
        if self._WRITE_RE.search(statement):
            raise DataSourceError(
                "Flux write/delete functions (to, experimental.to) are not allowed (read-only)"
            )

    def _run_sync(self, statement: str, limit: int) -> list[dict[str, Any]]:
        from influxdb_client import InfluxDBClient

        with InfluxDBClient(
            url=str(self.config.get("url", "")),
            token=str(self.secrets.get("token") or ""),
            org=str(self.config.get("org", "")),
            timeout=15_000,
        ) as client:
            tables = client.query_api().query(statement)
            docs: list[dict[str, Any]] = []
            for table in tables:
                for record in table.records:
                    docs.append(dict(jsonable(record.values)))
                    if len(docs) > limit:
                        return docs
            return docs

    async def query(self, statement: str, limit: int, timeout_s: float) -> QueryResult:
        self._assert_read_only(statement)
        t0 = time.monotonic()
        try:
            docs = await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, statement, limit), timeout=timeout_s + 5
            )
        except TimeoutError as error:
            raise DataSourceError(f"query exceeded {timeout_s}s") from error
        except Exception as error:
            raise DataSourceError(str(error)[:500]) from error
        return _docs_to_result(docs, limit, t0)

    async def probe(self) -> None:
        def _ping() -> bool:
            from influxdb_client import InfluxDBClient

            with InfluxDBClient(
                url=str(self.config.get("url", "")),
                token=str(self.secrets.get("token") or ""),
                org=str(self.config.get("org", "")),
            ) as client:
                return bool(client.ping())

        ok = await asyncio.to_thread(_ping)
        if not ok:
            raise DataSourceError("ping failed")

    async def introspect(self) -> SchemaModel:
        docs = await self.query("buckets() |> limit(n: 100)", 100, 15.0)
        names = (
            [row[docs.columns.index("name")] for row in docs.rows] if "name" in docs.columns else []
        )
        return SchemaModel(tables=[TableInfo(name=str(n)) for n in names], note="buckets")

    async def sample(self, table: str, n: int, timeout_s: float) -> QueryResult:
        flux = f'from(bucket: "{table}") |> range(start: -1h) |> limit(n: {min(n or 3, 20)})'
        return await self.query(flux, n or 3, timeout_s)
