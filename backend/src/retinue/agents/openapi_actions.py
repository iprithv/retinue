"""OpenAPI actions (§9.4): a pasted OpenAPI 3.x spec becomes tools.

Hard SSRF posture: DNS-resolve-then-deny private ranges unless the action's
explicit host allowlist covers the host; 10 s timeout; 1 MB response cap;
responses truncated for model context. Auth secrets live encrypted at rest.
"""

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

log = structlog.get_logger("retinue.actions")

MAX_OPERATIONS = 64
RESPONSE_CAP = 1024 * 1024  # 1 MB
MODEL_CAP = 16_000  # chars handed to the model
TIMEOUT_S = 10.0


class ActionError(Exception):
    pass


def parse_operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract operations: operationId → name, parameters/requestBody → schema."""
    if not str(spec.get("openapi", "")).startswith("3"):
        raise ActionError("only OpenAPI 3.x specs are supported")
    servers = spec.get("servers") or []
    base_url = str(servers[0].get("url", "")) if servers else ""
    operations: list[dict[str, Any]] = []
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or f"{method}_{path.strip('/').replace('/', '_')}"
            properties: dict[str, Any] = {}
            required: list[str] = []
            param_map: dict[str, str] = {}  # arg name -> in (path|query|header)
            for param in op.get("parameters") or []:
                if not isinstance(param, dict) or "name" not in param:
                    continue
                pname = str(param["name"])
                properties[pname] = param.get("schema") or {"type": "string"}
                if param.get("description"):
                    properties[pname]["description"] = param["description"]
                param_map[pname] = str(param.get("in", "query"))
                if param.get("required") or param.get("in") == "path":
                    required.append(pname)
            body_schema = None
            request_body = op.get("requestBody")
            if isinstance(request_body, dict):
                content = request_body.get("content") or {}
                json_body = content.get("application/json") or {}
                body_schema = json_body.get("schema")
                if body_schema:
                    properties["body"] = body_schema
                    if request_body.get("required"):
                        required.append("body")
            operations.append(
                {
                    "name": str(name)[:64],
                    "method": method.upper(),
                    "path": str(path),
                    "base_url": base_url,
                    "summary": op.get("summary") or op.get("description") or "",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                    "param_map": param_map,
                    "has_body": body_schema is not None,
                }
            )
            if len(operations) >= MAX_OPERATIONS:
                return operations
    if not operations:
        raise ActionError("spec contains no usable operations")
    return operations


def _host_allowed(host: str, allowlist: list[str]) -> bool:
    host = host.lower()
    return any(host == a.lower() or host.endswith("." + a.lower()) for a in allowlist)


def assert_egress_allowed(url: str, allowlist: list[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ActionError("only http(s) URLs are allowed")
    host = parts.hostname or ""
    if not host:
        raise ActionError("URL has no host")
    if _host_allowed(host, allowlist):
        return  # explicit allowlist override (§9.4)
    try:
        infos = socket.getaddrinfo(host, parts.port or 443)
    except OSError as exc:
        raise ActionError(f"cannot resolve host {host!r}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ActionError(
                f"host {host!r} resolves to a non-public address; add it to the "
                "action's host allowlist to permit it explicitly"
            )


def _apply_auth(auth: dict[str, Any], headers: dict[str, str], params: dict[str, Any]) -> None:
    auth_type = auth.get("type", "none")
    if auth_type == "api_key_header":
        headers[str(auth.get("header", "X-API-Key"))] = str(auth.get("key", ""))
    elif auth_type == "bearer":
        headers["Authorization"] = f"Bearer {auth.get('token', '')}"
    elif auth_type == "basic":
        import base64

        raw = f"{auth.get('user', '')}:{auth.get('password', '')}".encode()
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode()}"
    elif auth_type == "api_key_query":
        params[str(auth.get("param", "api_key"))] = str(auth.get("key", ""))


async def execute_operation(
    operation: dict[str, Any],
    args: dict[str, Any],
    *,
    auth: dict[str, Any],
    host_allowlist: list[str],
) -> str:
    path = operation["path"]
    params: dict[str, Any] = {}
    headers: dict[str, str] = {"Accept": "application/json"}
    body = None
    for key, value in args.items():
        if key == "body" and operation.get("has_body"):
            body = value
            continue
        where = operation.get("param_map", {}).get(key, "query")
        if where == "path":
            path = path.replace("{" + key + "}", str(value))
        elif where == "header":
            headers[key] = str(value)
        else:
            params[key] = value
    _apply_auth(auth or {}, headers, params)

    url = urljoin(operation.get("base_url", "").rstrip("/") + "/", path.lstrip("/"))
    assert_egress_allowed(url, host_allowlist)

    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as client:
        response = await client.request(
            operation["method"], url, params=params, headers=headers, json=body
        )
        content = response.content[:RESPONSE_CAP]
    text = content.decode("utf-8", errors="replace")
    if len(text) > MODEL_CAP:
        text = text[:MODEL_CAP] + "\n…(truncated)"
    return f"HTTP {response.status_code}\n{text}"
