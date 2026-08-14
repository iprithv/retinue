"""Uniform error envelope (§18): {"error": {code, message, retryable, details}}."""

from typing import Any

import orjson
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

log = structlog.get_logger("retinue.errors")


def json_response(
    content: Any, status_code: int = 200, headers: dict[str, str] | None = None
) -> Response:
    """orjson-encoded JSON response (D3) without FastAPI's deprecated class."""
    return Response(
        content=orjson.dumps(content),
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


# Stable, documented code enum (§18). Additive-only.
UNAUTHORIZED = "unauthorized"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
VALIDATION_ERROR = "validation_error"
RATE_LIMITED = "rate_limited"
NO_MODEL_CONFIGURED = "no_model_configured"
PROVIDER_ERROR = "provider_error"
CONTEXT_OVERFLOW = "context_overflow"
REGISTRATION_DISABLED = "registration_disabled"
CSRF_FAILED = "csrf_failed"
INTERNAL_ERROR = "internal_error"
NOT_READY = "not_ready"
EGRESS_DENIED = "egress_denied"


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = details or {}


def error_body(
    code: str, message: str, retryable: bool = False, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
    }


_STATUS_CODES = {
    401: UNAUTHORIZED,
    403: FORBIDDEN,
    404: NOT_FOUND,
    409: CONFLICT,
    422: VALIDATION_ERROR,
    429: RATE_LIMITED,
}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> Response:
        headers: dict[str, str] = {}
        if exc.code == RATE_LIMITED and "retry_after_s" in exc.details:
            headers["Retry-After"] = str(max(1, int(exc.details["retry_after_s"])))
        return json_response(
            error_body(exc.code, exc.message, exc.retryable, exc.details),
            status_code=exc.status,
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        code = _STATUS_CODES.get(
            exc.status_code, INTERNAL_ERROR if exc.status_code >= 500 else VALIDATION_ERROR
        )
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return json_response(
            error_body(code, message),
            status_code=exc.status_code,
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        return json_response(
            error_body(
                VALIDATION_ERROR,
                "request validation failed",
                details={"errors": [{k: str(v) for k, v in e.items()} for e in exc.errors()]},
            ),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        log.exception("unhandled_error", path=request.url.path)
        return json_response(
            error_body(INTERNAL_ERROR, "internal server error", retryable=True),
            status_code=500,
        )
