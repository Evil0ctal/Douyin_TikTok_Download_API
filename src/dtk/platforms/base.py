"""Platform adapter protocol: endpoint tables, request building, response parsing.

A platform package is the only place that knows a platform exists. It declares
which endpoints there are, how a query string for each one is shaped, and how a
raw response maps onto the normalized models in :mod:`dtk.models`.

Three constraints make the acceptance test in ``docs/design/01-architecture.md``
hold - adding a platform must require only a new ``src/dtk/platforms/<name>/``
directory:

1. **No IO.** A platform package performs no HTTP calls, reads no configuration
   and touches no database. It builds request descriptions and parses payloads.
   Retries, signing, proxies, cookies, logging and caching all live above it.
2. **Parsers are pure functions.** ``dict`` in, model out. That is what makes the
   replay tests in ``tests/replay/`` possible at all; see
   ``docs/design/13-testing.md``.
3. **Fingerprint values are injected.** Platform query strings echo back browser
   properties (screen size, browser version, language). Those must agree with
   the TLS emulation and User-Agent chosen for the identity, so they arrive as a
   :class:`ClientProfile` rather than being hardcoded here.

The transport layer consumes a :class:`RequestSpec` - a plain dict, so nothing
in ``platforms/`` depends on the wreq wrapper. The signing layer appends its own
parameters (``msToken``, ``a_bogus``, ``X-Bogus``, ``_signature``) to
``params`` after this module is done; they are deliberately absent here because
generating them needs an identity and, for ``msToken``, a network round trip.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol, TypedDict, runtime_checkable

from dtk.core.errors import InvalidParam
from dtk.core.types import Platform
from dtk.models import Author, Comment, Content, Page

HttpMethod = Literal["GET", "POST"]

#: A builder turns caller-facing arguments into a flat query string mapping.
#: Values are left un-encoded: percent-encoding happens once, in the transport
#: layer, because the signature is computed over the encoded query string and
#: encoding twice was a recurring source of V4 signature failures.
ParamBuilder = Callable[..., dict[str, str]]


class RequestSpec(TypedDict):
    """Everything the transport needs to issue one upstream request.

    A plain ``TypedDict`` rather than a model: it crosses a module boundary that
    must stay free of platform-specific types.
    """

    method: HttpMethod
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    body: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ClientProfile:
    """Browser-shaped values a platform query string echoes back.

    Derived from the identity's fingerprint by the caller so the query string
    agrees with the TLS emulation profile and the User-Agent header. The
    defaults describe a plausible desktop Chrome on Windows and exist so tests
    and the CLI can build a request without assembling an identity first.
    """

    browser_name: str = "Chrome"
    browser_version: str = "130.0.0.0"
    browser_platform: str = "Win32"
    browser_language: str = "en-US"
    engine_name: str = "Blink"
    engine_version: str = "130.0.0.0"
    os_name: str = "Windows"
    os_version: str = "10"
    screen_width: int = 1920
    screen_height: int = 1080
    cpu_core_num: int = 12
    device_memory: int = 8
    language: str = "en"
    timezone: str = "America/Los_Angeles"
    region: str = "US"

    def with_fingerprint(self, source: ProfileSource) -> ClientProfile:
        """This profile, with every field the identity actually determines replaced.

        Only the platform-neutral half. ``navigator.platform``, the screen size,
        the language and the zone mean the same thing to both platforms and come
        straight off the fingerprint; the OS is derived from the User-Agent by
        :func:`operating_system`. What each platform *calls* a browser and how it
        spells its version differ, and are left to the platform's own
        ``profile_for``.

        A field the fingerprint cannot state keeps this profile's value. That is
        deliberate: a request whose query contradicts its own User-Agent is a
        free signal to hand the platform, and so is one carrying a value nobody
        can vouch for.
        """
        screen = screen_size(source.screen)
        operating = operating_system(source.user_agent)
        language = primary_language(source.language)
        return replace(
            self,
            browser_platform=source.platform or self.browser_platform,
            screen_width=screen[0] if screen else self.screen_width,
            screen_height=screen[1] if screen else self.screen_height,
            browser_language=language or self.browser_language,
            timezone=source.timezone or self.timezone,
            region=region_of(language) or self.region,
            os_name=operating[0] if operating else self.os_name,
            os_version=operating[1] if operating else self.os_version,
        )


@runtime_checkable
class ProfileSource(Protocol):
    """The slice of an identity's fingerprint a query string echoes back.

    Structural on purpose, like ``dtk.signing.base.SigningFingerprint``: the
    transport owns the real ``Fingerprint`` and ``platforms/`` must not import
    it. Anything exposing these attributes will do.
    """

    @property
    def user_agent(self) -> str | None: ...

    @property
    def platform(self) -> str | None: ...

    @property
    def screen(self) -> str | None: ...

    @property
    def language(self) -> str | None: ...

    @property
    def timezone(self) -> str | None: ...


def screen_size(screen: str | None) -> tuple[int, int] | None:
    """``"1920x1080"`` as a pair, or None when it is not that."""
    if not screen:
        return None
    width, _, height = screen.partition("x")
    try:
        return int(width), int(height)
    except ValueError:
        return None


def primary_language(language: str | None) -> str | None:
    """The first tag of an Accept-Language value: ``"en-US,en;q=0.9"`` -> ``"en-US"``.

    A fingerprint may hold either a bare tag or a whole header, because both are
    things a minting browser reports. The query string carries
    ``navigator.language``, which is the first one.
    """
    if not language:
        return None
    tag = language.split(",", 1)[0].partition(";")[0].strip()
    return tag or None


def base_language(language: str | None) -> str | None:
    """The primary subtag of a BCP-47 tag: ``"en-US"`` -> ``"en"``."""
    if not language:
        return None
    return language.partition("-")[0].strip() or None


def region_of(language: str | None) -> str | None:
    """The region subtag of a BCP-47 tag: ``"en-US"`` -> ``"US"``."""
    if not language or "-" not in language:
        return None
    region = language.rsplit("-", 1)[1].strip()
    return region.upper() if len(region) == 2 and region.isalpha() else None


#: How Douyin's own page names an operating system, and the pattern it matches
#: on. Lifted verbatim from its ``chunk-43693`` bundle, where the table reads
#: ``[{s:"Windows 10",r:/(Windows 10.0|Windows NT 10.0|Windows NT 10.1)/}, ...]``
#: - so this is the platform's vocabulary rather than ours, which is the whole
#: point of echoing it back.
#:
#: The label carries the version inside it (``"Windows 10"``), and the page
#: splits the two apart; :func:`operating_system` does the same. Entries are
#: tried in order, so the specific ones come first.
_OS_TABLE: Final[tuple[tuple[str, str, str], ...]] = (
    ("Windows 10.0|Windows NT 10.0|Windows NT 10.1", "Windows", "10"),
    ("Windows 8.1|Windows NT 6.3", "Windows", "8.1"),
    ("Windows 8|Windows NT 6.2", "Windows", "8"),
    ("Windows 7|Windows NT 6.1", "Windows", "7"),
    ("Android", "Android", ""),
    ("SunOS", "Sun OS", ""),
    ("iPhone|iPad|iPod", "iOS", ""),
    ("Mac OS X", "Mac OS X", ""),
    ("MacPPC|MacIntel|Mac_PowerPC|Macintosh", "Mac OS", ""),
    ("Linux|X11", "Linux", ""),
)

#: Versions that live in the User-Agent rather than in the label above.
_OS_VERSION_PATTERNS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Mac OS X": r"Mac OS X (\d+(?:[._]\d+)*)",
        "Android": r"Android ([\d.]+)",
        "iOS": r"OS (\d+(?:[._]\d+)*) like Mac OS X",
    }
)


def operating_system(user_agent: str | None) -> tuple[str, str] | None:
    """``(os_name, os_version)`` as the platform's own detector would report them.

    None when the User-Agent names nothing in the table, which is the honest
    answer: an OS invented here would contradict the User-Agent sent beside it.
    """
    if not user_agent:
        return None
    for pattern, name, version in _OS_TABLE:
        if not re.search(pattern, user_agent):
            continue
        if version:
            return name, version
        found = _OS_VERSION_PATTERNS.get(name)
        match = re.search(found, user_agent) if found else None
        return name, (match.group(1).replace("_", ".") if match else "")
    return None


def browser_version(user_agent: str | None, browser_name: str) -> str | None:
    """The full version the User-Agent states for ``browser_name``.

    ``Chrome/146.0.0.0`` -> ``146.0.0.0``. The query string carries the same
    number the User-Agent does, because the platform reads it out of the User-Agent
    - so deriving it here is not a guess, it is the only value that can agree.
    """
    if not user_agent:
        return None
    match = re.search(rf"{re.escape(browser_name)}/([\d.]+)", user_agent)
    return match.group(1) if match else None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    """One upstream endpoint.

    ``name`` is the stable key the scheduler uses for token buckets, circuit
    breakers and the endpoint health board (``sched:bucket:{identity}:{name}``),
    so it is part of the operational contract and must not be renamed casually.

    ``risk_weight`` scales cooldown after a risk-control hit; see
    ``docs/design/03-scheduler.md``. Listing endpoints are heavier than a single
    detail lookup because they are the ones platforms watch most closely.
    """

    name: str
    path: str
    method: HttpMethod = "GET"
    #: Caller-supplied argument names that must be present and non-blank.
    required: tuple[str, ...] = ()
    #: Turns caller arguments into query parameters. When absent, arguments are
    #: stringified as-is, which is only useful for parameterless endpoints.
    build: ParamBuilder | None = None
    risk_weight: float = 1.0
    #: Whether the signing layer must append platform signature parameters.
    signed: bool = True
    #: Endpoint-specific headers merged over the adapter defaults.
    headers: tuple[tuple[str, str], ...] = ()
    summary: str = ""

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        """Validate required arguments and produce the query string mapping."""
        missing = tuple(name for name in self.required if _is_blank(kwargs.get(name)))
        if missing:
            raise InvalidParam(
                f"missing required parameter(s) for {self.name}: {', '.join(missing)}",
                details={"endpoint": self.name, "missing": list(missing)},
            )
        if self.build is None:
            return {key: str(value) for key, value in kwargs.items() if value is not None}
        try:
            return self.build(**kwargs)
        except TypeError as exc:
            if exc.__traceback__ is not None and exc.__traceback__.tb_next is not None:
                # Raised inside the builder rather than while binding its
                # arguments: that is a bug in this package, and relabelling it
                # INVALID_PARAM would send the caller off to fix their input.
                raise
            raise InvalidParam(
                f"unexpected parameter for {self.name}: {exc}",
                details={"endpoint": self.name},
            ) from exc

    def to_request(
        self,
        *,
        default_headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
        """Build the full :class:`RequestSpec` for this endpoint."""
        headers = dict(default_headers or {})
        headers.update(self.headers)
        return RequestSpec(
            method=self.method,
            url=self.path,
            params=self.build_params(**kwargs),
            headers=headers,
            body=None,
        )


@dataclass(frozen=True, slots=True)
class EndpointTable:
    """An immutable registry of endpoints keyed by their stable name."""

    specs: Mapping[str, EndpointSpec] = field(default_factory=dict)

    @classmethod
    def of(cls, *specs: EndpointSpec) -> EndpointTable:
        table: dict[str, EndpointSpec] = {}
        for spec in specs:
            if spec.name in table:
                raise ValueError(f"duplicate endpoint name: {spec.name}")
            table[spec.name] = spec
        # One table instance is shared by every caller of the adapter, so the
        # mapping is proxied: 'immutable' has to mean it, not just freeze the
        # attribute that points at a mutable dict.
        return cls(specs=MappingProxyType(table))

    def __contains__(self, name: object) -> bool:
        return name in self.specs

    def __iter__(self) -> Any:
        return iter(self.specs)

    def __len__(self) -> int:
        return len(self.specs)

    def __getitem__(self, name: str) -> EndpointSpec:
        try:
            return self.specs[name]
        except KeyError:
            raise InvalidParam(
                f"unknown endpoint: {name}",
                details={"endpoint": name, "known": sorted(self.specs)},
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self.specs)


@runtime_checkable
class PlatformAdapter(Protocol):
    """What every platform package must expose as ``<package>.adapter.ADAPTER``.

    Parsers take the decoded JSON body exactly as the platform returned it. They
    raise :class:`dtk.core.errors.UpstreamChanged` with the missing field path
    when the structure no longer matches, and
    :class:`dtk.core.errors.UpstreamRiskControl` when the response is a block
    rather than data. Returning a half-filled model is never allowed.
    """

    # Declared read-only so a frozen dataclass adapter satisfies the protocol;
    # a plain attribute satisfies a read-only member too, but not the reverse.
    @property
    def platform(self) -> Platform: ...

    @property
    def endpoints(self) -> EndpointTable: ...

    @property
    def default_headers(self) -> Mapping[str, str]: ...

    def build_request(self, endpoint: str, /, **params: Any) -> RequestSpec:
        """Describe one upstream call. No IO happens here."""
        ...

    def profile_for(self, source: ProfileSource) -> ClientProfile:
        """The query values that agree with this identity's User-Agent.

        Pass the result to :meth:`build_request` as ``profile``. It exists on
        the adapter rather than as one shared function because the two platforms
        report the same facts in different vocabularies - Douyin sends the
        marketing name and the real version, TikTok the navigator product
        strings - and a caller has no business knowing which.
        """
        ...

    def detect_risk_control(self, payload: Mapping[str, Any]) -> str | None:
        """Name the risk-control signature in ``payload``, or ``None``."""
        ...

    def parse_content(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Content: ...

    def parse_author(self, payload: Mapping[str, Any]) -> Author: ...

    def parse_author_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]: ...

    def parse_author_list(self, payload: Mapping[str, Any]) -> Page[Author]: ...

    def parse_comments(
        self, payload: Mapping[str, Any], *, content_id: str | None = None
    ) -> Page[Comment]: ...

    def parse_comment_replies(
        self,
        payload: Mapping[str, Any],
        *,
        content_id: str | None = None,
        parent_id: str | None = None,
    ) -> Page[Comment]: ...


__all__ = [
    "ClientProfile",
    "EndpointSpec",
    "EndpointTable",
    "HttpMethod",
    "ParamBuilder",
    "PlatformAdapter",
    "RequestSpec",
]
