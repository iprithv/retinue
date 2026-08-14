"""Auth endpoints (§16, §18).

Browser flow: access token lives in JS memory (returned in JSON); the refresh
token is an httpOnly SameSite=Lax cookie scoped to /api/auth, paired with a
double-submit CSRF cookie+header. Programmatic flow: refresh token in the JSON
body, no cookies. Refresh tokens rotate; presenting an already-rotated token
revokes the whole family (§16 reuse detection).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.api.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
    UserPatch,
)
from retinue.core.audit import audit
from retinue.core.crypto import hash_token
from retinue.core.deps import client_ip, get_current_user, rate_limit
from retinue.core.errors import (
    CSRF_FAILED,
    REGISTRATION_DISABLED,
    UNAUTHORIZED,
    VALIDATION_ERROR,
    AppError,
)
from retinue.core.ids import uuid7
from retinue.core.security import new_csrf_token, new_refresh_token
from retinue.core.state import AppState, get_state
from retinue.core.timeutil import now_ms
from retinue.db.models import RefreshToken, User

router = APIRouter()

REFRESH_COOKIE = "retinue_refresh"
CSRF_COOKIE = "retinue_csrf"
CSRF_HEADER = "x-csrf-token"


def _cookie_secure(state: AppState, request: Request) -> bool:
    configured = state.settings.auth.cookie_secure
    if configured is not None:
        return configured
    forwarded = request.headers.get("x-forwarded-proto")
    scheme = forwarded or request.url.scheme
    return scheme == "https"


async def _issue_pair(
    state: AppState,
    session: AsyncSession,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    rotate_from: RefreshToken | None = None,
) -> tuple[str, str]:
    """Returns (access_token, raw_refresh_token); rows go into the caller's txn."""
    access = state.tokens.make_access_token(
        user_id=user.id, role=user.role, session_version=user.session_version
    )
    raw_refresh = new_refresh_token()
    row = RefreshToken(
        id=uuid7(),
        user_id=user.id,
        family_id=family_id or uuid7(),
        token_hash=hash_token(raw_refresh),
        expires_at=now_ms() + state.settings.auth.refresh_ttl_days * 86_400_000,
    )
    session.add(row)
    if rotate_from is not None:
        rotate_from.rotated_to = row.id
    return access, raw_refresh


def _set_cookies(state: AppState, request: Request, response: Response, raw_refresh: str) -> str:
    secure = _cookie_secure(state, request)
    max_age = state.settings.auth.refresh_ttl_days * 86_400
    response.set_cookie(
        REFRESH_COOKIE,
        raw_refresh,
        max_age=max_age,
        path="/api/auth",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    csrf = new_csrf_token()
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        path="/",
        httponly=False,  # double-submit: JS must read it back as a header
        samesite="lax",
        secure=secure,
    )
    return csrf


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _token_response(
    state: AppState, access: str, user: User, refresh_token: str | None = None
) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        expires_in=state.settings.auth.access_ttl_s,
        user=UserOut.model_validate(user),
        refresh_token=refresh_token,
    )


@router.post("/register", dependencies=[Depends(rate_limit("auth"))])
async def register(body: RegisterRequest, request: Request, response: Response) -> TokenResponse:
    state = get_state(request)
    email = body.email.lower()
    async with state.db.write_session() as session:
        user_count = (await session.execute(select(func.count(User.id)))).scalar_one()
        if user_count > 0 and not state.settings.auth.registration_enabled:
            raise AppError(REGISTRATION_DISABLED, "registration is disabled", status=403)
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            raise AppError(VALIDATION_ERROR, "email is already registered", status=409)
        user = User(
            id=uuid7(),
            email=email,
            name=body.name,
            password_hash=state.passwords.hash(body.password),
            role="owner" if user_count == 0 else "member",
        )
        session.add(user)
        await session.flush()  # user row must exist before its refresh token (FK)
        access, raw_refresh = await _issue_pair(state, session, user)
        audit(session, action="user.register", actor_id=user.id, ip=client_ip(request))
    _set_cookies(state, request, response, raw_refresh)
    return _token_response(state, access, user, refresh_token=raw_refresh)


@router.post("/login", dependencies=[Depends(rate_limit("auth"))])
async def login(body: LoginRequest, request: Request, response: Response) -> TokenResponse:
    state = get_state(request)
    email = body.email.lower()
    async with state.db.read_session() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    password_ok = (
        user is not None
        and user.password_hash is not None
        and state.passwords.verify(user.password_hash, body.password)
    )
    if user is None or not password_ok or not user.is_active:
        if user is None:
            state.passwords.hash("timing-equalizer")  # equalize timing for unknown emails
        # audit in its own transaction — the 401 below must not roll it back
        async with state.db.write_session() as session:
            audit(session, action="auth.login_failed", target=email, ip=client_ip(request))
        raise AppError(UNAUTHORIZED, "invalid email or password", status=401)

    async with state.db.write_session() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        if state.passwords.needs_rehash(db_user.password_hash or ""):
            db_user.password_hash = state.passwords.hash(body.password)
        access, raw_refresh = await _issue_pair(state, session, db_user)
        audit(session, action="auth.login", actor_id=db_user.id, ip=client_ip(request))
        user = db_user
    _set_cookies(state, request, response, raw_refresh)
    return _token_response(state, access, user, refresh_token=raw_refresh)


def _refresh_from_request(request: Request, body: RefreshRequest | None) -> tuple[str, bool]:
    """Returns (raw_token, via_cookie)."""
    if body is not None and body.refresh_token:
        return body.refresh_token, False
    cookie = request.cookies.get(REFRESH_COOKIE)
    if cookie:
        return cookie, True
    raise AppError(UNAUTHORIZED, "no refresh token provided", status=401)


def _check_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or cookie != header:
        raise AppError(CSRF_FAILED, "CSRF token missing or mismatched", status=403)


@router.post("/refresh", dependencies=[Depends(rate_limit("auth"))])
async def refresh(
    request: Request, response: Response, body: RefreshRequest | None = None
) -> TokenResponse:
    state = get_state(request)
    raw, via_cookie = _refresh_from_request(request, body)
    if via_cookie:
        _check_csrf(request)
    token_hash = hash_token(raw)
    reuse_detected = False
    async with state.db.write_session() as session:
        row = (
            await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if row is None:
            raise AppError(UNAUTHORIZED, "invalid refresh token", status=401)
        if row.rotated_to is not None:
            # reuse detected -> revoke the entire family (§16). The revocation
            # must COMMIT, so the 401 is raised only after this block exits.
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now_ms())
            )
            audit(
                session,
                action="auth.refresh_reuse_detected",
                actor_id=row.user_id,
                target=str(row.family_id),
                ip=client_ip(request),
            )
            reuse_detected = True
        else:
            if row.revoked_at is not None or row.expires_at < now_ms():
                raise AppError(UNAUTHORIZED, "refresh token expired or revoked", status=401)
            user = await session.get(User, row.user_id)
            if user is None or not user.is_active:
                raise AppError(UNAUTHORIZED, "account is disabled", status=401)
            access, raw_refresh = await _issue_pair(
                state, session, user, family_id=row.family_id, rotate_from=row
            )
    if reuse_detected:
        raise AppError(UNAUTHORIZED, "refresh token reuse detected; session revoked", status=401)
    assert user is not None  # set on every non-reuse path above
    if via_cookie:
        _set_cookies(state, request, response, raw_refresh)
        return _token_response(state, access, user)
    # programmatic callers get the rotated refresh token in the body
    return _token_response(state, access, user, refresh_token=raw_refresh)


@router.post("/logout")
async def logout(
    request: Request, response: Response, body: RefreshRequest | None = None
) -> dict[str, bool]:
    state = get_state(request)
    try:
        raw, via_cookie = _refresh_from_request(request, body)
    except AppError:
        _clear_cookies(response)
        return {"ok": True}
    if via_cookie:
        _check_csrf(request)
    async with state.db.write_session() as session:
        row = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
            )
        ).scalar_one_or_none()
        if row is not None:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now_ms())
            )
            audit(session, action="auth.logout", actor_id=row.user_id, ip=client_ip(request))
    _clear_cookies(response)
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me")
async def patch_me(
    body: UserPatch, request: Request, user: Annotated[User, Depends(get_current_user)]
) -> UserOut:
    state = get_state(request)
    async with state.db.write_session() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        if body.name is not None:
            db_user.name = body.name
        if body.settings is not None:
            db_user.settings = {**db_user.settings, **body.settings}
        merged = db_user
    return UserOut.model_validate(merged)


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
) -> TokenResponse:
    state = get_state(request)
    async with state.db.write_session() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        if not db_user.password_hash or not state.passwords.verify(
            db_user.password_hash, body.current_password
        ):
            raise AppError(UNAUTHORIZED, "current password is incorrect", status=401)
        db_user.password_hash = state.passwords.hash(body.new_password)
        db_user.session_version += 1  # invalidates every outstanding access token
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == db_user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now_ms())
        )
        access, raw_refresh = await _issue_pair(state, session, db_user)
        audit(session, action="auth.password_change", actor_id=db_user.id, ip=client_ip(request))
        fresh = db_user
    _set_cookies(state, request, response, raw_refresh)
    return _token_response(state, access, fresh)
