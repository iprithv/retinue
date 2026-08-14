"""Agent tool runtime (§9.2): builds the tool surface for an agent version,
validates arguments against each tool's JSON Schema, and dispatches to
builtin | MCP | OpenAPI-action handlers.

Wire names are `<kind-safe>` OpenAI-style identifiers: builtins keep their
name; MCP tools are `mcp_<server-slug>__<tool>`; action operations are
`act_<action-slug>__<operationId>`. Results are data, never instructions.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from retinue.agents.tools import builtin as builtin_tools
from retinue.db.models import AgentVersion, McpServer, OpenApiAction, User

if TYPE_CHECKING:
    from retinue.core.state import AppState

log = structlog.get_logger("retinue.agents.runtime")

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("_", name)[:24].strip("_") or "x"


@dataclass(slots=True)
class ToolSpec:
    name: str  # wire name
    description: str
    parameters: dict[str, Any]
    mode: str  # auto|ask_user|disabled
    kind: str  # builtin|mcp|action
    ref: str  # builtin name | server_id | action_id
    extra: dict[str, Any] = field(default_factory=dict)  # mcp tool name / operation


@dataclass(slots=True)
class ToolOutcome:
    status: str  # ok|error|timeout|denied
    content: str


def validate_args(schema: dict[str, Any], args: Any) -> str | None:
    """Minimal JSON-Schema check: object shape + required keys + primitive
    types. Returns an error string (for the model to self-correct) or None."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required argument {key!r}"
    properties = schema.get("properties", {})
    checks: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, value in args.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        expected = prop.get("type")
        if isinstance(expected, str) and expected in checks:
            if expected == "integer" and isinstance(value, bool):
                return f"argument {key!r} must be an integer"
            if not isinstance(value, checks[expected]):
                return f"argument {key!r} must be of type {expected}"
    return None


class ToolExecutor:
    def __init__(self, state: "AppState", user: User, agent_version: AgentVersion) -> None:
        self.state = state
        self.user = user
        self.version = agent_version
        self.specs: dict[str, ToolSpec] = {}
        self._prepared = False

    async def prepare(self) -> None:
        """Resolve the tool surface. MCP discovery failures degrade to a
        missing server (logged), never a failed turn."""
        if self._prepared:
            return
        self._prepared = True

        for tool in self.version.tools or []:
            kind = tool.get("type")
            ref = str(tool.get("ref", ""))
            mode = (tool.get("config") or {}).get("mode", "auto")
            if mode == "disabled":
                continue
            if kind == "builtin":
                schema = builtin_tools.BUILTIN_SCHEMAS.get(ref)
                if schema is None:
                    log.warning("unknown_builtin_tool", ref=ref)
                    continue
                self.specs[ref] = ToolSpec(
                    name=ref,
                    description=schema["description"],
                    parameters=schema["parameters"],
                    mode=mode,
                    kind="builtin",
                    ref=ref,
                )
            elif kind == "action":
                await self._add_action(ref, mode)

        for attachment in self.version.mcp_servers or []:
            server_id = str(attachment.get("server_id", ""))
            allowlist = attachment.get("tool_allowlist") or None
            mode = attachment.get("mode", "auto")
            await self._add_mcp_server(server_id, allowlist, mode)

    async def _add_action(self, action_id: str, mode: str) -> None:
        try:
            aid = uuid.UUID(action_id)
        except ValueError:
            return
        async with self.state.db.read_session() as session:
            action = await session.get(OpenApiAction, aid)
        if action is None or action.owner_id != self.user.id:
            log.warning("action_not_found", action_id=action_id)
            return
        for operation in (action.spec or {}).get("_operations", []):
            wire = f"act_{_slug(action.name)}__{operation['name']}"[:64]
            self.specs[wire] = ToolSpec(
                name=wire,
                description=(operation.get("summary") or operation["name"])[:500],
                parameters=operation["parameters"],
                mode=mode,
                kind="action",
                ref=action_id,
                extra={"operation": operation},
            )

    async def _add_mcp_server(self, server_id: str, allowlist: list[str] | None, mode: str) -> None:
        try:
            sid = uuid.UUID(server_id)
        except ValueError:
            return
        async with self.state.db.read_session() as session:
            server = await session.get(McpServer, sid)
        if server is None or (server.owner_id is not None and server.owner_id != self.user.id):
            log.warning("mcp_server_not_found", server_id=server_id)
            return
        try:
            tools = await self.state.mcp.list_tools(sid)
        except Exception as exc:
            log.warning("mcp_discovery_failed", server=server.name, error=str(exc)[:200])
            return
        for tool in tools:
            if allowlist and tool["name"] not in allowlist:
                continue
            wire = f"mcp_{_slug(server.name)}__{_slug(tool['name'])}"[:64]
            self.specs[wire] = ToolSpec(
                name=wire,
                description=(tool.get("description") or tool["name"])[:500],
                parameters=tool.get("input_schema") or {"type": "object", "properties": {}},
                mode=mode,
                kind="mcp",
                ref=server_id,
                extra={"tool": tool["name"]},
            )

    def tool_defs(self) -> list[dict[str, Any]]:
        """OpenAI-shape function definitions, sorted for cache-stable bytes (§31.1)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in sorted(self.specs.values(), key=lambda s: s.name)
        ]

    def mode(self, name: str) -> str:
        spec = self.specs.get(name)
        return spec.mode if spec else "auto"

    async def dispatch(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        spec = self.specs.get(name)
        if spec is None:
            return ToolOutcome("error", f"unknown tool {name!r}")
        problem = validate_args(spec.parameters, args)
        if problem is not None:
            return ToolOutcome("error", f"invalid arguments: {problem}")
        try:
            if spec.kind == "builtin":
                return ToolOutcome("ok", await self._run_builtin(spec.ref, args))
            if spec.kind == "mcp":
                content = await self.state.mcp.call_tool(
                    uuid.UUID(spec.ref), spec.extra["tool"], args
                )
                return ToolOutcome("ok", content[:32_000])
            if spec.kind == "action":
                return ToolOutcome("ok", await self._run_action(spec, args))
            return ToolOutcome("error", f"unknown tool kind {spec.kind!r}")
        except Exception as exc:
            log.warning("tool_failed", tool=name, error=str(exc)[:300])
            return ToolOutcome("error", f"tool failed: {str(exc)[:500]}")

    async def _run_builtin(self, ref: str, args: dict[str, Any]) -> str:
        if ref == "web_search":
            return await builtin_tools.web_search(self.state, args)
        if ref == "file_read":
            return await builtin_tools.file_read(self.state, self.user, args)
        if ref == "file_search":
            return await builtin_tools.file_search(self.state, self.user, args)
        if ref == "code_exec":
            return await builtin_tools.code_exec(self.state, args)
        if ref == "db_sources":
            return await builtin_tools.db_sources(self.state, self.user)
        if ref == "db_schema":
            return await builtin_tools.db_schema(self.state, self.user, args)
        if ref == "db_query":
            return await builtin_tools.db_query(self.state, self.user, args)
        if ref == "db_sample":
            return await builtin_tools.db_sample(self.state, self.user, args)
        if ref == "web_fetch":
            return await builtin_tools.web_fetch(self.state, args)
        if ref == "image_gen":
            return await builtin_tools.image_gen(self.state, self.user, args)
        return f"error: builtin {ref!r} not implemented"

    async def _run_action(self, spec: ToolSpec, args: dict[str, Any]) -> str:
        from retinue.agents.openapi_actions import execute_operation

        async with self.state.db.read_session() as session:
            action = await session.get(OpenApiAction, uuid.UUID(spec.ref))
        if action is None:
            return "error: action no longer exists"
        auth: dict[str, Any] = {}
        if action.auth_ciphertext and action.auth_nonce:
            try:
                auth = dict(
                    orjson.loads(self.state.box.decrypt(action.auth_ciphertext, action.auth_nonce))
                )
            except Exception:
                return "error: action credentials cannot be decrypted"
        return await execute_operation(
            spec.extra["operation"],
            args,
            auth=auth,
            host_allowlist=[str(h) for h in (action.host_allowlist or [])],
        )
