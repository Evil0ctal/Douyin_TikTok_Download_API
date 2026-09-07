"""The request pipeline.

One place where a lease, an identity, a signature, a transport call, a
classification and a parse are strung together. Everything above this layer
(REST, MCP, CLI) calls in here; nothing below it knows the others exist.

The ordering constraints that matter:

* the lease is released with the outcome that actually occurred, because that is
  what decides whether the token is refunded and whether the identity cools;
* the identity's bookkeeping and the endpoint's circuit window are both updated
  even when the parse later fails, since the upstream call did happen;
* a NETWORK_ERROR is retried on a *different* identity, because the fault is in
  the egress rather than in the request.

See docs/design/01-architecture.md.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import (
    DtkError,
    Internal,
    NotFound,
    UpstreamChanged,
    UpstreamRiskControl,
)
from dtk.core.logging import get_logger
from dtk.core.types import Outcome, Platform
from dtk.db.models import RequestLog
from dtk.identity.pool import IdentityPool, LiveIdentity
from dtk.platforms import PlatformAdapter, get_adapter
from dtk.scheduler.leases import Lease
from dtk.scheduler.policies import policy_for
from dtk.scheduler.scheduler import Scheduler
from dtk.services import cache
from dtk.transport.base import (
    Fingerprint,
    RawResponse,
    Transport,
    TransportFailure,
    TransportIdentity,
)

log = get_logger(__name__)

#: How many different identities one logical request may burn on transport
#: failures before giving up. Small on purpose: if three separate egresses
#: cannot reach the platform, the problem is not the identity.
MAX_TRANSPORT_ATTEMPTS = 3


@dataclass(slots=True)
class FetchResult:
    payload: dict[str, Any]
    outcome: Outcome
    identity_id: str | None
    cached: bool
    duration_ms: int
    request_id: uuid.UUID
    signer: str | None = None


@dataclass(slots=True)
class FetchContext:
    """Everything one call needs, so the service holds no request state."""

    request_id: uuid.UUID = field(default_factory=uuid.uuid4)
    api_key_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    include_raw: bool = False


ProxyResolver = Callable[[str], Awaitable[str | None]]
SignFn = Callable[[Platform, str, dict[str, Any], Fingerprint], Awaitable[dict[str, str]]]


class FetchService:
    def __init__(
        self,
        *,
        scheduler: Scheduler,
        pool: IdentityPool,
        transport: Transport,
        sign: SignFn,
        proxy_resolver: ProxyResolver | None = None,
        cooldown_base: int = 60,
        cooldown_max: int = 21600,
        request_timeout: float = 25.0,
    ) -> None:
        self._scheduler = scheduler
        self._pool = pool
        self._transport = transport
        self._sign = sign
        self._proxy_resolver = proxy_resolver
        self._cooldown_base = cooldown_base
        self._cooldown_max = cooldown_max
        self._timeout = request_timeout

    # -- one upstream call -------------------------------------------------

    async def _call_once(
        self,
        session: AsyncSession,
        adapter: PlatformAdapter,
        endpoint: str,
        params: dict[str, Any],
        ctx: FetchContext,
    ) -> tuple[RawResponse | None, Lease, LiveIdentity | None, Outcome, str | None]:
        platform = Platform(adapter.platform)
        lease = await self._scheduler.acquire(endpoint, platform)

        identity: LiveIdentity | None = None
        response: RawResponse | None = None
        outcome = Outcome.NETWORK_ERROR
        error_code: str | None = None

        try:
            proxy_url = (
                await self._proxy_resolver(lease.identity_id) if self._proxy_resolver else None
            )
            identity = await self._pool.load(session, lease.identity_id, proxy_url=proxy_url)
            if identity is None:
                # It was retired between ranking and leasing.
                outcome = Outcome.NETWORK_ERROR
                error_code = "identity_gone"
                return None, lease, None, outcome, error_code

            spec = adapter.build_request(endpoint, **params)
            signed = await self._sign(
                platform, spec["url"], dict(spec.get("params") or {}), identity.fingerprint
            )
            merged = {**(spec.get("params") or {}), **signed}

            response = await self._transport.request(
                TransportIdentity(
                    id=identity.id,
                    cookies=identity.cookies,
                    fingerprint=identity.fingerprint,
                    proxy_url=identity.proxy_url,
                ),
                {**spec, "params": merged},
                self._timeout,
            )
            outcome = _classify(adapter, response)
        except TransportFailure as exc:
            outcome = Outcome.NETWORK_ERROR
            error_code = "transport_failure"
            log.warning(
                "fetch.transport_failed",
                endpoint=endpoint,
                identity_id=lease.identity_id,
                error=str(exc)[:200],
            )
        except DtkError as exc:
            outcome = Outcome.BUSINESS_ERROR
            error_code = exc.code.value
            raise
        finally:
            await self._scheduler.release(lease, outcome)
            await self._pool.record_outcome(
                session,
                lease.identity_id,
                outcome,
                cooldown_base=self._cooldown_base,
                cooldown_max=self._cooldown_max,
                risk_weight=policy_for(endpoint).risk_weight,
            )

        return response, lease, identity, outcome, error_code

    # -- public ------------------------------------------------------------

    async def fetch(
        self,
        session: AsyncSession,
        platform: Platform,
        endpoint: str,
        params: dict[str, Any],
        *,
        parse: Callable[[dict[str, Any]], Any],
        cache_ttl: int = 0,
        ctx: FetchContext | None = None,
    ) -> FetchResult:
        ctx = ctx or FetchContext()
        adapter = get_adapter(platform)
        started = time.monotonic()
        digest = cache.cache_key(endpoint, params)

        hit = await cache.get(digest) if cache_ttl > 0 else None
        if hit is not None:
            # A cache hit costs no identity quota; it is the cheapest protection
            # the pool has.
            return FetchResult(
                payload=hit,
                outcome=Outcome.OK,
                identity_id=None,
                cached=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                request_id=ctx.request_id,
            )

        last_error: str | None = None
        for attempt in range(MAX_TRANSPORT_ATTEMPTS):
            response, lease, _identity, outcome, error_code = await self._call_once(
                session, adapter, endpoint, params, ctx
            )
            duration_ms = int((time.monotonic() - started) * 1000)

            await self._log_request(
                session,
                ctx=ctx,
                platform=platform,
                endpoint=endpoint,
                identity_id=lease.identity_id,
                outcome=outcome,
                status=response.status if response else None,
                duration_ms=duration_ms,
                error_code=error_code,
            )

            if outcome is Outcome.NETWORK_ERROR:
                last_error = error_code or "network_error"
                if attempt + 1 < MAX_TRANSPORT_ATTEMPTS:
                    continue  # a different identity, i.e. a different egress
                raise Internal(
                    f"upstream unreachable after {MAX_TRANSPORT_ATTEMPTS} attempts",
                    details={"endpoint": endpoint, "last_error": last_error},
                )

            if outcome is Outcome.RISK_CONTROL:
                raise UpstreamRiskControl(
                    "the platform returned a risk-control response",
                    retry_after=self._cooldown_base,
                    details={"endpoint": endpoint},
                )

            assert response is not None
            payload = _decode(response)

            if outcome is Outcome.BUSINESS_ERROR:
                raise NotFound(
                    "the requested content does not exist or is unavailable",
                    details={"endpoint": endpoint},
                )

            parsed = parse(payload)
            result = _dump(parsed, include_raw=ctx.include_raw)
            if cache_ttl > 0:
                await cache.put(digest, result, cache_ttl)

            return FetchResult(
                payload=result,
                outcome=outcome,
                identity_id=lease.identity_id,
                cached=False,
                duration_ms=duration_ms,
                request_id=ctx.request_id,
            )

        raise Internal("unreachable")

    async def _log_request(
        self,
        session: AsyncSession,
        *,
        ctx: FetchContext,
        platform: Platform,
        endpoint: str,
        identity_id: str | None,
        outcome: Outcome,
        status: int | None,
        duration_ms: int,
        error_code: str | None,
    ) -> None:
        session.add(
            RequestLog(
                ts=datetime.now(UTC),
                request_id=ctx.request_id,
                task_id=ctx.task_id,
                platform=platform.value,
                endpoint=endpoint,
                identity_id=uuid.UUID(identity_id) if identity_id else None,
                api_key_id=ctx.api_key_id,
                outcome=outcome.value,
                http_status=status,
                duration_ms=duration_ms,
                cache_hit=False,
                error_code=error_code,
            )
        )


def _classify(adapter: PlatformAdapter, response: RawResponse) -> Outcome:
    """Map a response onto one of the four outcome classes.

    Separating BUSINESS_ERROR from RISK_CONTROL is the point. V4 treated every
    non-200 alike, so looking up a deleted video could condemn a working cookie.
    """
    if response.status in (429,) or response.status >= 500:
        return Outcome.RISK_CONTROL
    if not response.ok:
        return Outcome.BUSINESS_ERROR
    try:
        payload = _decode(response)
    except ValueError:
        return Outcome.RISK_CONTROL
    marker = adapter.detect_risk_control(payload)
    if marker:
        log.warning("fetch.risk_control", marker=marker, status=response.status)
        return Outcome.RISK_CONTROL
    return Outcome.OK


def _decode(response: RawResponse) -> dict[str, Any]:
    import json

    if not response.body:
        return {}
    return json.loads(response.body.decode(response.charset or "utf-8", errors="replace"))


def _dump(parsed: Any, *, include_raw: bool) -> dict[str, Any]:
    if hasattr(parsed, "model_dump"):
        exclude = None if include_raw else {"raw"}
        return parsed.model_dump(mode="json", exclude=exclude)
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


__all__ = [
    "MAX_TRANSPORT_ATTEMPTS",
    "FetchContext",
    "FetchResult",
    "FetchService",
    "UpstreamChanged",
]
