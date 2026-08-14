"""Embeddings (§10) with the §31.3 content-hash cache.

Vectors travel as packed little-endian float32 BLOBs; similarity is cosine.
Brute-force scan is the Solo-scale engine — honest to the documented ceilings;
the seam (this module) swaps for sqlite-vec/pgvector without touching callers.

Models: `provider/model` via LiteLLM; `mock/embed` is a deterministic local
bag-of-words hash embedding for keyless dev and tests.
"""

import hashlib
import math
import re
import struct
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from blake3 import blake3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.core.timeutil import now_ms
from retinue.db.models import EmbedCache
from retinue.providers.base import ProviderError

if TYPE_CHECKING:
    from retinue.core.state import AppState

log = structlog.get_logger("retinue.rag.embed")

MOCK_EMBED_MODEL = "mock/embed"
MOCK_DIM = 64
_WORD_RE = re.compile(r"[a-z0-9]+")


def pack_vector(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def mock_embedding(text: str) -> list[float]:
    """Deterministic bag-of-words hash embedding: same words → similar vectors."""
    vec = [0.0] * MOCK_DIM
    for word in _WORD_RE.findall(text.lower()):
        h = hashlib.blake2s(word.encode(), digest_size=4).digest()
        slot = int.from_bytes(h, "little")
        vec[slot % MOCK_DIM] += 1.0 if (slot >> 16) % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def default_embed_model(state: "AppState") -> str:
    configured = state.settings.models.embedding
    if configured:
        return configured
    if state.settings.models.mock_enabled:
        return MOCK_EMBED_MODEL
    return "openai/text-embedding-3-small"


async def _provider_embed(
    state: "AppState",
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    model: str,
    texts: list[str],
) -> list[list[float]]:
    if model == MOCK_EMBED_MODEL:
        return [mock_embedding(t) for t in texts]

    import litellm

    kwargs: dict[str, Any] = {"model": model, "input": texts}
    if user_id is not None:
        provider = model.split("/", 1)[0] if "/" in model else "openai"
        api_key, api_base = await state.registry.resolve_credential(session, user_id, provider)
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
    try:
        response = await litellm.aembedding(**kwargs)
    except Exception as exc:
        raise ProviderError(str(exc).split("\n", 1)[0][:500], code="provider_error") from exc
    data = sorted(response.data, key=lambda d: d["index"] if isinstance(d, dict) else d.index)
    return [d["embedding"] if isinstance(d, dict) else d.embedding for d in data]


async def embed_texts(
    state: "AppState",
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    model: str,
    texts: list[str],
) -> list[bytes]:
    """Embed with the content-hash cache; caller owns the (write) session."""
    keys = [blake3(t.encode()).hexdigest() for t in texts]
    cached: dict[str, bytes] = {}
    unique = sorted(set(keys))
    if unique:
        rows = (
            await session.execute(
                select(EmbedCache).where(EmbedCache.model == model, EmbedCache.blake3.in_(unique))
            )
        ).scalars()
        cached = {row.blake3: row.vector for row in rows}

    missing_keys: list[str] = []
    missing_texts: list[str] = []
    for key, text in zip(keys, texts, strict=True):
        if key not in cached and key not in missing_keys:
            missing_keys.append(key)
            missing_texts.append(text)

    if missing_texts:
        # provider batch cap 128 (§10)
        for start in range(0, len(missing_texts), 128):
            batch_keys = missing_keys[start : start + 128]
            batch_texts = missing_texts[start : start + 128]
            vectors = await _provider_embed(
                state, session, user_id=user_id, model=model, texts=batch_texts
            )
            for key, vector in zip(batch_keys, vectors, strict=True):
                blob = pack_vector([float(v) for v in vector])
                cached[key] = blob
                session.add(
                    EmbedCache(
                        blake3=key,
                        model=model,
                        dim=len(vector),
                        vector=blob,
                        created_at=now_ms(),
                    )
                )

    return [cached[key] for key in keys]
