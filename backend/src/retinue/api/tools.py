"""MCP servers + OpenAPI actions management (§18), and agent preflight (§30.7
v0.2 scope: model reachability, MCP health, collections, sandbox)."""

import uuid
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from retinue.agents.openapi_actions import ActionError, parse_operations
from retinue.api.schemas import (
    ActionCreate,
    ActionOut,
    McpServerCreate,
    McpServerOut,
    McpServerPatch,
    McpToolOut,
)
from retinue.core.deps import get_current_user
from retinue.core.errors import NOT_FOUND, VALIDATION_ERROR, AppError
from retinue.core.ids import uuid7
from retinue.core.state import AppState, get_state
from retinue.db.models import AgentVersion, Collection, McpServer, OpenApiAction, User

router = APIRouter()


# -- MCP servers -----------------------------------------------------------------


def _server_out(server: McpServer) -> McpServerOut:
    return McpServerOut(
        id=server.id,
        name=server.name,
        transport=server.transport,
        spec=server.spec or {},
        has_secrets=server.secret_ciphertext is not None,
        enabled=server.enabled,
        org=server.owner_id is None,
        last_status=server.last_status,
        created_at=server.created_at,
    )


async def _visible_server(state: AppState, user: User, server_id: uuid.UUID) -> McpServer:
    async with state.db.read_session() as session:
        server = await session.get(McpServer, server_id)
    if server is None or (server.owner_id is not None and server.owner_id != user.id):
        raise AppError(NOT_FOUND, "MCP server not found", status=404)
    return server


@router.get("/mcp/servers")
async def list_servers(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[McpServerOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(McpServer).where(
                        (McpServer.owner_id == user.id) | (McpServer.owner_id.is_(None))
                    )
                )
            )
            .scalars()
            .all()
        )
    return [_server_out(s) for s in rows]


@router.post("/mcp/servers", status_code=201)
async def create_server(
    body: McpServerCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> McpServerOut:
    state = get_state(request)
    if body.org and user.role not in ("owner", "admin"):
        raise AppError(VALIDATION_ERROR, "only admins can create org-global servers", status=403)
    if body.transport == "stdio" and not body.command:
        raise AppError(VALIDATION_ERROR, "stdio transport requires a command", status=422)
    if body.transport == "http" and not body.url:
        raise AppError(VALIDATION_ERROR, "http transport requires a url", status=422)

    spec: dict[str, Any] = (
        {"command": body.command, "args": body.args}
        if body.transport == "stdio"
        else {"url": body.url}
    )
    secrets = body.env if body.transport == "stdio" else body.headers
    ciphertext = nonce = None
    if secrets:
        ciphertext, nonce = state.box.encrypt(orjson.dumps(secrets))

    server = McpServer(
        id=uuid7(),
        owner_id=None if body.org else user.id,
        name=body.name,
        transport=body.transport,
        spec=spec,
        secret_ciphertext=ciphertext,
        secret_nonce=nonce,
    )
    async with state.db.write_session() as session:
        session.add(server)
    return _server_out(server)


@router.patch("/mcp/servers/{server_id}")
async def patch_server(
    server_id: uuid.UUID,
    body: McpServerPatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> McpServerOut:
    state = get_state(request)
    await _visible_server(state, user, server_id)
    updates = body.model_dump(exclude_unset=True)
    async with state.db.write_session() as session:
        server = await session.get(McpServer, server_id)
        assert server is not None
        for key, value in updates.items():
            setattr(server, key, value)
        result = server
    await state.mcp.drop(server_id)  # force reconnect with new state
    return _server_out(result)


@router.delete("/mcp/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    await _visible_server(state, user, server_id)
    await state.mcp.drop(server_id)
    async with state.db.write_session() as session:
        server = await session.get(McpServer, server_id)
        if server is not None:
            await session.delete(server)


@router.post("/mcp/servers/{server_id}/test")
async def test_server(
    server_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    state = get_state(request)
    await _visible_server(state, user, server_id)
    return await state.mcp.test(server_id)


@router.get("/mcp/servers/{server_id}/tools")
async def server_tools(
    server_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[McpToolOut]:
    state = get_state(request)
    await _visible_server(state, user, server_id)
    try:
        tools = await state.mcp.list_tools(server_id)
    except Exception as exc:
        raise AppError(VALIDATION_ERROR, f"cannot reach server: {exc}", status=502) from exc
    return [
        McpToolOut(
            name=t["name"],
            description=t.get("description"),
            input_schema=t.get("input_schema") or {},
        )
        for t in tools
    ]


# -- OpenAPI actions -----------------------------------------------------------------


def _action_out(action: OpenApiAction) -> ActionOut:
    operations = (action.spec or {}).get("_operations", [])
    return ActionOut(
        id=action.id,
        name=action.name,
        operations=[
            {
                "name": op["name"],
                "method": op["method"],
                "path": op["path"],
                "summary": op.get("summary", ""),
            }
            for op in operations
        ],
        host_allowlist=[str(h) for h in (action.host_allowlist or [])],
        auth_type=(action.spec or {}).get("_auth_type", "none"),
        created_at=action.created_at,
    )


@router.get("/actions")
async def list_actions(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> list[ActionOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (await session.execute(select(OpenApiAction).where(OpenApiAction.owner_id == user.id)))
            .scalars()
            .all()
        )
    return [_action_out(a) for a in rows]


@router.post("/actions", status_code=201)
async def create_action(
    body: ActionCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ActionOut:
    state = get_state(request)
    try:
        operations = parse_operations(body.spec)
    except ActionError as exc:
        raise AppError(VALIDATION_ERROR, str(exc), status=422) from exc

    stored_spec = {
        "openapi": body.spec.get("openapi"),
        "info": body.spec.get("info", {}),
        "_operations": operations,
        "_auth_type": body.auth.get("type", "none"),
    }
    ciphertext = nonce = None
    if body.auth:
        ciphertext, nonce = state.box.encrypt(orjson.dumps(body.auth))

    action = OpenApiAction(
        id=uuid7(),
        owner_id=user.id,
        name=body.name,
        spec=stored_spec,
        auth_ciphertext=ciphertext,
        auth_nonce=nonce,
        host_allowlist=body.host_allowlist,
    )
    async with state.db.write_session() as session:
        session.add(action)
    return _action_out(action)


@router.delete("/actions/{action_id}", status_code=204)
async def delete_action(
    action_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    state = get_state(request)
    async with state.db.write_session() as session:
        action = await session.get(OpenApiAction, action_id)
        if action is None or action.owner_id != user.id:
            raise AppError(NOT_FOUND, "action not found", status=404)
        await session.delete(action)


# -- agent preflight (§30.7, v0.2 stages) ----------------------------------------------


@router.post("/agents/{agent_id}/preflight")
async def preflight(
    agent_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    from retinue.db.models import Agent

    state = get_state(request)
    async with state.db.read_session() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None or (
            agent.owner_id != user.id and agent.visibility not in ("org", "public")
        ):
            raise AppError(NOT_FOUND, "agent not found", status=404)
        version = (
            await session.get(AgentVersion, agent.current_version_id)
            if agent.current_version_id
            else None
        )
    if version is None:
        raise AppError(NOT_FOUND, "agent has no versions", status=404)

    items: list[dict[str, Any]] = []

    # model reachable / known
    info = state.registry.model_info(version.model, quiet=True)
    known = info.display_name != version.model or version.model.startswith("mock/")
    items.append(
        {
            "check": "model",
            "ok": True,
            "detail": f"{version.model} (context {info.context_window})"
            if known
            else f"{version.model} — not in the catalog; calls may still work",
        }
    )

    # collections exist
    for collection_id in version.collection_ids or []:
        try:
            cid = uuid.UUID(str(collection_id))
        except ValueError:
            items.append({"check": "collection", "ok": False, "detail": f"bad id {collection_id}"})
            continue
        async with state.db.read_session() as session:
            collection = await session.get(Collection, cid)
        items.append(
            {
                "check": "collection",
                "ok": collection is not None,
                "detail": collection.name if collection else f"missing collection {collection_id}",
            }
        )

    # MCP servers healthy + tools discovered
    for attachment in version.mcp_servers or []:
        server_id = str(attachment.get("server_id", ""))
        try:
            result = await state.mcp.test(uuid.UUID(server_id))
        except ValueError:
            result = {"ok": False, "error": f"bad server id {server_id}"}
        items.append(
            {
                "check": "mcp",
                "ok": bool(result.get("ok")),
                "detail": f"{result.get('tools', 0)} tools"
                if result.get("ok")
                else str(result.get("error", "unreachable"))[:200],
            }
        )

    # sandbox needed?
    tool_refs = {t.get("ref") for t in (version.tools or [])}
    if "code_exec" in tool_refs:
        items.append(
            {
                "check": "sandbox",
                "ok": state.sandbox.available(),
                "detail": state.sandbox.describe(),
            }
        )
    if "web_search" in tool_refs:
        configured = state.settings.tools.web_search.provider != "none"
        items.append(
            {
                "check": "web_search",
                "ok": configured,
                "detail": state.settings.tools.web_search.provider,
            }
        )

    return {"agent_id": str(agent_id), "ok": all(i["ok"] for i in items), "items": items}
