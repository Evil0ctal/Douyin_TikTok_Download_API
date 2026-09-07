"""Connectivity probes shared by the CLI commands and the diagnostics report.

Two questions come up in every "I deployed it but I get no data" report, and
each is one probe here: does this proxy carry traffic and where does it come
out, and does this identity still get a real answer. ``dtk proxy test``,
``dtk identity test`` and the smoke step of ``dtk diagnose`` are three
presentations of the same two functions.

The wider six-step diagnosis lives in :mod:`dtk.ops.diagnose`, which the console
shares; these are the pieces the CLI needs on their own, plus the exit-address
lookup that ``dtk proxy test`` writes back onto the proxy row.

Every probe returns a result object instead of raising: a diagnosis has to keep
going after a failed step, and the failure itself is the interesting output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from dtk.cli.masking import scrub
from dtk.core.logging import get_logger
from dtk.core.types import Outcome, Platform
from dtk.identity.pool import LiveIdentity

log = get_logger(__name__)

#: Reports the caller's exit address, country and timezone in one JSON body,
#: which is exactly the trio doc 15 asks a proxy check to show. Overridable per
#: invocation for a deployment that cannot reach it.
DEFAULT_PROXY_PROBE_URL: Final = "https://ipinfo.io/json"

#: Fixed public links for the end-to-end smoke test. They are only ever used to
#: prove that an endpoint still answers: a deleted post produces a business
#: error, which is a pass for this purpose and reported as such.
SMOKE_URLS: Final[dict[Platform, str]] = {
    Platform.DOUYIN: "https://www.douyin.com/video/7298145681699622182",
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
    from dtk.cli.pipeline import call_endpoint
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


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "DEFAULT_PROXY_PROBE_URL",
    "PROBE_TIMEOUT_SECONDS",
    "SMOKE_URLS",
    "IdentityProbe",
    "ProxyProbe",
    "probe_identity",
    "probe_proxy",
]
