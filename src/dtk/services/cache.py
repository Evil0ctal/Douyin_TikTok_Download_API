"""Response cache and in-flight request coalescing.

A cache hit costs no identity quota, which makes it the cheapest protection the
pool has.

Coalescing falls out of the async-first API: when a hundred callers ask for the
same video at once, only one upstream request is made and the other ninety-nine
attach to the same task. That matters most exactly when the pool is tight.
See docs/design/06-api-auth-mcp.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from dtk.core.logging import get_logger
from dtk.core.redis import get_redis

log = get_logger(__name__)

CACHE_KEY = "cache:{digest}"
INFLIGHT_KEY = "inflight:{digest}"


def cache_key(endpoint: str, params: dict[str, Any]) -> str:
    """Digest of the normalized business parameters, not of the raw URL.

    Two callers who reach the same content by different URL spellings must land
    on the same entry.
    """
    canonical = json.dumps(
        {k: v for k, v in sorted(params.items()) if v is not None},
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(f"{endpoint}\x00{canonical}".encode()).hexdigest()[:32]


async def get(digest: str) -> dict[str, Any] | None:
    raw = await get_redis().get(CACHE_KEY.format(digest=digest))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # A corrupt entry must not take down the request; drop and re-fetch.
        await get_redis().delete(CACHE_KEY.format(digest=digest))
        return None


async def put(digest: str, payload: dict[str, Any], ttl: int) -> None:
    if ttl <= 0:
        return
    await get_redis().set(
        CACHE_KEY.format(digest=digest),
        json.dumps(payload, ensure_ascii=False, default=str),
        ex=ttl,
    )


async def invalidate(digest: str) -> None:
    await get_redis().delete(CACHE_KEY.format(digest=digest))


async def claim_inflight(digest: str, task_id: str, ttl: int = 120) -> str | None:
    """Register this task as the one fetching ``digest``.

    Returns ``None`` if the claim succeeded, or the id of the task already doing
    the work, which the caller should attach to instead of starting its own.
    """
    redis = get_redis()
    key = INFLIGHT_KEY.format(digest=digest)
    if await redis.set(key, task_id, ex=ttl, nx=True):
        return None
    existing = await redis.get(key)
    if existing is None:
        # It expired between the SET and the GET; take it.
        if await redis.set(key, task_id, ex=ttl, nx=True):
            return None
        existing = await redis.get(key)
    return existing if isinstance(existing, str) else None


async def release_inflight(digest: str, task_id: str) -> None:
    """Clear the claim, but only if this task still owns it."""
    key = INFLIGHT_KEY.format(digest=digest)
    current = await get_redis().get(key)
    if current == task_id:
        await get_redis().delete(key)


__all__ = [
    "CACHE_KEY",
    "INFLIGHT_KEY",
    "cache_key",
    "claim_inflight",
    "get",
    "invalidate",
    "put",
    "release_inflight",
]
