"""Transport contracts.

Everything above this layer talks to the platform through `Transport`; nothing
above it imports wreq. That boundary exists so the fingerprint rules of
docs/design/04-transport-signing.md are enforced in exactly one place instead of
being re-derived by every caller.

This module deliberately does not import `dtk.identity`. The transport only
needs the shape declared by `IdentityLike` - id, platform, cookies, proxy and
fingerprint - and depending on the concrete pool entity here would make the pool
impossible to test without a network stack, and would create an import cycle the
moment the pool wants to evict a client.

See docs/design/04-transport-signing.md and docs/design/02-identity-pool.md.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from dtk.core.errors import DtkError, ErrorCode
from dtk.core.types import BrowserFamily, Platform

if TYPE_CHECKING:
    # The ruleset lives in `dtk.transport.classify`, which imports this module;
    # naming its result type in the `Transport` protocol is what keeps every
    # caller - the fetch pipeline included - on one classifier instead of
    # growing a second, cruder one of its own.
    from dtk.transport.classify import Classification

#: Fallback charset when the response carries no usable `content-type`.
DEFAULT_CHARSET = "utf-8"

#: Requests never wait longer than this, whatever the caller asks for. A stuck
#: proxy must not pin an identity forever: the identity is serial, so one hung
#: request removes it from the pool for as long as it hangs.
MAX_TIMEOUT_SECONDS = 120.0

#: Stand-in for a proxy URL whose shape could not be parsed. Better to lose the
#: host than to guess wrong about where the credentials end.
REDACTED_PROXY = "[redacted]"


def mask_proxy_url(proxy_url: str | None) -> str | None:
    """Strip the userinfo out of a proxy URL, keeping scheme, host and port.

    Proxy credentials are as valuable as the cookies (docs/design/08-security.md)
    and must never reach a log line, a repr or an exception message. The host is
    kept because "which exit failed" is the whole diagnostic value.
    """
    if not proxy_url:
        return proxy_url
    scheme, separator, rest = proxy_url.partition("://")
    if not separator:
        return REDACTED_PROXY
    host = rest.rpartition("@")[2]
    return f"{scheme}://{host}" if host else f"{scheme}://{REDACTED_PROXY}"


def scrub_url(url: str) -> str:
    """Drop the query string, which carries `msToken`, `a_bogus` and friends.

    Used for values that leave the process: `DtkError.details` is serialized
    verbatim into the public API error envelope (`dtk.api.envelope`), so a raw
    upstream URL there would hand a caller live signature parameters.
    """
    return url.partition("?")[0]


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What an identity claims to be, across every observable layer.

    The three layers - TLS profile, HTTP headers and cookies - have to agree.
    A fingerprint with no browser family or major cannot be emulated safely, so
    those two fields being None is the signal to refuse the identity rather than
    to fall back to a default profile (docs/design/02-identity-pool.md).
    """

    browser_family: BrowserFamily | None = None
    browser_major: int | None = None
    user_agent: str | None = None
    #: `navigator.platform`, e.g. "Win32", "MacIntel", "Linux x86_64".
    platform: str | None = None
    #: Screen geometry as reported by the minting browser, e.g. "1920x1080".
    screen: str | None = None
    #: BCP-47 tag or a full Accept-Language value, e.g. "en-US" or "de-DE,de;q=0.9".
    language: str | None = None
    #: IANA zone aligned with the proxy exit, e.g. "Europe/Berlin".
    timezone: str | None = None
    #: ``navigator.hardwareConcurrency``: logical cores the page was told about.
    #: Douyin echoes it back as ``cpu_core_num``.
    hardware_concurrency: int | None = None
    #: ``navigator.deviceMemory``, in GiB. Chromium only, and deliberately
    #: coarse - the spec caps it and rounds to a power of two, so 8 on a 64GiB
    #: machine is the correct answer rather than a wrong one. Absent on Firefox
    #: and Safari, which is why it is optional rather than defaulted: a value
    #: invented for a browser that does not report one is a contradiction.
    device_memory: int | None = None

    @property
    def emulatable(self) -> bool:
        """Whether a TLS profile can be chosen for this fingerprint at all."""
        return self.browser_family is not None and self.browser_major is not None


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """One upstream call, fully described and free of identity concerns.

    Built by `dtk.platforms`; the transport adds identity, cookies, proxy and
    fingerprint headers on top. `endpoint` is the logical endpoint name used for
    scheduling, circuit breaking and log correlation - never the raw URL, which
    carries signatures and tokens.
    """

    url: str
    method: str = "GET"
    params: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = None
    body: bytes | None = None
    json_body: Any | None = None
    endpoint: str | None = None


@dataclass(frozen=True, slots=True)
class RawResponse:
    """An upstream response, decoupled from the HTTP client that produced it.

    Header keys are lowercased on construction, so lookups never have to guess
    the casing the platform used. That is enforced here rather than left to the
    producer: a response replayed from a cache or built in a fixture with
    `Content-Type` would otherwise report no charset and decode as UTF-8, and
    the mistake would surface as mojibake far from its cause.
    """

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    final_url: str = ""
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if any(name != name.lower() for name in self.headers):
            lowered = {name.lower(): value for name, value in self.headers.items()}
            object.__setattr__(self, "headers", lowered)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def charset(self) -> str:
        content_type = self.header("content-type") or ""
        for part in content_type.split(";"):
            key, _, value = part.partition("=")
            if key.strip().lower() == "charset" and value.strip():
                return value.strip().strip('"')
        return DEFAULT_CHARSET

    @property
    def text(self) -> str:
        try:
            return self.body.decode(self.charset, errors="replace")
        except LookupError:
            # The platform named an encoding this build of Python does not know.
            return self.body.decode(DEFAULT_CHARSET, errors="replace")

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json(self) -> Any:
        """Parse the body as JSON. Raises `ValueError` when it is not JSON."""
        return json.loads(self.body)

    def json_or_none(self) -> Any | None:
        """Parse the body as JSON, or None when it is not JSON at all.

        Used by the classifier, which has to make a decision about bodies that
        are captcha HTML just as often as about well-formed envelopes.

        Falls back to the declared charset: `json.loads` on raw bytes assumes
        UTF-8, so a GBK envelope would look like "not JSON at all" and its
        status code would go unread.
        """
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            pass
        if self.charset.lower().replace("_", "-") in ("utf-8", "utf8"):
            return None
        try:
            return json.loads(self.text)
        except ValueError:
            return None


class TransportFailure(DtkError):
    """The request never produced a response: DNS, TLS, proxy or timeout.

    Distinct from an error *response*, which is returned as a `RawResponse` and
    classified. Callers catch this and feed it to `classify` to get
    `Outcome.NETWORK_ERROR`, which is what triggers the proxy health probe in
    docs/design/02-identity-pool.md.

    It carries `ErrorCode.INTERNAL` because the failure is in our own egress
    path, not in the caller's request; services translate it once every identity
    has been tried.

    `details` is what `dtk.api.envelope` serializes into the public error body,
    so the URL is stored there without its query string. The full URL stays on
    `.url` for internal logging, where the log processor masks signatures.
    """

    code = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        identity_id: str,
        url: str,
        elapsed_ms: int,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "identity_id": identity_id,
                "url": scrub_url(url),
                "elapsed_ms": elapsed_ms,
            },
        )
        self.identity_id = identity_id
        self.url = url
        self.elapsed_ms = elapsed_ms
        self.cause = cause


@runtime_checkable
class IdentityLike(Protocol):
    """The slice of an identity the transport reads.

    `proxy_url` is fixed for the identity's lifetime: changing the exit IP under
    a live cookie set is the single most reliable way to get an identity flagged
    (docs/design/02-identity-pool.md). The transport treats a changed proxy as a
    programming error and rebuilds the client rather than silently reusing a
    connection pool bound to the old exit.
    """

    @property
    def id(self) -> str: ...

    @property
    def platform(self) -> Platform: ...

    @property
    def fingerprint(self) -> Fingerprint: ...

    @property
    def proxy_url(self) -> str | None: ...

    @property
    def cookies(self) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True, repr=False)
class TransportIdentity:
    """Concrete `IdentityLike` for callers that hold decrypted identity data.

    The pool decrypts its cookie jar and hands one of these to the transport, so
    the plaintext lives only for the duration of the request.

    The generated repr is suppressed: docs/design/08-security.md requires that
    an identity appearing in an exception stack cannot print its cookies, and a
    proxy URL carries account credentials in its userinfo. Cookie *names* are
    kept because "which cookies did this identity actually have" is the first
    question of every debugging session and the names are not secret.
    """

    id: str
    platform: Platform
    fingerprint: Fingerprint
    proxy_url: str | None = None
    cookies: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        names = ",".join(sorted(self.cookies))
        return (
            f"TransportIdentity(id={self.id!r}, platform={self.platform.value!r}, "
            f"browser={self.fingerprint.browser_family}/{self.fingerprint.browser_major}, "
            f"proxy_url={mask_proxy_url(self.proxy_url)!r}, "
            f"cookies=<{len(self.cookies)}: {names}>)"
        )


class CookieSink(Protocol):
    """Receives cookies the platform set during a request.

    The transport does not persist anything itself: it has no database session
    and no encryption key. Rotating `ttwid` or `msToken` values have to reach the
    identity record or the next request replays a stale jar.
    """

    async def __call__(self, identity_id: str, cookies: Mapping[str, str]) -> None: ...


class Transport(Protocol):
    """The thin interface the rest of the system sees."""

    def classify(
        self,
        response: RawResponse | None = None,
        exception: BaseException | None = None,
        *,
        empty_body_is_normal: bool = False,
    ) -> Classification: ...

    async def request(
        self,
        identity: IdentityLike,
        spec: RequestSpec,
        timeout: float | None = None,
    ) -> RawResponse: ...

    async def evict(self, identity_id: str) -> None: ...

    async def close(self) -> None: ...


__all__ = [
    "DEFAULT_CHARSET",
    "MAX_TIMEOUT_SECONDS",
    "REDACTED_PROXY",
    "CookieSink",
    "Fingerprint",
    "IdentityLike",
    "RawResponse",
    "RequestSpec",
    "Transport",
    "TransportFailure",
    "TransportIdentity",
    "mask_proxy_url",
    "scrub_url",
]
