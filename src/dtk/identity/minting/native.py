"""Identities minted without a browser, where a guest session is plain HTTP.

Douyin and TikTok hand a visitor its cookies from inside their page scripts,
which is why minting them takes browser-rpc. Bilibili does not: a guest's
``buvid3`` and ``buvid4`` are the JSON answer to one GET of
``/x/frontend/finger/spi``, the same request the site's own page makes on a
first visit. Measured 2026-10-18 from a guest: that answer alone, sent back
as cookies, was enough for the signed detail, ``playurl``, comment and space
endpoints to answer ``code: 0``.

So it is minted here, through this project's own transport - the TLS profile
the identity will use for the rest of its life, behind the proxy it is bound
to - which keeps the rule the browser path keeps: the session is created by
the same client, from the same exit, that will later spend it. An install
with no browser container can therefore still keep a Bilibili pool.

The fingerprint is a desktop Chrome at the newest major the installed wreq
emulates exactly, so its TLS and its User-Agent cannot disagree.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Final

from dtk.core.logging import get_logger
from dtk.core.types import BrowserFamily, Platform
from dtk.identity.minting.client import MintResult
from dtk.transport.base import (
    Fingerprint,
    RequestSpec,
    Transport,
    TransportFailure,
    TransportIdentity,
)
from dtk.transport.emulation import known_majors

log = get_logger(__name__)

#: Platforms whose guests are minted here rather than by browser-rpc.
NATIVE_MINT_PLATFORMS: Final[frozenset[Platform]] = frozenset({Platform.BILIBILI})

FINGER_SPI_URL: Final = "https://api.bilibili.com/x/frontend/finger/spi"
MINT_TIMEOUT_SECONDS: Final = 20.0


class NativeMintFailed(RuntimeError):
    """The platform did not hand out a guest session this time.

    Like :class:`~dtk.identity.minting.client.BrowserRpcUnavailable`, never
    fatal to the caller: the pool filler backs off and tries again.
    """


def chrome_fingerprint(*, timezone: str | None = None) -> Fingerprint:
    """A desktop Chrome on Windows at the newest major wreq emulates exactly."""
    major = max(known_majors(BrowserFamily.CHROME))
    return Fingerprint(
        browser_family=BrowserFamily.CHROME,
        browser_major=major,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        ),
        platform="Win32",
        screen="1920x1080",
        language="zh-CN",
        timezone=timezone or "Asia/Shanghai",
        hardware_concurrency=8,
        device_memory=8,
    )


class NativeMinter:
    """Mints the platforms in :data:`NATIVE_MINT_PLATFORMS`.

    The same call shape as :meth:`BrowserRpcClient.mint`, so the pool filler
    hands it a platform and an exit and gets back a jar and a fingerprint.
    """

    platforms: frozenset[Platform] = NATIVE_MINT_PLATFORMS

    def __init__(self, transport: Transport, *, clock: Callable[[], float] = time.time) -> None:
        self._transport = transport
        self._clock = clock

    def supports(self, platform: Platform) -> bool:
        return platform in self.platforms

    async def mint(
        self,
        platform: Platform,
        *,
        proxy_url: str | None = None,
        geo_hint: Mapping[str, Any] | None = None,
    ) -> MintResult:
        if platform is not Platform.BILIBILI:
            raise NativeMintFailed(f"{platform.value} is minted by browser-rpc, not here")
        timezone = (geo_hint or {}).get("timezone")
        fingerprint = chrome_fingerprint(timezone=timezone if isinstance(timezone, str) else None)
        # A throwaway id: the identity does not exist until the pool stores what
        # this returns, and the transport keys its client cache by id.
        sender = TransportIdentity(
            id=f"mint-{uuid.uuid4().hex}",
            platform=platform,
            fingerprint=fingerprint,
            proxy_url=proxy_url,
        )
        spec = RequestSpec(
            url=FINGER_SPI_URL,
            headers={"Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"},
            endpoint="bilibili.mint",
        )
        try:
            response = await self._transport.request(sender, spec, MINT_TIMEOUT_SECONDS)
        except TransportFailure as exc:
            raise NativeMintFailed(f"finger/spi was unreachable: {exc}") from exc
        finally:
            await self._transport.evict(sender.id)

        if not response.ok:
            raise NativeMintFailed(f"finger/spi answered HTTP {response.status}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NativeMintFailed("finger/spi did not answer JSON") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        buvid3 = data.get("b_3") if isinstance(data, dict) else None
        buvid4 = data.get("b_4") if isinstance(data, dict) else None
        if payload.get("code") != 0 or not isinstance(buvid3, str) or not buvid3:
            raise NativeMintFailed(
                f"finger/spi handed out no visitor id (code {payload.get('code')})"
            )

        cookies = {"buvid3": buvid3, "b_nut": str(int(self._clock()))}
        if isinstance(buvid4, str) and buvid4:
            cookies["buvid4"] = buvid4
        log.info("identity.mint.native", platform=platform.value, proxied=proxy_url is not None)
        return MintResult(cookies=cookies, fingerprint=fingerprint)


__all__ = [
    "FINGER_SPI_URL",
    "NATIVE_MINT_PLATFORMS",
    "NativeMintFailed",
    "NativeMinter",
    "chrome_fingerprint",
]
