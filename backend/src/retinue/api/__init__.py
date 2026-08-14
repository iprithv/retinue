"""API surface (§18). Everything mounts under /api; additive-only until 2.0."""

from fastapi import APIRouter

from retinue.api import (
    admin,
    agents,
    apikeys,
    auth,
    chat,
    collections,
    connectors,
    conversations,
    dataio,
    datasources,
    files,
    memories,
    models,
    search,
    shares,
    system,
    tools,
    usage,
)


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth.router, prefix="/auth", tags=["auth"])
    router.include_router(chat.router, tags=["chat"])
    router.include_router(conversations.router, tags=["conversations"])
    router.include_router(agents.router, tags=["agents"])
    router.include_router(files.router, tags=["files"])
    router.include_router(collections.router, tags=["collections"])
    router.include_router(datasources.router, tags=["datasources"])
    router.include_router(connectors.router, tags=["connectors"])
    router.include_router(search.router, tags=["search"])
    router.include_router(memories.router, tags=["memories"])
    router.include_router(tools.router, tags=["tools"])
    router.include_router(shares.router, tags=["shares"])
    router.include_router(dataio.router, tags=["data"])
    router.include_router(admin.router, tags=["admin"])
    router.include_router(models.router, tags=["models"])
    router.include_router(apikeys.router, prefix="/keys", tags=["keys"])
    router.include_router(usage.router, prefix="/usage", tags=["usage"])
    router.include_router(system.router, tags=["system"])
    return router
