"""Connectivity probes shared by the CLI, the console and the diagnostics report.

Two questions come up in every "I deployed it but I get no data" report, and
each is one probe here: does this proxy carry traffic and where does it come
out, and does this identity still get a real answer. ``dtk proxy test``,
``dtk identity test``, the console's per-row probe buttons and the smoke step of
``dtk diagnose`` are presentations of the same two functions.

The wider six-step diagnosis lives in :mod:`dtk.ops.diagnose`; these are the
pieces a caller needs on their own, plus the exit-address lookup that
``dtk proxy test`` writes back onto the proxy row.

Every probe returns a result object instead of raising: a diagnosis has to keep
going after a failed step, and the failure itself is the interesting output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from dtk.core.logging import get_logger
from dtk.core.types import Outcome, Platform
from dtk.identity.pool import LiveIdentity
from dtk.ops.masking import scrub

log = get_logger(__name__)

#: Reports the caller's exit address, country and timezone in one JSON body,
#: which is exactly the trio doc 15 asks a proxy check to show. Overridable per
#: invocation for a deployment that cannot reach it.
DEFAULT_PROXY_PROBE_URL: Final = "https://ipinfo.io/json"

#: Fixed public links for the end-to-end smoke test. They are only ever used to
#: prove that an endpoint still answers: a post that has been deleted or made
#: private produces a business error, which is a pass for this purpose and
#: reported as such.
#:
#: That tolerance is not theoretical. The Douyin link here was a post its owner
#: had since restricted, and until 2026-09-08 the classifier read the resulting
#: empty payload as risk control - so every identity probe and every diagnosis
#: reported the pool as blocked, on a healthy pool, for months. The rule that
#: separates "the platform explained why" from "the platform said nothing" is in
#: `dtk.transport.classify`, and it is what keeps a link rotting here from
#: looking like an outage. Replacing the link is still worth doing when it rots;
#: it is no longer urgent when it does.
SMOKE_URLS: Final[dict[Platform, str]] = {
    Platform.DOUYIN: "https://www.douyin.com/video/7675505205047348543",
    Platform.TIKTOK: "https://www.tiktok.com/@owlcitymusic/video/7218694761253735723",
}

PROBE_TIMEOUT_SECONDS: Final = 10.0


@dataclass(frozen=True, slots=True)
class ProxyProbe:
    """What one proxy check learned. ``ok`` is the only field callers must read."""

    ok: bool
    latency_ms: int | None = None
    exit_ip: str | None = None
    country: str | None = None
    timezone: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityProbe:
    """The result of one real, signed request made as one identity."""

    ok: bool
    outcome: Outcome | None = None
    status: int | None = None
    latency_ms: int | None = None
    rule: str | None = None
    detail: str | None = None


async def probe_proxy(
    proxy_url: str,
    *,
    probe_url: str = DEFAULT_PROXY_PROBE_URL,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> ProxyProbe:
    """Check that a proxy carries traffic, and report where it comes out.

    The exit address decides the locale a minted identity should claim, so a
    proxy whose country has changed is worth seeing even when it still works.
    """
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
            response = await client.get(probe_url)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
    except json.JSONDecodeError as exc:
        return ProxyProbe(ok=False, detail=f"probe returned no JSON: {exc}")
    except Exception as exc:
        # A probe reports; it never raises. Not every failure is an HTTPError:
        # httpx needs an optional extra for SOCKS and reports a missing one as
        # ImportError, and an unusable proxy URL as a plain ValueError - both
        # for schemes dtk.cli.proxies accepts. Scrubbed because an exception
        # from a proxied client can quote the proxy URL, credentials included.
        return ProxyProbe(ok=False, detail=scrub(f"{type(exc).__name__}: {exc}"))

    if not isinstance(body, dict):
        # A JSON array decodes fine and then has no .get; a probe URL pointed at
        # the wrong service must be a failed probe, not an AttributeError.
        return ProxyProbe(ok=False, detail="probe returned no JSON object")

    return ProxyProbe(
        ok=True,
        latency_ms=latency_ms,
        exit_ip=_text(body.get("ip")),
        country=_text(body.get("country")),
        timezone=_text(body.get("timezone")),
    )


async def probe_identity(
    transport: Any,
    signers: Any,
    identity: LiveIdentity,
    call: Any,
    *,
    timeout: float = 25.0,
) -> IdentityProbe:
    """Issue one signed request as ``identity`` and classify the answer.

    ``call`` is a :class:`dtk.worker.registry.ResolvedCall`, so the probe uses
    the same endpoint table and the same parameter translation the worker does.

    A business error counts as a pass. The question is whether the platform
    still talks to this identity, and "that post is gone" is an answer.
    """
    from dtk.ops.pipeline import call_endpoint
    from dtk.transport.base import TransportFailure

    started = time.perf_counter()
    try:
        response = await call_endpoint(transport, signers, identity, call, timeout=timeout)
    except TransportFailure as exc:
        # The message quotes the request URL, and for a signed call that URL
        # carries msToken and a_bogus. Masked here, where the value is
        # collected, rather than at each of the places that print it.
        return IdentityProbe(
            ok=False,
            outcome=Outcome.NETWORK_ERROR,
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail=scrub(str(exc)),
        )

    classification = transport.classify(response)
    return IdentityProbe(
        ok=classification.outcome in (Outcome.OK, Outcome.BUSINESS_ERROR),
        outcome=classification.outcome,
        status=response.status,
        latency_ms=response.elapsed_ms,
        rule=classification.rule,
        detail=scrub(classification.detail) if classification.detail else None,
    )


#: The endpoint that answers "whose session is this", per platform.
SESSION_CHECKS: Final[dict[Platform, str]] = {
    Platform.DOUYIN: "douyin.session_check",
    Platform.TIKTOK: "tiktok.session_check",
}


@dataclass(frozen=True, slots=True)
class SessionCall:
    """The three fields :func:`dtk.ops.pipeline.call_endpoint` actually reads.

    Not a :class:`dtk.worker.registry.ResolvedCall`. That table is keyed by
    `Capability` and requires a non-empty argument map, and a session check has
    no arguments at all - the cookies are the whole question. Registering one
    would mean inventing a capability, a cache TTL key and a scope for an
    endpoint no caller may name, and putting it on the endpoint health board
    where an operator would read it as something the API serves.

    The platform's own endpoint table still holds the path, the parameters and
    whether it is signed, so this bypasses the worker's registry and nothing
    else.
    """

    platform: Platform
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionProbe:
    """Whether an identity's login is still a login.

    Separate from :class:`IdentityProbe` because it answers a different
    question and a caller must not be able to confuse them. A guest identity
    passes an identity probe - the platform talks to it perfectly well - and
    fails this one, which is the whole point: a jar imported for its login is
    worthless the day the login expires, and until now the only symptom was
    logged-in-only data quietly going missing.
    """

    #: True only where a platform actually confirmed a login. False covers a
    #: guest jar, a lapsed one, a platform that refused to answer and one that
    #: cannot tell - `reason` separates those and this flag does not.
    logged_in: bool
    #: The account the platform says the cookies belong to, when it says.
    account_id: str | None = None
    #: `live`, `signed_out`, `indeterminate`, `refused` or `unreachable`.
    reason: str = "unreachable"
    status: int | None = None
    latency_ms: int | None = None
    detail: str | None = None


def read_session(platform: Platform, payload: Any) -> tuple[bool, str | None, str, str | None]:
    """Read a session-check answer: (logged in, account id, reason, detail).

    Neither platform uses an HTTP status to say it - a live session and a dead
    one both come back 200 - and only one of them says it at all.

    **TikTok's ``/passport/token/beat/web/`` is a real check.** Measured in both
    directions on 2026-09-09: a logged-in jar answers ``error_code: 0`` with the
    account in ``user_id_str``; a guest jar answers ``error_code: 401``,
    ``error_name: "session_expired"``, ``user_id_str: "0"``. That last shape is
    also what a lapsed login returns, so this reports ``signed_out`` rather than
    guessing which of the two it was - the platform does not distinguish them
    and neither should this.

    **Douyin's ``/aweme/v1/web/query/user/`` is not.** It looks like one, and it
    was proposed as one, but it answers a guest jar with the same fields and a
    ``user_uid`` of its own: measured, a freshly minted guest got
    ``status_code: 0`` and a 16-digit uid where a logged-in browser gets a
    19-digit account uid. Nothing in the response separates them -
    ``user_uid_type`` is 0 for both - and ``/passport/token/beat/`` answers
    ``{"message": "success"}`` for both as well.

    So Douyin returns ``indeterminate`` with the uid it named. That is worth
    something - the jar reached the platform and the platform recognised the
    client - and it is not a login, and saying otherwise would send somebody to
    re-import a jar over a verdict this endpoint cannot give.
    """
    if not isinstance(payload, dict):
        return False, None, "refused", None

    if platform is Platform.DOUYIN:
        if int(payload.get("status_code") or 0) != 0:
            return False, None, "refused", None
        return False, _text(payload.get("user_uid")), "indeterminate", None

    data = payload.get("data")
    if not isinstance(data, dict):
        return False, None, "refused", None
    account = _text(data.get("user_id_str"))
    # "0" is how the passport service spells "nobody", and it is a string.
    if account == "0":
        account = None
    if int(data.get("error_code") or 0) != 0:
        return False, account, "signed_out", _text(data.get("error_name"))
    return (True, account, "live", None) if account else (False, None, "signed_out", None)


async def probe_session(
    transport: Any,
    signers: Any,
    identity: LiveIdentity,
    call: Any,
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> SessionProbe:
    """Ask the platform whose session this identity is carrying.

    Runs through the same pipeline as every other call, so it spends the
    identity exactly as real traffic would and a refusal here is a refusal
    everywhere. Nothing is recorded against the identity, for the same reason
    ``probe_identity`` records nothing: a check that cooled what it measured
    would move the state the operator is reading.
    """
    from dtk.ops.pipeline import call_endpoint
    from dtk.transport.base import TransportFailure

    started = time.perf_counter()
    try:
        response = await call_endpoint(transport, signers, identity, call, timeout=timeout)
    except TransportFailure as exc:
        return SessionProbe(
            logged_in=False,
            reason="unreachable",
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail=scrub(str(exc)),
        )

    classification = transport.classify(response)
    if classification.outcome is Outcome.RISK_CONTROL:
        # The platform said nothing at all, which is not the same as saying the
        # session is over. Reporting it as expired would send somebody to
        # re-import a jar that is fine.
        return SessionProbe(
            logged_in=False,
            reason="refused",
            status=response.status,
            latency_ms=response.elapsed_ms,
            detail=scrub(classification.detail) if classification.detail else None,
        )

    logged_in, account, reason, detail = read_session(identity.platform, response.json())
    return SessionProbe(
        logged_in=logged_in,
        account_id=account,
        reason=reason,
        status=response.status,
        latency_ms=response.elapsed_ms,
        detail=detail,
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "DEFAULT_PROXY_PROBE_URL",
    "PROBE_TIMEOUT_SECONDS",
    "SESSION_CHECKS",
    "SMOKE_URLS",
    "IdentityProbe",
    "ProxyProbe",
    "SessionCall",
    "SessionProbe",
    "probe_identity",
    "probe_proxy",
    "probe_session",
    "read_session",
]
