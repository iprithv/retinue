"""RAG pipeline (§10): collections, chunking, embed cache, hybrid retrieval,
and agent-knowledge citations in chat."""

import asyncio
import uuid

from sqlalchemy import func, select
from tests.support import collect_chat, register_user

from retinue.db.models import Chunk, EmbedCache

DOC = """# Handbook

## Payments

Refunds are processed within 5 business days through the treasury desk.

## Shipping

Orders ship from the Rotterdam warehouse on Tuesdays.

## Security

All vault access requires two hardware keys held by separate officers.
"""


async def _upload(client, headers, name, content):
    response = await client.post(
        "/api/files/direct", headers=headers, files={"file": (name, content, "text/plain")}
    )
    assert response.status_code == 201
    return response.json()


async def _wait_indexed(client, headers, collection_id, timeout=8.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        status = (
            await client.get(f"/api/collections/{collection_id}/status", headers=headers)
        ).json()
        if status["files"] and all(f["status"] == "indexed" for f in status["files"]):
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"collection never indexed: {status}")


async def test_collection_index_and_hybrid_search(app_client):
    client, app = app_client
    headers = await register_user(client)
    file = await _upload(client, headers, "handbook.md", DOC.encode())

    response = await client.post("/api/collections", headers=headers, json={"name": "Ops KB"})
    assert response.status_code == 201
    collection = response.json()
    assert collection["embed_model"] == "mock/embed"

    response = await client.post(
        f"/api/collections/{collection['id']}/files",
        headers=headers,
        json={"file_ids": [file["id"]]},
    )
    assert response.status_code == 202
    status = await _wait_indexed(client, headers, collection["id"])
    assert status["files"][0]["chunks"] >= 1

    # hybrid search returns the relevant chunk with its heading locator
    hits = (
        await client.get(
            f"/api/collections/{collection['id']}/search",
            headers=headers,
            params={"q": "how long do refunds take"},
        )
    ).json()
    assert hits, "no hits returned"
    assert any("5 business days" in h["text"] for h in hits)

    # collection dimension recorded after first index (mock/embed = 64)
    detail = (await client.get("/api/collections", headers=headers)).json()[0]
    assert detail["embed_dim"] == 64

    # embed cache: re-adding the same file re-embeds nothing new
    state = app.state.retinue
    async with state.db.read_session() as session:
        cached_before = (
            await session.execute(select(func.count()).select_from(EmbedCache))
        ).scalar_one()
    await client.post(
        f"/api/collections/{collection['id']}/files",
        headers=headers,
        json={"file_ids": [file["id"]]},
    )
    await _wait_indexed(client, headers, collection["id"])
    async with state.db.read_session() as session:
        cached_after = (
            await session.execute(select(func.count()).select_from(EmbedCache))
        ).scalar_one()
    # only the (possibly new) query embedding may appear; chunk texts hit the cache
    assert cached_after <= cached_before + 1


async def test_agent_collection_flows_into_chat_with_citations(app_client):
    client, _ = app_client
    headers = await register_user(client)
    file = await _upload(client, headers, "handbook.md", DOC.encode())
    collection = (
        await client.post("/api/collections", headers=headers, json={"name": "KB"})
    ).json()
    await client.post(
        f"/api/collections/{collection['id']}/files",
        headers=headers,
        json={"file_ids": [file["id"]]},
    )
    await _wait_indexed(client, headers, collection["id"])

    agent = (
        await client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Ops Bot",
                "system_prompt": "Answer from the sources.",
                "model": "mock/echo",
                "collection_ids": [collection["id"]],
            },
        )
    ).json()

    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid.uuid4()),
            "text": "when does the Rotterdam warehouse ship orders?",
            "agent_id": agent["id"],
        },
    )
    citations = [e for e in events if e.event == "citation"]
    assert citations, "agent knowledge produced no citations"
    assert citations[0].data["file_name"] == "handbook.md"


async def test_remove_file_from_collection(app_client):
    client, app = app_client
    headers = await register_user(client)
    file = await _upload(client, headers, "doc.md", DOC.encode())
    collection = (
        await client.post("/api/collections", headers=headers, json={"name": "KB2"})
    ).json()
    await client.post(
        f"/api/collections/{collection['id']}/files",
        headers=headers,
        json={"file_ids": [file["id"]]},
    )
    await _wait_indexed(client, headers, collection["id"])

    response = await client.delete(
        f"/api/collections/{collection['id']}/files/{file['id']}", headers=headers
    )
    assert response.status_code == 204
    state = app.state.retinue
    async with state.db.read_session() as session:
        remaining = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    assert remaining == 0
