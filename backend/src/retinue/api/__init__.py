"""API surface (§18). Everything mounts under /api; additive-only until 2.0."""

from fastapi import APIRouter

from retinue.api import apikeys, auth, chat, conversations, models, system, usage


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth.router, prefix="/auth", tags=["auth"])
    router.include_router(chat.router, tags=["chat"])
    router.include_router(conversations.router, tags=["conversations"])
    router.include_router(models.router, tags=["models"])
    router.include_router(apikeys.router, prefix="/keys", tags=["keys"])
    router.include_router(usage.router, prefix="/usage", tags=["usage"])
    router.include_router(system.router, tags=["system"])
    return router
