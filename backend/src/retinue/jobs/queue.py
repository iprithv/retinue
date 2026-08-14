"""Job queue (§15): a jobs table plus an in-process wake signal.

The Redis/arq path (Org bundle) keeps the same enqueue signature.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from retinue.core.ids import uuid7
from retinue.core.timeutil import now_ms
from retinue.db.models import Job
from retinue.db.session import Database


class JobQueue:
    def __init__(self, db: Database) -> None:
        self._db = db
        self.wake = asyncio.Event()

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        priority: int = 5,
        run_at: int | None = None,
        session: AsyncSession | None = None,
    ) -> uuid.UUID:
        job = Job(
            id=uuid7(),
            type=job_type,
            payload=payload,
            priority=priority,
            run_at=run_at if run_at is not None else now_ms(),
        )
        if session is not None:
            session.add(job)  # caller's transaction
        else:
            async with self._db.write_session() as own:
                own.add(job)
        self.wake.set()
        return job.id
