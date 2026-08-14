"""In-process asyncio job worker (§15): concurrency 4, exponential-backoff
retries (max 5) then `dead`. Claiming is race-free because all writes serialize
through the single SQLite writer connection."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select, update

from retinue.config import Settings
from retinue.core.streams import StreamHub
from retinue.core.timeutil import now_ms
from retinue.core.tokens import TokenCounter
from retinue.db.models import Job
from retinue.db.session import Database
from retinue.jobs.queue import JobQueue
from retinue.providers.registry import ProviderRegistry

log = structlog.get_logger("retinue.jobs")


@dataclass
class JobContext:
    db: Database
    settings: Settings
    registry: ProviderRegistry
    hub: StreamHub
    counter: TokenCounter
    jobs: "JobQueue | None" = None
    state: Any = None  # AppState back-reference for RAG/memory handlers


Handler = Callable[[JobContext, dict[str, Any]], Awaitable[None]]

MAX_ATTEMPTS = 5


class JobWorker:
    def __init__(
        self,
        *,
        db: Database,
        queue: JobQueue,
        ctx: JobContext,
        handlers: dict[str, Handler],
        concurrency: int = 4,
        poll_interval_s: float = 2.0,
    ) -> None:
        self._db = db
        self._queue = queue
        self._ctx = ctx
        self._handlers = handlers
        self._sem = asyncio.Semaphore(concurrency)
        self._poll_interval_s = poll_interval_s
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        self._runner: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._runner = asyncio.create_task(self._run(), name="job-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._queue.wake.set()
        if self._runner is not None:
            await self._runner
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await self._claim_next()
            except Exception:
                log.exception("job_claim_failed")
                await asyncio.sleep(self._poll_interval_s)
                continue
            if job is None:
                self._queue.wake.clear()
                try:
                    await asyncio.wait_for(self._queue.wake.wait(), timeout=self._poll_interval_s)
                except TimeoutError:
                    pass
                continue
            await self._sem.acquire()
            task = asyncio.create_task(self._execute(job), name=f"job-{job.type}")
            self._tasks.add(task)
            task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._sem.release()
        if not task.cancelled() and task.exception() is not None:
            log.error("job_task_crashed", exc_info=task.exception())

    async def _claim_next(self) -> Job | None:
        async with self._db.write_session() as session:
            job = (
                await session.execute(
                    select(Job)
                    .where(Job.status == "queued", Job.run_at <= now_ms())
                    .order_by(Job.priority, Job.run_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if job is None:
                return None
            job.status = "running"
            job.locked_at = now_ms()
            job.attempts += 1
            return job

    async def _execute(self, job: Job) -> None:
        handler = self._handlers.get(job.type)
        try:
            if handler is None:
                raise RuntimeError(f"no handler registered for job type {job.type!r}")
            await handler(self._ctx, dict(job.payload))
        except Exception as exc:
            await self._record_failure(job, f"{type(exc).__name__}: {exc}")
        else:
            async with self._db.write_session() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job.id)
                    .values(status="done", finished_at=now_ms(), locked_at=None)
                )

    async def _record_failure(self, job: Job, error: str) -> None:
        retry = job.attempts < MAX_ATTEMPTS
        values: dict[str, Any] = {"last_error": error[:2000], "locked_at": None}
        if retry:
            backoff_ms = (2**job.attempts) * 1000
            values.update(status="queued", run_at=now_ms() + backoff_ms)
        else:
            values.update(status="dead", finished_at=now_ms())
        async with self._db.write_session() as session:
            await session.execute(update(Job).where(Job.id == job.id).values(**values))
        log.warning(
            "job_failed",
            job_id=str(job.id),
            job_type=job.type,
            attempts=job.attempts,
            retrying=retry,
            error=error[:200],
        )
