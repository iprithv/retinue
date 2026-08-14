"""structlog setup (§23): JSON logs to stdout, request-id bound via contextvars."""

import logging
import sys
from typing import Any

import orjson
import structlog


def _orjson_dumps(value: Any, **kwargs: Any) -> str:
    return orjson.dumps(value).decode("utf-8")


def configure_logging(level: str = "info", fmt: str = "console") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    renderer: structlog.typing.Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer(serializer=_orjson_dumps)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=log_level, format="%(message)s", stream=sys.stderr, force=True)
    # our request log replaces uvicorn's access log; provider SDKs are chatty
    for noisy in ("uvicorn.access", "httpx", "httpcore", "LiteLLM", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
