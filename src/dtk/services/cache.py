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


def cache_key(
    endpoint: str,
    params: dict[str, Any],
    *,
    include_raw: bool = False,
    egress: str | None = None,
) -> str:
    """Digest of the normalized business parameters, not of the raw URL.

    Two callers who reach the same content by different URL spellings must land
    on the same entry.

    ``include_raw`` is part of the key because the cached value is the *shaped*
    body, not the platform's answer: ``_dump`` strips ``raw`` before it is
    stored. Leaving it out froze whichever value the first caller used for the
    whole TTL, in both directions - a later ``include_raw=true`` got a body with
    no ``raw`` in it, and a later ``include_raw=false`` got one that still had
    it. The second is the one that matters: the raw payload is the largest
    thing this instance stores, and it was being handed to, and persisted in
    ``tasks.result`` for, a caller who explicitly opted out.

    It is not part of the *upstream* request, so this does mean two fetches
    where a smarter cache would do one. That is the honest trade until the
    cache stores the unshaped body.

    ``egress`` is the exit the request will leave through, when the caller chose
    one. It has to be here for the same reason ``include_raw`` does, only with
    worse consequences: the platforms answer differently by region, so a caller
    who supplied a proxy was being served whatever the first caller through a
    different exit had cached, and vice versa. It arrives as the proxy URL,
    which can carry credentials, and is hashed before it goes anywhere near a
    key so that no part of it is ever stored or printed.

    A request that pins an identity does not come through here at all - see
    :meth:`dtk.services.fetch.FetchService.fetch`. A session-scoped answer must
    not be written to a shared cache under any key.
    """
    canonical = json.dumps(
        {k: v for k, v in sorted(params.items()) if v is not None},
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    suffix = "\x00raw" if include_raw else ""
    if egress:
        suffix += "\x00exit" + hashlib.sha256(egress.encode()).hexdigest()[:16]
    return hashlib.sha256(f"{endpoint}\x00{canonical}{suffix}".encode()).hexdigest()[:32]


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
