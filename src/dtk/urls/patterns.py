"""Host allowlist and URL shape tables for the supported platforms.

This module is deliberately data only: every regular expression, host set and
route table lives here, while the matching logic lives in :mod:`dtk.urls.parse`.
Keeping them apart means a new share-link shape is a one-line table entry
instead of another branch in a parser that already has too many.

Origin: the route shapes are salvaged from the V4 tree on branch ``main``
(``crawlers/douyin/web/utils.py`` and ``crawlers/tiktok/web/utils.py``, classes
``SecUserIdFetcher`` / ``AwemeIdFetcher`` / ``MixIdFetcher`` /
``WebCastIdFetcher``). Those classes encode years of accumulated knowledge about
the messy formats users actually paste. What is dropped from them is the part
that made them untestable: they performed the redirect themselves and read
credentials out of ``config.yaml``.

The host allowlist is a security control, not a convenience. Every outbound URL
this service is asked to touch passes through it exactly once; see
docs/design/08-security.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from dtk.core.types import ContentKind, Platform


class ResourceKind(StrEnum):
    """What a recognized URL points at.

    ``VIDEO`` covers every single work (aweme) regardless of whether it turns
    out to be a video or an image album: a Douyin ``/video/<id>`` link can serve
    either, and only the detail response settles it. The URL-level hint, when
    the path does carry one, is reported separately as ``UrlKind.content_kind``.
    """

    VIDEO = "video"
    USER = "user"
    LIVE = "live"
    #: Douyin ``reflow`` links carry a ``room_id``, not the ``web_rid`` that
    #: ``live.douyin.com/<id>`` uses. They need the room-id endpoint instead, so
    #: they get their own kind rather than being silently mixed into LIVE.
    LIVE_ROOM = "live_room"
    MIX = "mix"
    MUSIC = "music"
    CHALLENGE = "challenge"
    SEARCH = "search"
    #: A short link. The target cannot be known before expansion, which is why
    #: doc 08 requires the allowlist to be applied a second time afterwards.
    SHORT_LINK = "short_link"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Host allowlist
# --------------------------------------------------------------------------

#: Registrable domains owned by Douyin. Subdomains are allowed (``www``,
#: ``live``, ``webcast``, ``sso``, ``m``, ...).
DOUYIN_DOMAINS: frozenset[str] = frozenset(
    {
        "douyin.com",
        "iesdouyin.com",
        "amemv.com",
    }
)

#: Registrable domains owned by TikTok. Covers ``www``, ``m``, ``vm``, ``vt``.
TIKTOK_DOMAINS: frozenset[str] = frozenset({"tiktok.com"})

ALLOWED_DOMAINS: frozenset[str] = DOUYIN_DOMAINS | TIKTOK_DOMAINS

PLATFORM_BY_DOMAIN: dict[str, Platform] = {
    **dict.fromkeys(DOUYIN_DOMAINS, Platform.DOUYIN),
    **dict.fromkeys(TIKTOK_DOMAINS, Platform.TIKTOK),
}

#: Hosts whose entire purpose is to redirect. A URL on one of these is opaque
#: until expanded, so it never reaches a platform endpoint directly.
SHORT_LINK_HOSTS: frozenset[str] = frozenset(
    {
        "v.douyin.com",
        "v.amemv.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    }
)

#: Hosts recognized in share text when the user pasted the link without a
#: scheme. Restricted to the allowlist so this convenience cannot widen it.
BARE_URL_HOSTS: frozenset[str] = SHORT_LINK_HOSTS | frozenset(
    {
        "douyin.com",
        "www.douyin.com",
        "live.douyin.com",
        "m.douyin.com",
        "www.iesdouyin.com",
        "iesdouyin.com",
        "tiktok.com",
        "www.tiktok.com",
        "m.tiktok.com",
    }
)

#: Only these schemes are ever fetched. ``javascript:``, ``data:``, ``file:``
#: and ``gopher:`` are rejected before any host check runs.
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

#: Names that always resolve to the machine running this service or to its
#: local network. Checked before the allowlist so the intent of a rejection is
#: recorded accurately even though the allowlist alone would also refuse them.
BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
    }
)

BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".home.arpa",
    ".arpa",
)

#: A host must look like an ASCII DNS name. This rejects Unicode homographs and
#: raw ``0x7f.0.0.1`` / ``2130706433`` style address literals in one step, since
#: neither can match an allowlisted domain anyway.
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$")

#: Purely numeric labels (``2130706433``) or hex forms are address literals in
#: disguise; a real hostname never ends in an all-digit label.
NUMERIC_HOST_RE = re.compile(r"^(0x[0-9a-f]+|\d+)$")


# --------------------------------------------------------------------------
# Query handling
# --------------------------------------------------------------------------

#: Share and analytics parameters removed during normalization. They vary per
#: share action, so keeping them would make the same work produce a different
#: canonical URL every time and defeat response caching.
#:
#: Resource identifiers must never be listed here even though they look like
#: share noise. ``sec_uid`` / ``sec_user_id`` in particular are the *only*
#: thing that identifies the author on an expanded profile share link, and
#: stripping them turned that link into an unrecognizable page.
#:
#: Signature and session parameters (``msToken``, ``a_bogus``, ``verifyFp``,
#: ``sessionid``, ...) are in here for a second reason: a user who pastes a URL
#: copied out of DevTools would otherwise have their own credentials echoed
#: back in the canonical URL, stored in the cache key and written to the
#: request log.
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "X-Bogus",
        # TikTok's own two signature parameters. Same reason as the rest of this
        # group: a URL copied out of DevTools carries them, and echoing a request
        # seal back into the canonical URL puts it in the cache key.
        "X-Dynosaur",
        "X-Gnarly",
        "_d",
        "_r",
        "_signature",
        "_t",
        "a_bogus",
        "activity_info",
        "app_language",
        "checksum",
        "device_id",
        "did",
        "ecom_share_track_params",
        "enter_from",
        "enter_from_merge",
        "enter_method",
        "extra_params",
        "from",
        "from_ssr",
        "from_tab_name",
        "iid",
        "im_channel_invite_id",
        "is_copy_url",
        "is_from_webapp",
        "is_recommend",
        "language",
        "lang",
        "msToken",
        "previous_page",
        "region",
        "relation_type",
        "request_id",
        "route_tab_name",
        "s_v_web_id",
        "schema_type",
        "sessionid",
        "sender_device",
        "share_app_id",
        "share_app_name",
        "share_iid",
        "share_item_id",
        "share_link_id",
        "share_sign",
        "share_token",
        "share_version",
        "sharer_language",
        "social_share_type",
        "source",
        "style",
        "timestamp",
        "titleType",
        "title_type",
        "tt_from",
        "u_code",
        "use_link_command",
        "utm_campaign",
        "utm_medium",
        "utm_source",
        "verifyFp",
        "web_id",
        "with_sec_did",
    }
)


# --------------------------------------------------------------------------
# URL extraction from share text
# --------------------------------------------------------------------------

#: Characters that terminate a URL inside pasted share text. CJK ideographs,
#: CJK punctuation (including the bracket pair used by Douyin share captions),
#: fullwidth forms, curly quotes and emoji all end the match, because share text
#: glues them directly onto the link with no separating space. The ranges are
#: written as escapes so that this file stays pure ASCII (docs/design/14-i18n.md).
_URL_BODY = (
    r"[^\s<>\"'`\\"
    r"\u2018\u2019\u201c\u201d"
    r"\u3000-\u303f"
    r"\u3400-\u4dbf"
    r"\u4e00-\u9fff"
    r"\uff00-\uffef"
    r"\U0001f000-\U0001faff"
    r"]"
)

URL_IN_TEXT_RE = re.compile(rf"https?://{_URL_BODY}+", re.IGNORECASE)

_BARE_HOST_ALTERNATION = "|".join(
    re.escape(host) for host in sorted(BARE_URL_HOSTS, key=len, reverse=True)
)

#: Scheme-less links, e.g. ``v.douyin.com/abc123/`` pasted straight out of a
#: chat app. Only allowlisted hosts qualify, and a path is required so that a
#: bare mention of a domain in prose is not mistaken for a link.
BARE_URL_IN_TEXT_RE = re.compile(
    rf"(?<![\w.@/-])(?:{_BARE_HOST_ALTERNATION})/{_URL_BODY}*",
    re.IGNORECASE,
)

#: Single pass over pasted text: an ``http(s)://`` link, or a scheme-less link
#: on an allowlisted host. One combined pattern keeps the matches in text order.
ANY_URL_IN_TEXT_RE = re.compile(
    rf"(?:https?://{_URL_BODY}+)"
    rf"|(?:(?<![\w.@/-])(?:{_BARE_HOST_ALTERNATION})/{_URL_BODY}*)",
    re.IGNORECASE,
)

#: Punctuation that belongs to the surrounding sentence rather than to the URL.
TRAILING_JUNK = "".join(
    [
        ".,;:!?)]}>",
        "'\"",
        # CJK and fullwidth sentence punctuation, written as escapes so this
        # file stays ASCII only.
        "\u3001\u3002\uff0c\uff01\uff1f\uff09\u3011\u300b\u201d\u2019",
    ]
)


# --------------------------------------------------------------------------
# Route tables
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Route:
    """One recognized URL shape.

    ``pattern`` is searched against ``path`` plus ``?query``, so a route can key
    off either. ``canonical`` is a :meth:`str.format_map` template filled from
    the pattern's named groups; a route whose template needs a group the match
    did not produce is skipped rather than emitting a half-built URL.
    """

    pattern: re.Pattern[str]
    resource: ResourceKind
    canonical: str
    content_kind: ContentKind | None = None
    #: When set, the route only applies to these exact hosts.
    hosts: frozenset[str] | None = None
    needs_expansion: bool = False
    #: Groups whose captured value is percent-decoded before it becomes
    #: ``UrlKind.resource_id``. The canonical URL always uses the raw capture.
    #: Decoding follows the part of the URL the group was captured from: a
    #: ``+`` in a query string is a space, a ``+`` in a path is a ``+``.
    decode: frozenset[str] = frozenset()


#: Douyin routes, most specific first.
#:
#: Ordering note: the ``modal_id`` / ``vid`` query routes come before the path
#: routes on purpose. ``douyin.com/user/MS4w...?modal_id=7123`` is a work opened
#: as a modal on top of a profile page, and the user who pasted it wants the
#: work, not the profile. V4's ``AwemeIdFetcher`` made the same choice; its
#: ``vid=`` branch was added in commit 9ab8e11 ("Support Douyin new URL format").
DOUYIN_ROUTES: tuple[Route, ...] = (
    Route(
        re.compile(r"[?&]modal_id=(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.douyin.com/video/{id}",
    ),
    Route(
        re.compile(r"[?&]vid=(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.douyin.com/video/{id}",
    ),
    Route(
        re.compile(r"^/(?:share/)?video/(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.douyin.com/video/{id}",
        content_kind=ContentKind.VIDEO,
    ),
    Route(
        re.compile(r"^/(?:share/)?note/(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.douyin.com/note/{id}",
        content_kind=ContentKind.IMAGE_ALBUM,
    ),
    Route(
        re.compile(r"^/(?:share/)?slides/(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.douyin.com/note/{id}",
        content_kind=ContentKind.IMAGE_ALBUM,
    ),
    Route(
        re.compile(r"^/(?:share/)?user/(?P<id>[^/?#&]+)"),
        ResourceKind.USER,
        "https://www.douyin.com/user/{id}",
    ),
    Route(
        re.compile(r"/webcast/reflow/(?P<id>\d+)"),
        ResourceKind.LIVE_ROOM,
        "https://webcast.amemv.com/douyin/webcast/reflow/{id}",
        content_kind=ContentKind.LIVE,
    ),
    Route(
        re.compile(r"^/(?P<id>\d+)(?:[/?#]|$)"),
        ResourceKind.LIVE,
        "https://live.douyin.com/{id}",
        content_kind=ContentKind.LIVE,
        hosts=frozenset({"live.douyin.com"}),
    ),
    Route(
        re.compile(r"^/(?:share/)?live/(?P<id>\d+)"),
        ResourceKind.LIVE,
        "https://live.douyin.com/{id}",
        content_kind=ContentKind.LIVE,
    ),
    Route(
        re.compile(r"^/collection/(?P<id>\d+)"),
        ResourceKind.MIX,
        "https://www.douyin.com/collection/{id}",
    ),
    Route(
        re.compile(r"[?&]mix_id=(?P<id>\d+)"),
        ResourceKind.MIX,
        "https://www.douyin.com/collection/{id}",
    ),
    Route(
        re.compile(r"^/music/(?P<id>\d+)"),
        ResourceKind.MUSIC,
        "https://www.douyin.com/music/{id}",
    ),
    Route(
        re.compile(r"^/(?:hashtag|challenge)/(?P<id>\d+)"),
        ResourceKind.CHALLENGE,
        "https://www.douyin.com/hashtag/{id}",
    ),
    Route(
        re.compile(r"^/search/(?P<id>[^/?#]+)"),
        ResourceKind.SEARCH,
        "https://www.douyin.com/search/{id}",
        decode=frozenset({"id"}),
    ),
    # Last resort, and the reason V4's SecUserIdFetcher existed: a profile
    # short link (``v.douyin.com/<code>`` opened on an author) expands to a
    # share page that carries the author only in the query -
    # ``iesdouyin.com/share/user/?sec_uid=MS4w...``, with an empty path
    # segment. V4 matched exactly this with ``sec_uid=([^&]*)`` and used the
    # path form only for links that were not short links, so both shapes have
    # to be here or every profile short link ends as ``unknown_resource``.
    # It is deliberately the last Douyin route: a work share link may also
    # carry the author's ``sec_uid``, and the work is what was shared.
    # The capture is used raw, as V4 used it: a sec id is base64url text, so
    # percent-decoding it could only corrupt it.
    Route(
        re.compile(r"[?&]sec_(?:uid|user_id)=(?P<id>[^&#]+)"),
        ResourceKind.USER,
        "https://www.douyin.com/user/{id}",
    ),
)

#: TikTok routes, most specific first. ``/@handle/live`` has to precede the
#: bare ``/@handle`` route, and ``/music/<slug>-<id>`` anchors on the trailing
#: id because the slug itself may contain hyphens.
TIKTOK_ROUTES: tuple[Route, ...] = (
    Route(
        re.compile(r"^/@(?P<handle>[^/?#]+)/video/(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.tiktok.com/@{handle}/video/{id}",
        content_kind=ContentKind.VIDEO,
    ),
    Route(
        re.compile(r"^/@(?P<handle>[^/?#]+)/photo/(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.tiktok.com/@{handle}/photo/{id}",
        content_kind=ContentKind.IMAGE_ALBUM,
    ),
    Route(
        re.compile(r"^/@(?P<handle>[^/?#]+)/live/?(?:[?#]|$)"),
        ResourceKind.LIVE,
        "https://www.tiktok.com/@{handle}/live",
        content_kind=ContentKind.LIVE,
    ),
    Route(
        re.compile(r"^/@(?P<handle>[^/?#]+)/?(?:[?#]|$)"),
        ResourceKind.USER,
        "https://www.tiktok.com/@{handle}",
    ),
    Route(
        re.compile(r"^/embed(?:/v2)?/(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.tiktok.com/video/{id}",
        content_kind=ContentKind.VIDEO,
    ),
    # Legacy mobile share form: m.tiktok.com/v/<id>.html. The author handle is
    # not present in it, so the canonical URL cannot carry one; the detail
    # endpoint keys off the numeric id and the parser rewrites web_url from the
    # response.
    Route(
        re.compile(r"^/v/(?P<id>\d+)(?:\.html)?"),
        ResourceKind.VIDEO,
        "https://www.tiktok.com/video/{id}",
        content_kind=ContentKind.VIDEO,
    ),
    Route(
        re.compile(r"^/t/(?P<id>[A-Za-z0-9]+)"),
        ResourceKind.SHORT_LINK,
        "https://www.tiktok.com/t/{id}",
        needs_expansion=True,
    ),
    Route(
        re.compile(r"^/music/(?P<slug>[^/?#]*?)-(?P<id>\d+)/?(?:[?#]|$)"),
        ResourceKind.MUSIC,
        "https://www.tiktok.com/music/{slug}-{id}",
    ),
    Route(
        re.compile(r"^/tag/(?P<id>[^/?#]+)"),
        ResourceKind.CHALLENGE,
        "https://www.tiktok.com/tag/{id}",
        decode=frozenset({"id"}),
    ),
    Route(
        re.compile(r"^/search\b[^?#]*(?:[?&]q=(?P<id>[^&#]+))"),
        ResourceKind.SEARCH,
        "https://www.tiktok.com/search?q={id}",
        decode=frozenset({"id"}),
    ),
    Route(
        re.compile(r"[?&]item_id=(?P<id>\d+)"),
        ResourceKind.VIDEO,
        "https://www.tiktok.com/video/{id}",
    ),
)

ROUTES_BY_PLATFORM: dict[Platform, tuple[Route, ...]] = {
    Platform.DOUYIN: DOUYIN_ROUTES,
    Platform.TIKTOK: TIKTOK_ROUTES,
}


__all__ = [
    "ALLOWED_DOMAINS",
    "ALLOWED_SCHEMES",
    "ANY_URL_IN_TEXT_RE",
    "BARE_URL_HOSTS",
    "BARE_URL_IN_TEXT_RE",
    "BLOCKED_HOSTNAMES",
    "BLOCKED_HOST_SUFFIXES",
    "DEFAULT_PORTS",
    "DOUYIN_DOMAINS",
    "DOUYIN_ROUTES",
    "HOSTNAME_RE",
    "NUMERIC_HOST_RE",
    "PLATFORM_BY_DOMAIN",
    "ROUTES_BY_PLATFORM",
    "SHORT_LINK_HOSTS",
    "TIKTOK_DOMAINS",
    "TIKTOK_ROUTES",
    "TRACKING_PARAMS",
    "TRAILING_JUNK",
    "URL_IN_TEXT_RE",
    "ResourceKind",
    "Route",
]
