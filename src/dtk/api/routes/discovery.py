"""Reads that start somewhere other than one post or one author.

What a platform recommends beside a post, a sound and the posts that use it, a
hashtag and the posts filed under it, a keyword search, an author's own series,
a live room and the trending board. They are the same four steps as
:mod:`dtk.api.routes.content` - authorize, validate, submit, answer - and use
its helpers, so the SSRF chokepoint and the 202 contract described there hold
here unchanged. They live apart only because that module had grown past what
one file should hold.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.content import (
    AWEME_ID_QUERY,
    COUNT_QUERY,
    CURSOR_QUERY,
    EXPLAIN_QUERY,
    IDENTITY_QUERY,
    PLATFORM_PATH,
    PROXY_QUERY,
    RAW_QUERY,
    REFRESH_QUERY,
    SEC_USER_ID_QUERY,
    URL_QUERY,
    WAIT_QUERY,
    author_params,
    authorize,
    content_params,
    identifier,
    supported,
    vet_url,
)
from dtk.api.routes.openapi import ASYNC_RESPONSES, I18N_KEY
from dtk.api.routes.operations import Operation
from dtk.api.routes.support import (
    resolve_count,
    resolve_explain,
    resolve_request_identity,
    resolve_request_proxy,
    resolve_wait,
)
from dtk.core.errors import InvalidParam
from dtk.core.types import Platform, TrendingBoard
from dtk.platforms import get_adapter
from dtk.platforms.paging import max_page_size
from dtk.urls import ResourceKind, UrlKind
from dtk.worker import registry
from dtk.worker.parsing import NAMED_BY_LINK

router = APIRouter(prefix="/api/v1", tags=["content"])

#: Longer than any id either platform issues for a sound or a hashtag, and
#: short enough that nothing else fits.
_ID_MAX_LENGTH = 32

MUSIC_ID_QUERY = Query(
    default=None,
    max_length=_ID_MAX_LENGTH,
    description=(
        "The sound's id, as an alternative to url: the music_id in a post's music block, "
        "or the number a music link ends in. Provide exactly one of the two."
    ),
)

HASHTAG_ID_QUERY = Query(
    default=None,
    max_length=_ID_MAX_LENGTH,
    description=(
        "The hashtag's numeric id, as an alternative to url: the number a Douyin "
        "hashtag link ends in, or the hashtag_id /hashtag returns."
    ),
)
HASHTAG_NAME_QUERY = Query(
    default=None,
    min_length=1,
    max_length=100,
    description=(
        "TikTok only: the hashtag's name, with or without the '#', as an alternative "
        "to url and hashtag_id. Douyin looks a hashtag up by its id alone."
    ),
)

KEYWORD_QUERY = Query(
    min_length=1,
    max_length=100,
    description="What to search for, as you would type it into the platform's search box.",
)

ROOM_ID_QUERY = Query(
    default=None,
    min_length=1,
    max_length=64,
    description=(
        "The room, as an alternative to url: the number in a live.douyin.com or "
        "live.bilibili.com link, or on TikTok the @handle of the account broadcasting."
    ),
)

LIVE_ID_QUERY = Query(
    default=None,
    min_length=1,
    max_length=_ID_MAX_LENGTH,
    description=(
        "Douyin and TikTok: the broadcast's own id, as an alternative to url and room_id - "
        "the live_id a room answer carries, or the number in a Douyin app share link."
    ),
)

LIVE_IDS_QUERY = Query(
    min_length=1,
    # A hundred 19-digit ids and their commas, with room to spare.
    max_length=2400,
    description=(
        "The broadcasts to check, comma-separated: live_id values from /live, up to 100 at a time."
    ),
)

TRENDING_BOARD_QUERY = Query(
    default=TrendingBoard.HOT,
    description=(
        "Which board: hot (the default), entertainment, society or challenge. Douyin has "
        "all four; Bilibili has hot alone and refuses the others."
    ),
)

#: Platforms whose live room is named by its owner's @handle rather than
#: numbered: a TikTok live link is ``/@<handle>/live`` and carries nothing else.
_ROOMS_BY_HANDLE = frozenset({Platform.TIKTOK})


def _numeric_id(value: str, *, field: str) -> str:
    """An id both platforms issue as a run of digits, checked before it queues."""
    text = value.strip()
    if not text.isdigit():
        raise InvalidParam(
            f"{field} must be the numeric id the platform issued",
            details={"field": field, "value": text[:_ID_MAX_LENGTH]},
        )
    return text


def addressed_params(
    platform: Platform,
    *,
    url: str | None,
    value: str | None,
    field: str,
    resource: ResourceKind,
) -> dict[str, Any]:
    """A link or an id for something that is not a post or an author.

    The shape :func:`dtk.api.routes.content.content_params` gives a post: the
    id read out of a full link here, so two spellings of one sound coalesce on
    one task, and a short link forwarded for the worker to expand. A link to
    the wrong kind of page goes on too, and is refused by name there
    (``dtk.worker.parsing.complete``) once it is known what it points at.
    """
    if not url and not value:
        raise InvalidParam(f"provide either url or {field}", details={"fields": ["url", field]})
    if value:
        return {field: _numeric_id(value, field=field), "url": None}
    kind = vet_url(str(url), expected=platform)
    extracted = identifier(kind, resource)
    return {field: extracted, "url": None if extracted else kind.url}


@router.get(
    "/{platform}/video/related",
    summary="Posts recommended beside a post",
    openapi_extra={I18N_KEY: "related_posts", **ASYNC_RESPONSES},
)
async def video_related(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    aweme_id: str | None = AWEME_ID_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """The posts the platform recommends beside one post, as one batch.

    Identify the post by **either** `url` **or** `aweme_id`. What comes back is
    what the platform's own player lists next to the post: a recommendation,
    so the same request can answer differently a minute later.

    All three platforms answer a post id that does not exist with a full batch
    of unrelated posts, so an answer here is no evidence that the post exists;
    `/video` is the call that says so.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`.
    - `url` - a link to the post.
    - `aweme_id` - the post id, if you already have it: a Bilibili bvid on
      Bilibili.
    - `count` - how many to ask for. Douyin answers at most 10 whatever is
      asked and Bilibili always 40; TikTok returns about as many as asked, up
      to 35.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. A repeat inside `cache.list_ttl` (5 minutes by
      default) is answered from the cache.

    **Returns**

    A page of posts in the same shape as `/user/posts`. It is always the last
    page: none of the three pages this list in a way a caller could follow.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.RELATED_POSTS)
    params = content_params(platform, url=url, aweme_id=aweme_id)
    params["count"] = resolve_count(count, maximum=max_page_size(platform))
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/music",
    summary="A sound",
    openapi_extra={I18N_KEY: "music_detail", **ASYNC_RESPONSES},
)
async def music(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    music_id: str | None = MUSIC_ID_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One sound: the audio a post is made with, on its own page.

    Identify it by **either** `url` **or** `music_id`. The id is the
    `music.music_id` of any post that uses the sound.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Bilibili answers `UNSUPPORTED_CONTENT`.
    - `url` - a link to the sound's page.
    - `music_id` - the sound's id.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      `/video`. A repeat inside `cache.author_ttl` (15 minutes by default) is
      answered from the cache.

    **Returns**

    Title, author name, length, a link to the audio and a cover, as a post's
    `music` block carries them, plus what only the sound's page knows: how
    many posts use it (`content_count`), whether it is a post's own audio
    rather than a library track (`is_original`), and the account that
    published it (`owner`), when the platform names one. A sound that does not
    exist is `NOT_FOUND`.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.MUSIC_DETAIL)
    params = addressed_params(
        platform, url=url, value=music_id, field="music_id", resource=ResourceKind.MUSIC
    )
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/music/posts",
    summary="Posts that use a sound",
    openapi_extra={I18N_KEY: "music_posts", **ASYNC_RESPONSES},
)
async def music_posts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    music_id: str | None = MUSIC_ID_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the posts made with a sound, in the platform's own order.

    Identify the sound by **either** `url` **or** `music_id`. A guest identity
    pages through these on both platforms; no login is needed.

    **Parameters**

    - `platform` - `douyin` or `tiktok`.
    - `url`, `music_id` - as on `/music`.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page. Douyin answers about 13 whatever is asked;
      TikTok refuses more than 35.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. A repeat inside `cache.list_ttl` (5 minutes by
      default) is answered from the cache.

    **Returns**

    A page of posts in the same shape as `/user/posts`, plus the cursor for
    the next page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.MUSIC_POSTS)
    params = addressed_params(
        platform, url=url, value=music_id, field="music_id", resource=ResourceKind.MUSIC
    )
    params["cursor"] = cursor
    params["count"] = resolve_count(count, maximum=max_page_size(platform))
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/hashtag",
    summary="A hashtag",
    openapi_extra={I18N_KEY: "hashtag_detail", **ASYNC_RESPONSES},
)
async def hashtag(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    hashtag_id: str | None = HASHTAG_ID_QUERY,
    name: str | None = HASHTAG_NAME_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One hashtag's page: its name, cover and counts.

    Identify it by **one** of `url`, `hashtag_id` or, on TikTok, `name`. A
    TikTok tag link names the hashtag (`/tag/<name>`); a Douyin hashtag link
    numbers it (`/hashtag/<id>`).

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Bilibili answers `UNSUPPORTED_CONTENT`.
    - `url` - a link to the hashtag's page.
    - `hashtag_id` - the hashtag's numeric id.
    - `name` - TikTok only: the hashtag's name.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      `/video`. A repeat inside `cache.author_ttl` (15 minutes by default) is
      answered from the cache.

    **Returns**

    `hashtag_id`, `name`, `description`, `cover`, `web_url`, and the counts
    the platform states: `content_count` (posts filed under it) and
    `view_count`. The `hashtag_id` is what `/hashtag/posts` takes. A hashtag
    that does not exist is `NOT_FOUND`.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.HASHTAG_DETAIL)
    if name is not None:
        if registry.HASHTAG_NAME not in registry.ENDPOINTS[endpoint].accepts:
            raise InvalidParam(
                f"{platform.value} looks a hashtag up by its id; pass hashtag_id or a link",
                details={"field": "name", "platform": platform.value},
            )
        if url or hashtag_id:
            raise InvalidParam(
                "provide one of url, hashtag_id or name",
                details={"fields": ["url", "hashtag_id", "name"]},
            )
        bare = name.strip().lstrip("#").strip()
        if not bare:
            raise InvalidParam(
                "name is a hashtag's name, not just a '#'", details={"field": "name"}
            )
        params: dict[str, Any] = {"name": bare, "url": None}
    else:
        params = addressed_params(
            platform,
            url=url,
            value=hashtag_id,
            field="hashtag_id",
            resource=ResourceKind.CHALLENGE,
        )
        named = NAMED_BY_LINK.get((platform, ResourceKind.CHALLENGE))
        if named is not None and hashtag_id is None and params.get("hashtag_id"):
            # A TikTok tag link names its hashtag; see NAMED_BY_LINK for what
            # reading it as a number did to #2024.
            params[named] = params.pop("hashtag_id")
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/hashtag/posts",
    summary="Posts under a hashtag",
    openapi_extra={I18N_KEY: "hashtag_posts", **ASYNC_RESPONSES},
)
async def hashtag_posts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    hashtag_id: str | None = HASHTAG_ID_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the posts filed under a hashtag. TikTok only, for now.

    Identify the hashtag by **either** `url` **or** `hashtag_id`. The list is
    keyed by the numeric id: a tag link carries only the name, so look it up
    with `/hashtag` first and pass the `hashtag_id` it returns.

    Douyin answers this list to a guest identity with nothing at all, and the
    signed-in answer has not been measured, so it is not offered there yet
    and answers `UNSUPPORTED_CONTENT`.

    **Parameters**

    - `platform` - `tiktok`.
    - `url` - a link that carries the id.
    - `hashtag_id` - the hashtag's numeric id.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page, up to 35.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. A repeat inside `cache.list_ttl` (5 minutes by
      default) is answered from the cache.

    **Returns**

    A page of posts in the same shape as `/user/posts`, plus the cursor for
    the next page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.HASHTAG_POSTS)
    params = addressed_params(
        platform, url=url, value=hashtag_id, field="hashtag_id", resource=ResourceKind.CHALLENGE
    )
    found = params.get("hashtag_id")
    if (
        hashtag_id is None
        and found is not None
        and (platform, ResourceKind.CHALLENGE) in (NAMED_BY_LINK)
    ):
        raise InvalidParam(
            "that link names the hashtag rather than numbering it; look it up with "
            "/hashtag and pass the hashtag_id it returns",
            details={"field": "url", "name": found, "hint": "GET /hashtag, then hashtag_id"},
        )
    params["cursor"] = cursor
    params["count"] = resolve_count(count, maximum=max_page_size(platform))
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/mixes",
    summary="An author's own series",
    openapi_extra={I18N_KEY: "author_mixes", **ASYNC_RESPONSES},
)
async def user_mixes(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    sec_user_id: str | None = SEC_USER_ID_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the series an author has published.

    Douyin calls them mixes, TikTok playlists and Bilibili seasons; each is
    a set of the author's own posts in order, and its `mix_id` is what
    `/mix/posts` takes. A guest identity reads all three.

    Bilibili's space lists its older "series" beside the seasons. A series is
    a different id that `/mix/posts` cannot read, so only the seasons come
    back; the paging still counts the series, so the last page can be empty.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`.
    - `url` - a link to the author's profile.
    - `sec_user_id` - the author's stable id: `secUid` on TikTok, the numeric
      `mid` on Bilibili. A TikTok link names an @handle, which is looked up
      first.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - series per page.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. A repeat inside `cache.list_ttl` (5 minutes by
      default) is answered from the cache.

    **Returns**

    `mix_id`, `name`, `description`, `cover`, `item_count`, `play_count`
    where the platform counts plays, `web_url`, and the owner's id and name.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.AUTHOR_MIXES)
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params["cursor"] = cursor
    params["count"] = resolve_count(count, maximum=max_page_size(platform))
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/live",
    summary="A live room",
    openapi_extra={I18N_KEY: "live_room", **ASYNC_RESPONSES},
)
async def live(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    room_id: str | None = ROOM_ID_QUERY,
    live_id: str | None = LIVE_ID_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One live room: whether it is broadcasting, and how to play it if it is.

    Identify it by **one** of `url`, `room_id` or `live_id`. A room is
    addressed the way its link addresses it: Douyin's
    `live.douyin.com/<web_rid>`, TikTok's `/@<handle>/live`, Bilibili's
    `live.bilibili.com/<room>`. A Douyin app share link names the broadcast
    instead, by its own id, and is answered the same way: so is `live_id`,
    the id a room answer hands back while the room is on.

    This reads a room; it does not record one. The pull URLs it returns are
    signed and expire within hours, and they go to a player or a recorder of
    your own, straight from the platform's CDN.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`.
    - `url` - a link to the room.
    - `room_id` - the number in a Douyin or Bilibili live link, or the
      TikTok account's @handle.
    - `live_id` - Douyin and TikTok: the broadcast's own id, a new one each
      time the room goes on air. Bilibili numbers a room once and has none.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      `/video`. A repeat inside `cache.list_ttl` (5 minutes by default) is
      answered from the cache; pass `refresh` to see whether a room has just
      gone on or off.

    **Returns**

    `room_id`, `is_live`, `title`, `cover`, `web_url`, the broadcaster as
    `author`, and while the room is live `live_id`, `viewer_count`,
    `started_at` where the platform states it, and `streams`: one entry per
    protocol (`flv` or `hls`) and quality, in the platform's own quality
    names. However the room was asked for, `room_id` is what its web link
    carries. Bilibili's `viewer_count` is the "watched" figure it prints, not
    who is in the room now. A room nobody has is `NOT_FOUND`.
    """
    authorize(principal, platform)
    if not url and not room_id and not live_id:
        raise InvalidParam(
            "provide one of url, room_id or live_id",
            details={"fields": ["url", "room_id", "live_id"]},
        )
    # An id the caller names wins over a link, as on every other route.
    kind = vet_url(str(url), expected=platform) if url and not room_id and not live_id else None
    params: dict[str, Any]
    if live_id:
        endpoint = supported(platform, Operation.LIVE_ROOM_BY_ID)
        params = {"live_id": _numeric_id(live_id, field="live_id"), "url": None}
    elif kind is not None and kind.resource is ResourceKind.LIVE_ROOM:
        # A reflow link names the broadcast, and the id is in the link, so it
        # goes to the endpoint that takes one without a hop.
        endpoint = supported(platform, Operation.LIVE_ROOM_BY_ID)
        params = {"live_id": identifier(kind, ResourceKind.LIVE_ROOM), "url": None}
    else:
        endpoint = supported(platform, Operation.LIVE_ROOM)
        params = _live_room_params(platform, kind=kind, room_id=room_id)
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


def _live_room_params(
    platform: Platform, *, kind: UrlKind | None, room_id: str | None
) -> dict[str, Any]:
    """A room by its link or by what the link carries: ``/live`` as it was."""
    if room_id or kind is None:
        room = (room_id or "").strip().lstrip("@").strip()
        if platform not in _ROOMS_BY_HANDLE and not room.isdigit():
            raise InvalidParam(
                f"a {platform.value} live room is the number in its link",
                details={"field": "room_id", "value": room[:_ID_MAX_LENGTH]},
            )
        if not room:
            raise InvalidParam("room_id is empty", details={"field": "room_id"})
        return {"room_id": room, "url": None}
    found = identifier(kind, ResourceKind.LIVE)
    return {"room_id": found, "url": None if found else kind.url}


@router.get(
    "/{platform}/live/status",
    summary="Whether live rooms are on air",
    openapi_extra={I18N_KEY: "live_status", **ASYNC_RESPONSES},
)
async def live_status(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    live_ids: str = LIVE_IDS_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Whether each of up to 100 broadcasts is still on air, in one call.

    For watching a list of rooms: ask this on a timer, and `/live` for the
    one that has just come on. It costs one request however many are asked
    about, where `/live` costs one each.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Bilibili answers `UNSUPPORTED_CONTENT`.
    - `live_ids` - the broadcasts, comma-separated: the `live_id` a `/live`
      answer carries while its room is on. Repeats are asked about once.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      `/live`. A repeat inside `cache.list_ttl` (5 minutes by default) is
      answered from the cache; pass `refresh` for a fresh answer.

    **Returns**

    One page with a `{live_id, is_live}` for each broadcast, in the order
    asked, so it pairs with the list by position. A broadcast that has ended
    and an id that was never one are both `is_live: false`.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.LIVE_STATUS)
    # The builder holds the list to its rules - numbers only, a hundred at
    # most - and asking it here makes a bad list a 400 rather than a failed
    # task. Its answer is the list with repeats gone, so two spellings of one
    # list coalesce on one task.
    built = get_adapter(platform).endpoints[endpoint].build_params(room_ids=live_ids)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params={"live_ids": built["room_ids"], "include_raw": include_raw},
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/live/ranking",
    summary="A live room's audience ranking",
    openapi_extra={I18N_KEY: "live_ranking", **ASYNC_RESPONSES},
)
async def live_ranking(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    live_id: str = Query(
        min_length=1,
        max_length=_ID_MAX_LENGTH,
        description="The broadcast: the live_id a /live answer carries while its room is on.",
    ),
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """The audience ranking of one broadcast: the list beside the stream.

    Up to 200 viewers, top first, in the order the room's own page lists
    them. A guest is told the order and not the scores behind it, so none
    are returned.

    **Parameters**

    - `platform` - `douyin`. TikTok and Bilibili answer `UNSUPPORTED_CONTENT`.
    - `live_id` - the broadcast: the `live_id` a `/live` answer carries while
      its room is on air.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      `/live`. A repeat inside `cache.list_ttl` (5 minutes by default) is
      answered from the cache.

    **Returns**

    One page with a `{rank, author}` per place. `author` is null for a
    viewer who chose not to be listed - the platform still sends their
    account, and it is not passed on - and for one the platform names to a
    guest only by a masked nickname. A broadcast nobody has is `NOT_FOUND`.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.LIVE_RANKING)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params={"live_id": _numeric_id(live_id, field="live_id"), "include_raw": include_raw},
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/trending",
    summary="What people are searching for",
    openapi_extra={I18N_KEY: "trending", **ASYNC_RESPONSES},
)
async def trending(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    board: TrendingBoard = TRENDING_BOARD_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """A trending board: the phrases people are searching for, ranked.

    Words, not posts - each line's `web_url` is the platform's own page for
    it, and `/search/posts` finds the posts behind it where the platform
    offers search.

    **Parameters**

    - `platform` - `douyin` or `bilibili`. TikTok answers `UNSUPPORTED_CONTENT`.
    - `board` - Douyin's four: `hot`, `entertainment`, `society`,
      `challenge`. Bilibili's hot search is `hot`, the default.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. The boards are the same for everyone, so a
      repeat inside `cache.list_ttl` (5 minutes by default) is free.

    **Returns**

    One page with the whole board: each line's `word`, `rank` (null for a
    line pinned above the ranking), `hot_value` in the platform's own units,
    `content_count` where the board counts posts, `cover` and `web_url`.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.TRENDING)
    # The builder knows which boards its platform has, and asking it here
    # turns Bilibili's `board=society` into a 400 rather than a failed task.
    get_adapter(platform).endpoints[endpoint].build_params(board=board.value)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params={"board": board.value, "include_raw": include_raw},
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


async def _search(
    request: Request,
    principal: Principal,
    *,
    platform: Platform,
    operation: Operation,
    keyword: str,
    cursor: str | None,
    count: int | None,
    include_raw: bool,
    wait: float | None,
    proxy: str | None,
    identity: str | None,
    refresh: bool,
    explain: bool,
) -> Any:
    authorize(principal, platform)
    endpoint = supported(platform, operation)
    text = keyword.strip()
    if not text:
        raise InvalidParam("a search needs a keyword", details={"field": "keyword"})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params={
            "keyword": text,
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        },
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/search/posts",
    summary="Search posts",
    openapi_extra={I18N_KEY: "search_posts", **ASYNC_RESPONSES},
)
async def search_posts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    keyword: str = KEYWORD_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the posts a keyword finds, in the platform's own ranking.

    **What a guest identity gets differs by platform**, and the capability
    table (`GET /api/v1/system/capabilities`) says so per endpoint:

    - **Bilibili** pages its video search for a guest, up to the 50 pages it
      ever offers.
    - **TikTok** gives a guest a thin first page and nothing after it, so a
      signed-in identity is used when the pool has one and is needed for every
      later page.
    - **Douyin** refuses search to a guest by name (`status_code 2483`) and
      the signed-in answer has not been measured, so it is not offered yet and
      answers `UNSUPPORTED_CONTENT`.

    Search is what platforms watch most closely, so these endpoints draw from
    the slowest bucket the scheduler has.

    **Parameters**

    - `platform` - `tiktok` or `bilibili`.
    - `keyword` - what to search for.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page: up to 35 on TikTok, 20 on Bilibili.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. A repeat inside `cache.list_ttl` (5 minutes by
      default) is answered from the cache.

    **Returns**

    A page of posts in the same shape as `/user/posts`, plus the cursor for
    the next page. A Bilibili hit is what the search page shows of a video -
    title, uploader, counts and cover, with the highlighting taken out - and
    no stream; `/video` has that.
    """
    return await _search(
        request,
        principal,
        platform=platform,
        operation=Operation.SEARCH_POSTS,
        keyword=keyword,
        cursor=cursor,
        count=count,
        include_raw=include_raw,
        wait=wait,
        proxy=proxy,
        identity=identity,
        refresh=refresh,
        explain=explain,
    )


@router.get(
    "/{platform}/search/users",
    summary="Search accounts",
    openapi_extra={I18N_KEY: "search_authors", **ASYNC_RESPONSES},
)
async def search_users(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    keyword: str = KEYWORD_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the accounts a keyword finds.

    Bilibili pages it for a guest identity; TikTok answers a guest's first
    page in full and no later one, and answers ten a page whatever `count`
    asks. Douyin is not offered, for the reason `/search/posts` gives.

    **Parameters**

    - `platform` - `tiktok` or `bilibili`.
    - `keyword`, `cursor`, `count`, `include_raw`, `wait`, `proxy`, `identity`,
      `refresh`, `explain` - as on `/search/posts`.

    **Returns**

    A page of authors in the same shape as `/user/followers`: each carries the
    id the author endpoints take (`sec_uid`), and the follower and post
    counts the search states.
    """
    return await _search(
        request,
        principal,
        platform=platform,
        operation=Operation.SEARCH_AUTHORS,
        keyword=keyword,
        cursor=cursor,
        count=count,
        include_raw=include_raw,
        wait=wait,
        proxy=proxy,
        identity=identity,
        refresh=refresh,
        explain=explain,
    )


__all__ = ["addressed_params", "router"]
