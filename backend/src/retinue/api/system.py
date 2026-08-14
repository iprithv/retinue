"""Ops endpoints (§18): healthz, readyz, guarded image proxy. Data takeout
lives in api.dataio."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

import retinue
from retinue.api.schemas import HealthOut
from retinue.core.deps import get_current_user
from retinue.core.egress import fetch_guarded
from retinue.core.errors import EGRESS_DENIED, NOT_READY, AppError, error_body, json_response
from retinue.core.state import get_state
from retinue.db.models import User

router = APIRouter()


@router.get("/healthz")
async def healthz() -> HealthOut:
    return HealthOut(status="ok", name="retinue", version=retinue.__version__)


@router.get("/readyz")
async def readyz(request: Request) -> Response:
    state = get_state(request)
    checks: dict[str, bool] = {}
    try:
        await state.db.ping()
        checks["database"] = True
    except Exception:
        checks["database"] = False
    try:
        probe = state.settings.resolved_data_dir / ".ready-probe"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        checks["data_dir"] = True
    except Exception:
        checks["data_dir"] = False
    if all(checks.values()):
        return json_response({"status": "ready", "checks": checks})
    return json_response(
        error_body(NOT_READY, "service not ready", retryable=True, details=checks),
        status_code=503,
    )


@router.get("/proxy/img")
async def proxy_image(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    url: str = Query(min_length=1, max_length=2000),
) -> Response:
    """SSRF-guarded image fetch (§6.4) so the strict CSP can stay `img-src 'self'`."""
    state = get_state(request)
    if not state.settings.image_proxy_enabled:
        raise AppError(EGRESS_DENIED, "image proxy is disabled", status=403)
    body, content_type = await fetch_guarded(url)
    if not content_type.lower().startswith("image/"):
        raise AppError(EGRESS_DENIED, "URL did not return an image", status=400)
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
