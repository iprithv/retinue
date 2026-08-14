"""Job handler registry (§15)."""

from retinue.jobs.handlers.titles import generate_title
from retinue.jobs.worker import Handler


def builtin_handlers() -> dict[str, Handler]:
    return {
        "generate_title": generate_title,
    }
