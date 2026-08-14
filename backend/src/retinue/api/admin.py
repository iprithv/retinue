"""Admin surface (§18): users, audit log, jobs, app settings, org usage."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from retinue.api.schemas import (
    AdminSettingsOut,
    AdminSettingsPatch,
    AdminUserOut,
    AdminUserPatch,
    AuditEntryOut,
    JobOut,
)
from retinue.core.deps import get_admin_user
from retinue.core.errors import CONFLICT, NOT_FOUND, AppError
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import AppSetting, AuditLog, Job, UsageEvent, User

router = APIRouter()


@router.get("/admin/users")
async def list_users(
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
    limit: int = Query(200, ge=1, le=1000),
) -> list[AdminUserOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (await session.execute(select(User).order_by(User.created_at).limit(limit)))
            .scalars()
            .all()
        )
    return [AdminUserOut.model_validate(u) for u in rows]


@router.patch("/admin/users/{user_id}")
async def patch_user(
    user_id: uuid.UUID,
    body: AdminUserPatch,
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
) -> AdminUserOut:
    state = get_state(request)
    updates = body.model_dump(exclude_unset=True)
    async with state.db.write_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise AppError(NOT_FOUND, "user not found", status=404)
        if user.role == "owner" and updates.get("role") not in (None, "owner"):
            raise AppError(CONFLICT, "the owner role cannot be demoted here", status=409)
        for field, value in updates.items():
            setattr(user, field, value)
        if updates.get("is_active") is False:
            user.session_version += 1  # global logout on deactivation (§16)
        result = user
    return AdminUserOut.model_validate(result)


@router.get("/admin/audit")
async def audit_log(
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
    limit: int = Query(200, ge=1, le=1000),
) -> list[AuditEntryOut]:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [AuditEntryOut.model_validate(a) for a in rows]


@router.get("/admin/jobs")
async def list_jobs(
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
    status: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[JobOut]:
    state = get_state(request)
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status:
        query = query.where(Job.status == status)
    async with state.db.read_session() as session:
        rows = (await session.execute(query)).scalars().all()
    return [JobOut.model_validate(j) for j in rows]


@router.post("/admin/jobs/{job_id}/retry")
async def retry_job(
    job_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
) -> JobOut:
    state = get_state(request)
    async with state.db.write_session() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise AppError(NOT_FOUND, "job not found", status=404)
        if job.status not in ("failed", "dead"):
            raise AppError(CONFLICT, "only failed/dead jobs can be retried", status=409)
        job.status = "queued"
        job.attempts = 0
        job.locked_at = None
        job.run_at = now_ms()
        result = job
    state.jobs.wake.set()
    return JobOut.model_validate(result)


@router.get("/admin/settings")
async def get_settings(
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
) -> AdminSettingsOut:
    state = get_state(request)
    async with state.db.read_session() as session:
        rows = (await session.execute(select(AppSetting))).scalars().all()
    return AdminSettingsOut(settings={row.key: row.value for row in rows})


@router.patch("/admin/settings")
async def patch_settings(
    body: AdminSettingsPatch,
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
) -> AdminSettingsOut:
    state = get_state(request)
    async with state.db.write_session() as session:
        for key, value in body.settings.items():
            row = await session.get(AppSetting, key)
            if row is None:
                session.add(AppSetting(key=key, value=value))
            else:
                row.value = value
    return await get_settings(request, admin)


@router.get("/admin/usage")
async def org_usage(
    request: Request,
    admin: Annotated[User, Depends(get_admin_user)],
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    state = get_state(request)
    since = now_ms() - days * 86_400_000
    async with state.db.read_session() as session:
        by_user = (
            await session.execute(
                select(
                    UsageEvent.user_id,
                    func.sum(UsageEvent.input_tokens),
                    func.sum(UsageEvent.output_tokens),
                    func.sum(UsageEvent.cost_usd),
                    func.count(),
                )
                .where(UsageEvent.created_at >= since)
                .group_by(UsageEvent.user_id)
            )
        ).all()
        email_rows = (
            await session.execute(
                select(User.id, User.email).where(
                    User.id.in_([r[0] for r in by_user] or [uuid.uuid4()])
                )
            )
        ).all()
        emails: dict[uuid.UUID, str] = {row[0]: row[1] for row in email_rows}
    return {
        "days": days,
        "by_user": [
            {
                "user_id": str(user_id),
                "email": emails.get(user_id, "?"),
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "cost_usd": round(float(cost or 0.0), 6),
                "messages": int(count),
            }
            for user_id, input_tokens, output_tokens, cost, count in by_user
        ],
    }
