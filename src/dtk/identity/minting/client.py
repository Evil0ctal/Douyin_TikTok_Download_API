"""Client for the browser-rpc minting and signing service.

Minting runs a real browser through the identity's own proxy, because the cookie
set and the fingerprint have to come from the same session that will later use
them. Everything about the exit - timezone, language, screen - is aligned with
the proxy's GeoIP: a proxy in Germany paired with an Asia/Shanghai clock is a
tell given away for free.

The service is a long-running RPC rather than a browser launched per call. A
cold browser start costs seconds; the signing fallback is already a degraded
path and cannot absorb that on top. See docs/design/04-transport-signing.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from dtk.core.errors import SigningFailed
from dtk.core.logging import get_logger
from dtk.core.types import BrowserFamily, Platform
from dtk.transport.base import Fingerprint

log = get_logger(__name__)

MINT_TIMEOUT_SECONDS = 90.0
SIGN_TIMEOUT_SECONDS = 15.0
HEALTH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class MintResult:
    cookies: dict[str, str]
    fingerprint: Fingerprint
    exit_ip: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RpcHealth:
    available: bool
    warm_contexts: int = 0
    chromium_major: int | None = None
    backend_version: str | None = None
    detail: str = ""


class BrowserRpcUnavailable(RuntimeError):
    """The service is not configured or not reachable.

    This is never fatal: with no minting the pool falls back to manually
    imported cookies, and with no RPC signer the native one still runs. The
    caller degrades rather than failing.
    """


class BrowserRpcClient:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self._base)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=MINT_TIMEOUT_SECONDS)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    async def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self.configured:
            raise BrowserRpcUnavailable("browser-rpc is not configured")
        client = await self._http()
        try:
            response = await client.post(f"{self._base}{path}", json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise BrowserRpcUnavailable(f"browser-rpc {path} failed: {exc}") from exc

    async def health(self) -> RpcHealth:
        if not self.configured:
            return RpcHealth(available=False, detail="not configured")
        try:
            client = await self._http()
            response = await client.get(f"{self._base}/rpc/health", timeout=HEALTH_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return RpcHealth(available=False, detail=str(exc))
        return RpcHealth(
            available=True,
            warm_contexts=int(body.get("warm_contexts", 0)),
            chromium_major=body.get("chromium_major"),
            backend_version=body.get("backend_version"),
        )

    async def mint(
        self,
        platform: Platform,
        *,
        proxy_url: str | None,
        geo_hint: dict[str, Any] | None = None,
    ) -> MintResult:
        """Mint a fresh guest identity through the given proxy."""
        body = await self._post(
            "/rpc/mint",
            {
                "platform": platform.value,
                "proxy_url": proxy_url,
                "geo_hint": geo_hint or {},
            },
            MINT_TIMEOUT_SECONDS,
        )
        cookies = {str(k): str(v) for k, v in (body.get("cookies") or {}).items()}
        if not cookies:
            raise BrowserRpcUnavailable("mint returned no cookies")

        family_raw = body.get("browser_family") or "chrome"
        try:
            family = BrowserFamily(str(family_raw).lower())
        except ValueError:
            family = BrowserFamily.CHROME

        fingerprint = Fingerprint(
            browser_family=family,
            browser_major=_as_int(body.get("browser_major")),
            user_agent=body.get("user_agent"),
            platform=body.get("platform_hint"),
            screen=body.get("screen"),
            language=body.get("language"),
            timezone=body.get("timezone"),
        )
        log.info(
            "identity.mint.succeeded",
            platform=platform.value,
            cookie_names=sorted(cookies),
            browser_family=family.value,
            browser_major=fingerprint.browser_major,
        )
        return MintResult(
            cookies=cookies,
            fingerprint=fingerprint,
            exit_ip=body.get("exit_ip"),
            raw=body,
        )

    async def sign(
        self,
        platform: Platform,
        *,
        url: str,
        params: dict[str, Any],
        user_agent: str | None,
        cookies: Mapping[str, str] | None = None,
        proxy_url: str | None = None,
    ) -> dict[str, str]:
        """Ask the live page to sign a request.

        Slower than the native implementation, but it follows the platform when
        the algorithm changes, which the native port cannot.

        ``cookies`` must be the jar the request will be sent with. The service
        loads its signing page with them, so that the ``verifyFp`` it returns is
        the ``s_v_web_id`` in this jar rather than some other session's; a
        signature and a jar that name different sessions get an empty payload
        back from both platforms.
        """
        try:
            body = await self._post(
                "/rpc/sign",
                {
                    "platform": platform.value,
                    "url": url,
                    "params": params,
                    "user_agent": user_agent,
                    "cookies": dict(cookies or {}),
                    "proxy_url": proxy_url,
                },
                SIGN_TIMEOUT_SECONDS,
            )
        except BrowserRpcUnavailable as exc:
            raise SigningFailed(str(exc)) from exc
        return {str(k): str(v) for k, v in body.items() if v is not None}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "BrowserRpcClient",
    "BrowserRpcUnavailable",
    "MintResult",
    "RpcHealth",
]
