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
from typing import Any, Protocol, cast
from urllib.parse import urlencode

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
from dtk.ops.masking import mask_url
from dtk.platforms import PlatformAdapter, get_adapter
from dtk.platforms.base import RequestSpec as PlatformRequestSpec
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
from dtk.transport.base import RequestSpec as TransportRequestSpec
from dtk.transport.classify import Classification
from dtk.transport.emulation import UnsupportedFingerprint
from dtk.transport.headers import build_headers

log = get_logger(__name__)

#: How many different identities one logical request may burn on transport
#: failures before giving up. Small on purpose: if three separate egresses
#: cannot reach the platform, the problem is not the identity.
MAX_TRANSPORT_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Explanation:
    """The request as it actually left this instance.

    Everything here is normally invisible on purpose. The signed URL carries
    the identity's ``msToken``, and ``cookie_header`` is the jar itself - so
    this is assembled only when a caller asks for it, behind the same scope
    that reveals a jar on the identities page, and it is never written to the
    request log.

    It exists because the console could show which identity served a call and
    could not show what that call *was*. Reproducing a platform request by hand
    - the thing anybody debugging one ends up doing - meant guessing at the
    query, the headers and the jar. `curl` is that guesswork removed.
    """

    method: str
    #: The full platform URL, signature parameters included.
    url: str
    headers: Mapping[str, str]
    #: The jar as one ``Cookie:`` header, ready to paste.
    cookie_header: str
    identity_id: str | None
    signer: str | None
    endpoint: str
    #: Masked. The exit is an operator credential and stays one.
    proxy: str | None


@dataclass(slots=True)
class FetchResult:
    payload: dict[str, Any]
    outcome: Outcome
    identity_id: str | None
    cached: bool
    duration_ms: int
    request_id: uuid.UUID
    signer: str | None = None
    #: Present only when the caller asked, and had the scope to.
    explain: Explanation | None = None


@dataclass(slots=True)
class FetchContext:
    """Everything one call needs, so the service holds no request state."""

    request_id: uuid.UUID = field(default_factory=uuid.uuid4)
    api_key_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    include_raw: bool = False
    #: The public demo account made this call, so no row is appended to
    #: ``request_log`` for it. Everything else about the request is identical -
    #: it spends an identity, its outcome cools or clears that identity, and it
    #: counts toward the endpoint's circuit - because all of that lives in
    #: Redis and none of it is what fills a disk. What is suppressed is only
    #: the durable per-request row, which on a public instance is the table
    #: that grows without bound and tells the operator nothing they wanted.
    is_demo: bool = False
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
    #: One identity, named by the caller, that this request must go out on -
    #: and must not silently be served by any other. The case it exists for is
    #: a jar the caller imported from their own logged-in browser: the content
    #: they are asking for is visible to that session and to no other, so a
    #: substitution does not degrade the answer, it changes what was asked.
    #: Requires `identity:manage`; see
    #: :func:`dtk.api.routes.support.resolve_request_identity`.
    identity_id: str | None = None
    #: Ignore whatever is in the response cache and go upstream. The fresh
    #: answer is still WRITTEN back, because "do not read the cache" and "do not
    #: keep this" are different requests and only the first one was asked for.
    refresh: bool = False
    #: Return the request as it went out - signed URL, headers and jar - so the
    #: caller can reproduce it outside this instance. Requires `identity:manage`
    #: and is audited, because the answer contains a credential. Implies
    #: `refresh`: a cached answer was signed by some earlier call with some
    #: other identity, and explaining THAT request while returning THIS payload
    #: would be a lie in the most confusing possible place.
    explain: bool = False
    #: Filled in by the service when :attr:`explain` is on, on the way past.
    #:
    #: An out-parameter, and deliberately so. A successful fetch hands its
    #: explanation back on :class:`FetchResult`; a refused one raises, and the
    #: one place this must never travel is inside the error - errors are
    #: serialized into the task row and rendered to any reader, while an
    #: explanation contains a cookie jar and is gated on ``identity:manage``.
    #: Writing it onto the context the caller already owns keeps it out of the
    #: exception and delivers it to exactly the caller that asked.
    #:
    #: "The request was refused, show me what we sent" is the case this whole
    #: feature exists for, so it had better survive the refusal.
    explained: Explanation | None = None


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
    explain: Explanation | None = None


class SignedRequest(Protocol):
    """What the injected signer hands back.

    Structural, so the pipeline still knows nothing about ``dtk.signing`` - but
    it has to name everything the signature consists of, and for a long time it
    named only half. Declaring just ``params`` and ``signer`` made the two
    missing pieces invisible at this seam, and the code below duly dropped
    both: the exact query string, and the headers. Every platform read through
    the API failed for it while the same call through ``dtk fetch`` - which
    uses the full object - succeeded.

    ``query`` is authoritative and ``params`` is not. The parameters are for
    logging and assertions; the query is the bytes that were signed, and
    re-encoding them changes the signature.
    """

    @property
    def query(self) -> str: ...

    @property
    def params(self) -> Mapping[str, str]: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def signer(self) -> str: ...

    def signed_url(self, base_url: str) -> str: ...


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
            lease = await self._scheduler.acquire(endpoint, platform, identity_id=ctx.identity_id)
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
        explanation: Explanation | None = None
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

            # The identity's own browser values, not a constant. Douyin and
            # TikTok both echo the screen, the language, the OS and the browser
            # version back in the query string, right beside a User-Agent
            # carrying the same facts - so a profile that does not follow the
            # fingerprint hands them a contradiction for free. It did: every
            # request claimed Chrome 130 on a zh-CN Windows box whatever the
            # identity actually was.
            spec = adapter.build_request(
                endpoint, profile=adapter.profile_for(identity.fingerprint), **params
            )
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

            outbound = _to_transport_spec(spec, signed, endpoint)
            if ctx.explain:
                # Built here rather than from the response, because this is the
                # only point where the signed request and the jar that signed it
                # are both in scope. The transport is free to retry on another
                # identity; what is described is the attempt that was made.
                explanation = Explanation(
                    method=outbound.method,
                    url=_full_url(outbound),
                    headers=_outbound_headers(identity.fingerprint, outbound.headers),
                    cookie_header=_cookie_header(identity.cookies),
                    identity_id=str(lease.identity_id),
                    signer=signer,
                    endpoint=endpoint,
                    proxy=mask_url(identity.proxy_url),
                )

            response = await self._transport.request(
                sender,
                outbound,
                self._timeout,
            )
            status = response.status
            classification, payload = self._classify(adapter, endpoint, response)
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
            explain=explanation,
        )

    def _classify(
        self, adapter: PlatformAdapter, endpoint: str, response: RawResponse
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
        spec = adapter.endpoints.specs.get(endpoint)
        classification = self._transport.classify(
            response,
            # The endpoint speaking about its own silence. Douyin answers a
            # private likes list with zero bytes, and without this every such
            # lookup - most of them, because most likes lists are private -
            # cooled the identity that asked.
            empty_body_is_normal=bool(spec is not None and spec.empty_body_is_normal),
        )
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
        digest = cache.cache_key(
            endpoint, params, include_raw=ctx.include_raw, egress=ctx.request_proxy
        )

        # A pinned request neither reads nor writes the shared cache. It is
        # asking what one particular session can see, and the answer is
        # frequently something no other caller is entitled to: a post visible
        # only to the account whose jar this is. Keying the entry by identity
        # would keep it correct, but it would still put private content in a
        # cache several other code paths can reach, for a saving that does not
        # exist - pinning is a deliberate, low-volume act.
        cacheable = cache_ttl > 0 and ctx.identity_id is None
        # `explain` reads the cache the way `refresh` does - not at all. An
        # explanation is a description of the call that produced the answer
        # being returned; served from cache there is no such call, and
        # describing the one that filled the cache hours ago, on some other
        # identity, would be wrong in the one place a reader is trusting it.
        hit = await cache.get(digest) if cacheable and not (ctx.refresh or ctx.explain) else None
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

        # Retrying is only worth anything because the next attempt lands on a
        # different identity behind a different exit. Pinned, it lands on the
        # same one, so the three attempts would spend three tokens out of a
        # bucket that holds three to five - turning one flaky call into an
        # identity with no quota left.
        attempts = 1 if ctx.identity_id else MAX_TRANSPORT_ATTEMPTS

        last_error: str | None = None
        for attempt in range(attempts):
            call = await self._call_once(session, adapter, endpoint, params, ctx, started=started)
            duration_ms = _elapsed_ms(started)
            # Before any raise below, and overwritten by each attempt: what is
            # described is the last attempt made, which is the one whose failure
            # the caller is holding.
            if call.explain is not None:
                ctx.explained = call.explain

            if call.outcome is Outcome.NETWORK_ERROR:
                last_error = call.error_code or "network_error"
                if attempt + 1 < attempts:
                    continue  # a different identity, i.e. a different egress
                raise Internal(
                    f"upstream unreachable after {attempts} attempts",
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
            if cacheable:
                await cache.put(digest, result, cache_ttl)

            return FetchResult(
                payload=result,
                outcome=call.outcome,
                identity_id=call.lease.identity_id,
                cached=False,
                duration_ms=duration_ms,
                request_id=ctx.request_id,
                signer=call.signer,
                explain=call.explain,
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

        Being the only writer is also what makes the demo suppression one line:
        there is nowhere else a request row can come from.
        """
        if ctx.is_demo:
            return
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


def _full_url(spec: TransportRequestSpec) -> str:
    """The URL as it goes on the wire, query included.

    The signers hand back a ready-made query string when the signature is over
    exact bytes, and a parameter mapping when it is not; both shapes reach the
    transport and both have to reach `curl` the same way round.
    """
    if spec.params:
        return f"{spec.url}?{urlencode(spec.params)}" if "?" not in spec.url else spec.url
    return spec.url


def _outbound_headers(fingerprint: Fingerprint, extra: Mapping[str, str] | None) -> dict[str, str]:
    """The headers the transport is about to send, not just the signer's.

    The signer contributes a handful; the User-Agent, the language and the
    client hints come from the identity's fingerprint and are added a few lines
    below this by the transport itself. Reporting only the signer's half would
    produce a curl that is missing the one header both platforms hash into the
    signature - which would fail, and look like the signature was wrong.

    A fingerprint with no User-Agent makes the transport raise in a moment
    anyway; explaining a request must not be the thing that raises first.
    """
    try:
        return build_headers(fingerprint, extra)
    except UnsupportedFingerprint:
        return dict(extra or {})


def _cookie_header(cookies: Mapping[str, str] | None) -> str:
    """One ``Cookie:`` header from a jar, in the order the jar holds them."""
    return "; ".join(f"{name}={value}" for name, value in (cookies or {}).items())


def _to_transport_spec(
    spec: PlatformRequestSpec | Mapping[str, Any],
    signed: SignedRequest,
    endpoint: str,
) -> TransportRequestSpec:
    """Bridge the platform's request description onto the transport's.

    Two deliberately different shapes meet here. ``dtk.platforms`` emits a plain
    TypedDict so platform packages stay free of transport types, while the
    transport takes a dataclass that also carries the logical endpoint name for
    scheduling and log correlation. Converting at this seam is the orchestration
    layer's job; letting either side learn the other's type is what turns two
    independent modules into one tangled one.

    Two things about the signature have to survive this conversion, and neither
    did until 2026-09-09 - which is why every platform read through the API
    failed while the identical call through ``dtk fetch`` succeeded.

    **The query goes byte for byte.** ``SignedRequest.query`` says so outright
    and adds that ``params`` is "for logging and assertions only"; this function
    rebuilt the query from ``params`` and let the transport re-encode it. That
    turns the ``/`` inside a base64 ``X-Gnarly`` into ``%2F`` - 30 bytes of
    difference on a TikTok detail call - and the signature is computed over the
    exact string, so the platform answered 200 with an empty payload, which
    classifies as risk control.

    **The signed headers come too.** Douyin's web signature is three headers -
    ``uifid``, ``x-secsdk-web-expire``, ``x-secsdk-web-signature`` - and they
    were dropped on the floor here, so every Douyin request arrived with a
    signature in the query and none in the headers, and was answered 403.
    """
    body = spec.get("body")
    return TransportRequestSpec(
        # The signed URL, so the query is exactly what was signed. `params` is
        # left None deliberately: handing the transport both would let it
        # re-encode and append them a second time.
        url=signed.signed_url(spec["url"]) if signed.query else spec["url"],
        method=str(spec.get("method") or "GET"),
        params=None if signed.query else {k: str(v) for k, v in (spec.get("params") or {}).items()},
        headers={**dict(spec.get("headers") or {}), **dict(signed.headers)},
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


def strip_raw(value: Any) -> Any:
    """Remove every ``raw`` key, at any depth.

    ``model_dump(exclude={"raw"})`` drops only the TOP-LEVEL field, which is not
    what a page is shaped like. Every list item carries its own ``raw`` - the
    parsers fill it unconditionally - so a page of twenty comments kept twenty
    untouched platform payloads in ``tasks.result`` and in the Redis response
    cache for ``retention.task_result_hours``, for a caller who asked not to
    receive them.

    Only the ``include_raw=False`` path comes here. ``include_raw=True`` still
    returns everything, per-item payloads included: some callers read fields the
    normalized model does not carry, and doc 11's promise to them is the reason
    ``raw`` exists at all.
    """
    if isinstance(value, dict):
        return {key: strip_raw(item) for key, item in value.items() if key != "raw"}
    if isinstance(value, list):
        return [strip_raw(item) for item in value]
    return value


def _dump(parsed: Any, *, include_raw: bool) -> dict[str, Any]:
    if hasattr(parsed, "model_dump"):
        dumped = parsed.model_dump(mode="json")
        return dumped if include_raw else cast("dict[str, Any]", strip_raw(dumped))
    if isinstance(parsed, dict):
        return parsed if include_raw else cast("dict[str, Any]", strip_raw(parsed))
    return {"value": parsed}


__all__ = [
    "MAX_TRANSPORT_ATTEMPTS",
    "FetchContext",
    "FetchResult",
    "FetchService",
    "SignedRequest",
    "UpstreamChanged",
    "strip_raw",
]
