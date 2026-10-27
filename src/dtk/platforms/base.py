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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol, TypedDict, runtime_checkable
from urllib.parse import urlencode

from dtk.core.errors import InvalidParam
from dtk.core.types import LoginNeed, Platform
from dtk.models import (
    Author,
    Collection,
    Comment,
    Content,
    ContentPart,
    Hashtag,
    LiveRoom,
    LiveStatus,
    LiveViewer,
    Mix,
    MusicDetail,
    Page,
    TrendingTopic,
)

HttpMethod = Literal["GET", "POST"]

#: What a browser sends with a ``key=value&...`` body, charset and all. Douyin's
#: own page sends exactly this on ``listcollection``.
FORM_CONTENT_TYPE: Final = "application/x-www-form-urlencoded; charset=UTF-8"

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
    #: The body as it goes on the wire, or None. Bytes rather than fields, and
    #: encoded here rather than in the transport, for the reason the query is
    #: sent verbatim: a_bogus hashes the body, so the signer and the transport
    #: have to be handed the same bytes, and the one way to guarantee that is
    #: to produce them once.
    body: bytes | None


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
            cpu_core_num=_positive(getattr(source, "hardware_concurrency", None))
            or self.cpu_core_num,
            device_memory=_positive(getattr(source, "device_memory", None)) or self.device_memory,
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

    @property
    def hardware_concurrency(self) -> int | None: ...

    @property
    def device_memory(self) -> int | None: ...


def _positive(value: object) -> int | None:
    """A count the browser reported, or None. Zero is not a machine."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


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


def is_later_page(cursor: Any) -> bool:
    """Whether a caller's cursor asks for anything but the first page.

    ``"0"`` is a first page and not a later one: both platforms number the
    start of a feed that way, and a client that echoes the cursor it started
    with must not be routed as if it were paging.
    """
    if _is_blank(cursor):
        return False
    return str(cursor).strip() != "0"


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
    #: Whether a 200 with no body at all is one of this endpoint's normal
    #: answers rather than a refusal.
    #:
    #: Almost nowhere. A body-less 200 is the canonical shape of a platform
    #: withholding a payload from an identity, and reading it as anything else
    #: is how a burnt identity stays in rotation. The exception is an endpoint
    #: whose "no" IS silence - and that has to be measured, not assumed.
    empty_body_is_normal: bool = False
    #: A top-level key whose null, in an answer that otherwise succeeded, means
    #: the thing the request names does not exist. Measured per endpoint, never
    #: assumed per platform: Bilibili's folder contents answer a folder that
    #: does not exist with ``{"code": 0, "data": null}``, which read as an
    #: empty folder, while its folder list answers an author with no folders
    #: in the same shape - and there an empty page is the right answer.
    absent_when_null: str | None = None
    #: What a guest identity loses on the first page. See :class:`LoginNeed`.
    login: LoginNeed = LoginNeed.NONE
    #: The same, for every page after the first; None means as ``login``. Its
    #: own field because the two differ: Douyin serves an author's first page
    #: to a guest, short of the newest posts, and every later page to nobody
    #: but a signed-in account.
    paging_login: LoginNeed | None = None
    #: Built parameters that travel as a form body instead of in the query, in
    #: the order given. Douyin's ``listcollection`` is POST-only and its page
    #: sends ``count`` and ``cursor`` nowhere but the body, in that order.
    #: a_bogus hashes the body, which is why it is built once, here.
    form: tuple[str, ...] = ()
    #: Whether the answer belongs to the session that asks rather than to
    #: anything the request names. Such an endpoint is served only to a pinned
    #: identity: unpinned, rotation would pick an account at random, and the
    #: shared cache would then hand that account's private list to the next
    #: caller - a demo visitor included.
    session_bound: bool = False
    #: Endpoint-specific headers merged over the adapter defaults.
    headers: tuple[tuple[str, str], ...] = ()
    summary: str = ""

    def __post_init__(self) -> None:
        if self.form and self.method != "POST":
            raise ValueError(f"{self.name}: a form body needs POST, not {self.method}")

    def login_for(self, params: Mapping[str, Any]) -> LoginNeed:
        """What serving these caller arguments from a guest would cost."""
        if self.paging_login is not None and is_later_page(params.get("cursor")):
            return self.paging_login
        return self.login

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
        built = self.build_params(**kwargs)
        if not self.form:
            return RequestSpec(
                method=self.method, url=self.path, params=built, headers=headers, body=None
            )
        fields = [(name, built[name]) for name in self.form if name in built]
        return RequestSpec(
            method=self.method,
            url=self.path,
            params={key: value for key, value in built.items() if key not in self.form},
            headers={"Content-Type": FORM_CONTENT_TYPE, **headers},
            body=urlencode(fields).encode(),
        )


#: Folds a follow-up answer into the answer that asked for it. Pure, like a
#: parser: two payloads in, one out.
Merge = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class FollowUp:
    """A second request an answer needs before it can be parsed.

    Bilibili's video detail has no playable file in it; the page asks
    ``playurl`` for one with the part id the detail just returned. The adapter
    says so by returning one of these, and the fetch layer makes the call -
    scheduled, signed and logged like any other - and hands the parser the
    merged payload. The platform package still performs no IO; it only
    describes the call and how to combine the answers.
    """

    endpoint: str
    #: Builder keywords, as for :meth:`PlatformAdapter.build_request`.
    params: Mapping[str, Any]
    merge: Merge
    #: Whether the first answer means anything without this one. A video's
    #: detail does - title, author and counts are real without the stream -
    #: so by default a failed follow-up leaves the first answer to be parsed.
    #: The weekly selection's index does not: it is a list of issue numbers,
    #: and parsed on its own it would read as an issue with no videos in it.
    required: bool = False


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

    def follow_up(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> FollowUp | None:
        """A second call this answer needs before parsing, or ``None``.

        Most answers are complete; see :class:`FollowUp` for the ones that are
        not. ``params`` are the builder keywords the first call was made with,
        for a follow-up that depends on what was asked as well as on what came
        back - which part of a video, which issue of a list.
        """
        ...

    def parse_content(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Content: ...

    def parse_author(self, payload: Mapping[str, Any]) -> Author: ...

    def parse_author_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]: ...

    def parse_author_list(self, payload: Mapping[str, Any]) -> Page[Author]: ...

    def parse_author_collections(self, payload: Mapping[str, Any]) -> Page[Collection]: ...

    def parse_collection_detail(self, payload: Mapping[str, Any]) -> Collection: ...

    def parse_collection_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime, cursor: str | None = None
    ) -> Page[Content]:
        """``cursor`` is the one this page was asked for. Douyin and TikTok
        hand the next one back and ignore it; Bilibili's folder answer says only
        whether there is more, so the next page is computed from it."""
        ...

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

    def parse_content_parts(self, payload: Mapping[str, Any]) -> Page[ContentPart]: ...

    def parse_popular(
        self,
        payload: Mapping[str, Any],
        *,
        board: str,
        fetched_at: datetime,
        cursor: str | None = None,
    ) -> Page[Content]:
        """A site-curated list of posts. ``board`` is the capability that asked -
        ``popular``, ``popular_weekly``, ``popular_precious`` or ``ranking`` -
        and ``cursor`` the page it asked for, which a numbered list needs to
        say what comes next."""
        ...

    def parse_related_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        """The posts a platform recommends beside one, as one batch: none of
        the three pages them in a way a caller could follow."""
        ...

    def parse_music_detail(self, payload: Mapping[str, Any]) -> MusicDetail: ...

    def parse_music_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]: ...

    def parse_hashtag_detail(self, payload: Mapping[str, Any]) -> Hashtag: ...

    def parse_hashtag_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]: ...

    def parse_search_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]: ...

    def parse_search_authors(self, payload: Mapping[str, Any]) -> Page[Author]: ...

    def parse_author_mixes(self, payload: Mapping[str, Any]) -> Page[Mix]: ...

    def parse_live_room(
        self, payload: Mapping[str, Any], *, room_id: str | None = None
    ) -> LiveRoom:
        """``room_id`` is the one the request named, for a platform whose
        answer does not repeat it: Douyin's never mentions the web_rid."""
        ...

    def parse_live_room_by_id(self, payload: Mapping[str, Any]) -> LiveRoom:
        """The same room, asked for by the broadcast's own id rather than by
        what its web link carries. A platform without such an id raises
        ``UnsupportedContent``."""
        ...

    def parse_live_status(
        self, payload: Mapping[str, Any], *, live_ids: Sequence[str] = ()
    ) -> Page[LiveStatus]:
        """Whether each broadcast asked about is still on air, as one page.

        ``live_ids`` is the list the request named, in its order. The page
        answers each of them, ``false`` for one the platform's answer leaves
        out, so a caller can pair its list with the page by position."""
        ...

    def parse_live_ranking(self, payload: Mapping[str, Any]) -> Page[LiveViewer]:
        """A broadcast's audience ranking, top first, as one page."""
        ...

    def parse_trending(self, payload: Mapping[str, Any]) -> Page[TrendingTopic]: ...

    def parse_mix_posts(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
        """The posts in one mix. Douyin's and TikTok's answer in the author post
        list's own envelope; Bilibili's season answer is a folder's."""
        ...


__all__ = [
    "FORM_CONTENT_TYPE",
    "ClientProfile",
    "EndpointSpec",
    "EndpointTable",
    "FollowUp",
    "HttpMethod",
    "Merge",
    "ParamBuilder",
    "PlatformAdapter",
    "RequestSpec",
    "is_later_page",
]
