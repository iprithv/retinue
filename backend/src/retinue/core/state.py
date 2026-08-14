"""Shared application state assembled in the lifespan (§7.3) and reached from
request handlers via `get_state(request)`."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from retinue.config import Settings
    from retinue.core.chat_engine import ChatEngine
    from retinue.core.crypto import SecretBox
    from retinue.core.ratelimit import RateLimiter
    from retinue.core.security import PasswordService, TokenService
    from retinue.core.streams import StreamHub
    from retinue.core.tokens import TokenCounter
    from retinue.db.session import Database
    from retinue.jobs.queue import JobQueue
    from retinue.jobs.worker import JobWorker
    from retinue.providers.pricing import PricingTable
    from retinue.providers.registry import ProviderRegistry


@dataclass
class AppState:
    settings: "Settings"
    db: "Database"
    hub: "StreamHub"
    registry: "ProviderRegistry"
    pricing: "PricingTable"
    jobs: "JobQueue"
    worker: "JobWorker"
    engine: "ChatEngine"
    tokens: "TokenService"
    passwords: "PasswordService"
    box: "SecretBox"
    limiter: "RateLimiter"
    counter: "TokenCounter"


def get_state(request: Request) -> AppState:
    return request.app.state.retinue  # type: ignore[no-any-return]
