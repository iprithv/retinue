"""MCP client manager (§9.3): owns configured servers, lazy-connects on first
use, caches tool discovery, reconnects with backoff, namespaces tools as
`server.tool`. Every result is treated as untrusted input downstream.
"""

import asyncio
import uuid
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from retinue.db.models import McpServer

if TYPE_CHECKING:
    from retinue.core.crypto import SecretBox
    from retinue.db.session import Database

log = structlog.get_logger("retinue.mcp")

CONNECT_TIMEOUT_S = 15.0
CALL_TIMEOUT_S = 30.0


class McpError(Exception):
    pass


class _Connection:
    """A live server connection owned by a dedicated task.

    anyio cancel scopes (inside the SDK's stdio/http transports) must be
    entered and exited by the *same* task. The owner task enters the
    AsyncExitStack, parks on `close_requested`, and unwinds the stack itself;
    `drop()`/`shutdown()` from any other task just set the event and await
    `closed` — no cross-task scope exits, no leaked subprocesses.
    """

    def __init__(self) -> None:
        self.session: Any = None
        self.tools: list[dict[str, Any]] = []
        self.close_requested = asyncio.Event()
        self.closed = asyncio.Event()
        self.owner: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        self.close_requested.set()
        if self.owner is not None:
            await self.closed.wait()


class McpManager:
    def __init__(self, db: "Database", box: "SecretBox") -> None:
        self._db = db
        self._box = box
        self._connections: dict[uuid.UUID, _Connection] = {}
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    def _decrypt_secrets(self, server: McpServer) -> dict[str, str]:
        if not server.secret_ciphertext or not server.secret_nonce:
            return {}
        try:
            return dict(
                orjson.loads(self._box.decrypt(server.secret_ciphertext, server.secret_nonce))
            )
        except Exception:
            log.error("mcp_secret_decrypt_failed", server_id=str(server.id))
            return {}

    async def _load_server(self, server_id: uuid.UUID) -> McpServer:
        async with self._db.read_session() as session:
            server = await session.get(McpServer, server_id)
        if server is None:
            raise McpError("MCP server not found")
        if not server.enabled:
            raise McpError(f"MCP server {server.name!r} is disabled")
        return server

    async def _open_transport(
        self, stack: AsyncExitStack, server: McpServer, secrets: dict[str, str]
    ) -> Any:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if server.transport == "stdio":
            spec = server.spec or {}
            params = StdioServerParameters(
                command=str(spec.get("command", "")),
                args=[str(a) for a in spec.get("args", [])],
                env=secrets or None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif server.transport == "http":
            from mcp.client import streamable_http as _sh

            spec = server.spec or {}
            # encrypted headers ride a dedicated client; mcp 2.x pins its
            # own httpx flavour, so build the client from its namespace
            httpx2 = _sh.httpx2  # type: ignore[attr-defined]  # mcp pins its own httpx
            http_client = httpx2.AsyncClient(headers=secrets or None, timeout=30.0)
            stack.push_async_callback(http_client.aclose)
            read, write = await stack.enter_async_context(
                _sh.streamable_http_client(str(spec.get("url", "")), http_client=http_client)
            )
        else:
            raise McpError(f"unknown transport {server.transport!r}")
        return await stack.enter_async_context(ClientSession(read, write))

    async def _connect(self, server: McpServer) -> _Connection:
        conn = _Connection()
        secrets = self._decrypt_secrets(server)
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def owner() -> None:
            # enter AND exit the stack in this one task (anyio cancel scopes)
            try:
                async with AsyncExitStack() as stack:
                    try:
                        session = await self._open_transport(stack, server, secrets)
                        await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)
                        listing = await asyncio.wait_for(
                            session.list_tools(), timeout=CONNECT_TIMEOUT_S
                        )
                    except BaseException as exc:
                        if not ready.done():
                            ready.set_exception(exc)
                        return
                    conn.session = session
                    conn.tools = [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "input_schema": getattr(tool, "inputSchema", None)
                            or getattr(tool, "input_schema", None)
                            or {},
                        }
                        for tool in listing.tools
                    ]
                    ready.set_result(None)
                    await conn.close_requested.wait()
            except BaseException:
                if not ready.done():
                    ready.set_exception(McpError("connection task failed"))
                log.debug("mcp_owner_unwind_failed", server=server.name, exc_info=True)
            finally:
                conn.session = None
                conn.closed.set()

        conn.owner = asyncio.create_task(owner(), name=f"mcp-{server.name[:16]}")
        try:
            await ready
        except BaseException:
            await conn.aclose()
            raise
        return conn

    async def _get_connection(self, server_id: uuid.UUID) -> _Connection:
        lock = self._locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            conn = self._connections.get(server_id)
            if conn is not None and conn.session is not None:
                return conn
            server = await self._load_server(server_id)
            try:
                conn = await self._connect(server)
            except McpError:
                raise
            except Exception as exc:
                await self._record_status(server_id, {"ok": False, "error": str(exc)[:500]})
                raise McpError(f"cannot connect to {server.name!r}: {exc}") from exc
            self._connections[server_id] = conn
            await self._record_status(server_id, {"ok": True, "tools": len(conn.tools)})
            return conn

    async def _record_status(self, server_id: uuid.UUID, status: dict[str, Any]) -> None:
        try:
            async with self._db.write_session() as session:
                server = await session.get(McpServer, server_id)
                if server is not None:
                    server.last_status = status
        except Exception:
            log.exception("mcp_status_write_failed")

    async def list_tools(self, server_id: uuid.UUID) -> list[dict[str, Any]]:
        conn = await self._get_connection(server_id)
        return conn.tools

    async def call_tool(self, server_id: uuid.UUID, tool_name: str, args: dict[str, Any]) -> str:
        """Returns the tool result as text; failures raise McpError."""
        conn = await self._get_connection(server_id)
        try:
            result = await asyncio.wait_for(
                conn.session.call_tool(tool_name, args), timeout=CALL_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise McpError(f"tool {tool_name!r} timed out") from exc
        except Exception as exc:
            await self.drop(server_id)  # force reconnect next call
            raise McpError(f"tool {tool_name!r} failed: {exc}") from exc
        parts: list[str] = []
        for content in result.content:
            text = getattr(content, "text", None)
            if text:
                parts.append(text)
        if getattr(result, "isError", False):
            raise McpError("\n".join(parts) or f"tool {tool_name!r} reported an error")
        return "\n".join(parts)

    async def test(self, server_id: uuid.UUID) -> dict[str, Any]:
        try:
            tools = await self.list_tools(server_id)
            return {"ok": True, "tools": len(tools)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500]}

    async def drop(self, server_id: uuid.UUID) -> None:
        conn = self._connections.pop(server_id, None)
        if conn is not None:
            try:
                await asyncio.wait_for(conn.aclose(), timeout=10)
            except Exception:
                log.debug("mcp_close_failed", server_id=str(server_id))

    async def shutdown(self) -> None:
        for server_id in list(self._connections):
            await self.drop(server_id)
