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
    URL_QUERY,
    WAIT_QUERY,
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
from dtk.core.types import Platform
from dtk.platforms.paging import max_page_size
from dtk.urls import ResourceKind
from dtk.worker import registry

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
        params: dict[str, Any] = {"name": name.strip().lstrip("#"), "url": None}
    else:
        params = addressed_params(
            platform,
            url=url,
            value=hashtag_id,
            field="hashtag_id",
            resource=ResourceKind.CHALLENGE,
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
    if found is not None and not str(found).isdigit():
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
