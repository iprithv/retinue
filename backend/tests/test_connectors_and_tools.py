"""Connector catalog (§28.6), new search providers, web_fetch, image_gen,
and vision input."""

import uuid

import pytest
from tests.support import collect_chat, register_user

from retinue.agents.connectors import CONNECTORS, connector_catalog

# -- catalog integrity -----------------------------------------------------------


def test_catalog_integrity():
    assert len(CONNECTORS) >= 20
    entries = connector_catalog()
    assert len(entries) == len(CONNECTORS)
    categories = {e["category"] for e in entries}
    assert {"chat", "dev", "tickets", "observability", "incidents", "cloud", "docs"} <= categories
    for connector in CONNECTORS.values():
        assert connector.kind in ("mcp-stdio", "mcp-http", "openapi")
        if connector.kind == "mcp-stdio":
            assert connector.command, connector.key
        elif connector.kind == "mcp-http":
            assert connector.url.startswith("https://"), connector.key
        else:
            assert connector.spec.get("openapi") == "3.0.0", connector.key
            # bundled specs must parse into operations once params are filled
            if not connector.params:
                from retinue.agents.openapi_actions import parse_operations

                assert parse_operations(dict(connector.spec)), connector.key


def test_expected_integrations_present():
    keys = set(CONNECTORS)
    assert {
        "slack", "github", "gitlab", "atlassian", "linear", "notion",
        "grafana", "prometheus", "datadog", "sentry", "newrelic", "splunk",
        "pagerduty", "opsgenie", "kubernetes", "aws", "gdrive",
        "zendesk", "servicenow", "intercom", "slack-webhook", "discord-webhook",
    } <= keys  # fmt: skip


# -- install flow ---------------------------------------------------------------------


async def test_install_mcp_connector(app_client):
    client, app = app_client
    headers = await register_user(client)

    catalog = (await client.get("/api/connectors", headers=headers)).json()
    slack = next(c for c in catalog if c["key"] == "slack")
    assert {s["name"] for s in slack["secrets"]} == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}

    # missing secrets rejected with the human labels
    response = await client.post("/api/connectors/slack/install", headers=headers, json={})
    assert response.status_code == 422
    assert "Bot token" in response.json()["error"]["message"]

    response = await client.post(
        "/api/connectors/slack/install",
        headers=headers,
        json={"secrets": {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_TEAM_ID": "T123"}},
    )
    assert response.status_code == 201, response.text
    installed = response.json()
    assert installed["kind"] == "mcp"
    assert installed["mcp_server"]["transport"] == "stdio"
    assert installed["mcp_server"]["has_secrets"] is True
    assert "npx" in installed["note"]

    # secrets are encrypted at rest, never in the spec
    from sqlalchemy import select

    from retinue.db.models import McpServer

    state = app.state.retinue
    async with state.db.read_session() as session:
        server = (await session.execute(select(McpServer))).scalar_one()
    assert "xoxb-test" not in str(server.spec)
    assert server.secret_ciphertext is not None

    # it shows up in the normal MCP server list (same bus, §28.6)
    servers = (await client.get("/api/mcp/servers", headers=headers)).json()
    assert servers[0]["name"] == "Slack"


async def test_install_openapi_connector_with_params(app_client):
    client, _ = app_client
    headers = await register_user(client)

    response = await client.post(
        "/api/connectors/prometheus/install",
        headers=headers,
        json={"params": {"base_url": "http://prom.internal:9090"}},
    )
    assert response.status_code == 201, response.text
    installed = response.json()
    assert installed["kind"] == "action"
    ops = {o["name"] for o in installed["action"]["operations"]}
    assert {"instantQuery", "rangeQuery", "series", "labelValues"} <= ops
    # the substituted host lands on the allowlist so private ranges work
    assert "prom.internal" in installed["action"]["host_allowlist"]

    # pagerduty: static host + api-key auth
    response = await client.post(
        "/api/connectors/pagerduty/install",
        headers=headers,
        json={"secrets": {"key": "Token token=u+abc"}},
    )
    assert response.status_code == 201
    assert response.json()["action"]["auth_type"] == "api_key_header"

    response = await client.post("/api/connectors/nonsense/install", headers=headers, json={})
    assert response.status_code == 404


async def test_installed_action_is_usable_by_agents(app_client):
    """A connector-installed action rides the normal tool loop end to end."""
    import asyncio

    import uvicorn
    from fastapi import FastAPI

    api = FastAPI()

    @api.get("/api/v1/query")
    async def prom_query(query: str):
        return {"status": "success", "data": {"result": [{"value": [0, "42"]}]}}

    config = uvicorn.Config(api, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        client, _ = app_client
        headers = await register_user(client)
        installed = (
            await client.post(
                "/api/connectors/prometheus/install",
                headers=headers,
                json={"params": {"base_url": f"http://127.0.0.1:{port}"}},
            )
        ).json()
        agent = (
            await client.post(
                "/api/agents",
                headers=headers,
                json={
                    "name": "Metrics Analyst",
                    "system_prompt": "Query metrics.",
                    "model": "mock/tool",
                    "tools": [{"type": "action", "ref": installed["action"]["id"]}],
                },
            )
        ).json()
        events = await collect_chat(
            client,
            headers,
            {
                "message_id": str(uuid.uuid4()),
                "agent_id": agent["id"],
                "text": 'use:act_Prometheus__instantQuery {"query": "up"}',
            },
        )
        result = next(e for e in events if e.event == "tool_result")
        assert result.data["status"] == "ok"
        assert "42" in result.data["summary"]
    finally:
        server.should_exit = True
        await task


# -- web_fetch tool ----------------------------------------------------------------------


async def test_web_fetch_tool(app_client, monkeypatch):
    client, _ = app_client
    headers = await register_user(client)

    async def fake_fetch(url, *, max_bytes=0, timeout_s=10.0):
        return (
            b"<html><head><script>evil()</script></head>"
            b"<body><h1>Quarterly Report</h1><p>Revenue grew 12%.</p></body></html>",
            "text/html",
        )

    import retinue.core.egress as egress_mod

    monkeypatch.setattr(egress_mod, "fetch_guarded", fake_fetch)

    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Researcher",
                "system_prompt": "Fetch pages.",
                "model": "mock/tool",
                "tools": [{"type": "builtin", "ref": "web_fetch"}],
            },
        )
    ).json()
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:web_fetch {"url": "https://example.com/report"}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "ok"
    assert "Quarterly Report" in result.data["summary"]
    assert "evil()" not in result.data["summary"]  # scripts stripped


async def test_web_fetch_ssrf_guard_holds(app_client):
    """Un-mocked: a private address must be denied by the egress guard."""
    client, _ = app_client
    headers = await register_user(client)
    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "R2",
                "system_prompt": "x",
                "model": "mock/tool",
                "tools": [{"type": "builtin", "ref": "web_fetch"}],
            },
        )
    ).json()
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:web_fetch {"url": "http://169.254.169.254/latest/meta-data"}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert "error" in result.data["summary"]


# -- image generation ---------------------------------------------------------------------


async def test_image_gen_mock_creates_file(app_client):
    client, _ = app_client
    headers = await register_user(client)
    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Artist",
                "system_prompt": "Draw.",
                "model": "mock/tool",
                "tools": [{"type": "builtin", "ref": "image_gen"}],
            },
        )
    ).json()
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:image_gen {"prompt": "an indigo pixel"}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "ok"
    assert "file_id:" in result.data["summary"]

    files = (await client.get("/api/files", headers=headers)).json()
    generated = next(f for f in files if f["meta"].get("generated"))
    assert generated["mime"] == "image/png"
    # the bytes are really there and downloadable
    response = await client.get(f"/api/files/{generated['id']}/content", headers=headers)
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


# -- vision input -----------------------------------------------------------------------


async def test_vision_model_sees_image_attachments(app_client):
    client, _ = app_client
    headers = await register_user(client)
    png = b"\x89PNG\r\n\x1a\n" + bytes.fromhex(
        "0000000d494844520000000100000001080600000  01f15c489".replace(" ", "")
    )
    # a real (tiny) png via the mock generator helper keeps this honest
    from retinue.agents.tools.builtin import _mock_png

    png = _mock_png()
    upload = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("pixel.png", png, "image/png")},
    )
    assert upload.status_code == 201
    file_id = upload.json()["id"]
    assert upload.json()["mime"] == "image/png"

    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "text": "what is in this picture?",
            "model": "mock/vision",
            "file_ids": [file_id],
        },
    )
    text = "".join(e.data["text"] for e in events if e.event == "delta")
    assert "I can see 1 image(s)" in text
    assert "what is in this picture?" in text


async def test_non_vision_model_ignores_images(app_client):
    client, _ = app_client
    headers = await register_user(client)
    from retinue.agents.tools.builtin import _mock_png

    upload = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("pixel.png", _mock_png(), "image/png")},
    )
    file_id = upload.json()["id"]
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "text": "describe",
            "model": "mock/echo",  # no vision support → plain string content
            "file_ids": [file_id],
        },
    )
    kinds = [e.event for e in events]
    assert "message_end" in kinds  # no crash, image simply not attached


# -- search provider request shapes (serper/jina) -------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [("serper", "google.serper.dev"), ("jina", "s.jina.ai")],
)
async def test_new_search_providers_request_shape(app_client, monkeypatch, provider, expected_url):
    from types import SimpleNamespace

    import httpx

    import retinue.agents.tools.builtin as builtin_mod

    client, app = app_client
    headers = await register_user(client)
    state = app.state.retinue
    monkeypatch.setattr(state.settings.tools.web_search, "provider", provider)
    monkeypatch.setattr(state.settings.tools.web_search, "api_key", "k")

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        payload = (
            {"organic": [{"title": "T", "link": "https://x", "snippet": "S"}]}
            if provider == "serper"
            else {"data": [{"title": "T", "url": "https://x", "description": "S"}]}
        )
        return httpx.Response(200, json=payload)

    def fake_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # scope the stub to the tool module — the ASGI test client stays real
    monkeypatch.setattr(
        builtin_mod, "httpx", SimpleNamespace(AsyncClient=fake_client, HTTPError=httpx.HTTPError)
    )

    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": f"S-{provider}",
                "system_prompt": "x",
                "model": "mock/tool",
                "tools": [{"type": "builtin", "ref": "web_search"}],
            },
        )
    ).json()
    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "text": 'use:web_search {"query": "retinue"}',
        },
    )
    result = next(e for e in events if e.event == "tool_result")
    assert result.data["status"] == "ok"
    assert expected_url in seen["url"]
    assert "T" in result.data["summary"]
