"""Job worker (§15): claim, retry with backoff, dead-lettering, title handler."""

import asyncio

import pytest

from retinue.config import Settings
from retinue.core.crypto import SecretBox
from retinue.core.ids import uuid7
from retinue.core.streams import StreamHub
from retinue.core.timeutil import now_ms
from retinue.core.tokens import TokenCounter
from retinue.db.migrate import run_migrations
from retinue.db.models import Conversation, Job, Message, MessagePart, User
from retinue.db.session import Database
from retinue.jobs.handlers.titles import generate_title
from retinue.jobs.queue import JobQueue
from retinue.jobs.worker import JobContext, JobWorker
from retinue.providers.registry import ProviderRegistry


@pytest.fixture
async def db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/jobs.db"
    await run_migrations(url)
    database = Database(url)
    yield database
    await database.dispose()


def make_ctx(db, tmp_path, **model_overrides) -> JobContext:
    settings = Settings(
        home_dir=tmp_path / "home",
        secret="test-secret",
        models={"mock_enabled": True, **model_overrides},
    )
    return JobContext(
        db=db,
        settings=settings,
        registry=ProviderRegistry(settings, SecretBox("test-secret")),
        hub=StreamHub(),
        counter=TokenCounter(force_fallback=True),
    )


async def run_worker_until(db, queue, ctx, handlers, predicate, timeout=8.0):
    worker = JobWorker(db=db, queue=queue, ctx=ctx, handlers=handlers, poll_interval_s=0.05)
    worker.start()
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await predicate():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("worker did not reach expected state in time")
    finally:
        await worker.stop()


async def _job_status(db, job_id) -> str:
    async with db.read_session() as session:
        job = await session.get(Job, job_id)
        return job.status if job else "missing"


async def test_job_success(db, tmp_path):
    queue = JobQueue(db)
    ran = []

    async def handler(ctx, payload):
        ran.append(payload["value"])

    job_id = await queue.enqueue("touch", {"value": 42})

    async def done() -> bool:
        return await _job_status(db, job_id) == "done"

    await run_worker_until(db, queue, make_ctx(db, tmp_path), {"touch": handler}, done)
    assert ran == [42]


async def test_job_retries_then_dead(db, tmp_path):
    queue = JobQueue(db)
    attempts = []

    async def failing(ctx, payload):
        attempts.append(1)
        raise RuntimeError("boom")

    job_id = await queue.enqueue("explode", {})
    # exponential backoff: 2s, 4s... too slow for tests -> shrink run_at manually
    worker = JobWorker(
        db=db,
        queue=queue,
        ctx=make_ctx(db, tmp_path),
        handlers={"explode": failing},
        poll_interval_s=0.05,
    )
    worker.start()
    try:
        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            status = await _job_status(db, job_id)
            if status == "dead":
                break
            # collapse the backoff so five attempts happen quickly
            async with db.write_session() as session:
                job = await session.get(Job, job_id)
                if job and job.status == "queued" and job.run_at > now_ms():
                    job.run_at = now_ms()
                    queue.wake.set()
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()

    assert await _job_status(db, job_id) == "dead"
    assert len(attempts) == 5  # MAX_ATTEMPTS
    async with db.read_session() as session:
        job = await session.get(Job, job_id)
    assert "boom" in (job.last_error or "")


async def test_unknown_job_type_goes_dead_not_crash(db, tmp_path):
    queue = JobQueue(db)
    job_id = await queue.enqueue("no_such_handler", {})
    worker = JobWorker(
        db=db, queue=queue, ctx=make_ctx(db, tmp_path), handlers={}, poll_interval_s=0.05
    )
    worker.start()
    try:
        for _ in range(100):
            async with db.write_session() as session:
                job = await session.get(Job, job_id)
                if job.status == "dead":
                    break
                if job.status == "queued":
                    job.run_at = now_ms()
                    queue.wake.set()
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()
    assert await _job_status(db, job_id) == "dead"


async def _seed_conversation(db, *, assistant_model: str | None) -> Conversation:
    async with db.write_session() as session:
        user = User(id=uuid7(), email=f"{uuid7().hex}@t.dev", role="member")
        session.add(user)
        await session.flush()
        conversation = Conversation(id=uuid7(), user_id=user.id, title=None)
        session.add(conversation)
        await session.flush()
        user_msg = Message(
            id=uuid7(), conversation_id=conversation.id, role="user", status="complete"
        )
        session.add(user_msg)
        await session.flush()
        session.add(
            MessagePart(
                id=uuid7(),
                message_id=user_msg.id,
                idx=0,
                type="text",
                content={"text": "how do rust lifetimes work exactly"},
                text_content="how do rust lifetimes work exactly",
            )
        )
        assistant = Message(
            id=uuid7(),
            conversation_id=conversation.id,
            parent_id=user_msg.id,
            role="assistant",
            status="complete",
            model=assistant_model,
        )
        session.add(assistant)
        await session.flush()
        session.add(
            MessagePart(
                id=uuid7(),
                message_id=assistant.id,
                idx=0,
                type="text",
                content={"text": "They are regions."},
                text_content="They are regions.",
            )
        )
    return conversation


async def test_title_via_model(db, tmp_path):
    conversation = await _seed_conversation(db, assistant_model="mock/echo")
    await generate_title(make_ctx(db, tmp_path), {"conversation_id": str(conversation.id)})
    async with db.read_session() as session:
        row = await session.get(Conversation, conversation.id)
    assert row.title == "how do rust lifetimes"


async def test_title_heuristic_fallback_when_model_fails(db, tmp_path):
    conversation = await _seed_conversation(db, assistant_model=None)
    ctx = make_ctx(db, tmp_path, housekeeping="mock/fail-auth")
    await generate_title(ctx, {"conversation_id": str(conversation.id)})
    async with db.read_session() as session:
        row = await session.get(Conversation, conversation.id)
    assert row.title == "how do rust lifetimes work exactly"


async def test_title_never_clobbers_existing(db, tmp_path):
    conversation = await _seed_conversation(db, assistant_model="mock/echo")
    async with db.write_session() as session:
        row = await session.get(Conversation, conversation.id)
        row.title = "User chosen"
    await generate_title(make_ctx(db, tmp_path), {"conversation_id": str(conversation.id)})
    async with db.read_session() as session:
        row = await session.get(Conversation, conversation.id)
    assert row.title == "User chosen"
