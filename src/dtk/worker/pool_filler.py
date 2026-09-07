"""Keeps the identity pool above its low-water mark.

Minting is the one background job whose *pace* is part of its correctness. Doc
02 is explicit: top the pool up one identity at a time, because a burst of
simultaneous mints is itself the anomaly - five fresh visitors appearing from
one deployment inside a second is a stronger signal than the requests those
identities would go on to make.

So this module mints exactly one identity per tick, holds a cross-process lock
while doing it (``docker compose up --scale worker=N`` must not turn one mint
into N), and backs off exponentially when minting keeps failing. Refilling from
the low-water mark to the target therefore takes minutes, which is the intended
speed: minting is off the request path and nothing waits on it.

When ``DTK_BROWSER_RPC_URL`` is unset the whole job is skipped. That deployment
has no browser container at all and lives on manually imported cookies; a
filler that logged an error every minute would be pure noise.

See docs/design/02-identity-pool.md.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dtk.core.config import Config
from dtk.core.crypto import Cipher
from dtk.core.db import session_scope
from dtk.core.logging import get_logger
from dtk.core.redis import run_script
from dtk.core.types import IdentitySource, IdentityState, Platform
from dtk.identity.minting import BrowserRpcClient, BrowserRpcUnavailable
from dtk.identity.pool import IdentityPool
from dtk.worker.alerts import Alerter, NotifyEvent, raise_alert

log = get_logger(__name__)

#: One mint at a time across every worker replica.
MINT_LOCK_KEY = "worker:mint:lock"

#: Compare-and-delete, so a lock that already expired and was taken by another
#: replica is not released by the previous holder.
_UNLOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

SessionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class FillerConfig:
    #: How often the pool level is checked. The mint itself takes far longer.
    interval_seconds: float = 60.0
    backoff_initial_seconds: float = 60.0
    backoff_max_seconds: float = 3600.0
    #: Lock lifetime; must exceed the slowest mint or two replicas could mint
    #: at once, which is the one thing this job may not do.
    lock_ttl_seconds: int = 300
    platforms: tuple[Platform, ...] = tuple(Platform)


@dataclass(frozen=True, slots=True)
class FillResult:
    """What one tick did, so the caller and the tests can assert on it."""

    platform: Platform | None = None
    identity_id: uuid.UUID | None = None
    minted: bool = False
    reason: str = "idle"


class PoolFiller:
    def __init__(
        self,
        *,
        pool: IdentityPool,
        rpc: BrowserRpcClient | None,
        config: Callable[[], Config] | Config,
        cipher: Cipher | None = None,
        options: FillerConfig | None = None,
        session_factory: SessionFactory = session_scope,
        clock: Callable[[], float] = time.monotonic,
        distributed_lock: bool = True,
        alerter: Alerter | None = None,
    ) -> None:
        self._pool = pool
        self._rpc = rpc
        self._cipher = cipher
        self._alerter = alerter
        self._config: Callable[[], Config] = config if callable(config) else (lambda: config)
        self._options = options or FillerConfig()
        self._session_factory = session_factory
        self._now = clock
        self._distributed_lock = distributed_lock
        self._local_lock = asyncio.Lock()
        self._failures = 0
        self._blocked_until = 0.0
        #: Platforms currently being refilled. Entered below ``min_size`` and
        #: left at ``target_size``, so the pool is not re-triggered every tick
        #: while it sits one identity under the mark.
        self._filling: set[Platform] = set()

    # -- state -------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """False when no browser-rpc is configured: manual imports only."""
        return self._rpc is not None and self._rpc.configured

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def backing_off(self) -> bool:
        return self._now() < self._blocked_until

    # -- one tick ----------------------------------------------------------

    async def tick(self) -> FillResult:
        """Survey the pool, alert on it, and mint at most one identity.

        The survey runs even with minting disabled: a deployment living on
        imported cookies still needs to hear that its pool has run dry.
        """
        target = await self.survey()
        if not self.enabled:
            return FillResult(platform=target, reason="disabled")
        if self.backing_off:
            return FillResult(platform=target, reason="backoff")
        if target is None:
            return FillResult(reason="satisfied")
        return await self.top_up_once(target)

    async def survey(self) -> Platform | None:
        """Check every platform's level and return the neediest one.

        Alerts fire off the ACTIVE count, which is what a caller can actually
        use right now. The refill decision uses ACTIVE plus COOLING, because a
        cooling identity comes back on its own and minting a replacement for it
        during a platform-wide risk-control event is the worst possible timing.

        The alerts are collected here and delivered once the session has closed:
        a notifier POSTs to a webhook, and waiting for someone else's HTTP
        endpoint with a database transaction open would pin a pooled connection
        for as long as that endpoint feels like taking.
        """
        config = self._config()
        min_size = int(config.get("pool.min_size"))
        target_size = max(min_size, int(config.get("pool.target_size")))

        worst: tuple[int, Platform] | None = None
        pending: list[tuple[NotifyEvent, dict[str, Any]]] = []
        async with self._session_factory() as session:
            for platform in self._options.platforms:
                counts = await self._pool.counts(session, platform)
                active = int(counts.get(IdentityState.ACTIVE.value, 0))
                live = _live(counts)

                if active == 0:
                    pending.append((NotifyEvent.POOL_EMPTY, {"platform": platform.value}))
                elif active < min_size:
                    pending.append(
                        (
                            NotifyEvent.POOL_BELOW_MIN,
                            {"platform": platform.value, "active": active, "minimum": min_size},
                        )
                    )

                if live < min_size:
                    self._filling.add(platform)
                elif live >= target_size:
                    self._filling.discard(platform)
                if platform not in self._filling:
                    continue
                if worst is None or live < worst[0]:
                    worst = (live, platform)

        for event, args in pending:
            await raise_alert(self._alerter, event, **args)
        return worst[1] if worst else None

    async def top_up_once(self, platform: Platform) -> FillResult:
        """Mint exactly one identity for ``platform``.

        Serialized twice: an in-process lock for this worker's own loops and a
        Redis lock for the other replicas.
        """
        if not self.enabled:
            return FillResult(platform=platform, reason="disabled")
        if self._local_lock.locked():
            return FillResult(platform=platform, reason="busy")

        async with self._local_lock:
            token = uuid.uuid4().hex
            if not await self._acquire_remote(token):
                return FillResult(platform=platform, reason="locked")
            try:
                return await self._mint(platform)
            finally:
                await self._release_remote(token)

    async def _mint(self, platform: Platform) -> FillResult:
        assert self._rpc is not None  # guarded by `enabled`
        async with self._session_factory() as session:
            proxy, reason = await self._pick_proxy(session, platform)
            if reason is not None:
                log.info("worker.mint.skipped", platform=platform.value, reason=reason)
                return FillResult(platform=platform, reason=reason)

            proxy_url = None
            geo_hint: dict[str, Any] = {}
            if proxy is not None:
                proxy_url = self._decrypt(proxy)
                if proxy_url is None:
                    # Minting on the direct egress instead would bind the
                    # identity to an exit it will never use again.
                    return FillResult(platform=platform, reason="proxy_undecryptable")
                geo_hint = {
                    k: v for k, v in (("country", proxy.country), ("timezone", proxy.timezone)) if v
                }

            log.info(
                "worker.mint.started",
                platform=platform.value,
                proxy_id=str(proxy.id) if proxy is not None else None,
            )
            try:
                result = await self._rpc.mint(platform, proxy_url=proxy_url, geo_hint=geo_hint)
                identity_id = await self._pool.add(
                    session,
                    platform=platform,
                    cookies=result.cookies,
                    fingerprint=result.fingerprint,
                    source=IdentitySource.MINTED,
                    proxy_id=proxy.id if proxy is not None else None,
                )
            except BrowserRpcUnavailable as exc:
                return self._failed(platform, "rpc_unavailable", exc)
            except ValueError as exc:
                # A fingerprint with no inferable browser. Doc 02: refuse it
                # rather than pair a default UA with an unknown TLS profile.
                return self._failed(platform, "unusable_fingerprint", exc)

        self._failures = 0
        self._blocked_until = 0.0
        log.info(
            "worker.mint.succeeded",
            platform=platform.value,
            identity_id=str(identity_id),
            proxy_id=str(proxy.id) if proxy is not None else None,
        )
        return FillResult(platform=platform, identity_id=identity_id, minted=True, reason="minted")

    def _failed(self, platform: Platform, reason: str, exc: Exception) -> FillResult:
        self._failures += 1
        delay = min(
            self._options.backoff_initial_seconds * (2 ** (self._failures - 1)),
            self._options.backoff_max_seconds,
        )
        self._blocked_until = self._now() + delay
        log.warning(
            "worker.mint.failed",
            platform=platform.value,
            reason=reason,
            consecutive_failures=self._failures,
            backoff_seconds=int(delay),
            error=str(exc)[:200],
        )
        return FillResult(platform=platform, reason=reason)

    # -- proxies -----------------------------------------------------------

    async def _pick_proxy(self, session: Any, platform: Platform) -> tuple[Any, str | None]:
        """Choose an egress no live identity of this platform already uses.

        Two identities behind one exit address is the recombination doc 02
        forbids, so when every proxy is taken this job waits rather than
        doubling up. A deployment with no proxies at all mints on the direct
        egress, which is what a user without proxies asked for.
        """
        from dtk.db.repositories import ProxyRepository

        proxies = ProxyRepository(session)
        free = await proxies.list_unbound(platform)
        if free:
            return free[0], None
        if not await proxies.list_all():
            return None, None
        return None, "no_free_proxy"

    def _decrypt(self, proxy: Any) -> str | None:
        if self._cipher is None:
            # Without the key the URL cannot be used, and minting on the direct
            # egress instead would put the identity behind the wrong exit.
            log.warning("worker.mint.no_cipher", proxy_id=str(proxy.id))
            return None
        return self._cipher.decrypt(proxy.url_encrypted, aad=str(proxy.id))

    # -- cross-process lock ------------------------------------------------

    async def _acquire_remote(self, token: str) -> bool:
        if not self._distributed_lock:
            return True
        from dtk.core.redis import get_redis

        try:
            return bool(
                await get_redis().set(
                    MINT_LOCK_KEY, token, ex=self._options.lock_ttl_seconds, nx=True
                )
            )
        except Exception as exc:
            # Without the lock this cannot promise one mint at a time, so it
            # declines to mint rather than risking a burst.
            log.warning("worker.mint.lock_failed", error=f"{type(exc).__name__}: {exc}")
            return False

    async def _release_remote(self, token: str) -> None:
        if not self._distributed_lock:
            return
        try:
            await run_script("worker_mint_unlock", _UNLOCK_LUA, [MINT_LOCK_KEY], [token])
        except Exception as exc:  # pragma: no cover - the TTL still frees it
            log.warning("worker.mint.unlock_failed", error=f"{type(exc).__name__}: {exc}")


def _live(counts: dict[str, int]) -> int:
    """Identities that count towards the pool level.

    Cooling identities are included: they come back on their own when the
    backoff elapses. Counting only ACTIVE would trigger a minting spree during
    a platform-wide risk-control event, which is the worst possible moment to
    add fresh identities. DEGRADED is excluded - it is a last-resort tier.
    """
    return int(counts.get(IdentityState.ACTIVE.value, 0)) + int(
        counts.get(IdentityState.COOLING.value, 0)
    )


__all__ = ["MINT_LOCK_KEY", "FillResult", "FillerConfig", "PoolFiller"]
