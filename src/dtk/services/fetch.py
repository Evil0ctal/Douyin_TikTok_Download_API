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
  the egress rather than in the request;
* every attempt writes exactly one ``request_log`` row, including the ones that
  never reached the platform. This service is the table's only writer, so a path
  that skips it is a hole in the console, in the health aggregates and in the
  audit trail at once.

The outcome itself is not decided here: :mod:`dtk.transport.classify` owns the
tables, and this module only adds the platform's own 200-body signature on top
of them. Two classifiers disagreeing about what a 403 means is how a refused
identity gets reported as missing content.

See docs/design/01-architecture.md.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

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
from dtk.db.models import Identity as IdentityRow
from dtk.db.models import RequestLog
from dtk.identity.pool import IdentityPool
from dtk.platforms import PlatformAdapter, get_adapter
from dtk.platforms.base import RequestSpec as PlatformRequestSpec
from dtk.scheduler.leases import Lease
from dtk.scheduler.policies import policy_for
from dtk.scheduler.scheduler import Scheduler
from dtk.services import cache
from dtk.transport.base import (
    RawResponse,
    Transport,
    TransportFailure,
    TransportIdentity,
)
from dtk.transport.base import RequestSpec as TransportRequestSpec
from dtk.transport.classify import Classification

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
    #: An egress the caller asked for, already validated by
    #: :mod:`dtk.api.request_proxy`. It REPLACES the identity's own exit, which
    #: is a real cost and not a free option: the identity's cookies were minted
    #: behind one address and would now be presented from another, and a jar
    #: that disagrees with its exit is the incoherence docs 02 and 04 spend
    #: their length avoiding. It is offered because a deployment with no proxies
    #: configured has no other way to give a caller an egress, and because the
    #: caller asking is the one who bears the cost. Off unless an operator turns
    #: it on.
    request_proxy: str | None = None


@dataclass(frozen=True, slots=True)
class _Attempt:
    """What one pass through :meth:`FetchService._call_once` produced.

    ``payload`` is decoded there rather than here because the platform's
    risk-control signature is read from the same bytes; ``None`` on an otherwise
    OK response means the body was not JSON at all.
    """

    lease: Lease
    outcome: Outcome
    payload: dict[str, Any] | None = None
    error_code: str | None = None
    signer: str | None = None


class SignedRequest(Protocol):
    """What the injected signer hands back.

    Structural, so the pipeline still knows nothing about ``dtk.signing`` beyond
    the two things it needs: the parameters to send, and which signer produced
    them - ``native`` or ``browser``, which is a column on ``request_log`` and
    the first thing to look at when one platform's success rate drops alone.
    """

    @property
    def params(self) -> Mapping[str, str]: ...

    @property
    def signer(self) -> str: ...


ProxyResolver = Callable[[str], Awaitable[str | None]]
#: The identity is passed whole rather than as a bare fingerprint. Signing needs
#: the cookie jar as well: the browser signer loads a page with it, so that the
#: `verifyFp` in the query is the `s_v_web_id` in the jar the request carries.
#: Passing only the fingerprint is what let the two disagree, and a request whose
#: signature names a different session than its cookies gets an empty payload.
SignFn = Callable[[Platform, str, dict[str, Any], TransportIdentity], Awaitable[SignedRequest]]


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
        *,
        started: float,
    ) -> _Attempt:
        platform = Platform(adapter.platform)
        try:
            lease = await self._scheduler.acquire(endpoint, platform)
        except DtkError as exc:
            # A refusal is the shape of an outage, and it happens before there is
            # anything else to log. Without this row the Logs page falls silent
            # precisely while someone is watching it to find out why.
            await self._log_request(
                session,
                ctx=ctx,
                platform=platform,
                endpoint=endpoint,
                identity_id=None,
                outcome=Outcome.NETWORK_ERROR,
                status=None,
                duration_ms=_elapsed_ms(started),
                error_code=exc.code.value,
                reject_reason=_reject_reason(exc),
            )
            raise

        proxy_id: uuid.UUID | None = None
        payload: dict[str, Any] | None = None
        status: int | None = None
        signer: str | None = None
        outcome = Outcome.NETWORK_ERROR
        error_code: str | None = None

        try:
            proxy_url = (
                await self._proxy_resolver(lease.identity_id) if self._proxy_resolver else None
            )
            if ctx.request_proxy:
                # Say it happened. An identity sent from an exit it was not
                # minted behind is a plausible cause of a later refusal, and a
                # silent override would leave nothing to correlate that against.
                log.info(
                    "fetch.request_proxy.applied",
                    identity_id=str(lease.identity_id),
                    endpoint=endpoint,
                    replaced_bound_proxy=proxy_url is not None,
                )
                proxy_url = ctx.request_proxy
            identity = await self._pool.load(session, lease.identity_id, proxy_url=proxy_url)
            if identity is None:
                # It was retired between ranking and leasing.
                outcome = Outcome.NETWORK_ERROR
                error_code = "identity_gone"
                return _Attempt(lease=lease, outcome=outcome, error_code=error_code)
            proxy_id = await _proxy_id_of(session, identity.id)

            spec = adapter.build_request(endpoint, **params)
            # Built once and used twice, deliberately: the signature and the
            # request have to describe the same visitor, and building the
            # identity separately for each is how they came to disagree.
            sender = TransportIdentity(
                id=identity.id,
                platform=identity.platform,
                cookies=identity.cookies,
                fingerprint=identity.fingerprint,
                proxy_url=identity.proxy_url,
            )
            signed = await self._sign(platform, spec["url"], dict(spec.get("params") or {}), sender)
            signer = signed.signer
            merged = {**(spec.get("params") or {}), **signed.params}

            response = await self._transport.request(
                sender,
                _to_transport_spec(spec, merged, endpoint),
                self._timeout,
            )
            status = response.status
            classification, payload = self._classify(adapter, response)
            outcome = classification.outcome
            error_code = _error_code_for(classification, payload)
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
            await self._log_request(
                session,
                ctx=ctx,
                platform=platform,
                endpoint=endpoint,
                identity_id=lease.identity_id,
                outcome=outcome,
                status=status,
                duration_ms=_elapsed_ms(started),
                error_code=error_code,
                proxy_id=proxy_id,
                signer=signer,
            )

        return _Attempt(
            lease=lease,
            outcome=outcome,
            payload=payload,
            error_code=error_code,
            signer=signer,
        )

    def _classify(
        self, adapter: PlatformAdapter, response: RawResponse
    ) -> tuple[Classification, dict[str, Any] | None]:
        """Judge one response, decoding it only when the verdict needs the body.

        The tabled classifier behind the transport rules on the status, so a
        401, 403, 444 or 407 is what it is: the platform refusing this identity,
        or an exit that is no longer usable. The adapter's own signature stays
        on top of it for the 200s that carry the refusal in the body instead.

        A body that will not decode is *not* evidence about the identity - the
        markers table is what speaks to that - so it is reported as a missing
        payload and the caller turns it into ``UpstreamChanged``.
        """
        classification = self._transport.classify(response)
        if classification.outcome is not Outcome.OK:
            return classification, None
        try:
            payload = _decode(response)
        except ValueError:
            return classification, None
        marker = adapter.detect_risk_control(payload)
        if marker:
            log.warning("fetch.risk_control", marker=marker, status=response.status)
            return Classification(Outcome.RISK_CONTROL, "platform.risk_marker", marker), payload
        return classification, payload

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
            # the pool has. It is still a request the caller made, and a Logs
            # page that omits them makes a cache that has started serving
            # everything look like an endpoint nobody is calling.
            await self._log_request(
                session,
                ctx=ctx,
                platform=platform,
                endpoint=endpoint,
                identity_id=None,
                outcome=Outcome.OK,
                status=None,
                duration_ms=_elapsed_ms(started),
                error_code=None,
                cache_hit=True,
            )
            return FetchResult(
                payload=hit,
                outcome=Outcome.OK,
                identity_id=None,
                cached=True,
                duration_ms=_elapsed_ms(started),
                request_id=ctx.request_id,
            )

        last_error: str | None = None
        for attempt in range(MAX_TRANSPORT_ATTEMPTS):
            call = await self._call_once(session, adapter, endpoint, params, ctx, started=started)
            duration_ms = _elapsed_ms(started)

            if call.outcome is Outcome.NETWORK_ERROR:
                last_error = call.error_code or "network_error"
                if attempt + 1 < MAX_TRANSPORT_ATTEMPTS:
                    continue  # a different identity, i.e. a different egress
                raise Internal(
                    f"upstream unreachable after {MAX_TRANSPORT_ATTEMPTS} attempts",
                    details={"endpoint": endpoint, "last_error": last_error},
                )

            if call.outcome is Outcome.RISK_CONTROL:
                raise UpstreamRiskControl(
                    "the platform returned a risk-control response",
                    retry_after=self._cooldown_base,
                    details={"endpoint": endpoint},
                )

            if call.outcome is Outcome.BUSINESS_ERROR:
                raise NotFound(
                    "the requested content does not exist or is unavailable",
                    details={"endpoint": endpoint},
                )

            if call.payload is None:
                # Classified as a working response, and still not JSON: the
                # endpoint answers in a shape this build cannot read.
                raise UpstreamChanged("response.body", platform=platform.value)

            parsed = parse(call.payload)
            result = _dump(parsed, include_raw=ctx.include_raw)
            if cache_ttl > 0:
                await cache.put(digest, result, cache_ttl)

            return FetchResult(
                payload=result,
                outcome=call.outcome,
                identity_id=call.lease.identity_id,
                cached=False,
                duration_ms=duration_ms,
                request_id=ctx.request_id,
                signer=call.signer,
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
        proxy_id: uuid.UUID | None = None,
        signer: str | None = None,
        cache_hit: bool = False,
        reject_reason: str | None = None,
    ) -> None:
        """Append one row. Every column the Logs page reads is filled here.

        The four that used to be left out - the egress, the signer, the cache
        flag and the refusal reason - are the ones an outage is diagnosed with,
        and this is the only writer the table has.
        """
        session.add(
            RequestLog(
                ts=datetime.now(UTC),
                request_id=ctx.request_id,
                task_id=ctx.task_id,
                platform=platform.value,
                endpoint=endpoint,
                identity_id=uuid.UUID(identity_id) if identity_id else None,
                proxy_id=proxy_id,
                api_key_id=ctx.api_key_id,
                outcome=outcome.value,
                http_status=status,
                duration_ms=duration_ms,
                cache_hit=cache_hit,
                signer=signer,
                error_code=error_code,
                reject_reason=reject_reason,
            )
        )


def _to_transport_spec(
    spec: PlatformRequestSpec | Mapping[str, Any], params: dict[str, Any], endpoint: str
) -> TransportRequestSpec:
    """Bridge the platform's request description onto the transport's.

    Two deliberately different shapes meet here. ``dtk.platforms`` emits a plain
    TypedDict so platform packages stay free of transport types, while the
    transport takes a dataclass that also carries the logical endpoint name for
    scheduling and log correlation. Converting at this seam is the orchestration
    layer's job; letting either side learn the other's type is what turns two
    independent modules into one tangled one.
    """
    body = spec.get("body")
    return TransportRequestSpec(
        url=spec["url"],
        method=str(spec.get("method") or "GET"),
        params={k: str(v) for k, v in params.items() if v is not None},
        headers=dict(spec.get("headers") or {}),
        json_body=body if body else None,
        # The logical name, never the signed URL: that carries tokens and would
        # make every log line unique and uncorrelatable.
        endpoint=endpoint,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _reject_reason(exc: DtkError) -> str | None:
    """The scheduler's own code for a refusal, as it goes into the log row.

    A wire code rather than the sentence: the console translates the ones it
    knows and prints the rest verbatim, and a runbook can key off it.
    """
    reason = exc.details.get("reject_reason")
    return str(reason) if reason else None


def _error_code_for(classification: Classification, payload: dict[str, Any] | None) -> str | None:
    """What a classified response leaves in the log row's ``error_code``.

    The matched rule, but only where the identity or the egress is implicated:
    that column is the one place the table has for *why*, and a shift in the mix
    of risk signals is what it is worth spending on. A missing video is already
    fully told by the outcome and the status beside it.
    """
    if classification.outcome in (Outcome.RISK_CONTROL, Outcome.NETWORK_ERROR):
        return classification.rule
    if payload is None and classification.outcome is Outcome.OK:
        return UpstreamChanged.code.value
    return None


async def _proxy_id_of(session: AsyncSession, identity_id: str) -> uuid.UUID | None:
    """The egress this identity is bound to, for the log row.

    The pool has just loaded the same row through this session, so this is an
    identity-map lookup rather than a second round trip. The id is what the
    console joins on; the decrypted URL next to it carries credentials and never
    reaches a row at all.
    """
    row = await session.get(IdentityRow, uuid.UUID(identity_id))
    return row.proxy_id if row is not None else None


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
    "SignedRequest",
    "UpstreamChanged",
]
