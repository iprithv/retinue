"""Tool runtime (§9.2-§9.4): loop, approvals, MCP, OpenAPI actions, sandbox."""

import sys
import uuid

from tests.support import StreamReader, collect_chat, register_user

from retinue.agents.openapi_actions import (
    ActionError,
    assert_egress_allowed,
    parse_operations,
)
from retinue.agents.runtime import validate_args

WIDGET_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Widgets", "version": "1"},
    "servers": [{"url": "http://127.0.0.1:9/api"}],  # port patched per-test
    "paths": {
        "/widgets/{wid}": {
            "get": {
                "operationId": "getWidget",
                "summary": "Fetch a widget",
                "parameters": [
                    {
                        "name": "wid",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            }
        }
    },
}


# -- unit: schema validation & spec parsing -----------------------------------------


def test_validate_args():
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["q"],
    }
    assert validate_args(schema, {"q": "x"}) is None
    assert validate_args(schema, {"q": "x", "n": 3}) is None
    assert "missing required" in validate_args(schema, {})
    assert "must be of type" in validate_args(schema, {"q": 5})
    assert "integer" in validate_args(schema, {"q": "x", "n": True})


def test_parse_operations_and_ssrf_guard():
    ops = parse_operations(WIDGET_SPEC)
    assert ops[0]["name"] == "getWidget"
    assert ops[0]["method"] == "GET"
    assert ops[0]["param_map"]["wid"] == "path"
    assert "wid" in ops[0]["parameters"]["required"]

    # private addresses denied without an allowlist, allowed with one
    try:
        assert_egress_allowed("http://127.0.0.1:9999/x", [])
        raise AssertionError("expected ActionError")
    except ActionError:
        pass
    assert_egress_allowed("http://127.0.0.1:9999/x", ["127.0.0.1"])  # no raise = allowed
    try:
        assert_egress_allowed("ftp://example.com/x", [])
        raise AssertionError("expected ActionError")
    except ActionError:
        pass


# -- end-to-end: builtin tool loop ------------------------------------------------


async def _agent_with_tools(client, headers, tools, model="mock/tool", **kw):
    response = await client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": kw.pop("name", "Tool Agent"),
            "system_prompt": "Use your tools.",
            "model": model,
            "tools": tools,
            **kw,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_builtin_file_read_tool_loop(app_client):
    client, _ = app_client
    headers = await register_user(client)

    # upload a file and wait for extraction
    upload = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("brief.txt", b"the password is SWORDFISH", "text/plain")},
    )
    file_id = upload.json()["id"]
    import asyncio

    for _ in range(50):
        meta = (await client.get(f"/api/files/{file_id}", headers=headers)).json()["meta"]
        if "text_chars" in meta:
            break
        await asyncio.sleep(0.1)

    agent = await _agent_with_tools(client, headers, [{"type": "builtin", "ref": "file_read"}])
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": f'use:file_read {{"file_id": "{file_id}"}}',
        },
    )
    kinds = [e.event for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds

    call = next(e for e in events if e.event == "tool_call")
    assert call.data["name"] == "file_read"
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "ok"
    assert "SWORDFISH" in result.data["summary"]

    # the final text quotes the tool result (mock/tool second hop)
    text = "".join(e.data["text"] for e in events if e.event == "delta")
    assert "Tool result received" in text
    assert "SWORDFISH" in text

    # persisted parts mirror the streamed shape (§19)
    start = next(e for e in events if e.event == "message_start")
    messages = (
        await client.get(
            f"/api/conversations/{start.data['conversation_id']}/messages", headers=headers
        )
    ).json()["messages"]
    types = [p["type"] for p in messages[-1]["parts"]]
    assert "tool_call" in types and "tool_result" in types and "text" in types


async def test_invalid_args_return_error_result(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = await _agent_with_tools(client, headers, [{"type": "builtin", "ref": "file_read"}])
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:file_read {"wrong": 1}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "error"
    assert "missing required argument" in result.data["summary"]


async def test_sandbox_unavailable_is_clean(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = await _agent_with_tools(client, headers, [{"type": "builtin", "ref": "code_exec"}])
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:code_exec {"code": "print(1)"}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    # wasmtime interpreter is not installed in the test env: honest error text
    assert result.data["status"] == "ok"
    assert "sandbox unavailable" in result.data["summary"]
    end = next(e for e in events if e.event == "message_end")
    assert end.data["stop_reason"] == "end"


# -- approvals (§9.2 ask_user) ------------------------------------------------------


async def test_tool_approval_flow(live):
    client = live.client
    headers = await register_user(client)
    agent = await _agent_with_tools(
        client,
        headers,
        [{"type": "builtin", "ref": "file_search", "config": {"mode": "ask_user"}}],
    )

    async with client.stream(
        "POST",
        "/api/chat",
        headers=headers,
        json={
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:file_search {"query": "anything"}',
        },
    ) as response:
        reader = StreamReader(response)
        approval = None
        while approval is None:
            event = await reader.next_event()
            assert event is not None, "stream ended before approval_required"
            if event.event == "approval_required":
                approval = event
        message_id = reader.events[0].data["message_id"]

        response2 = await client.post(
            f"/api/messages/{message_id}/approve",
            headers=headers,
            json={"call_id": approval.data["call_id"], "approve": True},
        )
        assert response2.status_code == 200
        await reader.drain()

    result = next(e for e in reader.events if e.event == "tool_result")
    assert result.data["status"] == "ok"


async def test_tool_denial(live):
    client = live.client
    headers = await register_user(client)
    agent = await _agent_with_tools(
        client,
        headers,
        [{"type": "builtin", "ref": "file_search", "config": {"mode": "ask_user"}}],
    )
    async with client.stream(
        "POST",
        "/api/chat",
        headers=headers,
        json={
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:file_search {"query": "x"}',
        },
    ) as response:
        reader = StreamReader(response)
        approval = None
        while approval is None:
            event = await reader.next_event()
            assert event is not None
            if event.event == "approval_required":
                approval = event
        message_id = reader.events[0].data["message_id"]
        await client.post(
            f"/api/messages/{message_id}/approve",
            headers=headers,
            json={"call_id": approval.data["call_id"], "approve": False},
        )
        await reader.drain()

    result = next(e for e in reader.events if e.event == "tool_result")
    assert result.data["status"] == "denied"


# -- MCP integration (real stdio server) ---------------------------------------------


async def test_mcp_server_tools_and_chat(app_client):
    client, _ = app_client
    headers = await register_user(client)

    response = await client.post(
        "/api/mcp/servers",
        headers=headers,
        json={
            "name": "echo",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["tests/mcp_echo_server.py"],
        },
    )
    assert response.status_code == 201, response.text
    server = response.json()

    tools = (await client.get(f"/api/mcp/servers/{server['id']}/tools", headers=headers)).json()
    names = {t["name"] for t in tools}
    assert {"echo", "add"} <= names

    status = (await client.post(f"/api/mcp/servers/{server['id']}/test", headers=headers)).json()
    assert status["ok"] is True

    # agent uses the MCP tool through the chat loop
    agent = await _agent_with_tools(
        client,
        headers,
        [],
        mcp_servers=[{"server_id": server["id"]}],
        name="MCP Agent",
    )
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:mcp_echo__echo {"text": "marco"}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "ok"
    assert "echo: marco" in result.data["summary"]


# -- OpenAPI actions end-to-end -------------------------------------------------------


async def test_openapi_action_through_chat(app_client, unused_tcp_port_factory=None):
    import asyncio

    import uvicorn
    from fastapi import FastAPI

    api = FastAPI()

    @api.get("/api/widgets/{wid}")
    async def get_widget(wid: str):
        return {"widget": wid, "color": "vermilion"}

    config = uvicorn.Config(api, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        client, _ = app_client
        headers = await register_user(client)
        spec = {**WIDGET_SPEC, "servers": [{"url": f"http://127.0.0.1:{port}/api"}]}
        response = await client.post(
            "/api/actions",
            headers=headers,
            json={"name": "Widgets", "spec": spec, "host_allowlist": ["127.0.0.1"]},
        )
        assert response.status_code == 201, response.text
        action = response.json()
        assert action["operations"][0]["name"] == "getWidget"

        agent = await _agent_with_tools(
            client,
            headers,
            [{"type": "action", "ref": action["id"]}],
            name="Action Agent",
        )
        events = await collect_chat(
            client,
            headers,
            {
                "message_id": str(uuid.uuid4()),
                "agent_id": agent["id"],
                "text": 'use:act_Widgets__getWidget {"wid": "42"}',
            },
        )
        result = next(e for e in events if e.event == "tool_result")
        assert result.data["status"] == "ok"
        assert "vermilion" in result.data["summary"]
    finally:
        server.should_exit = True
        await task
