"""Where the WBI keys come from: one ``nav`` request an hour, per process.

The keys are public and the same for every visitor, so they belong to no
identity and are fetched without one - no cookie, no proxy. They rotate about
once a day; an hour's cache is many times fresher than that and costs one
request an hour per worker.

A failed refresh keeps signing with the keys it has, for up to a day after
they were fetched. A key that went stale an hour ago is almost certainly
still the right one, and refusing every Bilibili request because ``nav``
timed out once would turn a blip into an outage. Past a day there is no
reason left to trust them, and signing fails with the reason.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Final, Protocol

import httpx

from dtk.core.errors import SigningFailed
from dtk.core.logging import get_logger
from dtk.signing.native.wbi import WbiKeys, keys_from_nav

log = get_logger(__name__)

NAV_URL: Final = "https://api.bilibili.com/x/web-interface/nav"
#: How long fetched keys are used before asking again.
FRESH_SECONDS: Final = 3600.0
#: How long they may still be used when asking again fails.
STALE_SECONDS: Final = 86400.0
TIMEOUT_SECONDS: Final = 10.0
#: Sent on the key request only. The keys do not depend on it, and a client
#: that announces itself as a script is answered differently by some edges.
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class KeySource(Protocol):
    async def keys(self) -> WbiKeys: ...


class NavKeySource:
    """Fetches the keys from ``nav`` and holds them for :data:`FRESH_SECONDS`."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._clock = clock
        self._keys: WbiKeys | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def keys(self) -> WbiKeys:
        if self._keys is not None and self._clock() - self._fetched_at < FRESH_SECONDS:
            return self._keys
        async with self._lock:
            # Another caller may have refreshed while this one waited.
            if self._keys is not None and self._clock() - self._fetched_at < FRESH_SECONDS:
                return self._keys
            try:
                keys = await self._fetch()
            except (httpx.HTTPError, ValueError) as exc:
                age = self._clock() - self._fetched_at
                if self._keys is not None and age < STALE_SECONDS:
                    log.warning(
                        "signing.wbi.refresh_failed",
                        error=f"{type(exc).__name__}: {exc}"[:200],
                        key_age_seconds=int(age),
                    )
                    return self._keys
                raise SigningFailed(
                    f"could not fetch Bilibili's WBI keys: {type(exc).__name__}: {exc}"
                ) from exc
            self._keys, self._fetched_at = keys, self._clock()
            return keys

    async def _fetch(self) -> WbiKeys:
        headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        if self._client is not None:
            response = await self._client.get(NAV_URL, headers=headers, timeout=TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.get(NAV_URL, headers=headers)
        response.raise_for_status()
        return keys_from_nav(response.json())


__all__ = ["FRESH_SECONDS", "NAV_URL", "STALE_SECONDS", "KeySource", "NavKeySource"]
