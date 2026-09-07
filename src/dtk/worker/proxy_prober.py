"""Proxy connectivity and GeoIP probing.

A dead proxy shows up as "every identity behind it started failing". Without
this job the system reads that as a batch of bad cookies and retires them, and
credentials that would have been fine behind a working egress are gone for good.

So two probes exist, as doc 02 requires:

* a low-frequency sweep of every proxy, which also refreshes the GeoIP used to
  align a minted browser's timezone and language;
* an on-demand probe as soon as one proxy accumulates three network errors in
  five minutes, because waiting for the next sweep to notice is five more
  minutes of burning identities on a dead exit.

A failed probe **cools** the identities behind the proxy. It never retires them
and never moves them to another proxy: the cookies are fine, only the egress is
down, and re-pairing a live cookie jar with a different exit IP is the single
most correlatable thing this system could do. Retiring the identities of a
permanently dead proxy is the operator's call - a decision, not a side effect of
a timeout.

See docs/design/02-identity-pool.md and docs/design/15-operations.md.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select

from dtk.core.crypto import Cipher
from dtk.core.db import session_scope
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Outcome
from dtk.db.models import Identity as IdentityRow
from dtk.db.models import Proxy as ProxyRow
from dtk.db.models import RequestLog
from dtk.identity.pool import IdentityPool
from dtk.transport import mask_proxy_url
from dtk.worker.alerts import Alerter, NotifyEvent, raise_alert

log = get_logger(__name__)

#: Probe target. Returns the exit address plus its country and timezone, which
#: is exactly the GeoIP minting needs to align the browser locale.
DEFAULT_PROBE_URL = "https://ipinfo.io/json"

#: Redis key for the on-demand network-error counter.
ERROR_KEY = "worker:proxy:netfail:{proxy_id}"

SessionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Connectivity and GeoIP for one egress."""

    ok: bool
    exit_ip: str | None = None
    country: str | None = None
    timezone: str | None = None
    latency_ms: int | None = None
    detail: str | None = None


class ProbeClient(Protocol):
    async def probe(self, proxy_url: str | None) -> ProbeResult: ...

    async def aclose(self) -> None: ...


class HttpxProbeClient:
    """Default probe: one small JSON request through the proxy.

    A fresh client per probe on purpose. Connection pools are bound to an
    egress, and reusing one across proxies would report the health of whichever
    exit the pooled connection happens to hold.
    """

    __slots__ = ("_timeout", "_url")

    def __init__(self, url: str = DEFAULT_PROBE_URL, *, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout

    async def probe(self, proxy_url: str | None) -> ProbeResult:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url, timeout=self._timeout, follow_redirects=True
            ) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ProbeResult(
                ok=False,
                latency_ms=int((time.monotonic() - started) * 1000),
                detail=f"{type(exc).__name__}: {exc}"[:200],
            )
        return ProbeResult(
            ok=True,
            exit_ip=_first_str(body, "ip", "query", "exit_ip"),
            country=_first_str(body, "country", "country_code", "countryCode"),
            timezone=_first_str(body, "timezone", "time_zone", "timeZone"),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ProberConfig:
    interval_seconds: float = 300.0
    #: Doc 02: three network errors on one proxy inside five minutes.
    error_window_seconds: int = 300
    error_threshold: int = 3
    #: How long identities behind a failed proxy stay cool. Long enough that
    #: they are not tried again before the next sweep can clear the proxy.
    cooldown_seconds: int = 900
    #: Floor between two probes of the same proxy, so a burst of errors does
    #: not turn into a burst of probes.
    min_probe_interval_seconds: float = 60.0


@dataclass(slots=True)
class SweepReport:
    checked: int = 0
    healthy: int = 0
    unhealthy: int = 0
    cooled_identities: int = 0
    probed_on_demand: list[str] = field(default_factory=list)


class ProxyProber:
    def __init__(
        self,
        *,
        cipher: Cipher,
        pool: IdentityPool,
        client: ProbeClient,
        options: ProberConfig | None = None,
        session_factory: SessionFactory = session_scope,
        clock: Callable[[], float] = time.monotonic,
        alerter: Alerter | None = None,
    ) -> None:
        self._cipher = cipher
        self._pool = pool
        self._client = client
        self._options = options or ProberConfig()
        self._session_factory = session_factory
        self._now = clock
        self._alerter = alerter
        self._last_probe: dict[uuid.UUID, float] = {}
        #: Consecutive failed probes per proxy, so an alert can say how many.
        self._failures: dict[uuid.UUID, int] = {}

    # -- entry points ------------------------------------------------------

    async def tick(self) -> SweepReport:
        """One scheduled round: urgent probes first, then the full sweep."""
        urgent = await self.check_error_bursts()
        sweep = await self.sweep()
        sweep.checked += urgent.checked
        sweep.cooled_identities += urgent.cooled_identities
        sweep.probed_on_demand = urgent.probed_on_demand
        return sweep

    async def sweep(self) -> SweepReport:
        """Probe every proxy and write back health and GeoIP."""
        report = SweepReport()
        async with self._session_factory() as session:
            proxies = (await session.execute(select(ProxyRow))).scalars().all()
            for proxy in proxies:
                await self._probe_proxy(session, proxy, report)
        return report

    async def probe_one(self, proxy_id: uuid.UUID, *, force: bool = False) -> ProbeResult | None:
        """Probe a single proxy. Used by the CLI and the console's proxy test."""
        async with self._session_factory() as session:
            proxy = await session.get(ProxyRow, proxy_id)
            if proxy is None:
                return None
            return await self._probe_proxy(session, proxy, SweepReport(), force=force)

    async def note_network_error(self, proxy_id: uuid.UUID) -> bool:
        """Count one network error against a proxy; probe once it hits three.

        The counter lives in Redis with the window as its TTL, so every worker
        replica contributes to the same count and nothing has to clean it up.
        """
        key = ERROR_KEY.format(proxy_id=proxy_id)
        redis = get_redis()
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, self._options.error_window_seconds)
        if count < self._options.error_threshold:
            return False
        await redis.delete(key)
        log.info("worker.proxy.error_burst", proxy_id=str(proxy_id), errors=count)
        await self.probe_one(proxy_id, force=True)
        return True

    async def check_error_bursts(self) -> SweepReport:
        """Find proxies whose identities are failing and probe them now.

        Derived from ``request_log`` rather than from an in-process counter, so
        it sees the failures of every worker replica.
        """
        report = SweepReport()
        since = datetime.now(UTC) - timedelta(seconds=self._options.error_window_seconds)
        async with self._session_factory() as session:
            stmt = (
                select(IdentityRow.proxy_id, func.count())
                .select_from(RequestLog)
                .join(IdentityRow, IdentityRow.id == RequestLog.identity_id)
                .where(
                    RequestLog.ts >= since,
                    RequestLog.outcome == Outcome.NETWORK_ERROR.value,
                    IdentityRow.proxy_id.is_not(None),
                )
                .group_by(IdentityRow.proxy_id)
                .having(func.count() >= self._options.error_threshold)
            )
            rows: Sequence[Any] = (await session.execute(stmt)).all()
            for proxy_id, errors in rows:
                proxy = await session.get(ProxyRow, proxy_id)
                if proxy is None:
                    continue
                log.info("worker.proxy.error_burst", proxy_id=str(proxy_id), errors=int(errors))
                if await self._probe_proxy(session, proxy, report, force=True) is not None:
                    report.probed_on_demand.append(str(proxy_id))
        return report

    # -- one proxy ---------------------------------------------------------

    async def _probe_proxy(
        self, session: Any, proxy: Any, report: SweepReport, *, force: bool = False
    ) -> ProbeResult | None:
        if not force and not self._due(proxy.id):
            return None
        self._last_probe[proxy.id] = self._now()

        try:
            url = self._cipher.decrypt(proxy.url_encrypted, aad=str(proxy.id))
        except Exception as exc:
            # Wrong master key, or a row written by another deployment. Not a
            # proxy fault, so nothing is cooled on the strength of it.
            log.error(
                "worker.proxy.undecryptable", proxy_id=str(proxy.id), error=type(exc).__name__
            )
            return None

        try:
            result = await self._client.probe(url)
        except Exception as exc:
            # The probe client itself broke rather than the egress: a scheme it
            # has no optional dependency for (``socks5://`` without
            # ``httpx[socks]``), a malformed row, a bug. That says nothing about
            # whether the proxy works, so nothing is cooled on the strength of
            # it - and, more importantly, one unprobeable proxy must not abort
            # the sweep and leave every proxy after it unchecked.
            log.error(
                "worker.proxy.probe_error",
                proxy_id=str(proxy.id),
                proxy=proxy.label or mask_proxy_url(url),
                error=(_without_credentials(f"{type(exc).__name__}: {exc}", url) or "")[:200],
            )
            return None

        report.checked += 1
        await self._apply(session, proxy, result, report, url=url)
        return result

    async def _apply(
        self, session: Any, proxy: Any, result: ProbeResult, report: SweepReport, *, url: str
    ) -> None:
        from dtk.db.repositories import ProxyRepository

        proxies = ProxyRepository(session)
        was_healthy = bool(proxy.healthy)
        await proxies.set_health(proxy.id, healthy=result.ok, checked_at=datetime.now(UTC))

        if result.ok:
            report.healthy += 1
            self._failures.pop(proxy.id, None)
            if result.country or result.timezone:
                await proxies.set_geo(
                    proxy.id,
                    country=result.country or proxy.country,
                    timezone=result.timezone or proxy.timezone,
                )
            log.info(
                "worker.proxy.healthy",
                proxy_id=str(proxy.id),
                recovered=not was_healthy,
                country=result.country,
                latency_ms=result.latency_ms,
            )
            return

        report.unhealthy += 1
        failures = self._failures.get(proxy.id, 0) + 1
        self._failures[proxy.id] = failures
        # Cool, never retire, and never re-point the identities at another
        # proxy: the cookies are fine, the egress is not.
        cooled = await self._pool.cool_all_on_proxy(
            session, proxy.id, seconds=self._options.cooldown_seconds
        )
        report.cooled_identities += cooled
        log.warning(
            "worker.proxy.unhealthy",
            proxy_id=str(proxy.id),
            label=proxy.label,
            cooled_identities=cooled,
            consecutive_failures=failures,
            # The client's message quotes whatever it was handed, so it is
            # scrubbed before it reaches a log line (doc 08).
            detail=_without_credentials(result.detail, url),
        )
        # The label is what an operator recognizes; the masked URL is the
        # fallback and never carries the proxy credentials.
        await raise_alert(
            self._alerter,
            NotifyEvent.PROXY_UNHEALTHY,
            proxy=proxy.label or mask_proxy_url(url) or str(proxy.id),
            failures=failures,
        )

    def _due(self, proxy_id: uuid.UUID) -> bool:
        last = self._last_probe.get(proxy_id)
        if last is None:
            return True
        return (self._now() - last) >= self._options.min_probe_interval_seconds

    async def aclose(self) -> None:
        await self._client.aclose()


def _without_credentials(text: str | None, url: str) -> str | None:
    """Replace any echo of the proxy URL with its masked form.

    An exception raised while connecting through a proxy quotes the URL it was
    given often enough that this cannot be left to chance: proxy credentials are
    as valuable as the cookies (docs/design/08-security.md) and a log line is
    forever.
    """
    if not text:
        return text
    masked = mask_proxy_url(url) or ""
    cleaned = text.replace(url, masked)
    userinfo = url.partition("://")[2].rpartition("@")[0]
    if userinfo:
        cleaned = cleaned.replace(f"{userinfo}@", "").replace(userinfo, "")
    return cleaned


def _first_str(body: Any, *keys: str) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


__all__ = [
    "DEFAULT_PROBE_URL",
    "ERROR_KEY",
    "HttpxProbeClient",
    "ProbeClient",
    "ProbeResult",
    "ProberConfig",
    "ProxyProber",
    "SweepReport",
]
