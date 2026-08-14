"""Files subsystem (§11): direct upload, resumable upload with kill-and-resume,
BLAKE3 dedupe, Range/ETag downloads, refcounted delete, extraction job."""

import asyncio

from blake3 import blake3
from tests.support import register_user


async def _wait_for_extraction(client, headers, file_id, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        meta = (await client.get(f"/api/files/{file_id}", headers=headers)).json()["meta"]
        if "text_chars" in meta or "extraction" in meta:
            return meta
        await asyncio.sleep(0.1)
    raise AssertionError("extraction job never ran")


async def test_direct_upload_download_delete(app_client):
    client, _ = app_client
    headers = await register_user(client)
    content = b"# Notes\n\nRetinue keeps your experts in attendance.\n" * 10

    response = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("notes.md", content, "text/markdown")},
    )
    assert response.status_code == 201, response.text
    file = response.json()
    assert file["status"] == "ready"
    assert file["mime"] == "text/markdown"
    assert file["size"] == len(content)

    # download with ETag + Range
    response = await client.get(f"/api/files/{file['id']}/content", headers=headers)
    assert response.status_code == 200
    assert response.content == content
    etag = response.headers["etag"]
    assert etag == f'"{blake3(content).hexdigest()}"'
    assert "attachment" in response.headers["content-disposition"]

    response = await client.get(
        f"/api/files/{file['id']}/content",
        headers={**headers, "Range": "bytes=0-9"},
    )
    assert response.status_code == 206
    assert response.content == content[:10]
    assert response.headers["content-range"] == f"bytes 0-9/{len(content)}"

    response = await client.get(
        f"/api/files/{file['id']}/content", headers={**headers, "If-None-Match": etag}
    )
    assert response.status_code == 304

    # extraction populated file_texts
    meta = await _wait_for_extraction(client, headers, file["id"])
    assert meta["text_chars"] > 0

    response = await client.delete(f"/api/files/{file['id']}", headers=headers)
    assert response.status_code == 204
    response = await client.get(f"/api/files/{file['id']}", headers=headers)
    assert response.status_code == 404


async def test_direct_upload_dedupes_blobs(app_client, settings):
    client, app = app_client
    headers = await register_user(client)
    content = b"same bytes every time" * 100

    ids = []
    for name in ("a.txt", "b.txt"):
        response = await client.post(
            "/api/files/direct", headers=headers, files={"file": (name, content, "text/plain")}
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])

    state = app.state.retinue
    from sqlalchemy import select

    from retinue.db.models import Blob

    async with state.db.read_session() as session:
        blobs = (await session.execute(select(Blob))).scalars().all()
    assert len(blobs) == 1
    assert blobs[0].refcount == 2

    # deleting one reference keeps the blob; deleting both GCs it
    await client.delete(f"/api/files/{ids[0]}", headers=headers)
    async with state.db.read_session() as session:
        blob = (await session.execute(select(Blob))).scalar_one()
        assert blob.refcount == 1
    await client.delete(f"/api/files/{ids[1]}", headers=headers)
    async with state.db.read_session() as session:
        assert (await session.execute(select(Blob))).scalar_one_or_none() is None


async def test_resumable_upload_with_resume(app_client):
    client, _ = app_client
    headers = await register_user(client)
    content = bytes(range(256)) * 2048  # 512 KB binary
    digest = blake3(content).hexdigest()

    response = await client.post(
        "/api/files",
        headers=headers,
        json={"name": "data.bin", "size": len(content), "blake3": digest},
    )
    assert response.status_code == 201
    session_info = response.json()
    assert session_info["already_exists"] is False
    upload_id = session_info["upload_id"]

    # first chunk
    half = len(content) // 2
    response = await client.patch(
        f"/api/uploads/{upload_id}",
        headers={
            **headers,
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        },
        content=content[:half],
    )
    assert response.status_code == 204
    assert response.headers["upload-offset"] == str(half)

    # simulate reconnect: ask the server where we are
    response = await client.head(f"/api/uploads/{upload_id}", headers=headers)
    assert response.headers["upload-offset"] == str(half)

    # wrong offset is rejected (lost chunk protection)
    response = await client.patch(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Upload-Offset": "0"},
        content=content[:1],
    )
    assert response.status_code == 409

    response = await client.patch(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Upload-Offset": str(half)},
        content=content[half:],
    )
    assert response.status_code == 204

    response = await client.post(
        f"/api/uploads/{upload_id}/complete", headers=headers, json={"blake3": digest}
    )
    assert response.status_code == 200, response.text
    file = response.json()
    assert file["status"] == "ready"

    response = await client.get(f"/api/files/{file['id']}/content", headers=headers)
    assert response.content == content

    # §11.3 instant dedupe: same hash again → no upload needed
    response = await client.post(
        "/api/files",
        headers=headers,
        json={"name": "copy.bin", "size": len(content), "blake3": digest},
    )
    assert response.status_code == 201
    assert response.json()["already_exists"] is True
    assert response.json()["upload_id"] is None


async def test_resumable_hash_mismatch_fails(app_client):
    client, _ = app_client
    headers = await register_user(client)
    content = b"payload"
    response = await client.post(
        "/api/files", headers=headers, json={"name": "x.bin", "size": len(content)}
    )
    upload_id = response.json()["upload_id"]
    await client.patch(
        f"/api/uploads/{upload_id}", headers={**headers, "Upload-Offset": "0"}, content=content
    )
    response = await client.post(
        f"/api/uploads/{upload_id}/complete",
        headers=headers,
        json={"blake3": "0" * 64},
    )
    assert response.status_code == 422


async def test_upload_size_caps(app_client):
    client, _ = app_client
    headers = await register_user(client)
    response = await client.post(
        "/api/files",
        headers=headers,
        json={"name": "big.bin", "size": 600 * 1024 * 1024 * 1024},
    )
    assert response.status_code == 413


async def test_attachment_flows_into_chat_context(app_client):
    import uuid as uuid_mod

    from tests.support import collect_chat

    client, _ = app_client
    headers = await register_user(client)
    secret = "the launch code is JUPITER-42"
    response = await client.post(
        "/api/files/direct",
        headers=headers,
        files={"file": ("brief.txt", secret.encode(), "text/plain")},
    )
    file_id = response.json()["id"]
    await _wait_for_extraction(client, headers, file_id)

    events = await collect_chat(
        client,
        headers,
        {
            "message_id": str(uuid_mod.uuid4()),
            "text": "what is the launch code?",
            "file_ids": [file_id],
        },
    )
    kinds = [e.event for e in events]
    assert "citation" in kinds
    citation = next(e for e in events if e.event == "citation")
    assert citation.data["file_id"] == file_id
    assert citation.data["n"] == 1

    # the citation is persisted as a message part
    start = next(e for e in events if e.event == "message_start")
    messages = (
        await client.get(
            f"/api/conversations/{start.data['conversation_id']}/messages", headers=headers
        )
    ).json()["messages"]
    assistant = messages[-1]
    assert any(p["type"] == "citation" for p in assistant["parts"])
    user_msg = messages[0]
    assert user_msg["attachments"][0]["file_id"] == file_id
