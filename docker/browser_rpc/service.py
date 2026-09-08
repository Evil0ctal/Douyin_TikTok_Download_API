"""Service layer: pools, budgets and the policy the backends do not own.

Two context lifecycles, deliberately opposite (docs/design/04-transport-signing.md):

* **mint contexts are single use.** A fresh profile directory per session, wiped
  when the session ends. Reusing a profile hands the next identity the previous
  one's traces, which is the one mistake that makes a whole pool correlatable.
* **signing contexts stay warm, but only for one identity at a time.** Starting
  a browser costs seconds and the signing fallback is already the degraded path;
  a resident page answers in milliseconds. They are rebuilt on a timer because
  the platform ships new JavaScript regularly, and rebuilt on demand whenever
  the next signature is for a different jar - because a signature is only
  coherent alongside the cookies the page was loaded with (see the pooling note
  in `backends/cloak.py`). A slot therefore has a *binding*, and reusing one
  across identities is the bug that made every scraped payload come back
  withheld.

Every budget here is shorter than the matching client timeout, so the caller
reads an error that says what happened instead of hitting its own deadline.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
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

#: The binding of a context that carries no identity: what prewarm builds, and
#: what a caller sending no cookies asks for.
ANONYMOUS = "anonymous"


def binding_key(cookies: Mapping[str, str] | None, proxy_url: str | None) -> str:
    """Which warm slots can serve this request, as one comparable string.

    Both halves matter and for different reasons. The **jar** decides the
    signature: `verifyFp` is the `s_v_web_id` inside it, so a page loaded with a
    different jar signs a different identity. The **exit** decides who the
    platform sees loading that page: reusing a context opened through one
    proxy to sign for an identity that exits somewhere else would show the
    platform this jar arriving from an address that is not its own, which is the
    correlation the identity pool exists to avoid.

    Hashed rather than kept whole because this value is logged and compared, and
    a cookie jar is a credential. The digest is not a security boundary - it is
    an equality test that does not carry the secret around with it.
    """
    if not cookies and not proxy_url:
        return ANONYMOUS
    canonical = "\n".join(f"{name}={value}" for name, value in sorted((cookies or {}).items()))
    digest = hashlib.sha256(f"{proxy_url or ''}\x00{canonical}".encode()).hexdigest()
    return digest[:16]


@dataclass(frozen=True, slots=True)
class MintOutcome:
    """A minted identity plus the context it was minted in."""

    platform: Platform
    profile: MintedProfile
    geo: GeoProfile
    exit_ip: str | None = None


@dataclass(slots=True)
class WarmSlot:
    """One warm signing context, the moment it was built, and whose it is."""

    context: SigningContext
    created_at: float
    #: The jar this page was loaded with, as `binding_key` renders it. A slot is
    #: reusable only for the same binding: its signatures name that jar's
    #: `s_v_web_id` as `verifyFp`, so handing it to another identity produces a
    #: query that contradicts the cookies the request will carry.
    binding: str = ""
    generation: int = 0


@dataclass(slots=True)
class _PlatformPool:
    """Warm slots for one platform, and the condition that hands them out.

    A list rather than a queue because acquisition is now a *search*: a caller
    wants the slot already bound to its identity, and settles for rebinding
    another only when there is none. `live` counts slots that exist or are being
    opened, so capacity is respected while a 4-second launch is in flight.
    """

    idle: list[WarmSlot] = field(default_factory=list)
    live: int = 0
    capacity: int = 1
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)


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
        """Build one context per platform so the first signature is fast.

        Prewarmed slots are unbound: no identity is known at startup. The first
        signature for a real identity therefore pays a rebind, and what prewarm
        buys is not that signature but the proof - visible on /rpc/health as a
        non-zero `warm_contexts` - that this deployment can open a page at all.
        """
        for platform in Platform:
            pool = self._pools[platform]
            try:
                slot = await self._open_slot(platform, ANONYMOUS, None, None)
            except RpcError as exc:
                # Not fatal: the pool fills lazily on the first request.
                logger.warning(
                    "browser_rpc.prewarm_failed platform=%s error=%s", platform.value, exc.message
                )
                continue
            async with pool.cond:
                pool.live += 1
                pool.idle.append(slot)

    async def close(self) -> None:
        self._running = False
        for platform, pool in self._pools.items():
            async with pool.cond:
                idle, pool.idle = pool.idle, []
                pool.live -= len(idle)
                pool.cond.notify_all()
            for slot in idle:
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
        *,
        cookies: Mapping[str, str] | None = None,
        proxy_url: str | None = None,
        identity_id: str | None = None,
    ) -> dict[str, str]:
        """Sign through a page loaded with this identity's own cookies.

        The jar is what makes the answer usable: signing in a page that holds
        somebody else's cookies yields a query naming somebody else's
        `verifyFp`, and the platform withholds the payload rather than
        rejecting the request, so the failure arrives looking like a block.
        """
        self._require_running()
        target = validate_target_url(url, platform=platform)
        jar = {str(k): str(v) for k, v in (cookies or {}).items()}
        # An identity's own exit when it has one, the deployment's signing proxy
        # otherwise. Both are validated; an invalid one is the caller's error.
        exit_url = proxy_url or self._settings.sign_proxy_url
        proxy = validate_proxy_url(exit_url)
        binding = binding_key(jar, exit_url)
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
            try:
                # Inside the retry deliberately. Opening a page can fail on its
                # own - a renderer that dies mid-warm-up reports "Target
                # crashed" - and leaving this outside meant one unlucky launch
                # became a 502 for the caller with no second try. Under memory
                # pressure that is the common failure, not the rare one.
                slot = await self._acquire_slot(platform, binding, jar, proxy, identity_id)
            except RpcError as exc:
                last_error = exc
                logger.warning(
                    "browser_rpc.sign.open_failed platform=%s attempt=%d error=%s",
                    platform.value,
                    attempt,
                    exc.message,
                )
                continue
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

            await self._release_slot(platform, slot)
            return signed

        raise last_error or BackendFailure("signing produced no result")

    async def _acquire_slot(
        self,
        platform: Platform,
        binding: str,
        cookies: Mapping[str, str],
        proxy: Any,
        identity_id: str | None,
    ) -> WarmSlot:
        """A page bound to `binding`, opening or rebinding one if there is none.

        The caller owns exactly one live credit when this returns, and must give
        it back through `_release_slot` or `_discard_slot`. Opening and closing
        both take seconds and are deliberately done outside the lock, so a
        launch in flight never blocks another platform's release.
        """
        pool = self._pools[platform]
        deadline = self._clock() + self._settings.context_open_timeout_seconds
        while True:
            evicted: WarmSlot | None = None
            async with pool.cond:
                hit = next((s for s in pool.idle if s.binding == binding), None)
                if hit is not None:
                    pool.idle.remove(hit)
                    if not self._is_stale(hit):
                        return hit
                    # Its credit passes to the replacement we are about to open.
                    logger.info("browser_rpc.warm.refresh platform=%s", platform.value)
                    evicted = hit
                elif pool.live < pool.capacity:
                    pool.live += 1
                elif pool.idle:
                    # Every page belongs to someone else. The oldest is the one
                    # least likely to be wanted again soon.
                    evicted = pool.idle.pop(0)
                    logger.info(
                        "browser_rpc.warm.rebind platform=%s identity=%s",
                        platform.value,
                        _short(identity_id),
                    )
                else:
                    # Every page exists and every one is in use; wait for a
                    # release rather than exceeding the configured browser count.
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise OperationTimeout(
                            f"waiting for a warm {platform.value} context exceeded "
                            f"{self._settings.context_open_timeout_seconds:g}s"
                        )
                    # A timeout here is not the answer, only the end of this
                    # nap: the loop re-checks the deadline and gives up there.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(pool.cond.wait(), remaining)
                    continue

            if evicted is not None:
                await self._close_slot(platform, evicted)
            try:
                return await self._open_slot(platform, binding, cookies, proxy)
            except BaseException:
                async with pool.cond:
                    pool.live -= 1
                    pool.cond.notify()
                raise

    def _is_stale(self, slot: WarmSlot) -> bool:
        return (self._clock() - slot.created_at) >= self._settings.warm_refresh_seconds

    async def _open_slot(
        self,
        platform: Platform,
        binding: str,
        cookies: Mapping[str, str] | None,
        proxy: Any,
    ) -> WarmSlot:
        """Open one page carrying `cookies`. The caller already holds the credit."""
        geo_profile = geo_module.resolve(None, None, self._settings.default_country)
        context = await self._with_timeout(
            self._backend.open_signing_context(platform, geo_profile, proxy, cookies),
            self._settings.context_open_timeout_seconds,
            f"opening a signing context for {platform.value}",
        )
        return WarmSlot(context=context, created_at=self._clock(), binding=binding)

    async def _release_slot(self, platform: Platform, slot: WarmSlot) -> None:
        pool = self._pools[platform]
        async with pool.cond:
            pool.idle.append(slot)
            pool.cond.notify()

    async def _discard_slot(self, platform: Platform, slot: WarmSlot) -> None:
        pool = self._pools[platform]
        async with pool.cond:
            pool.live -= 1
            pool.cond.notify()
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


def _short(identity_id: str | None) -> str:
    """An identity id abbreviated for a log line, never the cookies behind it."""
    return (identity_id or "-")[:8]


__all__ = ["ANONYMOUS", "BrowserRpcService", "MintOutcome", "WarmSlot", "binding_key"]
