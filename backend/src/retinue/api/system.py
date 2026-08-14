"""Ops + data endpoints (§18): healthz, readyz, export, guarded image proxy."""

import io
import zipfile
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select

import retinue
from retinue.api.schemas import HealthOut
from retinue.core.deps import get_current_user
from retinue.core.egress import fetch_guarded
from retinue.core.errors import EGRESS_DENIED, NOT_READY, AppError, error_body, json_response
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import Conversation, Message, MessagePart, User

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


@router.get("/export")
async def export_data(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> Response:
    """User data takeout: conversations + messages as JSONL in a zip (§18 Data)."""
    state = get_state(request)
    async with state.db.read_session() as session:
        conversations = (
            (await session.execute(select(Conversation).where(Conversation.user_id == user.id)))
            .scalars()
            .all()
        )
        conv_ids = [c.id for c in conversations]
        messages = []
        parts_by_message: dict = {}
        if conv_ids:
            messages = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id.in_(conv_ids))
                        .order_by(Message.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for part in (
                (
                    await session.execute(
                        select(MessagePart).where(
                            MessagePart.message_id.in_([m.id for m in messages])
                        )
                    )
                )
                .scalars()
                .all()
            ):
                parts_by_message.setdefault(part.message_id, []).append(part)

    conv_lines = b"\n".join(
        orjson.dumps(
            {
                "id": str(c.id),
                "title": c.title,
                "model_override": c.model_override,
                "pinned": c.pinned,
                "folder": c.folder,
                "created_at": c.created_at,
                "last_message_at": c.last_message_at,
            }
        )
        for c in conversations
    )
    msg_lines = b"\n".join(
        orjson.dumps(
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "parent_id": str(m.parent_id) if m.parent_id else None,
                "role": m.role,
                "status": m.status,
                "model": m.model,
                "created_at": m.created_at,
                "parts": [
                    {"idx": p.idx, "type": p.type, "content": p.content}
                    for p in sorted(parts_by_message.get(m.id, []), key=lambda p: p.idx)
                ],
            }
        )
        for m in messages
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.jsonl", conv_lines)
        archive.writestr("messages.jsonl", msg_lines)
        archive.writestr(
            "manifest.json",
            orjson.dumps(
                {
                    "app": "retinue",
                    "version": retinue.__version__,
                    "exported_at": now_ms(),
                    "user": user.email,
                }
            ),
        )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="retinue-export.zip"'},
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
