"""Service layer: pools, budgets and the policy the backends do not own.

Two context lifecycles, deliberately opposite (docs/design/04-transport-signing.md):

* **mint contexts are single use.** A fresh profile directory per session, wiped
  when the session ends. Reusing a profile hands the next identity the previous
  one's traces, which is the one mistake that makes a whole pool correlatable.
* **signing contexts stay warm.** Starting a browser costs seconds and the
  signing fallback is already the degraded path; a resident page with the
  platform's JavaScript loaded answers in hundreds of milliseconds. They are
  rebuilt on a timer because the platform ships new JavaScript regularly.

Every budget here is shorter than the matching client timeout, so the caller
reads an error that says what happened instead of hitting its own deadline.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from browser_rpc import geo as geo_module
from browser_rpc.backends.base import (
    BrowserBackend,
    MintedProfile,
    MintPlan,
    SigningContext,
    SignPlan,
)
from browser_rpc.errors import BackendFailure, BackendUnavailable, OperationTimeout, RpcError
from browser_rpc.geo import GeoProfile
from browser_rpc.settings import Settings
from browser_rpc.validation import (
    LANDING_URLS,
    Platform,
    validate_proxy_url,
    validate_target_url,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MintOutcome:
    """A minted identity plus the context it was minted in."""

    platform: Platform
    profile: MintedProfile
    geo: GeoProfile
    exit_ip: str | None = None


@dataclass(slots=True)
class WarmSlot:
    """One warm signing context and the moment it was built."""

    context: SigningContext
    created_at: float
    generation: int = 0


@dataclass(slots=True)
class _PlatformPool:
    """Warm slots for one platform, plus the lock that serializes rebuilds."""

    idle: asyncio.LifoQueue[WarmSlot] = field(default_factory=asyncio.LifoQueue)
    live: int = 0
    capacity: int = 1
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserRpcService:
    """Everything the HTTP layer calls. Holds no request state of its own."""

    def __init__(
        self,
        settings: Settings,
        backend: BrowserBackend,
        *,
        clock: Callable[[], float] = time.monotonic,
        probe: Callable[..., Any] = geo_module.probe_exit,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._clock = clock
        self._probe = probe
        self._started_at = clock()
        self._start_error: str | None = None
        self._running = False
        self._mint_slots = asyncio.Semaphore(settings.max_concurrent_mints)
        self._pools: dict[Platform, _PlatformPool] = {
            platform: _PlatformPool(capacity=max(1, settings.warm_contexts))
            for platform in Platform
        }

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Bring the backend up. A failure here is recorded, not raised.

        The container stays up and reports itself unhealthy on /rpc/health: the
        operator reads one clear line in `docker compose logs browser-rpc`
        instead of watching a container restart every ten seconds, and api and
        worker degrade to manual cookie import exactly as they would if the
        service were switched off.
        """
        self._started_at = self._clock()
        try:
            await self._backend.start()
        except RpcError as exc:
            self._start_error = exc.message
            logger.error("browser_rpc.backend_unavailable error=%s", exc.message)
            return
        except Exception as exc:
            self._start_error = str(exc)
            logger.exception("browser_rpc.backend_start_failed")
            return

        self._running = True
        self._start_error = None
        logger.info(
            "browser_rpc.started backend=%s warm_contexts=%d",
            self._settings.backend,
            self._settings.warm_contexts,
        )
        if self._settings.prewarm and self._settings.warm_contexts > 0:
            await self._prewarm()

    async def _prewarm(self) -> None:
        """Build one warm context per platform so the first signature is fast."""
        for platform in Platform:
            try:
                slot = await self._open_slot(platform)
            except RpcError as exc:
                # Not fatal: the pool fills lazily on the first request.
                logger.warning(
                    "browser_rpc.prewarm_failed platform=%s error=%s", platform.value, exc.message
                )
                continue
            self._pools[platform].idle.put_nowait(slot)

    async def close(self) -> None:
        self._running = False
        for platform, pool in self._pools.items():
            while not pool.idle.empty():
                slot = pool.idle.get_nowait()
                pool.live -= 1
                await self._close_slot(platform, slot)
        try:
            await self._backend.close()
        except Exception:
            logger.exception("browser_rpc.backend_close_failed")

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Never raises: an unhealthy service still has to answer the probe."""
        info = self._backend.info()
        warm = sum(pool.live for pool in self._pools.values())
        body: dict[str, Any] = {
            "status": "ok" if self._running else "unavailable",
            "backend": info.name,
            "backend_version": info.version or info.pin or info.name,
            "chromium_major": info.chromium_major,
            "warm_contexts": warm,
            "uptime": round(self._clock() - self._started_at, 3),
        }
        if info.pin:
            body["backend_pin"] = info.pin
        if self._start_error:
            body["error"] = self._start_error
        return body

    def _require_running(self) -> None:
        if not self._running:
            raise BackendUnavailable(
                self._start_error or "the browser backend is not running",
            )

    # -- minting ----------------------------------------------------------

    async def mint(
        self,
        platform: Platform,
        proxy_url: str | None,
        geo_hint: Mapping[str, Any] | None = None,
    ) -> MintOutcome:
        """Run one single-use minting session through the identity's own proxy."""
        self._require_running()
        proxy = validate_proxy_url(proxy_url)

        # Measured before the context exists, because locale and timezone have
        # to be set at creation time.
        exit_info = await self._probe(
            proxy_url,
            self._settings.geo_probe_url,
            self._settings.geo_probe_timeout_seconds,
        )
        profile = geo_module.resolve(geo_hint, exit_info.country, self._settings.default_country)

        async with self._mint_slots:
            profile_dir = self._new_profile_dir(platform)
            plan = MintPlan(
                platform=platform,
                landing_url=LANDING_URLS[platform],
                profile_dir=profile_dir,
                geo=profile,
                proxy=proxy,
                timeout_seconds=self._settings.mint_timeout_seconds,
            )
            started = self._clock()
            logger.info(
                "browser_rpc.mint.start platform=%s proxy=%s timezone=%s locale=%s",
                platform.value,
                proxy.masked() if proxy else "direct",
                profile.timezone,
                profile.locale,
            )
            try:
                minted = await self._with_timeout(
                    self._backend.mint(plan),
                    self._settings.mint_timeout_seconds,
                    f"minting for {platform.value}",
                )
            finally:
                # Single use is enforced here rather than trusted to the
                # backend: the directory goes away whatever happened inside it.
                shutil.rmtree(profile_dir, ignore_errors=True)

        logger.info(
            "browser_rpc.mint.done platform=%s cookies=%d duration_ms=%d",
            platform.value,
            len(minted.cookies),
            int((self._clock() - started) * 1000),
        )
        return MintOutcome(
            platform=platform,
            profile=minted,
            geo=profile,
            exit_ip=minted.exit_ip or exit_info.ip,
        )

    def _new_profile_dir(self, platform: Platform) -> str:
        root = Path(self._settings.profile_root)
        root.mkdir(parents=True, exist_ok=True)
        return tempfile.mkdtemp(prefix=f"mint-{platform.value}-{uuid.uuid4().hex[:8]}-", dir=root)

    # -- signing ----------------------------------------------------------

    def user_agent_for(self, platform: Platform, requested: str | None) -> str:
        """The User-Agent a signature for this platform was computed under.

        Returned to the caller because TikTok binds the signature to the exact
        UA string - a one-digit Chrome version change is enough for the API to
        answer with an empty body - so the caller has to send under precisely
        this value rather than under whatever it believes the identity uses.
        """
        if requested:
            return requested
        # Fall back to whatever the warm context reports. An empty string is
        # honest here: the caller can then see that no UA was pinned, rather
        # than being handed a plausible-looking default it never signed under.
        slot = getattr(self, "_warm", {}).get(platform) if hasattr(self, "_warm") else None
        return str(getattr(slot, "user_agent", "") or "")

    async def sign(
        self,
        platform: Platform,
        url: str,
        query: str,
        params: Mapping[str, str] | None = None,
        user_agent: str | None = None,
    ) -> dict[str, str]:
        """Sign through a warm page, rebuilding the page once if it is stale."""
        self._require_running()
        target = validate_target_url(url, platform=platform)
        plan = SignPlan(
            platform=platform,
            url=target,
            query=query,
            params=dict(params or {}),
            user_agent=user_agent,
            timeout_seconds=self._settings.sign_timeout_seconds,
        )

        last_error: RpcError | None = None
        # Two attempts: a warm page that has been up for a while is the most
        # common failure, and it is fixed by throwing it away.
        for attempt in (1, 2):
            slot = await self._acquire_slot(platform)
            try:
                signed = await self._with_timeout(
                    slot.context.sign(plan),
                    self._settings.sign_timeout_seconds,
                    f"signing for {platform.value}",
                )
            except RpcError as exc:
                last_error = exc
                await self._discard_slot(platform, slot)
                logger.warning(
                    "browser_rpc.sign.attempt_failed platform=%s attempt=%d error=%s",
                    platform.value,
                    attempt,
                    exc.message,
                )
                continue
            except Exception as exc:
                last_error = BackendFailure(f"signing failed: {exc}")
                await self._discard_slot(platform, slot)
                logger.exception("browser_rpc.sign.crashed platform=%s", platform.value)
                continue

            if not signed:
                # A page that answers with nothing has lost its signing code;
                # keeping it warm would only produce the same empty answer.
                last_error = BackendFailure("the signing page returned no signature")
                await self._discard_slot(platform, slot)
                continue

            self._release_slot(platform, slot)
            return signed

        raise last_error or BackendFailure("signing produced no result")

    async def _acquire_slot(self, platform: Platform) -> WarmSlot:
        pool = self._pools[platform]
        while True:
            slot: WarmSlot | None = None
            if not pool.idle.empty():
                slot = pool.idle.get_nowait()
            elif pool.live < pool.capacity:
                async with pool.lock:
                    if pool.live < pool.capacity:
                        return await self._open_slot(platform, count=True)
                continue
            else:
                slot = await self._with_timeout(
                    pool.idle.get(),
                    self._settings.sign_timeout_seconds,
                    f"waiting for a warm {platform.value} context",
                )

            if self._is_stale(slot):
                logger.info("browser_rpc.warm.refresh platform=%s", platform.value)
                pool.live -= 1
                await self._close_slot(platform, slot)
                continue
            return slot

    def _is_stale(self, slot: WarmSlot) -> bool:
        return (self._clock() - slot.created_at) >= self._settings.warm_refresh_seconds

    async def _open_slot(self, platform: Platform, *, count: bool = True) -> WarmSlot:
        pool = self._pools[platform]
        geo_profile = geo_module.resolve(None, None, self._settings.default_country)
        # A warm page is not an identity - it harvests no cookies - but it does
        # load the platform's site, so it goes through the configured exit when
        # there is one rather than showing the platform the host's address.
        proxy = validate_proxy_url(self._settings.sign_proxy_url)
        context = await self._with_timeout(
            self._backend.open_signing_context(platform, geo_profile, proxy),
            self._settings.context_open_timeout_seconds,
            f"opening a signing context for {platform.value}",
        )
        if count:
            pool.live += 1
        return WarmSlot(context=context, created_at=self._clock())

    def _release_slot(self, platform: Platform, slot: WarmSlot) -> None:
        self._pools[platform].idle.put_nowait(slot)

    async def _discard_slot(self, platform: Platform, slot: WarmSlot) -> None:
        self._pools[platform].live -= 1
        await self._close_slot(platform, slot)

    @staticmethod
    async def _close_slot(platform: Platform, slot: WarmSlot) -> None:
        try:
            await slot.context.close()
        except Exception:
            logger.warning("browser_rpc.warm.close_failed platform=%s", platform.value)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    async def _with_timeout(awaitable: Any, timeout: float, what: str) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout)
        except TimeoutError as exc:
            raise OperationTimeout(f"{what} exceeded {timeout:g}s") from exc


__all__ = ["BrowserRpcService", "MintOutcome", "WarmSlot"]
