"""One definition of an endpoint, shared by the worker, REST, MCP and the CLI.

A platform package declares *how* a call is built and parsed; it deliberately
knows nothing about caching, scopes or caller-facing parameter names. Those
three live here, in one table, because the alternative is what V4 did: every
entry point re-deriving the same mapping and drifting apart from the others.

Three things are bound to an endpoint name here:

* the adapter call - which platform builds the request and with which arguments;
* the parse function - which of the adapter's parsers turns the payload into a
  normalized model, plus the context that parser needs (a comment page has to be
  told which content it belongs to, since the payload does not always say);
* the cache TTL key - the runtime setting that decides how long the result may
  be reused.

Caller-facing parameters are canonical (``content_id``, ``author_id``, ...) and
are translated into each platform's own argument names. Douyin calls a post
``aweme_id`` and TikTok calls it ``item_id``; a caller of this project should
not have to care, and an agent driving the MCP tools certainly should not. The
platform spellings are still accepted as aliases, because the REST paths in
docs/design/06-api-auth-mcp.md expose them.

See docs/design/11-data-contracts.md for the P0 endpoint set.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from dtk.core.config import Config
from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.core.types import CommentSort, Platform, Scope
from dtk.platforms import PlatformAdapter, get_adapter
from dtk.platforms.registry import available_platforms

log = get_logger(__name__)


class Capability(StrEnum):
    """What an endpoint does, independent of which platform serves it.

    The values are also the suffix of the endpoint name, so
    ``douyin.content_detail`` is ``Platform.DOUYIN`` plus
    ``Capability.CONTENT_DETAIL``. Keeping the two in step means the scheduler
    key, the console board and this table never need a translation layer.
    """

    CONTENT_DETAIL = "content_detail"
    AUTHOR_PROFILE = "author_profile"
    AUTHOR_POSTS = "author_posts"
    COMMENTS = "comments"
    COMMENT_REPLIES = "comment_replies"
    AUTHOR_LIKES = "author_likes"
    AUTHOR_COLLECTIONS = "author_collections"
    AUTHOR_BOOKMARKS = "author_bookmarks"
    COLLECTION_POSTS = "collection_posts"
    AUTHOR_REPOSTS = "author_reposts"
    COLLECTION_DETAIL = "collection_detail"
    MIX_POSTS = "mix_posts"
    AUTHOR_FOLLOWERS = "author_followers"
    AUTHOR_FOLLOWING = "author_following"
    #: The parts of a video uploaded in several. Bilibili only.
    CONTENT_PARTS = "content_parts"
    #: Site-curated lists of posts, keyed by nobody. Bilibili's four boards.
    POPULAR = "popular"
    POPULAR_WEEKLY = "popular_weekly"
    POPULAR_PRECIOUS = "popular_precious"
    RANKING = "ranking"
    #: What the platform recommends beside one post: one batch, not a page.
    RELATED_POSTS = "related_posts"
    #: A sound a post is made with, and the posts that use it.
    MUSIC_DETAIL = "music_detail"
    MUSIC_POSTS = "music_posts"
    #: A hashtag's page, and the posts filed under it.
    HASHTAG_DETAIL = "hashtag_detail"
    HASHTAG_POSTS = "hashtag_posts"
    #: A keyword search, for posts and for accounts.
    SEARCH_POSTS = "search_posts"
    SEARCH_AUTHORS = "search_authors"
    #: The series an author has published: Douyin's mixes, TikTok's playlists,
    #: Bilibili's seasons. Their ids are what ``mix_posts`` takes.
    AUTHOR_MIXES = "author_mixes"
    #: A live room: whether it is broadcasting, and the pull URLs if it is.
    LIVE_ROOM = "live_room"
    #: The same room, by the broadcast's own id: what an app's share link
    #: carries, and the ``live_id`` a room answer hands back.
    LIVE_ROOM_BY_ID = "live_room_by_id"
    #: Whether each of many broadcasts is on air, by their live_ids.
    LIVE_STATUS = "live_status"
    #: A broadcast's audience ranking: who is at the top of the room's list.
    LIVE_RANKING = "live_ranking"
    #: A board of what people are searching for: words, not posts.
    TRENDING = "trending"


#: Every capability that answers a site-curated list rather than something
#: about one post or one author. They take no id, only a page.
BOARDS: Final[tuple[Capability, ...]] = (
    Capability.POPULAR,
    Capability.POPULAR_WEEKLY,
    Capability.POPULAR_PRECIOUS,
    Capability.RANKING,
)


#: The P0 set from docs/design/11-data-contracts.md. A platform is only usable
#: once it declares all of them, and `missing_capabilities` is what says so.
#:
#: Listed explicitly rather than derived from the enum. `tuple(Capability)` made
#: every capability mandatory everywhere, which is right for the core five and
#: wrong for everything after them: the platforms genuinely differ. TikTok
#: groups posts into playlists it exposes as their own list; Douyin's follower
#: endpoint wants a numeric uid its profile call has to supply first; Douyin
#: serves an author's likes only to a signed-in identity. Requiring each of
#: those of both platforms would mean shipping an endpoint that cannot work in
#: order to satisfy a test.
P0_CAPABILITIES: Final[tuple[Capability, ...]] = (
    Capability.CONTENT_DETAIL,
    Capability.AUTHOR_PROFILE,
    Capability.AUTHOR_POSTS,
    Capability.COMMENTS,
    Capability.COMMENT_REPLIES,
)

#: Everything beyond the core. A platform declares what it can actually serve,
#: and nobody has to discover the difference from an empty page:
#: `GET /api/v1/admin/endpoints/health` walks every adapter's own endpoint table,
#: so it lists exactly what each platform declares, and a caller asking for a
#: capability a platform does not have gets `UNSUPPORTED_CONTENT` naming the
#: platform and the capability in `details`.
#:
#: This comment previously named `GET /api/v1/system/status` as the place to see
#: the difference. It never reported capabilities - the payload is version,
#: uptime, components, pool and storage - so the one instruction it gave a reader
#: was the one thing that would not work.
OPTIONAL_CAPABILITIES: Final[tuple[Capability, ...]] = tuple(
    c for c in Capability if c not in P0_CAPABILITIES
)

# --- canonical caller-facing parameters ---------------------------------------

CONTENT_ID: Final = "content_id"
AUTHOR_ID: Final = "author_id"
UNIQUE_ID: Final = "unique_id"
MIX_ID: Final = "mix_id"
COLLECTION_ID: Final = "collection_id"
#: A sound, by the id a post's ``music`` carries and a music link ends in.
MUSIC_ID: Final = "music_id"
#: A hashtag by number. A TikTok tag link names it instead, and the name goes
#: to HASHTAG_NAME - see ``dtk.worker.parsing.NAMED_BY_LINK``.
HASHTAG_ID: Final = "hashtag_id"
#: A hashtag by name, without the '#'. TikTok's detail answers either.
HASHTAG_NAME: Final = "name"
#: What a search looks for, as typed.
KEYWORD: Final = "keyword"
#: A live room by what its link carries: Douyin's web_rid, the owner's
#: @handle on TikTok, Bilibili's room number.
ROOM_ID: Final = "room_id"
#: A broadcast by the platform's own id for it: Douyin's and TikTok's
#: ``room_id``, which ``LiveRoom.live_id`` carries. Not ROOM_ID, which names
#: the room rather than one broadcast in it - and which, on Douyin, the
#: platform calls ``web_rid``.
LIVE_ID: Final = "live_id"
#: Several of them, comma-separated, for the one call that takes a list.
LIVE_IDS: Final = "live_ids"
#: A :class:`dtk.core.types.TrendingBoard` value.
BOARD: Final = "board"

#: Every stable author id both platforms issue starts with this. It is the only
#: way to tell one from an @handle without asking the platform.
SEC_UID_PREFIX: Final = "MS4wLjAB"


def looks_like_sec_uid(value: str) -> bool:
    """Whether this identifier is a stable author id rather than an @handle."""
    return value.startswith(SEC_UID_PREFIX)


#: Platforms where an author id that is not a sec uid can only be a handle.
#: Bilibili has no handles: its author id is a number, and reading that number
#: as an @handle refused every Bilibili author call before it was sent.
HANDLE_PLATFORMS: Final[frozenset[Platform]] = frozenset({Platform.DOUYIN, Platform.TIKTOK})


COMMENT_ID: Final = "comment_id"
CURSOR: Final = "cursor"
COUNT: Final = "count"
#: A :class:`dtk.core.types.CommentSort` value. Mapped only where a platform
#: takes it; :func:`comment_sort_params` decides what the others are sent.
SORT: Final = "sort"
#: Which part of a video uploaded in several, from 1. Bilibili only.
PART: Final = "part"

#: Platform spellings accepted for a canonical name. ``aweme_id`` and
#: ``item_id`` are the same thing on both platforms as far as a caller is
#: concerned, so both resolve to ``content_id``; ``sec_user_id`` and ``sec_uid``
#: are likewise one identifier under two spellings. These four are exactly the
#: spellings the REST paths in docs/design/06-api-auth-mcp.md expose.
#:
#: Nothing else belongs here. ``uid``, ``user_id`` and ``video_id`` name
#: *different* identifiers on these platforms - the numeric uid rotates and is
#: not the sec id (see :mod:`dtk.platforms.douyin.params`), and TikTok's ``uid``
#: is numeric while its author key is ``secUid``. Accepting them as aliases
#: would send a caller's numeric id upstream in the sec-id slot and return an
#: empty page with nothing anywhere to say the id had been misread. Rejecting
#: an unknown spelling costs a sentence; misinterpreting one costs a debugging
#: session.
ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "aweme_id": CONTENT_ID,
        "item_id": CONTENT_ID,
        "sec_user_id": AUTHOR_ID,
        "sec_uid": AUTHOR_ID,
        "mixId": MIX_ID,
        "playlist_id": MIX_ID,
    }
)

#: Keys that belong to the request envelope rather than to the upstream call.
#: They are dropped rather than rejected so a caller can pass one task payload
#: straight through from REST or MCP.
ENVELOPE_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "callback_url",
        # "describe the request you made". It changes what comes back about the
        # call, never what the call asks the platform for.
        "explain",
        # The identity the caller pinned. Like the proxy below it, it decides
        # HOW the call is made and is never a parameter of the call itself.
        "identity",
        "include_raw",
        # "go upstream even if the answer is already cached". Like the others
        # here it decides HOW the call is made, never what is asked for.
        "refresh",
        "lang",
        "language",
        "platform",
        "priority",
        # The caller's own egress. It belongs to the request, never to the
        # upstream query - sending it on would leak the proxy, credentials and
        # all, to the platform.
        "proxy",
        "url",
        "wait",
    }
)

#: Canonical parameters whose value is an identifier. Ids are always strings:
#: a 19-digit aweme_id exceeds the JavaScript safe range (doc 11).
_ID_PARAMS: Final[frozenset[str]] = frozenset(
    {
        CONTENT_ID,
        AUTHOR_ID,
        UNIQUE_ID,
        COMMENT_ID,
        MIX_ID,
        COLLECTION_ID,
        MUSIC_ID,
        HASHTAG_ID,
        ROOM_ID,
        LIVE_ID,
        LIVE_IDS,
    }
)

#: canonical name -> platform builder keyword, per platform and capability.
_ARGUMENTS: Final[Mapping[Platform, Mapping[Capability, Mapping[str, str]]]] = {
    Platform.DOUYIN: {
        Capability.CONTENT_DETAIL: {CONTENT_ID: "aweme_id"},
        Capability.AUTHOR_PROFILE: {AUTHOR_ID: "sec_user_id"},
        Capability.AUTHOR_POSTS: {AUTHOR_ID: "sec_user_id", CURSOR: "cursor", COUNT: "count"},
        Capability.COMMENTS: {CONTENT_ID: "aweme_id", CURSOR: "cursor", COUNT: "count"},
        Capability.COMMENT_REPLIES: {
            CONTENT_ID: "item_id",
            COMMENT_ID: "comment_id",
            CURSOR: "cursor",
            COUNT: "count",
        },
        Capability.AUTHOR_LIKES: {AUTHOR_ID: "sec_user_id", CURSOR: "cursor", COUNT: "count"},
        Capability.MIX_POSTS: {MIX_ID: "mix_id", CURSOR: "cursor", COUNT: "count"},
        # No author id, deliberately. `collects/list/` has no subject but
        # the session holding it, so an author id is not a parameter this
        # endpoint has - and `canonical_params` rejecting it as unknown is
        # the honest answer. Dropping it would return the operator's own
        # folders under a stranger's name.
        Capability.AUTHOR_COLLECTIONS: {CURSOR: "cursor", COUNT: "count"},
        # The same absence for the same reason: `listcollection` answers about
        # the session sending it and cannot be pointed at anyone.
        Capability.AUTHOR_BOOKMARKS: {CURSOR: "cursor", COUNT: "count"},
        Capability.COLLECTION_POSTS: {
            COLLECTION_ID: "collection_id",
            CURSOR: "cursor",
            COUNT: "count",
        },
        Capability.RELATED_POSTS: {CONTENT_ID: "aweme_id", COUNT: "count"},
        Capability.MUSIC_DETAIL: {MUSIC_ID: "music_id"},
        Capability.MUSIC_POSTS: {MUSIC_ID: "music_id", CURSOR: "cursor", COUNT: "count"},
        Capability.HASHTAG_DETAIL: {HASHTAG_ID: "ch_id"},
        Capability.AUTHOR_MIXES: {AUTHOR_ID: "sec_user_id", CURSOR: "cursor", COUNT: "count"},
        Capability.LIVE_ROOM: {ROOM_ID: "web_rid"},
        Capability.LIVE_ROOM_BY_ID: {LIVE_ID: "room_id"},
        Capability.LIVE_STATUS: {LIVE_IDS: "room_ids"},
        Capability.LIVE_RANKING: {LIVE_ID: "room_id"},
        Capability.TRENDING: {BOARD: "board"},
    },
    Platform.TIKTOK: {
        Capability.CONTENT_DETAIL: {CONTENT_ID: "item_id"},
        Capability.AUTHOR_PROFILE: {AUTHOR_ID: "sec_uid", UNIQUE_ID: "unique_id"},
        Capability.AUTHOR_POSTS: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        # TikTok's comment endpoints are the one place it keeps Douyin's
        # snake_case naming, ``aweme_id`` included.
        Capability.COMMENTS: {CONTENT_ID: "aweme_id", CURSOR: "cursor", COUNT: "count"},
        Capability.COMMENT_REPLIES: {
            CONTENT_ID: "item_id",
            COMMENT_ID: "comment_id",
            CURSOR: "cursor",
            COUNT: "count",
        },
        Capability.AUTHOR_LIKES: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_COLLECTIONS: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_BOOKMARKS: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        Capability.COLLECTION_POSTS: {
            COLLECTION_ID: "collection_id",
            CURSOR: "cursor",
            COUNT: "count",
        },
        Capability.AUTHOR_REPOSTS: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        Capability.COLLECTION_DETAIL: {COLLECTION_ID: "collection_id"},
        Capability.MIX_POSTS: {MIX_ID: "mix_id", CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_FOLLOWERS: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_FOLLOWING: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        Capability.RELATED_POSTS: {CONTENT_ID: "item_id", COUNT: "count"},
        Capability.MUSIC_DETAIL: {MUSIC_ID: "music_id"},
        Capability.MUSIC_POSTS: {MUSIC_ID: "music_id", CURSOR: "cursor", COUNT: "count"},
        Capability.HASHTAG_DETAIL: {HASHTAG_ID: "challenge_id", HASHTAG_NAME: "challenge_name"},
        Capability.HASHTAG_POSTS: {
            HASHTAG_ID: "challenge_id",
            CURSOR: "cursor",
            COUNT: "count",
        },
        Capability.SEARCH_POSTS: {KEYWORD: "keyword", CURSOR: "cursor", COUNT: "count"},
        Capability.SEARCH_AUTHORS: {KEYWORD: "keyword", CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_MIXES: {AUTHOR_ID: "sec_uid", CURSOR: "cursor", COUNT: "count"},
        # A /@handle/live link names the owner, and the owner is how the
        # room is asked for.
        Capability.LIVE_ROOM: {ROOM_ID: "unique_id"},
        Capability.LIVE_ROOM_BY_ID: {LIVE_ID: "room_id"},
        Capability.LIVE_STATUS: {LIVE_IDS: "room_ids"},
    },
    # A Bilibili author is a numeric `mid`, the one id it has for a user; a
    # video is a bvid (an `av` number is accepted and converted by the
    # builder). The comment endpoints want the numeric aid and get it from the
    # bvid, so the caller never has to know there are two.
    Platform.BILIBILI: {
        # `part` picks which part's file the detail's follow-up fetches; the
        # detail itself is the whole upload's either way.
        Capability.CONTENT_DETAIL: {CONTENT_ID: "bvid", PART: "part"},
        Capability.CONTENT_PARTS: {CONTENT_ID: "bvid"},
        # No id: a board is the site's, not anybody's. Precious and ranking
        # answer one page and drop the cursor; weekly's cursor is an issue.
        Capability.POPULAR: {CURSOR: "cursor", COUNT: "count"},
        Capability.POPULAR_WEEKLY: {CURSOR: "cursor", COUNT: "count"},
        Capability.POPULAR_PRECIOUS: {CURSOR: "cursor", COUNT: "count"},
        Capability.RANKING: {CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_PROFILE: {AUTHOR_ID: "mid"},
        Capability.AUTHOR_POSTS: {AUTHOR_ID: "mid", CURSOR: "cursor", COUNT: "count"},
        Capability.COMMENTS: {
            CONTENT_ID: "bvid",
            CURSOR: "cursor",
            COUNT: "count",
            SORT: "sort",
        },
        Capability.COMMENT_REPLIES: {
            CONTENT_ID: "bvid",
            COMMENT_ID: "root",
            CURSOR: "cursor",
            COUNT: "count",
        },
        # Every public folder in one answer: it does not page. The cursor and
        # size are accepted because the route always sends them, and dropped
        # by the builder.
        Capability.AUTHOR_COLLECTIONS: {AUTHOR_ID: "mid", CURSOR: "cursor", COUNT: "count"},
        Capability.COLLECTION_POSTS: {
            COLLECTION_ID: "media_id",
            CURSOR: "cursor",
            COUNT: "count",
        },
        # Forty whatever is asked; the size is accepted and dropped, as the
        # folder list's is.
        Capability.RELATED_POSTS: {CONTENT_ID: "bvid", COUNT: "count"},
        Capability.SEARCH_POSTS: {KEYWORD: "keyword", CURSOR: "cursor", COUNT: "count"},
        Capability.SEARCH_AUTHORS: {KEYWORD: "keyword", CURSOR: "cursor", COUNT: "count"},
        Capability.AUTHOR_MIXES: {AUTHOR_ID: "mid", CURSOR: "cursor", COUNT: "count"},
        # A season, by its season_id. One answer: the page and size are
        # accepted and dropped, as the folder list's are.
        Capability.MIX_POSTS: {MIX_ID: "season_id", CURSOR: "cursor", COUNT: "count"},
        Capability.LIVE_ROOM: {ROOM_ID: "room_id"},
        Capability.TRENDING: {BOARD: "board"},
    },
}

#: Which runtime setting holds the TTL for each capability. List endpoints move
#: faster than a profile and much faster than a finished video.
_CACHE_TTL_KEYS: Final[Mapping[Capability, str]] = MappingProxyType(
    {
        Capability.CONTENT_DETAIL: "cache.content_ttl",
        Capability.AUTHOR_PROFILE: "cache.author_ttl",
        Capability.AUTHOR_POSTS: "cache.list_ttl",
        Capability.COMMENTS: "cache.list_ttl",
        Capability.COMMENT_REPLIES: "cache.list_ttl",
        Capability.AUTHOR_LIKES: "cache.list_ttl",
        Capability.AUTHOR_COLLECTIONS: "cache.list_ttl",
        Capability.AUTHOR_BOOKMARKS: "cache.list_ttl",
        Capability.COLLECTION_POSTS: "cache.list_ttl",
        Capability.AUTHOR_REPOSTS: "cache.list_ttl",
        # A folder's name and cover change far less often than its contents.
        Capability.COLLECTION_DETAIL: "cache.author_ttl",
        Capability.MIX_POSTS: "cache.list_ttl",
        Capability.AUTHOR_FOLLOWERS: "cache.list_ttl",
        Capability.AUTHOR_FOLLOWING: "cache.list_ttl",
        # A video's parts are fixed once uploaded, like its detail.
        Capability.CONTENT_PARTS: "cache.content_ttl",
        Capability.POPULAR: "cache.list_ttl",
        Capability.POPULAR_WEEKLY: "cache.list_ttl",
        Capability.POPULAR_PRECIOUS: "cache.list_ttl",
        Capability.RANKING: "cache.list_ttl",
        Capability.RELATED_POSTS: "cache.list_ttl",
        # A sound's title and publisher are fixed; its use count moves at the
        # speed of an author's follower count.
        Capability.MUSIC_DETAIL: "cache.author_ttl",
        Capability.MUSIC_POSTS: "cache.list_ttl",
        Capability.HASHTAG_DETAIL: "cache.author_ttl",
        Capability.HASHTAG_POSTS: "cache.list_ttl",
        Capability.SEARCH_POSTS: "cache.list_ttl",
        Capability.SEARCH_AUTHORS: "cache.list_ttl",
        Capability.AUTHOR_MIXES: "cache.list_ttl",
        # Whether a room is on changes by the minute; the shortest TTL there is.
        Capability.LIVE_ROOM: "cache.list_ttl",
        Capability.LIVE_ROOM_BY_ID: "cache.list_ttl",
        Capability.LIVE_STATUS: "cache.list_ttl",
        Capability.LIVE_RANKING: "cache.list_ttl",
        Capability.TRENDING: "cache.list_ttl",
    }
)


def _scope_for(platform: Platform) -> Scope:
    """The API-key scope guarding this platform.

    A platform can be added before anyone edits the scope enum, and an
    authorization decision has exactly one safe default: an endpoint whose own
    scope does not exist yet requires ``admin``. The fallback is the value
    rather than a ``None`` every caller has to remember to interpret - that
    convention matches :func:`dtk.api.routes.content.read_scope`, and it is the
    difference between a new platform being locked down and it being open.
    """
    try:
        return Scope(f"{platform.value}:read")
    except ValueError:
        return Scope.ADMIN


def endpoint_name(platform: Platform, capability: Capability) -> str:
    return f"{platform.value}.{capability.value}"


@dataclass(frozen=True, slots=True)
class EndpointDefinition:
    """Everything an entry point needs to run one endpoint."""

    name: str
    platform: Platform
    capability: Capability
    #: Canonical parameters that must be present.
    required: tuple[str, ...]
    #: Every canonical parameter this endpoint understands.
    accepts: tuple[str, ...]
    cache_ttl_key: str
    scope: Scope
    risk_weight: float
    summary: str
    #: canonical name -> platform builder keyword.
    arguments: Mapping[str, str]
    #: Served only to a pinned identity. See ``EndpointSpec.session_bound``.
    session_bound: bool = False

    @property
    def adapter(self) -> PlatformAdapter:
        return get_adapter(self.platform)

    # -- parameters --------------------------------------------------------

    def canonical_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize caller input onto the canonical names.

        Unknown parameters are rejected rather than ignored: a silently dropped
        ``max_cursor`` looks exactly like an endpoint that ignores paging, and
        that misreading costs far more to debug than an error does.
        """
        normalized: dict[str, Any] = {}
        unknown: list[str] = []
        for key, value in params.items():
            if key in ENVELOPE_PARAMS:
                continue
            canonical = key if key in self.accepts else ALIASES.get(key, "")
            if canonical not in self.accepts:
                unknown.append(key)
                continue
            if value is None:
                continue  # absent is None, never 0 or ""
            normalized[canonical] = value
        if unknown:
            raise InvalidParam(
                f"unknown parameter(s) for {self.name}: {', '.join(sorted(unknown))}",
                details={
                    "endpoint": self.name,
                    "unknown": sorted(unknown),
                    "accepts": list(self.accepts),
                },
            )
        self._route_handle(normalized)
        missing = tuple(
            name for name in self.required if name not in normalized or _is_blank(normalized[name])
        )
        if missing:
            raise InvalidParam(
                f"missing required parameter(s) for {self.name}: {', '.join(missing)}",
                details={"endpoint": self.name, "missing": list(missing)},
            )
        return normalized

    def _route_handle(self, normalized: dict[str, Any]) -> None:
        """Send an @handle to the parameter that accepts one.

        A TikTok profile link is ``/@handle``, so every URL-shaped lookup
        resolves to a handle rather than to a ``secUid`` - and ``author_id``
        reaches the platform in the ``secUid`` slot. TikTok answers that with
        ``statusCode 10221`` and an empty user, which is a 200 with a plausible
        body, so it classifies as success and surfaces as an author who simply
        has no data. Measured 2026-09-08 on one identity: ``secUid=<secUid>``
        and ``uniqueId=<handle>`` both returned the profile, ``secUid=<handle>``
        returned nothing.

        Only ``author_profile`` accepts a handle; the post list has no such
        parameter, so there the handle is refused by name instead of being sent
        somewhere it cannot work. :mod:`dtk.mcp.routing` has always done this -
        this is the same rule, moved to where every caller passes through.
        """
        value = normalized.get(AUTHOR_ID)
        if self.platform not in HANDLE_PLATFORMS:
            return
        if not isinstance(value, str) or not value or looks_like_sec_uid(value):
            return
        handle = value.lstrip("@")
        if UNIQUE_ID in self.accepts:
            del normalized[AUTHOR_ID]
            normalized[UNIQUE_ID] = handle
            return
        raise InvalidParam(
            f"{self.name} needs the author's stable id, which starts with "
            f"'{SEC_UID_PREFIX}', not the @handle '{handle}'; look the author up "
            f"with {self.platform.value}.author_profile first and pass the id from "
            "its result",
            details={"endpoint": self.name, "field": AUTHOR_ID, "value": handle},
        )

    def platform_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Canonical parameters translated into the adapter's own keywords."""
        canonical = self.canonical_params(params)
        built: dict[str, Any] = {}
        for name, value in canonical.items():
            if name in _ID_PARAMS:
                value = str(value)
            elif name == COUNT:
                value = _as_count(self.name, value)
            elif name == CURSOR:
                value = str(value)
            built[self.arguments[name]] = value
        return built

    # -- results -----------------------------------------------------------

    def cache_ttl(self, config: Config) -> int:
        ttl = config.get(self.cache_ttl_key)
        return max(0, int(ttl))

    def parse(
        self,
        payload: Mapping[str, Any],
        *,
        params: Mapping[str, Any] | None = None,
        fetched_at: datetime | None = None,
    ) -> Any:
        """Turn a decoded upstream payload into the normalized model.

        The parser is a pure function of the payload plus the identifiers the
        payload does not repeat back; both come from the caller's parameters.
        """
        canonical = self.canonical_params(params or {}) if params else {}
        content_id = canonical.get(CONTENT_ID)
        adapter = self.adapter
        when = fetched_at or datetime.now(UTC)

        match self.capability:
            case Capability.CONTENT_DETAIL:
                return adapter.parse_content(payload, fetched_at=when)
            case Capability.AUTHOR_PROFILE:
                return adapter.parse_author(payload)
            case Capability.AUTHOR_POSTS | Capability.AUTHOR_LIKES | Capability.AUTHOR_REPOSTS:
                # All three are a page of posts in the platform's own list
                # envelope - `aweme_list`/`max_cursor` on Douyin, `itemList`/
                # `cursor` on TikTok - so one parser answers for all of them.
                return adapter.parse_author_posts(payload, fetched_at=when)
            case Capability.MIX_POSTS:
                # Was in the group above until Bilibili had mixes: a season's
                # videos come in a folder's envelope, not the space list's.
                return adapter.parse_mix_posts(payload, fetched_at=when)
            case Capability.AUTHOR_FOLLOWERS | Capability.AUTHOR_FOLLOWING:
                # One endpoint answers both, selected by a `scene` parameter, so
                # one parser answers for both too.
                return adapter.parse_author_list(payload)
            case Capability.AUTHOR_COLLECTIONS:
                return adapter.parse_author_collections(payload)
            case Capability.COLLECTION_POSTS | Capability.AUTHOR_BOOKMARKS:
                # Not in the group above: Douyin pages these by `cursor` where
                # author_posts pages by `max_cursor`, so sharing the parser
                # would break on the first folder needing page two. Bookmarks
                # sat in that group while only TikTok had them, where the two
                # parsers are one; measured on Douyin's `listcollection`
                # 2026-10-17, a 16-post account paged 10 + 6 by `cursor`.
                # The cursor that asked goes in: Bilibili's folder answer says
                # only whether there is more, not which page it was.
                cursor = canonical.get(CURSOR)
                return adapter.parse_collection_posts(
                    payload, fetched_at=when, cursor=str(cursor) if cursor is not None else None
                )
            case Capability.CONTENT_PARTS:
                return adapter.parse_content_parts(payload)
            case (
                Capability.POPULAR
                | Capability.POPULAR_WEEKLY
                | Capability.POPULAR_PRECIOUS
                | Capability.RANKING
            ):
                cursor = canonical.get(CURSOR)
                return adapter.parse_popular(
                    payload,
                    board=self.capability.value,
                    fetched_at=when,
                    cursor=str(cursor) if cursor is not None else None,
                )
            case Capability.RELATED_POSTS:
                return adapter.parse_related_posts(payload, fetched_at=when)
            case Capability.MUSIC_DETAIL:
                return adapter.parse_music_detail(payload)
            case Capability.MUSIC_POSTS:
                return adapter.parse_music_posts(payload, fetched_at=when)
            case Capability.HASHTAG_DETAIL:
                return adapter.parse_hashtag_detail(payload)
            case Capability.HASHTAG_POSTS:
                return adapter.parse_hashtag_posts(payload, fetched_at=when)
            case Capability.SEARCH_POSTS:
                return adapter.parse_search_posts(payload, fetched_at=when)
            case Capability.SEARCH_AUTHORS:
                return adapter.parse_search_authors(payload)
            case Capability.AUTHOR_MIXES:
                return adapter.parse_author_mixes(payload)
            case Capability.TRENDING:
                return adapter.parse_trending(payload)
            case Capability.LIVE_ROOM:
                room = canonical.get(ROOM_ID)
                return adapter.parse_live_room(
                    payload, room_id=str(room) if room is not None else None
                )
            case Capability.LIVE_ROOM_BY_ID:
                return adapter.parse_live_room_by_id(payload)
            case Capability.LIVE_STATUS:
                return adapter.parse_live_status(payload, live_ids=_listed(canonical.get(LIVE_IDS)))
            case Capability.LIVE_RANKING:
                return adapter.parse_live_ranking(payload)
            case Capability.COLLECTION_DETAIL:
                return adapter.parse_collection_detail(payload)
            case Capability.COMMENTS:
                return adapter.parse_comments(payload, content_id=content_id)
            case Capability.COMMENT_REPLIES:
                return adapter.parse_comment_replies(
                    payload,
                    content_id=content_id,
                    parent_id=canonical.get(COMMENT_ID),
                )
        raise InvalidParam(  # pragma: no cover - unreachable while Capability is closed
            f"no parser bound for {self.name}", details={"endpoint": self.name}
        )

    def parser(self, params: Mapping[str, Any] | None = None) -> Callable[[Mapping[str, Any]], Any]:
        """A one-argument parse function, as the fetch service expects."""
        bound = dict(params or {})

        def _parse(payload: Mapping[str, Any]) -> Any:
            return self.parse(payload, params=bound)

        return _parse


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """An endpoint plus one caller's parameters, ready for the fetch service."""

    definition: EndpointDefinition
    params: dict[str, Any]
    cache_ttl: int
    parse: Callable[[Mapping[str, Any]], Any]

    @property
    def endpoint(self) -> str:
        return self.definition.name

    @property
    def platform(self) -> Platform:
        return self.definition.platform


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _listed(value: Any) -> tuple[str, ...]:
    """A comma-separated parameter as its builder sent it upstream: trimmed,
    blanks and repeats gone, in order. Not checked here - a list the builder
    refuses fails before there is any payload to parse."""
    if value is None:
        return ()
    parts = (part.strip() for part in str(value).split(","))
    return tuple(dict.fromkeys(part for part in parts if part))


def _as_count(endpoint: str, value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidParam(
            f"count must be an integer for {endpoint}",
            details={"endpoint": endpoint, "count": str(value)},
        ) from None


def _builder_keywords(build: Callable[..., Any] | None) -> frozenset[str]:
    if build is None:
        return frozenset()
    return frozenset(inspect.signature(build).parameters)


def _definition(
    adapter: PlatformAdapter, platform: Platform, capability: Capability
) -> EndpointDefinition | None:
    name = endpoint_name(platform, capability)
    if name not in adapter.endpoints:
        return None
    spec = adapter.endpoints[name]
    arguments = dict(_ARGUMENTS.get(platform, {}).get(capability, {}))
    if not arguments:
        log.warning("worker.registry.unmapped_arguments", endpoint=name)
        return None

    # The argument map is the one place where this table can drift away from a
    # platform package. Checking it against the real builder signature turns
    # that drift into an import-time failure instead of a runtime one.
    keywords = _builder_keywords(spec.build)
    if keywords:
        wrong = sorted(k for k in arguments.values() if k not in keywords)
        if wrong:
            raise RuntimeError(
                f"endpoint registry maps {name} onto unknown builder argument(s): "
                f"{', '.join(wrong)}"
            )

    inverse = {target: canonical for canonical, target in arguments.items()}
    required = tuple(inverse[k] for k in spec.required if k in inverse)
    return EndpointDefinition(
        name=name,
        platform=platform,
        capability=capability,
        required=required,
        accepts=tuple(arguments),
        cache_ttl_key=_CACHE_TTL_KEYS[capability],
        scope=_scope_for(platform),
        risk_weight=spec.risk_weight,
        summary=spec.summary,
        arguments=MappingProxyType(arguments),
        session_bound=spec.session_bound,
    )


def _build() -> dict[str, EndpointDefinition]:
    table: dict[str, EndpointDefinition] = {}
    for name in available_platforms():
        platform = Platform(name)
        adapter = get_adapter(platform)
        for capability in Capability:
            definition = _definition(adapter, platform, capability)
            if definition is not None:
                table[definition.name] = definition
    return table


#: The endpoint table. Built once at import from the platform adapters, so a
#: platform package stays the only place that declares an endpoint exists.
ENDPOINTS: Final[Mapping[str, EndpointDefinition]] = MappingProxyType(_build())


def definition_for(endpoint: str) -> EndpointDefinition:
    try:
        return ENDPOINTS[endpoint]
    except KeyError:
        raise InvalidParam(
            f"unknown endpoint: {endpoint}",
            details={"endpoint": endpoint, "known": sorted(ENDPOINTS)},
        ) from None


def definitions_for(platform: Platform) -> tuple[EndpointDefinition, ...]:
    return tuple(d for d in ENDPOINTS.values() if d.platform is platform)


def endpoint_names() -> tuple[str, ...]:
    return tuple(sorted(ENDPOINTS))


def missing_capabilities(platform: Platform) -> tuple[Capability, ...]:
    """P0 capabilities this platform does not offer. Empty means complete."""
    present = {d.capability for d in definitions_for(platform)}
    return tuple(c for c in P0_CAPABILITIES if c not in present)


def comment_sort_params(platform: Platform, sort: CommentSort | None) -> dict[str, str]:
    """The ``sort`` to send with a comments call on this platform, if any.

    REST and MCP both ask here, so the rule is written once. A platform whose
    comments endpoint takes no order serves its own ranking, which is what
    HOT asks for, so HOT is dropped there; NEW is refused by name, because
    sending nothing would return the hottest comments to a caller who asked
    for the newest and nothing in the page would say so.
    """
    if sort is None:
        return {}
    definition = definition_for(endpoint_name(platform, Capability.COMMENTS))
    if SORT in definition.accepts:
        return {SORT: sort.value}
    if sort is CommentSort.HOT:
        return {}
    supported = sorted(
        d.platform.value
        for d in ENDPOINTS.values()
        if d.capability is Capability.COMMENTS and SORT in d.accepts
    )
    raise InvalidParam(
        f"{platform.value} serves comments in its own order; sort={sort.value} is "
        f"offered on {', '.join(supported)} only",
        details={"field": SORT, "platform": platform.value, "supported": supported},
    )


def resolve(endpoint: str, params: Mapping[str, Any], config: Config) -> ResolvedCall:
    """Bind an endpoint, one caller's parameters and the current config."""
    definition = definition_for(endpoint)
    return ResolvedCall(
        definition=definition,
        params=definition.platform_params(params),
        cache_ttl=definition.cache_ttl(config),
        parse=definition.parser(params),
    )


__all__ = [
    "ALIASES",
    "AUTHOR_ID",
    "BOARD",
    "BOARDS",
    "COMMENT_ID",
    "CONTENT_ID",
    "COUNT",
    "CURSOR",
    "ENDPOINTS",
    "ENVELOPE_PARAMS",
    "HANDLE_PLATFORMS",
    "HASHTAG_ID",
    "HASHTAG_NAME",
    "KEYWORD",
    "LIVE_ID",
    "LIVE_IDS",
    "MUSIC_ID",
    "OPTIONAL_CAPABILITIES",
    "P0_CAPABILITIES",
    "PART",
    "ROOM_ID",
    "SORT",
    "UNIQUE_ID",
    "Capability",
    "EndpointDefinition",
    "ResolvedCall",
    "comment_sort_params",
    "definition_for",
    "definitions_for",
    "endpoint_name",
    "endpoint_names",
    "missing_capabilities",
    "resolve",
]
