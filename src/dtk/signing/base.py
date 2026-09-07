"""Signing contracts shared by every signer implementation and by transport.

The ``Signer`` protocol is deliberately tiny: a request goes in, the extra query
parameters and headers that make it acceptable to the platform come out. Two
implementations exist (see docs/design/04-transport-signing.md):

* ``NativeSigner`` - pure Python port of the platform algorithms, microseconds.
* ``RpcSigner``    - browser-rpc executes the site's own JavaScript, hundreds of
  milliseconds, but follows the platform automatically when it changes.

``SignedParams.query`` is the load-bearing field. Signatures are computed over an
exact byte sequence, so the caller must send that string verbatim instead of
re-encoding ``params``: a single extra percent-escape invalidates the signature.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from dtk.core.types import Platform

#: Value of ``request_log.signer`` for the pure Python path.
SIGNER_NATIVE = "native"
#: Value of ``request_log.signer`` for the browser-rpc path.
SIGNER_BROWSER = "browser"

_EMPTY: Mapping[str, str] = MappingProxyType({})


class SignatureAlgorithm(StrEnum):
    """Signature schemes the project knows about.

    Values double as the query parameter name the signature is sent under, which
    is why they are spelled exactly as the platform spells them.
    """

    A_BOGUS = "a_bogus"
    X_BOGUS = "X-Bogus"
    SIGNATURE = "_signature"


#: Query parameter carrying the session token on both platforms.
MS_TOKEN_PARAM = "msToken"


#: Algorithm each platform's Web API expects by default. Douyin moved to
#: A-Bogus; TikTok Web still takes X-Bogus. Every signer resolves the algorithm
#: through this map so the native and browser paths never disagree about which
#: one - and therefore which query encoding - a request needs.
DEFAULT_ALGORITHMS: Mapping[Platform, SignatureAlgorithm] = MappingProxyType(
    {
        Platform.DOUYIN: SignatureAlgorithm.A_BOGUS,
        Platform.TIKTOK: SignatureAlgorithm.X_BOGUS,
    }
)


def encode_query(params: Mapping[str, str], algorithm: SignatureAlgorithm) -> str:
    """The exact byte sequence signed for ``algorithm``, and sent on the wire.

    A-Bogus is computed over the form-encoded query; X-Bogus over the raw
    ``key=value`` join, which is what V4 sent in production and what the platform
    accepts (a base64 ``msToken`` ends in ``==``, and percent-escaping it changes
    the signed bytes).

    Both signers must go through this function. When they encode differently they
    sign different bytes for the same request, which sends a wrong signature on
    the fallback path and makes the shadow comparison report a mismatch for a
    reason that has nothing to do with the algorithm.
    """
    if algorithm is SignatureAlgorithm.X_BOGUS:
        return "&".join(f"{key}={value}" for key, value in params.items())
    return urlencode(params)


#: Host suffix to platform. Used to infer the platform when a caller does not
#: pass one explicitly; unknown hosts stay unknown rather than defaulting.
HOST_PLATFORMS: Mapping[str, Platform] = MappingProxyType(
    {
        "douyin.com": Platform.DOUYIN,
        "iesdouyin.com": Platform.DOUYIN,
        "amemv.com": Platform.DOUYIN,
        "tiktok.com": Platform.TIKTOK,
        "tiktokv.com": Platform.TIKTOK,
    }
)


def platform_of(url: str) -> Platform | None:
    """Infer the platform from a request URL, or None when the host is unknown."""
    host = urlsplit(url).hostname
    if not host:
        return None
    host = host.lower()
    for suffix, platform in HOST_PLATFORMS.items():
        if host == suffix or host.endswith("." + suffix):
            return platform
    return None


def endpoint_of(url: str) -> str:
    """The scheduler-visible endpoint key: the URL path, without query."""
    return urlsplit(url).path or "/"


def _freeze(mapping: Mapping[str, str] | None) -> Mapping[str, str]:
    if not mapping:
        return _EMPTY
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """One outbound platform request, before signing.

    ``params`` holds the business query parameters in the order the platform
    expects them; signing appends to that order, never reorders it.
    """

    method: str
    url: str
    params: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = None
    body: bytes | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", self.method.upper())
        object.__setattr__(self, "params", _freeze(self.params))
        object.__setattr__(self, "headers", _freeze(self.headers))

    @classmethod
    def get(
        cls,
        url: str,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> RequestSpec:
        """Convenience constructor: every signed platform call today is a GET."""
        return cls(method="GET", url=url, params=params, headers=headers)

    @property
    def platform(self) -> Platform | None:
        return platform_of(self.url)

    @property
    def endpoint(self) -> str:
        return endpoint_of(self.url)

    def with_params(self, params: Mapping[str, str]) -> RequestSpec:
        """Return a copy carrying ``params``; the original is never mutated."""
        return RequestSpec(
            method=self.method,
            url=self.url,
            params=params,
            headers=self.headers,
            body=self.body,
        )


@dataclass(frozen=True, slots=True)
class SignedParams:
    """Everything signing adds to a request.

    Attributes:
        query: The complete query string to send, signature included. Send it
            byte for byte; ``params`` is for logging and assertions only.
        params: The signature parameters that were added, unencoded.
        headers: Extra headers the signature requires (empty on the web APIs).
        signer: ``native`` or ``browser``; goes straight into ``request_log``.
        algorithm: Which scheme produced the signature.
    """

    query: str
    params: Mapping[str, str] = _EMPTY
    headers: Mapping[str, str] = _EMPTY
    signer: str = SIGNER_NATIVE
    algorithm: SignatureAlgorithm = SignatureAlgorithm.A_BOGUS

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", _freeze(self.params))
        object.__setattr__(self, "headers", _freeze(self.headers))

    def signed_url(self, base_url: str) -> str:
        """``base_url`` with the signed query attached, ready for transport."""
        head = base_url.split("?", 1)[0]
        return f"{head}?{self.query}" if self.query else head


@dataclass(frozen=True, slots=True)
class SignerHealth:
    """Result of a signer health probe.

    ``NativeSigner`` is always healthy - it is a pure function. ``RpcSigner``
    reports what browser-rpc says about its warm contexts so the console can show
    why a fallback did or did not happen.
    """

    signer: str
    healthy: bool
    detail: str | None = None
    latency_ms: float | None = None
    warm_contexts: int | None = None
    backend_version: str | None = None
    uptime_seconds: float | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class SigningFingerprint(Protocol):
    """The slice of ``Identity.fingerprint`` that signing depends on.

    Structural on purpose: ``identity`` owns the real ``Fingerprint`` model, and
    signing must not import it. Anything exposing these attributes will do.
    """

    @property
    def user_agent(self) -> str: ...

    @property
    def browser_platform(self) -> str | None: ...

    @property
    def screen_width(self) -> int | None: ...

    @property
    def screen_height(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class StaticFingerprint:
    """Minimal concrete ``SigningFingerprint`` for tests and for callers that
    only have a User-Agent to hand."""

    user_agent: str
    browser_platform: str | None = None
    screen_width: int | None = None
    screen_height: int | None = None


class Signer(Protocol):
    """Turns an unsigned request into the parameters the platform accepts."""

    #: ``native`` or ``browser``; recorded in ``request_log.signer``.
    name: str

    async def sign(
        self, spec: RequestSpec, identity_fingerprint: SigningFingerprint
    ) -> SignedParams: ...

    async def health(self) -> SignerHealth: ...


__all__ = [
    "DEFAULT_ALGORITHMS",
    "HOST_PLATFORMS",
    "MS_TOKEN_PARAM",
    "SIGNER_BROWSER",
    "SIGNER_NATIVE",
    "RequestSpec",
    "SignatureAlgorithm",
    "SignedParams",
    "Signer",
    "SignerHealth",
    "SigningFingerprint",
    "StaticFingerprint",
    "encode_query",
    "endpoint_of",
    "platform_of",
]
