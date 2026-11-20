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
import re
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
from browser_rpc.errors import (
    BackendFailure,
    BackendUnavailable,
    OperationTimeout,
    RpcError,
    Suspect,
    UserAgentMismatch,
)
from browser_rpc.geo import GeoProfile
from browser_rpc.settings import Settings
from browser_rpc.validation import (
    LANDING_URLS,
    Platform,
    ProxyEndpoint,
    validate_proxy_url,
    validate_target_url,
)

logger = logging.getLogger(__name__)

#: The binding of a context that carries no identity and loads through no
#: proxy: what a caller sending no cookies asks for when no signing proxy is
#: configured. With DTK_BROWSER_SIGN_PROXY_URL set, the same caller's binding is
#: that proxy's (see `binding_key`), and so is what prewarm builds.
ANONYMOUS = "anonymous"

#: How long a platform whose prewarm failed waits before it is tried again, and
#: the ceiling the wait doubles up to. Retried at all because dtk routes to this
#: service only while /rpc/health reports a warm context: a pool left empty by a
#: signing proxy that was down at boot stayed empty after the proxy came back,
#: since the request that would have filled it was never sent (review of the
#: #830 fix).
PREWARM_RETRY_SECONDS: float = 30.0
PREWARM_RETRY_MAX_SECONDS: float = 600.0


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
    #: Signatures this page has produced, for the log line that explains why
    #: it was thrown away: a page that failed on its first use and one that
    #: failed after a thousand point at different causes.
    signs: int = 0


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
        self._prewarm_task: asyncio.Task[None] | None = None
        #: Set once prewarm has tried every platform, whatever the outcome.
        self._first_prewarm = asyncio.Event()
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
            self._first_prewarm.set()
            return
        except Exception as exc:
            self._start_error = str(exc)
            logger.exception("browser_rpc.backend_start_failed")
            self._first_prewarm.set()
            return

        self._running = True
        self._start_error = None
        logger.info(
            "browser_rpc.started backend=%s warm_contexts=%d",
            self._settings.backend,
            self._settings.warm_contexts,
        )
        if self._settings.prewarm and self._settings.warm_contexts > 0:
            # In the background, so the service answers /rpc/health and
            # /rpc/mint at once. Awaited here it held the port closed for as
            # long as two opens could hang on an exit that neither answered nor
            # refused - up to a minute and a half through a bad signing proxy,
            # with minting blocked too although it never uses that proxy.
            self._prewarm_task = asyncio.create_task(self._prewarm())
        else:
            self._first_prewarm.set()

    async def prewarmed(self) -> None:
        """Wait until prewarm has tried every platform once."""
        await self._first_prewarm.wait()

    async def _prewarm(self) -> None:
        """Build one context per platform so the first signature is fast.

        Prewarmed slots carry no identity: none is known at startup. The first
        signature for a real identity therefore pays a rebind, and what prewarm
        buys is not that signature but the proof - visible on /rpc/health as a
        non-zero `warm_contexts` - that this deployment can open a page at all.

        Through the exit an identity-less signature would use, which is the
        signing proxy when one is set. Prewarm used to open its pages directly
        whatever the configuration said, under the binding no request with a
        signing proxy ever asks for: the pages went unused, the host's own
        address reached both platforms at every start against the operator's
        wishes, and /rpc/health reported two warm contexts while a dead signing
        proxy failed every signature (the review of #830's fix, with the proxy
        pointed at a closed port).
        """
        exit_url = self._settings.sign_proxy_url
        kind = _exit_kind(None, exit_url)
        try:
            proxy = validate_proxy_url(exit_url)
        except RpcError as exc:
            # A configuration error, not an outage: retrying cannot fix it.
            logger.warning("browser_rpc.prewarm_failed exit=%s error=%s", kind, exc.message)
            self._first_prewarm.set()
            return
        binding = binding_key({}, exit_url)
        missing = list(Platform)
        delay = PREWARM_RETRY_SECONDS
        try:
            while self._running:
                missing = [
                    platform
                    for platform in missing
                    if not await self._prewarm_one(platform, binding, proxy, kind)
                ]
                self._first_prewarm.set()
                if not missing:
                    return
                logger.info(
                    "browser_rpc.prewarm_retry platforms=%s exit=%s in_s=%.0f",
                    ",".join(platform.value for platform in missing),
                    kind,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, PREWARM_RETRY_MAX_SECONDS)
        finally:
            self._first_prewarm.set()

    async def _prewarm_one(
        self, platform: Platform, binding: str, proxy: ProxyEndpoint | None, kind: str
    ) -> bool:
        """Open one prewarmed page for ``platform``; False when it should be retried.

        Nothing is opened when a request has got there first - a page under the
        same binding is already idle, or the pool is full - because what prewarm
        is for has then already happened.
        """
        pool = self._pools[platform]
        async with pool.cond:
            if pool.live >= pool.capacity or any(s.binding == binding for s in pool.idle):
                return True
            pool.live += 1
        try:
            slot = await self._open_slot(platform, binding, None, proxy)
        except RpcError as exc:
            async with pool.cond:
                pool.live -= 1
                pool.cond.notify_all()
            logger.warning(
                "browser_rpc.prewarm_failed platform=%s exit=%s error=%s",
                platform.value,
                kind,
                exc.message,
            )
            return False
        except BaseException:
            async with pool.cond:
                pool.live -= 1
                pool.cond.notify_all()
            raise
        async with pool.cond:
            pool.idle.append(slot)
            pool.cond.notify_all()
        return True

    async def close(self) -> None:
        self._running = False
        if self._prewarm_task is not None:
            self._prewarm_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._prewarm_task
            self._prewarm_task = None
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
            # The whole string, not just the major above: a caller holding an
            # identity minted elsewhere can see before asking that this browser
            # cannot sign for it (#832).
            "user_agent": info.user_agent,
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

    def signing_user_agent(self) -> str:
        """The User-Agent this service's signatures are computed under.

        Returned to the caller because TikTok binds the signature to the exact
        UA string - a one-digit Chrome version change is enough for the API to
        answer with an empty body - so the caller has to send under precisely
        this value rather than under whatever it believes the identity uses.

        The page's own, as the backend read it. This used to echo whatever the
        request asked for, and fall back to a `_warm` attribute that no longer
        existed, while the page signed under its own string regardless: the
        caller compared its UA with itself, and its check could never fire
        (#832). An empty string means no page has said yet, which is honest
        where a plausible default would not be.
        """
        return self._backend.info().user_agent or ""

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
        method: str = "GET",
        body: str | None = None,
        content_type: str | None = None,
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
            # The request accepted `method` long before it reached the page:
            # the plan had no field for it, so every signature was taken over
            # a GET. Harmless while nothing posted a body.
            method=(method or "GET").upper(),
            body=body,
            content_type=content_type,
        )

        # Which exit the page loads through, by kind and never by address: the
        # half of the story the operator could not see in #830, where both
        # attempts failed the same way because both went out the same way.
        exit_kind = _exit_kind(proxy_url, exit_url)
        # Before any page is opened or rebound when the browser's User-Agent is
        # already known: refusing then also keeps this identity's jar from
        # being loaded on the platform under a User-Agent that is not its own.
        _refuse_a_foreign_agent(platform, user_agent, self._backend.info().user_agent, identity_id)
        failures: list[RpcError] = []
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
                failures.append(exc)
                logger.warning(
                    "browser_rpc.sign.open_failed platform=%s attempt=%d exit=%s identity=%s "
                    "error=%s",
                    platform.value,
                    attempt,
                    exit_kind,
                    _short(identity_id),
                    exc.message,
                )
                continue
            try:
                _refuse_a_foreign_agent(platform, user_agent, slot.context.user_agent, identity_id)
            except UserAgentMismatch:
                # Nothing is wrong with the page or its binding, so it stays
                # warm; and no second attempt, which would open a page in the
                # same browser and be refused the same way. #832's own patch
                # refused one step later, inside the page's sign, where a
                # failure throws a healthy page away, pays for a cold one that
                # fails alike, and answers 502 blaming the browser.
                await self._release_slot(platform, slot)
                raise
            try:
                signed = await self._with_timeout(
                    slot.context.sign(plan),
                    self._settings.sign_timeout_seconds,
                    f"signing for {platform.value}",
                )
            except RpcError as exc:
                failures.append(exc)
                await self._discard_slot(platform, slot)
                logger.warning(
                    "browser_rpc.sign.attempt_failed platform=%s attempt=%d exit=%s identity=%s "
                    "slot_age_s=%.0f slot_signs=%d error=%s",
                    platform.value,
                    attempt,
                    exit_kind,
                    _short(identity_id),
                    self._clock() - slot.created_at,
                    slot.signs,
                    exc.message,
                )
                continue
            except Exception as exc:
                failures.append(BackendFailure(f"signing failed: {exc}"))
                await self._discard_slot(platform, slot)
                logger.exception("browser_rpc.sign.crashed platform=%s", platform.value)
                continue

            if not signed:
                # A page that answers with nothing has lost its signing code;
                # keeping it warm would only produce the same empty answer.
                failures.append(BackendFailure("the signing page returned no signature"))
                await self._discard_slot(platform, slot)
                continue

            slot.signs += 1
            await self._release_slot(platform, slot)
            return signed

        raise _final_error(failures, exit_kind)

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
                    problem = hit.context.problem
                    if problem is None and not self._is_stale(hit):
                        return hit
                    # Its credit passes to the replacement we are about to open.
                    if problem is not None:
                        # A page that went bad while it sat idle - crashed, or
                        # carried off the platform by a navigation that failed
                        # after load. Handing it out cost the caller its first
                        # attempt in #830, to learn what was already known.
                        logger.warning(
                            "browser_rpc.warm.discarded platform=%s reason=%s age_s=%.0f signs=%d",
                            platform.value,
                            problem,
                            self._clock() - hit.created_at,
                            hit.signs,
                        )
                    else:
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
            # An open that hangs is almost always a navigation waiting on an
            # exit that neither answers nor refuses.
            suspect=Suspect.EXIT,
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
    async def _with_timeout(
        awaitable: Any, timeout: float, what: str, *, suspect: Suspect | None = None
    ) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout)
        except TimeoutError as exc:
            raise OperationTimeout(f"{what} exceeded {timeout:g}s", suspect=suspect) from exc


#: The platforms that refuse a request whose User-Agent is not, byte for byte,
#: the one its signature sealed. TikTok: one digit of the Chrome version is
#: enough for an empty body (measured 2026-09-07, again 2026-11-20).
UA_BOUND_PLATFORMS: frozenset[Platform] = frozenset({Platform.TIKTOK})

#: The platforms that refuse a browser signature for a request naming another
#: operating system than the page's. Douyin, measured on 2026-11-20 against
#: /aweme/v1/web/aweme/detail/ with a page running Chrome/146 on Windows, three
#: tries each: an identity on Chrome/145, /130 or Edge/146 on Windows answered,
#: and so did a header naming macOS over a query naming Windows; a query naming
#: macOS - which dtk builds from a macOS identity's User-Agent - was refused
#: with 403 "Blocked by ArgusSecurityPlugin", under either header, while the
#: native signer answered for the same identity. Refusing every Douyin
#: identity whose string differs, as this first did, sent ones the browser
#: signs fine to the native signer (#832 review); signing every one, as before,
#: handed a macOS identity a signature Douyin refuses.
SYSTEM_BOUND_PLATFORMS: frozenset[Platform] = frozenset({Platform.DOUYIN})

#: What a User-Agent claims, by the parts a person compares: the browser and
#: its major, and the system. The systems are tried in order, because an
#: Android string also says Linux and an iPhone one also says Mac OS X; the
#: browsers too, because Edge and Opera also say Chrome, and every Chromium
#: says Safari.
_AGENT_BROWSERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bEdg[AeiOS]*/(\d+)"), "Edge"),
    (re.compile(r"\bOPR/(\d+)"), "Opera"),
    (re.compile(r"\bFirefox/(\d+)"), "Firefox"),
    (re.compile(r"\bChrome/(\d+)"), "Chrome"),
    (re.compile(r"\bVersion/(\d+)"), "Safari"),
)
#:
#: The systems mirror dtk's own detector - `dtk.platforms.base._OS_TABLE`, same
#: markers, same order - because what Douyin judges is the system dtk writes
#: into the query, not the one a person would read off the string. A table of
#: our own missed X11, SunOS and a bare Macintosh and signed, under a Windows
#: page, for a query naming Linux: the refusal measured 0/11 (review of the
#: #832 fix). ChromeOS says "X11; CrOS", so it is Linux here as there. A
#: contract test in tests/test_rpc_api.py holds the two tables equal.
_AGENT_SYSTEMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Windows",), "Windows"),
    (("Android",), "Android"),
    (("SunOS",), "Sun OS"),
    (("iPhone", "iPad", "iPod"), "iOS"),
    (("Mac OS X",), "macOS"),
    (("MacPPC", "MacIntel", "Mac_PowerPC", "Macintosh"), "Mac OS"),
    (("Linux", "X11"), "Linux"),
)

#: What dtk writes into the query for a User-Agent naming none of the above
#: (`ClientProfile.os_name`'s default).
_QUERY_DEFAULT_SYSTEM = "Windows"


def describe_agent(agent: str) -> str:
    """'Chrome/146 on Windows': what a User-Agent claims, without the rest of it."""
    browser = next(
        (
            f"{name}/{match.group(1)}"
            for pattern, name in _AGENT_BROWSERS
            if (match := pattern.search(agent)) is not None
        ),
        "an unrecognised browser",
    )
    system = agent_system(agent)
    return f"{browser} on {system}" if system else browser


def agent_system(agent: str) -> str | None:
    """The operating system a User-Agent names, or None when it names none."""
    return next(
        (name for markers, name in _AGENT_SYSTEMS if any(marker in agent for marker in markers)),
        None,
    )


def _is_foreign(platform: Platform, wanted: str, page_agent: str) -> bool:
    """Whether a signature from a page running ``page_agent`` fails for ``wanted``."""
    if platform in UA_BOUND_PLATFORMS:
        return wanted != page_agent
    if platform in SYSTEM_BOUND_PLATFORMS:
        # The caller's side is the system dtk will write into the query, which
        # for a User-Agent naming none is its default, Windows - not "unknown":
        # a page on another system refuses that query as it would any other.
        # The page's side is what it runs; a page whose string names no system
        # cannot be held to one.
        theirs = agent_system(wanted) or _QUERY_DEFAULT_SYSTEM
        ours = agent_system(page_agent)
        return ours is not None and theirs != ours
    return False


def _refuse_a_foreign_agent(
    platform: Platform, wanted: str | None, page_agent: str | None, identity_id: str | None
) -> None:
    """Raise `UserAgentMismatch` when the page's signature would fail for the caller's UA.

    How much has to agree is the platform's: the whole string on TikTok
    (`UA_BOUND_PLATFORMS`), the operating system on Douyin
    (`SYSTEM_BOUND_PLATFORMS`). Nothing to compare when one side is unknown:
    a caller that names no User-Agent takes the page's, and a page that did
    not report one cannot be held to it.
    """
    if not wanted or not page_agent or not _is_foreign(platform, wanted, page_agent):
        return
    page, theirs = describe_agent(page_agent), describe_agent(wanted)
    logger.info(
        "browser_rpc.sign.user_agent_mismatch platform=%s identity=%s page=%s requested=%s",
        platform.value,
        _short(identity_id),
        page,
        theirs,
    )
    # Two strings that read alike still differ somewhere - a build number, a
    # brand token this summary does not name - and "Chrome/146 and Chrome/146"
    # would leave the operator nothing to look for.
    other = (
        f"this identity's is {theirs}"
        if theirs != page
        else f"this identity's is a different {theirs} string (sha256 {_agent_sha(wanted)}, "
        f"this browser's {_agent_sha(page_agent)})"
    )
    rule = (
        "refuses a request whose User-Agent is not the one its signature sealed"
        if platform in UA_BOUND_PLATFORMS
        else "refuses a browser signature for a request naming another operating system"
    )
    raise UserAgentMismatch(
        f"this browser signs {platform.value} requests as {page} and {other}: the platform "
        f"{rule}, so one taken here would be refused for this identity - dtk signs it "
        "natively where it may"
    )


def _agent_sha(agent: str) -> str:
    """A short digest of a User-Agent, to tell two that read alike apart in a log."""
    return hashlib.sha256(agent.encode()).hexdigest()[:10]


def _short(identity_id: str | None) -> str:
    """An identity id abbreviated for a log line, never the cookies behind it."""
    return (identity_id or "-")[:8]


def _exit_kind(identity_proxy: str | None, exit_url: str | None) -> str:
    """Which exit a page loads through, by kind and never by address."""
    if identity_proxy:
        return "identity_proxy"
    return "sign_proxy" if exit_url else "direct"


#: What two pages that failed alike share, per suspect, as the note says it.
#: The exit is named only where the failure implicates it: both pages also load
#: the same platform bundle in the same browser, and a note that blamed the exit
#: for everything sent operators to their proxy when the platform had stopped
#: signing.
_SHARED: dict[Suspect | None, str] = {
    Suspect.EXIT: "through the same exit ({exit})",
    Suspect.BUNDLE: "loading the same platform bundle through the same exit ({exit})",
    Suspect.BROWSER: "in the same browser container",
    None: "",
}


def _final_error(failures: list[RpcError], exit_kind: str) -> RpcError:
    """The error a signature that failed twice reports.

    The last failure, as before - except when both attempts failed with the same
    message. They ran on two different pages, so the same answer twice is not a
    page that went stale but something both pages share, and the note says
    what that is for this kind of failure (`Suspect`). That is the difference
    between "restart the browser" and "look at the proxy", which the reporter of
    #830 could not tell from two identical log lines.

    The cause comes first and the note last. dtk keeps only the head of this
    message, and when the note led, the cut fell in the middle of the #830
    sentence's remedy - the list of exits to check.
    """
    if not failures:
        return BackendFailure("signing produced no result")
    last = failures[-1]
    if len(failures) > 1 and all(_cause(f) == _cause(last) for f in failures):
        shared = _SHARED.get(last.suspect, "").format(exit=exit_kind)
        where = f"on two different pages {shared}" if shared else "on two different pages"
        return type(last)(
            f"{last.message}; both attempts failed this way, {where}",
            detail=last.detail,
            suspect=last.suspect,
        )
    return last


#: The one count a repeated failure's message carries that differs between two
#: pages failing for the same reason.
_PROBE_COUNT = re.compile(r"\(\d+ probes\)")


def _cause(error: RpcError) -> tuple[type, Suspect | None, str]:
    """What makes two failures the same failure, with the counts taken out.

    Comparing whole messages dropped the note for the case it was written for:
    a page that never signed reports how many probes fit in its budget, and two
    pages often differ by one (review of the #830 fix).
    """
    return type(error), error.suspect, _PROBE_COUNT.sub("(# probes)", error.message)


__all__ = ["ANONYMOUS", "BrowserRpcService", "MintOutcome", "WarmSlot", "binding_key"]
