"""Regression tests for the production-hardening review findings."""

import uuid

import pytest
from tests.support import StreamReader, register_user

from retinue.datasources.base import DataSourceError
from retinue.datasources.guard import SourcePolicy, validate_select

POLICY = SourcePolicy()


# -- finding: writable CTEs must not pass the read-only guard --------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t",
        "WITH t AS (UPDATE users SET role='admin' RETURNING id) SELECT * FROM t",
        "WITH t AS (INSERT INTO logs VALUES (1) RETURNING *) SELECT * FROM t",
    ],
)
def test_writable_cte_rejected(statement):
    with pytest.raises(DataSourceError, match="read-only"):
        validate_select(statement, "postgres", POLICY)


def test_plain_cte_still_allowed():
    validate_select("WITH t AS (SELECT 1 AS x) SELECT x FROM t", "postgres", POLICY)


# -- finding: Flux write functions rejected on InfluxDB --------------------------------


def test_influx_flux_writes_rejected():
    from retinue.datasources.engines.nosql import InfluxAdapter
    from retinue.datasources.registry import engine_info

    adapter = InfluxAdapter(engine_info("influxdb"), {"url": "http://x", "org": "o"}, {})
    with pytest.raises(DataSourceError, match="read-only"):
        adapter._assert_read_only('from(bucket:"a") |> to(bucket:"b")')
    adapter._assert_read_only('from(bucket:"a") |> range(start: -1h)')  # reads pass


# -- finding: URL-embedded credentials must not persist in plaintext config -------------


async def test_uri_credentials_move_to_encrypted_secrets(app_client):
    client, app = app_client
    headers = await register_user(client)
    response = await client.post(
        "/api/datasources",
        headers=headers,
        json={
            "name": "M",
            "engine": "mongodb",
            "config": {"uri": "mongodb://admin:hunter2@db.internal:27017", "database": "app"},
        },
    )
    assert response.status_code == 201, response.text
    source = response.json()
    # the API echo and the stored config are redacted; the secret is encrypted
    assert "hunter2" not in str(source["config"])
    assert source["config"]["uri"] == "mongodb://db.internal:27017"
    assert source["has_secrets"] is True

    from retinue.db.models import DataSourceRow

    state = app.state.retinue
    async with state.db.read_session() as session:
        row = await session.get(DataSourceRow, uuid.UUID(source["id"]))
    assert "hunter2" not in str(row.config)
    assert row.secret_ciphertext is not None
    assert b"hunter2" not in row.secret_ciphertext  # AES-GCM, not plaintext


# -- finding: stop must interrupt an ask_user approval wait ------------------------------


async def test_stop_interrupts_approval_wait(live):
    client = live.client
    headers = await register_user(client)
    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Gated",
                "system_prompt": "x",
                "model": "mock/tool",
                "tools": [
                    {"type": "builtin", "ref": "file_search", "config": {"mode": "ask_user"}}
                ],
            },
        )
    ).json()

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
        while True:
            event = await reader.next_event()
            assert event is not None, "stream ended before approval_required"
            if event.event == "approval_required":
                break
        message_id = reader.events[0].data["message_id"]
        # stop instead of approving: the turn must end promptly, not after 300s
        stop = await client.post(f"/api/messages/{message_id}/stop", headers=headers)
        assert stop.json()["stopped"] is True
        await reader.drain()

    end = next(e for e in reader.events if e.event == "message_end")
    assert end.data["stop_reason"] == "stopped"


# -- finding: duplicate concurrent sends must not double-generate ------------------------


async def test_concurrent_duplicate_send_attaches(live):
    import asyncio

    client = live.client
    headers = await register_user(client)
    message_id = str(uuid.uuid4())
    payload = {"message_id": message_id, "text": "race me", "model": "mock/slow"}

    async def send() -> list:
        async with client.stream("POST", "/api/chat", headers=headers, json=payload) as r:
            assert r.status_code == 200
            reader = StreamReader(r)
            await reader.drain()
            return reader.events

    events_a, events_b = await asyncio.gather(send(), send())
    starts = {
        e.data["message_id"]
        for events in (events_a, events_b)
        for e in events
        if e.event == "message_start"
    }
    # both requests observed the SAME assistant message — no sibling fork
    assert len(starts) == 1

    conversations = (await client.get("/api/conversations", headers=headers)).json()
    assert len(conversations) == 1
    messages = (
        await client.get(
            f"/api/conversations/{conversations[0]['id']}/messages?all=true", headers=headers
        )
    ).json()["messages"]
    assert sum(1 for m in messages if m["role"] == "assistant") == 1


# -- finding: tool activity must survive an idempotent replay (block fidelity) -----------


async def test_db_replay_includes_tool_blocks(app_client, tmp_path):
    from tests.support import collect_chat

    client, _ = app_client
    headers = await register_user(client)
    upload = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("n.txt", b"the code is AZURE-9", "text/plain")},
    )
    file_id = upload.json()["id"]
    import asyncio

    for _ in range(50):
        meta = (await client.get(f"/api/files/{file_id}", headers=headers)).json()["meta"]
        if "text_chars" in meta:
            break
        await asyncio.sleep(0.1)

    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "T",
                "system_prompt": "x",
                "model": "mock/tool",
                "tools": [{"type": "builtin", "ref": "file_read"}],
            },
        )
    ).json()
    message_id = str(uuid.uuid4())
    payload = {
        "message_id": message_id,
        "agent_id": agent["id"],
        "text": f'use:file_read {{"file_id": "{file_id}"}}',
    }
    first = await collect_chat(client, headers, payload)
    assert any(e.event == "tool_result" for e in first)

    # idempotent retry after the stream is gone → DB replay with typed events
    replay = await collect_chat(client, headers, {"message_id": message_id})
    kinds = [e.event for e in replay]
    assert "tool_call" in kinds and "tool_result" in kinds
    result = next(e for e in replay if e.event == "tool_result")
    assert "AZURE-9" in result.data["summary"]
