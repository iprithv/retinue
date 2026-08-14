"""Connector install API (§28.6): the catalog is data; installing creates the
underlying McpServer or OpenApiAction row with encrypted secrets."""

from typing import Annotated, Any
from urllib.parse import urlsplit

import orjson
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from retinue.agents.connectors import CONNECTORS, connector_catalog, substitute_params
from retinue.agents.openapi_actions import ActionError, parse_operations
from retinue.api.schemas import ActionOut, McpServerOut
from retinue.api.tools import _action_out, _server_out
from retinue.core.deps import get_current_user
from retinue.core.errors import NOT_FOUND, VALIDATION_ERROR, AppError
from retinue.core.ids import uuid7
from retinue.core.state import get_state
from retinue.db.models import McpServer, OpenApiAction, User

router = APIRouter()


class ConnectorInstallRequest(BaseModel):
    secrets: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    name: str | None = Field(None, max_length=200)


class ConnectorInstalled(BaseModel):
    kind: str  # mcp | action
    mcp_server: McpServerOut | None = None
    action: ActionOut | None = None
    note: str | None = None


@router.get("/connectors")
async def list_connectors(
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    return connector_catalog()


@router.post("/connectors/{key}/install", status_code=201)
async def install_connector(
    key: str,
    body: ConnectorInstallRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ConnectorInstalled:
    state = get_state(request)
    connector = CONNECTORS.get(key)
    if connector is None:
        raise AppError(NOT_FOUND, f"unknown connector {key!r}", status=404)

    missing = [s.label for s in connector.secrets if s.required and not body.secrets.get(s.name)]
    missing += [p.label for p in connector.params if p.required and not body.params.get(p.name)]
    if missing:
        raise AppError(
            VALIDATION_ERROR, f"missing required fields: {', '.join(missing)}", status=422
        )
    name = body.name or connector.name

    if connector.kind in ("mcp-stdio", "mcp-http"):
        secrets = {k: v for k, v in body.secrets.items() if v}
        ciphertext = nonce = None
        if secrets:
            ciphertext, nonce = state.box.encrypt(orjson.dumps(secrets))
        spec: dict[str, Any] = (
            {
                "command": substitute_params(connector.command, body.params),
                "args": [substitute_params(a, body.params) for a in connector.args],
            }
            if connector.kind == "mcp-stdio"
            else {"url": substitute_params(connector.url, body.params)}
        )
        server = McpServer(
            id=uuid7(),
            owner_id=user.id,
            name=name,
            transport="stdio" if connector.kind == "mcp-stdio" else "http",
            spec=spec,
            secret_ciphertext=ciphertext,
            secret_nonce=nonce,
        )
        async with state.db.write_session() as session:
            session.add(server)
        note = f"requires {connector.runtime!r} on the server host" if connector.runtime else None
        return ConnectorInstalled(kind="mcp", mcp_server=_server_out(server), note=note)

    # openapi connector: substitute params across the whole spec, then parse
    raw = orjson.dumps(connector.spec).decode()
    raw = substitute_params(raw, body.params)
    spec = orjson.loads(raw)
    try:
        operations = parse_operations(spec)
    except ActionError as error:  # a bad param (e.g. malformed base_url) surfaces here
        raise AppError(VALIDATION_ERROR, str(error), status=422) from error

    allowlist = list(connector.host_allowlist)
    base_url = str((spec.get("servers") or [{}])[0].get("url", ""))
    host = urlsplit(base_url).hostname
    if host and host not in allowlist:
        allowlist.append(host)

    auth: dict[str, Any] = {}
    if connector.auth_type == "api_key_header":
        auth = {
            "type": "api_key_header",
            "header": connector.auth_header or "Authorization",
            "key": body.secrets.get("key", ""),
        }
    elif connector.auth_type == "bearer":
        auth = {"type": "bearer", "token": body.secrets.get("token", "")}
    elif connector.auth_type == "basic":
        auth = {
            "type": "basic",
            "user": body.secrets.get("user", ""),
            "password": body.secrets.get("password", ""),
        }

    ciphertext = nonce = None
    if auth:
        ciphertext, nonce = state.box.encrypt(orjson.dumps(auth))
    action = OpenApiAction(
        id=uuid7(),
        owner_id=user.id,
        name=name,
        spec={
            "openapi": spec.get("openapi"),
            "info": spec.get("info", {}),
            "_operations": operations,
            "_auth_type": connector.auth_type or "none",
            "_connector": connector.key,
        },
        auth_ciphertext=ciphertext,
        auth_nonce=nonce,
        host_allowlist=allowlist,
    )
    async with state.db.write_session() as session:
        session.add(action)
    return ConnectorInstalled(kind="action", action=_action_out(action))
