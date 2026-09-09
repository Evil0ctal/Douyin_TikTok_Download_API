"""Public data endpoints.

Every one of them does the same four things: authorize, validate, submit,
answer. The upstream request happens in a worker; this module never opens a
socket to a platform (doc 01).

Two rules are enforced here and nowhere else:

* **Every caller-supplied URL passes through :mod:`dtk.urls` before anything
  else happens.** That is the SSRF chokepoint from doc 08 - host allowlist,
  no userinfo, no odd port, and a hard rejection of loopback, private and
  link-local targets. Short links are re-validated after expansion, which
  happens in the worker because following one is a network call.
* **Submitting returns 202.** The scheduler may have no identity free at any
  moment, so an endpoint promising an immediate answer would hold connections
  open exactly when the workers are needed elsewhere (doc 06). ``?wait=`` moves
  the polling loop to the server for clients that cannot poll, capped by
  ``api.max_wait_seconds``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request

from dtk.api import request_proxy
from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.openapi import ACCEPTED_RESPONSES, ASYNC_RESPONSES, I18N_KEY
from dtk.api.routes.operations import Operation, endpoint_name
from dtk.api.routes.schemas import BatchRequest, ParseRequest
from dtk.api.routes.support import (
    MAX_PAGE_SIZE,
    has_scope,
    language,
    ok,
    resolve_count,
    resolve_request_identity,
    resolve_request_proxy,
    resolve_wait,
    validate_callback_url,
)
from dtk.core.errors import (
    DtkError,
    ForbiddenScope,
    InvalidParam,
    InvalidUrl,
    UnsupportedContent,
)
from dtk.core.logging import get_logger
from dtk.core.types import Platform, Scope
from dtk.i18n.messages import render_error
from dtk.urls import ResourceKind, UrlKind, first_url, identify
from dtk.worker import registry

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["content"])

WAIT_QUERY = Query(
    default=None,
    ge=0,
    description="Seconds to wait for the result before answering 202 with a task id.",
)
COUNT_QUERY = Query(default=None, ge=1, le=MAX_PAGE_SIZE, description="Items per page.")
CURSOR_QUERY = Query(
    default=None,
    max_length=512,
    description="Opaque cursor from the previous page; omit for the first page.",
)
RAW_QUERY = Query(default=False, description="Include the untouched platform payload.")
PROXY_QUERY = Query(
    default=None,
    max_length=request_proxy.MAX_LENGTH,
    description=(
        "Send the upstream request through this proxy, as a full URL. Refused "
        "unless an administrator has enabled security.request_proxy."
    ),
)
REFRESH_QUERY = Query(
    default=False,
    description=(
        "Ignore any cached or in-flight answer and make the request again. Two "
        "things make a repeat return the same thing - this instance joins an "
        "identical task that is already running or recently finished, and it "
        "caches the shaped body - and this turns off both. The fresh answer is "
        "still cached. It costs an identity and an upstream request, so it is "
        "for checking whether something changed, not for every call. How long "
        "the cached answer lives depends on what was asked for and is set in "
        "the console: cache.content_ttl for one post (30 minutes by default), "
        "cache.author_ttl for a profile (15 minutes), cache.list_ttl for "
        "anything paged (5 minutes). Entries expire on their own and Redis is "
        "capped below its container limit, dropping the least recently used "
        "expiring keys before it fills, so the cache cannot grow without bound."
    ),
)
IDENTITY_QUERY = Query(
    default=None,
    max_length=36,
    description=(
        "Send the request as this identity and no other, by id. For content only "
        "that account can see, such as your own private posts, fetched with a jar "
        "you imported from your own browser. Requires identity:manage; the request "
        "is never served from another identity and never from the response cache."
    ),
)
PLATFORM_PATH = Path(description="Platform the request is addressed to: douyin or tiktok.")
URL_QUERY = Query(
    default=None,
    max_length=4096,
    description=(
        "A link to the resource. Text with a link inside it is accepted, so clipboard "
        "content from the apps can be sent unedited."
    ),
)
AWEME_ID_QUERY = Query(
    default=None,
    max_length=64,
    description="The post id, as an alternative to url. Provide exactly one of the two.",
)
SEC_USER_ID_QUERY = Query(
    default=None,
    max_length=256,
    description=("The author's stable id, as an alternative to url. On TikTok this is secUid."),
)
MIX_ID_QUERY = Query(
    min_length=1,
    max_length=64,
    description="The mix or playlist to read; Douyin calls it mix_info, TikTok playlistId.",
)
COMMENT_ID_QUERY = Query(
    min_length=1,
    max_length=64,
    description="The parent comment whose replies you want; ids come from the comments endpoint.",
)


def read_scope(platform: Platform) -> Scope:
    """The scope a key needs to read one platform.

    Derived from the platform name so a new platform needs no change here. An
    unknown platform falls back to ``admin``: failing closed is the only safe
    default for an authorization decision.
    """
    try:
        return Scope(f"{platform.value}:read")
    except ValueError:
        return Scope.ADMIN


def supported(platform: Platform, operation: Operation) -> str:
    """The endpoint name, once this platform is known to serve this operation.

    The platforms are not symmetric past the core five, and the difference is
    not a bug to be papered over: Douyin answers "not signed in" for a follow
    graph and returns nothing for an author's likes, both to a healthy guest
    identity. A route that submitted the task anyway would answer with an empty
    page and let the caller conclude the author has no followers.
    """
    name = endpoint_name(platform, operation)
    if name not in registry.ENDPOINTS:
        raise UnsupportedContent(
            f"{platform.value} does not offer {operation.value}",
            details={
                "platform": platform.value,
                "operation": operation.value,
                "supported": sorted(
                    p.value for p in Platform if endpoint_name(p, operation) in registry.ENDPOINTS
                ),
            },
        )
    return name


def authorize(principal: Principal, platform: Platform) -> None:
    if not has_scope(principal, (read_scope(platform),)):
        raise ForbiddenScope(
            "this credential lacks the scope required for this platform",
            details={"required": [read_scope(platform).value]},
        )


def vet_url(url: str, *, expected: Platform | None = None) -> UrlKind:
    """Run one caller-supplied URL through the allowlist.

    Returns the classification rather than a bare boolean so the caller can use
    the extracted id and skip a round trip. An unrecognized resource is still
    accepted when the host is allowed - a link shape we do not know yet is the
    worker's problem to report, not a security failure - but a disallowed host
    never gets past this function.

    Input that is not a bare link gets one second pass: the platform apps put a
    caption, a numeric code and the link on the clipboard together, which is
    what an iOS Shortcut and a paste into the console actually send, and both
    :mod:`dtk.cli` and :mod:`dtk.mcp` already accept it. The extracted
    candidate goes back through :func:`identify`, so the allowlist remains the
    only thing that decides what may be fetched.
    """
    kind = identify(url)
    if not kind.allowed:
        candidate = first_url(url)
        if candidate is not None:
            kind = identify(candidate)
    if not kind.allowed:
        raise InvalidUrl(
            "not a supported Douyin or TikTok URL",
            details={"reason": "host_not_allowed"},
        )
    if expected is not None and kind.platform is not None and kind.platform is not expected:
        raise InvalidUrl(
            "this URL belongs to a different platform than the endpoint addressed",
            details={
                "reason": "platform_mismatch",
                "url_platform": kind.platform.value,
                "endpoint_platform": expected.value,
            },
        )
    return kind


def _identifier(kind: UrlKind, wanted: ResourceKind) -> str | None:
    """The resource id from a URL, when it is already the right kind.

    A short link yields nothing here on purpose: its target is unknowable
    before it is followed, so the worker expands it and re-validates.
    """
    if kind.needs_expansion or kind.resource is ResourceKind.SHORT_LINK:
        return None
    if kind.resource is wanted:
        return kind.resource_id
    return None


# --------------------------------------------------------------------------
# Platform-agnostic entry points
# --------------------------------------------------------------------------


@router.post(
    "/parse",
    summary="Parse any supported link",
    openapi_extra={I18N_KEY: "parse", **ASYNC_RESPONSES},
)
async def parse(
    request: Request,
    body: ParseRequest,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """The front door: hand it a link or the share text around one.

    Accepts either platform, so it requires read access to one of them; the
    worker decides which after expanding the URL.
    """
    if not has_scope(principal, (Scope.DOUYIN_READ, Scope.TIKTOK_READ)):
        raise ForbiddenScope(
            "this credential lacks read access to any platform",
            details={"required": [Scope.DOUYIN_READ.value, Scope.TIKTOK_READ.value]},
        )

    kind = vet_url(body.url)
    if kind.platform is not None:
        authorize(principal, kind.platform)
    callback = validate_callback_url(request, body.callback_url)

    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=Operation.PARSE.value,
        params={
            "url": kind.url,
            "include_raw": body.include_raw,
            "callback_url": callback,
        },
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        # The platform comes from the link rather than from the path here, and
        # is None for a short link that has not been expanded yet. The pin is
        # still checked for existence and retirement; the platform check falls
        # to the scheduler, which cannot see an identity of the wrong platform
        # in the first place.
        identity=await resolve_request_identity(
            request, principal, identity, platform=kind.platform
        ),
    )


@router.post(
    "/tasks/batch",
    summary="Submit many links at once",
    openapi_extra={I18N_KEY: "batch", **ACCEPTED_RESPONSES},
)
async def batch(
    request: Request,
    body: BatchRequest,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One round trip, N independent tasks.

    Submission is batched, tracking is not: each item gets its own task id and
    its own fate, so one bad link cannot smear the whole request into a single
    error the way a synchronous bulk endpoint would (doc 07).
    """
    if not has_scope(principal, (Scope.DOUYIN_READ, Scope.TIKTOK_READ)):
        raise ForbiddenScope(
            "this credential lacks read access to any platform",
            details={"required": [Scope.DOUYIN_READ.value, Scope.TIKTOK_READ.value]},
        )
    callback = validate_callback_url(request, body.callback_url)
    # Vetted once for the whole batch rather than per item: the answer cannot
    # differ between items, and checking N times would log N acceptances. The
    # pin is checked without a platform for the same reason it is on /parse -
    # each item names its own link, and they need not agree.
    egress = resolve_request_proxy(request, proxy)
    pinned = await resolve_request_identity(request, principal, identity)

    results: list[dict[str, Any]] = []
    accepted = 0
    for item in body.items:
        try:
            kind = vet_url(item.url)
            if kind.platform is not None:
                authorize(principal, kind.platform)
            task_id, state = await operations.submit(
                request,
                principal,
                endpoint=Operation.PARSE.value,
                params={
                    "url": kind.url,
                    "include_raw": item.include_raw,
                    "callback_url": callback,
                    "proxy": egress,
                    "identity": pinned,
                },
            )
        except DtkError as exc:
            results.append(
                {
                    "url": item.url,
                    "task_id": None,
                    # Rendered in the caller's language, like every other error
                    # this API returns. Batch was the one place that shipped a
                    # bare code: the console's own BatchItemResult declares
                    # `message?` and fell back to a generic sentence, and the
                    # endpoint's `lang` parameter changed nothing.
                    "error": {
                        "code": exc.code.value,
                        "message": render_error(exc, language(request)),
                        "details": exc.details or None,
                    },
                }
            )
            continue
        accepted += 1
        results.append({"url": item.url, "task_id": str(task_id), "state": state.value})

    log.info("api.batch_submitted", submitted=accepted, rejected=len(results) - accepted)
    return ok(
        request,
        {"items": results, "submitted": accepted, "rejected": len(results) - accepted},
        status_code=202,
    )


# --------------------------------------------------------------------------
# Per-platform endpoints
# --------------------------------------------------------------------------


@router.get(
    "/{platform}/video",
    summary="One post, video or image album",
    openapi_extra={I18N_KEY: "content_detail", **ASYNC_RESPONSES},
)
async def video(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    aweme_id: str | None = AWEME_ID_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One post: a video, or an image album, with its author and statistics.

    Identify the post by **either** `url` **or** `aweme_id` - exactly one is
    required. A share link works, including the shortened `v.douyin.com` and
    `vm.tiktok.com` forms, and a link that carries the id needs no id.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Must match the link you pass.
    - `url` - a link to the post. Text with a link inside it is accepted, so
      the clipboard content the apps produce can be sent unedited.
    - `aweme_id` - the post id, if you already have it.
    - `include_raw` - also return the platform's own untouched payload.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.content_ttl` (30 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The post's media URLs, cover, caption, statistics, author and timestamps,
    normalized to the same shape for both platforms.
    """
    authorize(principal, platform)
    params = _content_params(platform, url=url, aweme_id=aweme_id)
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.CONTENT_DETAIL),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/video/comments",
    summary="Top level comments on a post",
    openapi_extra={I18N_KEY: "comments", **ASYNC_RESPONSES},
)
async def comments(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    aweme_id: str | None = AWEME_ID_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of top level comments on a post.

    Identify the post by **either** `url` **or** `aweme_id`. To read the
    replies under a comment, use `/video/comments/replies`.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Must match the link you pass.
    - `url` - a link to the post.
    - `aweme_id` - the post id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - comments per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    Comment text, author, like count, reply count and timestamp, plus the
    cursor for the next page.
    """
    authorize(principal, platform)
    params = _content_params(platform, url=url, aweme_id=aweme_id)
    params.update({"cursor": cursor, "count": resolve_count(count), "include_raw": include_raw})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.COMMENTS),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/video/comments/replies",
    summary="Replies under one comment",
    openapi_extra={I18N_KEY: "comment_replies", **ASYNC_RESPONSES},
)
async def comment_replies(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    comment_id: str = COMMENT_ID_QUERY,
    url: str | None = URL_QUERY,
    aweme_id: str | None = AWEME_ID_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of replies underneath a single comment.

    Both the comment and the post it belongs to are required: pass
    `comment_id` together with **either** `url` **or** `aweme_id`. Comment ids
    come from `/video/comments`.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Must match the link you pass.
    - `comment_id` - the parent comment to read replies under.
    - `url` - a link to the post the comment is on.
    - `aweme_id` - that post's id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - replies per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The same comment shape as `/video/comments`, plus the cursor for the next
    page.
    """
    authorize(principal, platform)
    params = _content_params(platform, url=url, aweme_id=aweme_id)
    params.update(
        {
            "comment_id": comment_id,
            "cursor": cursor,
            "count": resolve_count(count),
            "include_raw": include_raw,
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.COMMENT_REPLIES),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user",
    summary="Author profile",
    openapi_extra={I18N_KEY: "author_profile", **ASYNC_RESPONSES},
)
async def user(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    sec_user_id: str | None = SEC_USER_ID_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One author's public profile.

    Identify the author by **either** `url` **or** `sec_user_id` - exactly one
    is required. A profile link is enough; the id is read out of it.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Must match the link you pass.
    - `url` - a link to the author's profile page.
    - `sec_user_id` - the author's stable id, if you already have it. On
      TikTok this is `secUid`.
    - `include_raw` - also return the platform's own untouched payload.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.author_ttl` (15 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    Nickname, signature, avatar, verification state, and follower, following,
    like and post counts.
    """
    authorize(principal, platform)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.AUTHOR_PROFILE),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/posts",
    summary="Author post list",
    openapi_extra={I18N_KEY: "author_posts", **ASYNC_RESPONSES},
)
async def user_posts(
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
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of an author's own posts, newest first.

    Identify the author by **either** `url` **or** `sec_user_id`. Page through
    the feed with `cursor`.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Must match the link you pass.
    - `url` - a link to the author's profile page.
    - `sec_user_id` - the author's stable id, if you already have it. On
      TikTok this is `secUid`.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The same post shape as `/video`, one entry per post, plus the cursor for
    the next page.
    """
    authorize(principal, platform)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update({"cursor": cursor, "count": resolve_count(count), "include_raw": include_raw})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.AUTHOR_POSTS),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/likes",
    summary="Posts an author has liked",
    openapi_extra={I18N_KEY: "author_likes", **ASYNC_RESPONSES},
)
async def user_likes(
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
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the posts an author has publicly liked.

    Availability differs by platform, measured 2026-09-08:

    - **TikTok** answers a guest request. Accounts hide their likes by default,
      so an empty page usually means the author keeps the list private rather
      than that anything failed.
    - **Douyin** does not serve this list to a guest identity at all - the same
      identity that reads posts and mixes gets an empty response here. It needs
      an imported logged-in identity; see the console's identity import.

    **Parameters**

    - `platform` - `douyin` or `tiktok`. Must match the link you pass.
    - `url` - a link to the author's profile page.
    - `sec_user_id` - the author's stable id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The same post shape as `/video`, plus the cursor for the next page.
    """
    authorize(principal, platform)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update({"cursor": cursor, "count": resolve_count(count), "include_raw": include_raw})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=supported(platform, Operation.AUTHOR_LIKES),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/mix/posts",
    summary="Posts inside a mix or playlist",
    openapi_extra={I18N_KEY: "mix_posts", **ASYNC_RESPONSES},
)
async def mix_posts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    mix_id: str = MIX_ID_QUERY,
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the posts collected in a mix.

    A mix is Douyin's series and TikTok's playlist - an ordered set an author
    groups their own posts into. The id comes from any post that belongs to
    one: Douyin returns it as `mix_info`, TikTok as `playlistId`.

    **Parameters**

    - `platform` - `douyin` or `tiktok`.
    - `mix_id` - the mix or playlist to read.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The same post shape as `/video`, plus the cursor for the next page.
    """
    authorize(principal, platform)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=supported(platform, Operation.MIX_POSTS),
        params={
            "mix_id": mix_id,
            "cursor": cursor,
            "count": resolve_count(count),
            "include_raw": include_raw,
        },
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/followers",
    summary="Accounts that follow an author",
    openapi_extra={I18N_KEY: "author_followers", **ASYNC_RESPONSES},
)
async def user_followers(
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
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the accounts that follow an author.

    **TikTok only.** Douyin answers this with "not signed in" for any guest
    identity, so the endpoint is not offered there and asking returns a
    `UNSUPPORTED_CONTENT` error naming the platforms that do serve it.

    **Parameters**

    - `platform` - must be `tiktok`.
    - `url` - a link to the author's profile page.
    - `sec_user_id` - the author's stable id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - accounts per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    One author record per follower - nickname, avatar, signature and counts -
    plus the cursor for the next page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.AUTHOR_FOLLOWERS)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update({"cursor": cursor, "count": resolve_count(count), "include_raw": include_raw})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/following",
    summary="Accounts an author follows",
    openapi_extra={I18N_KEY: "author_following", **ASYNC_RESPONSES},
)
async def user_following(
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
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the accounts an author follows.

    **TikTok only**, for the same reason as `/user/followers`. Accounts hide
    this side of the graph far more often than they hide their followers, so an
    empty page is a common and correct answer even on TikTok.

    **Parameters**

    - `platform` - must be `tiktok`.
    - `url` - a link to the author's profile page.
    - `sec_user_id` - the author's stable id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - accounts per page.
    - `include_raw` - include each item's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. For content
      only one account can see. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    One author record per followed account, plus the cursor for the next page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.AUTHOR_FOLLOWING)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update({"cursor": cursor, "count": resolve_count(count), "include_raw": include_raw})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=resolve_request_proxy(request, proxy),
        refresh=refresh,
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


# --------------------------------------------------------------------------
# Parameter assembly
# --------------------------------------------------------------------------


def _content_params(platform: Platform, *, url: str | None, aweme_id: str | None) -> dict[str, Any]:
    """Accept either a link or an id, and normalize to what the worker needs.

    When the URL already carries the id, it is extracted here so two callers
    who pass the same post by different spellings coalesce onto one task.
    """
    if not url and not aweme_id:
        raise InvalidParam(
            "provide either url or aweme_id",
            details={"fields": ["url", "aweme_id"]},
        )
    if url:
        kind = vet_url(url, expected=platform)
        extracted = _identifier(kind, ResourceKind.VIDEO)
        return {"aweme_id": aweme_id or extracted, "url": None if extracted else kind.url}
    return {"aweme_id": aweme_id, "url": None}


def _author_params(
    platform: Platform, *, url: str | None, sec_user_id: str | None
) -> dict[str, Any]:
    if not url and not sec_user_id:
        raise InvalidParam(
            "provide either url or sec_user_id",
            details={"fields": ["url", "sec_user_id"]},
        )
    if url:
        kind = vet_url(url, expected=platform)
        extracted = _identifier(kind, ResourceKind.USER)
        return {
            "sec_user_id": sec_user_id or extracted,
            "url": None if extracted else kind.url,
        }
    return {"sec_user_id": sec_user_id, "url": None}


__all__ = ["authorize", "read_scope", "router", "vet_url"]
