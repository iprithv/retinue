"""Usage & cost summary (§18) — first-party dashboards from usage_events (§23)."""

import datetime
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from retinue.api.schemas import UsageByDay, UsageByModel, UsageSummaryOut, UsageTotals
from retinue.core.deps import get_current_user
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import UsageEvent, User

router = APIRouter()


@router.get("/summary")
async def usage_summary(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    days: int = Query(30, ge=1, le=365),
) -> UsageSummaryOut:
    state = get_state(request)
    since = now_ms() - days * 86_400_000
    base = select(
        func.coalesce(func.sum(UsageEvent.input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cached_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
        func.count(UsageEvent.id),
    ).where(UsageEvent.user_id == user.id, UsageEvent.created_at >= since)

    async with state.db.read_session() as session:
        totals_row = (await session.execute(base)).one()
        model_rows = (
            await session.execute(base.add_columns(UsageEvent.model).group_by(UsageEvent.model))
        ).all()
        day_expr = UsageEvent.created_at.op("/")(86_400_000)
        day_rows = (await session.execute(base.add_columns(day_expr).group_by(day_expr))).all()

    def totals(row: Sequence[Any]) -> dict[str, Any]:
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "cached_tokens": int(row[2]),
            "cost_usd": round(float(row[3]), 6),
            "messages": int(row[4]),
        }

    by_day = [
        UsageByDay(
            **totals(row),
            day=datetime.datetime.fromtimestamp(int(row[5]) * 86_400, tz=datetime.UTC).strftime(
                "%Y-%m-%d"
            ),
        )
        for row in sorted(day_rows, key=lambda r: int(r[5]))
    ]
    return UsageSummaryOut(
        days=days,
        totals=UsageTotals(**totals(totals_row)),
        by_model=sorted(
            (UsageByModel(**totals(row), model=str(row[5])) for row in model_rows),
            key=lambda m: -m.cost_usd,
        ),
        by_day=by_day,
    )
