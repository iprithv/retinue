"""Request dependencies: bearer auth (JWT or API key), role checks, rate limits."""

import uuid
from collections.abc import Awaitable, Callable

import jwt as pyjwt
from fastapi import Depends, Request
from sqlalchemy import select, update

from retinue.core.crypto import hash_token
from retinue.core.errors import FORBIDDEN, RATE_LIMITED, UNAUTHORIZED, AppError
from retinue.core.security import API_KEY_PREFIX
from retinue.core.state import get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import ApiKey, User

_API_KEY_TOUCH_INTERVAL_MS = 60_000


def _unauthorized(message: str = "authentication required") -> AppError:
    return AppError(UNAUTHORIZED, message, status=401)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _user_from_api_key(request: Request, token: str) -> User:
    state = get_state(request)
    key_hash = hash_token(token)
    async with state.db.read_session() as session:
        key = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        ).scalar_one_or_none()
        if key is None:
            raise _unauthorized("invalid API key")
        if key.expires_at is not None and key.expires_at < now_ms():
            raise _unauthorized("API key expired")
        user = await session.get(User, key.user_id)
    if user is None or not user.is_active:
        raise _unauthorized("account is disabled")
    if key.last_used_at is None or now_ms() - key.last_used_at > _API_KEY_TOUCH_INTERVAL_MS:
        async with state.db.write_session() as wsession:
            await wsession.execute(
                update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now_ms())
            )
    return user


async def _user_from_jwt(request: Request, token: str) -> User:
    state = get_state(request)
    try:
        claims = state.tokens.decode_access_token(token)
    except pyjwt.InvalidTokenError as exc:
        raise _unauthorized("invalid or expired token") from exc
    try:
        user_id = uuid.UUID(str(claims.get("sub", "")))
    except ValueError as exc:
        raise _unauthorized("invalid token subject") from exc
    async with state.db.read_session() as session:
        user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("account is disabled")
    if user.session_version != claims.get("sv"):
        raise _unauthorized("session revoked")
    return user


async def get_current_user(request: Request) -> User:
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise _unauthorized()
    token = authorization[7:].strip()
    if not token:
        raise _unauthorized()
    user = (
        await _user_from_api_key(request, token)
        if token.startswith(API_KEY_PREFIX)
        else await _user_from_jwt(request, token)
    )
    request.state.user = user  # rate limiting keys on identity when present
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("owner", "admin"):
        raise AppError(FORBIDDEN, "admin role required", status=403)
    return user


def rate_limit(route_class: str) -> Callable[[Request], Awaitable[None]]:
    async def dependency(request: Request) -> None:
        state = get_state(request)
        cfg = state.settings.ratelimit
        if not cfg.enabled:
            return
        rate = float(getattr(cfg, f"{route_class}_per_min", cfg.default_per_min))
        user = getattr(request.state, "user", None)
        who = str(user.id) if user is not None else client_ip(request)
        retry_after = state.limiter.check(f"{route_class}:{who}", rate, rate * cfg.burst_factor)
        if retry_after is not None:
            raise AppError(
                RATE_LIMITED,
                "rate limit exceeded",
                status=429,
                retryable=True,
                details={"retry_after_s": round(retry_after, 2)},
            )

    return dependency
