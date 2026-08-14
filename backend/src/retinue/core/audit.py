"""Audit log helper (§16): auth events, key touches, admin actions."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from retinue.core.ids import uuid7
from retinue.db.models import AuditLog


def audit(
    session: AsyncSession,
    *,
    action: str,
    actor_id: uuid.UUID | None = None,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Adds a row to the caller's transaction; commits with it."""
    session.add(
        AuditLog(
            id=uuid7(),
            actor_id=actor_id,
            action=action,
            target=target,
            meta=meta or {},
            ip=ip,
        )
    )
