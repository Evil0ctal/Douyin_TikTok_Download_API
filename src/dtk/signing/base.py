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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
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
    #: Bilibili's. Computed from two public keys, so native is the only signer.
    WBI = "w_rid"


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
        Platform.BILIBILI: SignatureAlgorithm.WBI,
    }
)

#: Platforms browser-rpc can sign for. Its page knows Douyin's and TikTok's
#: SDKs and nobody else's; Bilibili's WBI needs no browser at all, so a
#: request for it must never be routed there, whatever `signing.mode` says.
RPC_PLATFORMS: frozenset[Platform] = frozenset({Platform.DOUYIN, Platform.TIKTOK})


def encode_query(params: Mapping[str, str], algorithm: SignatureAlgorithm) -> str:
    """The exact byte sequence signed for ``algorithm``, and sent on the wire.

    A-Bogus is computed over the form-encoded query; X-Bogus - TikTok - over
    the query as a browser would send it, :func:`encode_browser_query`.

    Both signers must go through this function. When they encode differently they
    sign different bytes for the same request, which sends a wrong signature on
    the fallback path and makes the shadow comparison report a mismatch for a
    reason that has nothing to do with the algorithm.

    That happened. TikTok's native signer moved to the browser's encoding when
    ``/api/user/detail/`` was found to check it, and this function kept the raw
    ``key=value`` join it had before - which only the browser signer still
    used. The page escaped ``browser_version=5.0 (Windows)`` to ``5.0%20...``
    before its SDK hashed it, the request went out with the space, and every
    endpoint that checks that hash answered with an empty body. Measured on
    2026-10-21 by decoding a browser-signed ``/api/post/item_list/``: both
    seals covered a query other than the one sent, and covered the sent one
    once this encoder was used.
    """
    if algorithm is SignatureAlgorithm.X_BOGUS:
        return encode_browser_query(params.items())
    return urlencode(params)


#: The only characters a browser escapes inside a query string. Chrome leaves
#: everything else - parentheses, slashes, colons, commas, ``=`` - literal, and
#: the `#` is here because it would otherwise start the fragment.
_BROWSER_MUST_ESCAPE: Mapping[str, str] = MappingProxyType(
    {" ": "%20", '"': "%22", "<": "%3C", ">": "%3E", "`": "%60", "#": "%23"}
)


def encode_browser_query(pairs: Iterable[tuple[str, str]]) -> str:
    """Serialize query parameters exactly as a browser would send them.

    This is NOT ``urlencode``, and the difference is the whole reason
    ``/api/user/detail/`` used to fail while its neighbours worked.

    TikTok's X-Dynosaur field 0x2E is a hash of the query, and the SDK hashes
    the string the browser handed it - which is the URL after the browser's own
    normalisation, not after a library's. Chrome escapes a space to ``%20`` and
    leaves parentheses, slashes and colons alone, so the real page hashes
    ``browser_version=5.0%20(Windows)&root_referer=https://www.tiktok.com/``.
    Percent-encoding everything produced ``5.0%20%28Windows%29`` and
    ``https%3A%2F%2F...`` - a different string, a different hash, and a
    signature bound to bytes the platform never sees.

    Recovered by capturing a browser-signed request, decoding its X-Dynosaur and
    searching for the input that reproduces 0x2E: only-escape-the-space matches
    byte for byte, and nothing else tried does. TikTok verifies that binding on
    ``/api/user/detail/`` and ``/api/post/item_list/`` and ignores it on
    ``/api/item/detail/`` and ``/api/comment/list/``, which is why the symptom
    is one endpoint silently returning nothing.

    The same string is hashed and sent, so this must stay the only encoder in
    the path.
    """
    return "&".join(f"{_browser_escape(key)}={_browser_escape(value)}" for key, value in pairs)


def _browser_escape(text: str) -> str:
    """Percent-escape only what a browser has to, leaving the rest literal.

    Printable ASCII passes through untouched apart from the handful in
    :data:`_BROWSER_MUST_ESCAPE`. Everything else - control characters and
    anything non-ASCII - is percent-encoded from its UTF-8 bytes, which is what
    a browser puts on the wire for a query it was handed as text.
    """
    out: list[str] = []
    for ch in text:
        if ch in _BROWSER_MUST_ESCAPE:
            out.append(_BROWSER_MUST_ESCAPE[ch])
        elif " " < ch <= "~":
            out.append(ch)
        else:
            out.extend(f"%{byte:02X}" for byte in ch.encode("utf-8"))
    return "".join(out)


#: Host suffix to platform. Used to infer the platform when a caller does not
#: pass one explicitly; unknown hosts stay unknown rather than defaulting.
HOST_PLATFORMS: Mapping[str, Platform] = MappingProxyType(
    {
        "douyin.com": Platform.DOUYIN,
        "iesdouyin.com": Platform.DOUYIN,
        "amemv.com": Platform.DOUYIN,
        "tiktok.com": Platform.TIKTOK,
        "tiktokv.com": Platform.TIKTOK,
        "bilibili.com": Platform.BILIBILI,
        "b23.tv": Platform.BILIBILI,
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
        """Convenience constructor for the common case, a GET with no body.

        Not every signed call is one: Douyin's ``listcollection`` is a form POST
        and a_bogus hashes its body, so the fetch pipeline builds the spec in
        full rather than through here.
        """
        return cls(method="GET", url=url, params=params, headers=headers)

    @classmethod
    def of(cls, request: Mapping[str, Any]) -> RequestSpec:
        """Everything a platform request description says that a signature covers.

        Takes the mapping ``dtk.platforms`` builds, read structurally so that
        signing stays free of platform types. The worker used to hand over the
        URL and the parameters alone - everything a GET has - so a POST's body
        could never have reached the signer that hashes it.
        """
        return cls(
            method=str(request.get("method") or "GET"),
            url=str(request["url"]),
            params={key: str(value) for key, value in (request.get("params") or {}).items()},
            headers=request.get("headers"),
            body=request.get("body"),
        )

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
        cookies: Cookies the signature was computed under, which the request
            must carry in place of the identity's own values for those names.
            Empty unless the signer worked in a session other than the jar's -
            see ``RpcSigner``, whose page seals its own ``msToken``.
    """

    query: str
    params: Mapping[str, str] = _EMPTY
    headers: Mapping[str, str] = _EMPTY
    signer: str = SIGNER_NATIVE
    algorithm: SignatureAlgorithm = SignatureAlgorithm.A_BOGUS
    cookies: Mapping[str, str] = _EMPTY

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", _freeze(self.params))
        object.__setattr__(self, "headers", _freeze(self.headers))
        object.__setattr__(self, "cookies", _freeze(self.cookies))

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

    @classmethod
    def of(cls, source: Any) -> StaticFingerprint:
        """The slice of an identity's fingerprint the signers need.

        Structural, like the protocol above: anything carrying ``user_agent``,
        ``platform`` and a ``"1920x1080"`` ``screen`` will do, so this stays
        free of the transport's own ``Fingerprint`` type.

        Worth having in one place. A-Bogus carries the screen geometry inside
        the signature, and a caller that passed only the User-Agent made every
        signature claim the fallback desktop whatever the identity was - which
        contradicts the ``screen_width`` the same request sends in its query.
        Two call sites did this and only one of them got it right.
        """
        width: int | None = None
        height: int | None = None
        screen = getattr(source, "screen", None)
        if isinstance(screen, str) and "x" in screen:
            raw_width, _, raw_height = screen.partition("x")
            width, height = _as_int(raw_width), _as_int(raw_height)
        return cls(
            user_agent=getattr(source, "user_agent", None) or "",
            browser_platform=getattr(source, "platform", None),
            screen_width=width,
            screen_height=height,
        )


def _as_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SigningSession:
    """The session a signature has to be coherent with.

    Separate from ``SigningFingerprint`` because it is a different kind of fact.
    A fingerprint describes the browser; this describes *which visitor* is
    speaking, and the platform checks the two against each other.

    Measured on 2026-09-08 against a live page: Douyin's ``verifyFp`` query
    parameter IS the ``s_v_web_id`` cookie of whatever browser computed the
    signature. Sign in one session and send the request with another session's
    cookies and the two contradict each other; Douyin and TikTok both answer by
    withholding the payload rather than by rejecting the request, so the failure
    surfaces as an empty body and reads exactly like rate limiting.

    ``NativeSigner`` computes from the parameters alone and ignores this;
    ``RpcSigner`` hands it to the browser, which is the whole point of it.
    """

    #: The jar the request will be sent with. Empty means an anonymous
    #: signature, which is only coherent for a caller that sends no cookies.
    cookies: Mapping[str, str] = _EMPTY
    #: The exit the request will leave through, so the signing page can present
    #: these cookies from the address they belong to.
    proxy_url: str | None = None
    #: For logs and slot affinity only. Coherence is decided by ``cookies``.
    identity_id: str | None = None


class Signer(Protocol):
    """Turns an unsigned request into the parameters the platform accepts."""

    #: ``native`` or ``browser``; recorded in ``request_log.signer``.
    name: str

    async def sign(
        self,
        spec: RequestSpec,
        identity_fingerprint: SigningFingerprint,
        session: SigningSession | None = None,
    ) -> SignedParams: ...

    async def health(self) -> SignerHealth: ...


__all__ = [
    "DEFAULT_ALGORITHMS",
    "HOST_PLATFORMS",
    "MS_TOKEN_PARAM",
    "RPC_PLATFORMS",
    "SIGNER_BROWSER",
    "SIGNER_NATIVE",
    "RequestSpec",
    "SignatureAlgorithm",
    "SignedParams",
    "Signer",
    "SignerHealth",
    "SigningFingerprint",
    "SigningSession",
    "StaticFingerprint",
    "encode_browser_query",
    "encode_query",
    "endpoint_of",
    "platform_of",
]
