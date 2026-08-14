"""Provider & model registry (§8).

Caches model lists (TTL 1h, manual refresh endpoint), merges admin allow/deny
policies, tags capabilities, and resolves credentials per request:
user key -> org key -> provider env var (litellm reads env itself).
"""

import fnmatch
import os
import time
import uuid
from typing import Any

import orjson
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.config import Settings
from retinue.core.crypto import SecretBox
from retinue.db.models import Credential, ModelPolicy
from retinue.providers.base import ChatCall, ModelInfo, ProviderAdapter
from retinue.providers.litellm_adapter import LiteLLMAdapter
from retinue.providers.mock_adapter import MOCK_MODELS, MockAdapter

log = structlog.get_logger("retinue.registry")

# env var -> litellm provider slug; presence alone makes a provider usable
ENV_PROVIDER_KEYS = {
    "OPENAI_API_KEY": "openai",
    "ANTHROPIC_API_KEY": "anthropic",
    "GEMINI_API_KEY": "gemini",
    "GROQ_API_KEY": "groq",
    "MISTRAL_API_KEY": "mistral",
    "DEEPSEEK_API_KEY": "deepseek",
    "OPENROUTER_API_KEY": "openrouter",
    "XAI_API_KEY": "xai",
    "TOGETHERAI_API_KEY": "together_ai",
    "OLLAMA_API_BASE": "ollama",
}

_MAX_MODELS_PER_PROVIDER = 80


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else "openai"


class ProviderRegistry:
    def __init__(self, settings: Settings, secret_box: SecretBox) -> None:
        self._settings = settings
        self._box = secret_box
        self._litellm = LiteLLMAdapter()
        self._mock = MockAdapter()
        self._models_cache: tuple[float, list[ModelInfo]] | None = None

    # -- adapters -----------------------------------------------------------

    def adapter_for(self, model: str) -> ProviderAdapter:
        if model.startswith("mock/"):
            return self._mock
        return self._litellm

    # -- credentials ----------------------------------------------------------

    async def resolve_credential(
        self, session: AsyncSession, user_id: uuid.UUID | None, provider: str
    ) -> tuple[str | None, str | None]:
        """Returns (api_key, base_url). (None, None) => litellm falls back to env."""
        rows = (
            (
                await session.execute(
                    select(Credential).where(
                        Credential.kind == "llm", Credential.provider == provider
                    )
                )
            )
            .scalars()
            .all()
        )
        chosen: Credential | None = None
        for row in rows:
            if user_id is not None and row.user_id == user_id:
                chosen = row
                break
            if row.user_id is None and chosen is None:
                chosen = row  # org-global fallback
        if chosen is None:
            return None, None
        try:
            payload: dict[str, Any] = orjson.loads(
                self._box.decrypt(chosen.data_ciphertext, chosen.data_nonce)
            )
        except Exception:
            log.error("credential_decrypt_failed", credential_id=str(chosen.id))
            return None, None
        return payload.get("api_key"), chosen.base_url

    async def prepare_call(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID | None,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> ChatCall:
        api_key, base_url = (None, None)
        if not model.startswith("mock/"):
            api_key, base_url = await self.resolve_credential(session, user_id, provider_of(model))
        return ChatCall(
            model=model, messages=messages, params=params, api_key=api_key, api_base=base_url
        )

    # -- model catalog --------------------------------------------------------

    async def configured_providers(self, session: AsyncSession) -> list[str]:
        providers: dict[str, None] = {}  # ordered set
        rows = await session.execute(select(Credential.provider).where(Credential.kind == "llm"))
        for (provider,) in rows:
            providers.setdefault(provider)
        for env_var, provider in ENV_PROVIDER_KEYS.items():
            if os.environ.get(env_var):
                providers.setdefault(provider)
        return list(providers)

    def _curated_for_provider(self, provider: str) -> list[ModelInfo]:
        try:
            import litellm

            names = list(litellm.models_by_provider.get(provider, []))
        except Exception:
            names = []
        models: list[ModelInfo] = []
        for name in names[: _MAX_MODELS_PER_PROVIDER * 2]:
            bare = name.split("/", 1)[1] if name.startswith(f"{provider}/") else name
            info = self.model_info(f"{provider}/{bare}", quiet=True)
            if info.context_window <= 0:
                continue
            models.append(info)
            if len(models) >= _MAX_MODELS_PER_PROVIDER:
                break
        models.sort(key=lambda m: m.id)
        return models

    def model_info(self, model: str, quiet: bool = False) -> ModelInfo:
        """Info for any model string; never raises — defaults keep chat working."""
        provider = provider_of(model)
        bare = model.split("/", 1)[1] if "/" in model else model
        if model.startswith("mock/"):
            for m in MOCK_MODELS:
                if m.id == model:
                    return m
            return ModelInfo(id=model, provider="mock", display_name=bare)
        entry: dict[str, Any] = {}
        try:
            import litellm

            entry = dict(litellm.get_model_info(model=model))
        except Exception:
            if not quiet:
                log.debug("model_info_unknown", model=model)
        mode = entry.get("mode")
        if mode not in (None, "chat", "responses"):
            return ModelInfo(id=model, provider=provider, display_name=bare, context_window=0)
        in_cost = entry.get("input_cost_per_token")
        out_cost = entry.get("output_cost_per_token")
        return ModelInfo(
            id=model,
            provider=provider,
            display_name=bare,
            context_window=entry.get("max_input_tokens") or entry.get("max_tokens") or 128_000,
            max_output_tokens=entry.get("max_output_tokens") or 4096,
            supports_vision=bool(entry.get("supports_vision")),
            supports_tools=bool(entry.get("supports_function_calling")),
            input_cost_per_mtok=round(in_cost * 1_000_000, 4) if in_cost else None,
            output_cost_per_mtok=round(out_cost * 1_000_000, 4) if out_cost else None,
        )

    async def list_models(self, session: AsyncSession) -> list[ModelInfo]:
        now = time.monotonic()
        if self._models_cache and now - self._models_cache[0] < self._settings.models.list_ttl_s:
            return self._models_cache[1]

        models: list[ModelInfo] = []
        if self._settings.models.mock_enabled:
            models.extend(MOCK_MODELS)
        for provider in await self.configured_providers(session):
            models.extend(self._curated_for_provider(provider))

        policies = (await session.execute(select(ModelPolicy))).scalars().all()
        denies = [p.pattern for p in policies if not p.allow]
        allows = [p.pattern for p in policies if p.allow]
        if denies:
            models = [m for m in models if not any(fnmatch.fnmatch(m.id, d) for d in denies)]
        if allows:
            models = [m for m in models if any(fnmatch.fnmatch(m.id, a) for a in allows)]

        self._models_cache = (now, models)
        return models

    def invalidate_models_cache(self) -> None:
        self._models_cache = None

    async def default_model(
        self, session: AsyncSession, user_settings: dict[str, Any]
    ) -> str | None:
        configured = user_settings.get("default_model") or self._settings.models.default
        if configured:
            return str(configured)
        models = await self.list_models(session)
        return models[0].id if models else None
