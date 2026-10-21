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
    resolve_explain,
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
from dtk.core.types import PLATFORM_READ_SCOPES, CommentSort, Platform, PopularBoard, Scope
from dtk.i18n.messages import render_error
from dtk.platforms.paging import max_page_size
from dtk.services.download_requests import resolve_part
from dtk.urls import ResourceKind, UrlKind, first_url, identify, require_content_id
from dtk.worker import registry

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["content"])

WAIT_QUERY = Query(
    default=None,
    ge=0,
    # THE English text: `_translate` declines on the default language, so the
    # catalogue's `openapi.param.wait` is this sentence's translation and not
    # its source. `test_openapi_completeness` pins the two together.
    description=(
        "Hold the connection until the task finishes, up to this many seconds - this is how to make the call synchronous. Finished in time gives 200 with the result; not finished gives 202 with the task id and `state: running`, which is not an error and loses nothing. Above the instance ceiling shown as `maximum` it is a 400, rejected rather than shortened. Omitted or 0 returns 202 at once. See the description at the top of this document."
    ),
)
COUNT_QUERY = Query(
    default=None,
    ge=1,
    le=MAX_PAGE_SIZE,
    # `le` is this project's ceiling and the only one OpenAPI can state,
    # because the platform is a path parameter and the real limit differs
    # between them. TikTok refuses more than 35 - see dtk.platforms.paging -
    # so the route checks again and answers 400 rather than letting the call
    # go upstream to be read back as "this author has nothing".
    description=(
        "Items per page. The ceiling depends on the platform: Douyin serves up to 50, "
        "TikTok refuses more than 35. Above it this is a 400 naming the maximum, "
        "rejected rather than shortened."
    ),
)
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
EXPLAIN_QUERY = Query(
    default=False,
    description=(
        "Return the request as it went out - the signed URL, the headers and "
        "the identity's cookie jar - so it can be replayed outside this "
        "instance. The answer contains a credential, so it needs "
        "`identity:manage` and an operator role, it is written to the audit "
        "log, and it is stripped from the stored task result for any reader "
        "without that scope. Implies `refresh`: an explanation of a cached "
        "answer would describe a call this request did not make."
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
PLATFORM_PATH = Path(
    description=(
        "Platform the request is addressed to: douyin, tiktok or bilibili. Not every "
        "endpoint is served on every platform."
    )
)
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
    description=(
        "The author's stable id, as an alternative to url. On TikTok this is secUid; "
        "on Bilibili, the numeric mid."
    ),
)
MIX_ID_QUERY = Query(
    min_length=1,
    max_length=64,
    description=(
        "The mix, playlist or season to read: the mix_id /user/mixes returns. A post "
        "carries it too, as mix_info on Douyin and playlistId on TikTok."
    ),
)
COLLECTION_ID_QUERY = Query(
    min_length=1,
    max_length=64,
    description="The saved folder to read; ids come from /user/collections.",
)
SORT_QUERY = Query(
    default=None,
    description=(
        "hot or new. Only Bilibili offers a choice; Douyin and TikTok rank comments "
        "their own way, accept hot, and refuse new. Send the same value again with "
        "the cursor of a later page."
    ),
)
PART_QUERY = Query(
    default=None,
    ge=1,
    le=1000,
    description=(
        "Bilibili only: which part of a video uploaded in several, from 1. A link's ?p= "
        "says the same. The stream returned is that part's."
    ),
)
BOARD_QUERY = Query(
    default=PopularBoard.HOT,
    description=(
        "hot: popular now, paged by number. weekly: the weekly selection, newest issue "
        "first; the cursor is the next older issue. precious: the editors' all-time "
        "selection, one page. rank: the site-wide top 100, one page."
    ),
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
            details={**principal.denial(scopes=[read_scope(platform)]), "platform": platform.value},
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
            "not a supported Douyin, TikTok or Bilibili URL",
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


def identifier(kind: UrlKind, wanted: ResourceKind) -> str | None:
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
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """The front door: hand it a link or the share text around one.

    Accepts either platform, so it requires read access to one of them; the
    worker decides which after expanding the URL.
    """
    if not has_scope(principal, PLATFORM_READ_SCOPES):
        raise ForbiddenScope(
            "this credential lacks read access to any platform",
            details=principal.denial(scopes=PLATFORM_READ_SCOPES),
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
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
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
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One round trip, N independent tasks.

    Submission is batched, tracking is not: each item gets its own task id and
    its own fate, so one bad link cannot smear the whole request into a single
    error the way a synchronous bulk endpoint would (doc 07).
    """
    if not has_scope(principal, PLATFORM_READ_SCOPES):
        raise ForbiddenScope(
            "this credential lacks read access to any platform",
            details=principal.denial(scopes=PLATFORM_READ_SCOPES),
        )
    callback = validate_callback_url(request, body.callback_url)
    # Vetted once for the whole batch rather than per item: the answer cannot
    # differ between items, and checking N times would log N acceptances. The
    # pin is checked without a platform for the same reason it is on /parse -
    # each item names its own link, and they need not agree.
    egress = await resolve_request_proxy(request, proxy)
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
    part: int | None = PART_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One post: a video, or an image album, with its author and statistics.

    Identify the post by **either** `url` **or** `aweme_id` - exactly one is
    required. A share link works, including the shortened `v.douyin.com` and
    `vm.tiktok.com` forms, and a link that carries the id needs no id.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`. Must match the link you pass.
    - `url` - a link to the post. Text with a link inside it is accepted, so
      the clipboard content the apps produce can be sent unedited.
    - `aweme_id` - the post id, if you already have it.
    - `part` - Bilibili only: which part of a video uploaded in several, from
      1; a link's `?p=` says the same. The stream returned is that part's.
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
    normalized to the same shape for every platform. A Bilibili video lists its
    parts in `parts`.
    """
    authorize(principal, platform)
    params = content_params(platform, url=url, aweme_id=aweme_id)
    later = resolve_part(platform, part, url)
    if later is not None:
        params["part"] = later
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.CONTENT_DETAIL),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
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
    sort: CommentSort | None = SORT_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of top level comments on a post.

    Identify the post by **either** `url` **or** `aweme_id`. To read the
    replies under a comment, use `/video/comments/replies`.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`. Must match the link you pass.
    - `url` - a link to the post.
    - `aweme_id` - the post id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - comments per page.
    - `sort` - `hot` (the default) or `new`. Only Bilibili offers `new`;
      Douyin and TikTok answer it with `400 INVALID_PARAM`. A cursor belongs
      to the order it was read in, so send the same `sort` with it.
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
    params = content_params(platform, url=url, aweme_id=aweme_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
            **registry.comment_sort_params(platform, sort),
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.COMMENTS),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
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
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of replies underneath a single comment.

    Both the comment and the post it belongs to are required: pass
    `comment_id` together with **either** `url` **or** `aweme_id`. Comment ids
    come from `/video/comments`.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`. Must match the link you pass.
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
    params = content_params(platform, url=url, aweme_id=aweme_id)
    params.update(
        {
            "comment_id": comment_id,
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.COMMENT_REPLIES),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/video/parts",
    summary="The parts of a video",
    openapi_extra={I18N_KEY: "content_parts", **ASYNC_RESPONSES},
)
async def video_parts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = URL_QUERY,
    aweme_id: str | None = AWEME_ID_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Every part of a video uploaded in several, in order. Bilibili only.

    Identify the video by **either** `url` **or** `aweme_id`. A single-part
    video lists its one part. The same list arrives with `/video` as `parts`;
    this is the cheaper call when the list is all you need.

    **Parameters**

    - `platform` - `bilibili`. Douyin and TikTok answer `UNSUPPORTED_CONTENT`.
    - `url` - a link to the video.
    - `aweme_id` - the bvid (an `av` number is accepted too).
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      `/video`.

    **Returns**

    Each part's 1-based `part` number, `cid`, `title`, `duration_ms`, `width`
    and `height`. Pass the number as `part` to `/video` for that part's stream,
    or to `POST /api/v1/downloads` to store it.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.CONTENT_PARTS)
    params = content_params(platform, url=url, aweme_id=aweme_id)
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


#: Which operation reads each board.
_BOARDS: dict[PopularBoard, Operation] = {
    PopularBoard.HOT: Operation.POPULAR,
    PopularBoard.WEEKLY: Operation.POPULAR_WEEKLY,
    PopularBoard.PRECIOUS: Operation.POPULAR_PRECIOUS,
    PopularBoard.RANK: Operation.RANKING,
}


@router.get(
    "/{platform}/popular",
    summary="A site-curated list of posts",
    openapi_extra={I18N_KEY: "popular", **ASYNC_RESPONSES},
)
async def popular(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    board: PopularBoard = BOARD_QUERY,
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
    """One page of a list the site itself curates. Bilibili only.

    **Parameters**

    - `platform` - `bilibili`. Douyin and TikTok answer `UNSUPPORTED_CONTENT`.
    - `board` - which list:
      - `hot` (the default): what is popular now, paged by number.
      - `weekly`: the weekly selection, 50 videos an issue, newest issue
        first. The cursor is the next older issue's number; pass an issue
        number to start there.
      - `precious`: the editors' all-time selection, one page.
      - `rank`: the site-wide top 100, one page.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - items per page, for `hot` only; the others are the size the
      site makes them.
    - `include_raw`, `wait`, `proxy`, `identity`, `refresh`, `explain` - as on
      the other list endpoints. The lists are the same for everyone, so a
      repeat inside `cache.list_ttl` (5 minutes by default) is free.

    **Returns**

    A page of posts in the same shape as `/user/posts`, plus the cursor for
    the next page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, _BOARDS[board])
    params: dict[str, Any] = {
        "cursor": cursor,
        "count": resolve_count(count, maximum=max_page_size(platform)),
        "include_raw": include_raw,
    }
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
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One author's public profile.

    Identify the author by **either** `url` **or** `sec_user_id` - exactly one
    is required. A profile link is enough; the id is read out of it.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`. Must match the link you pass.
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
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.AUTHOR_PROFILE),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
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
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of an author's own posts, newest first.

    Identify the author by **either** `url` **or** `sec_user_id`. Page through
    the feed with `cursor`.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`. Must match the link you pass.
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
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.AUTHOR_POSTS),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
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
    explain: bool = EXPLAIN_QUERY,
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
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=supported(platform, Operation.AUTHOR_LIKES),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/reposts",
    summary="Other people's posts an author reposted",
    openapi_extra={I18N_KEY: "author_reposts", **ASYNC_RESPONSES},
)
async def user_reposts(
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
    """One page of the posts an author has reposted onto their own profile.

    **TikTok only.** These are other people's videos, which is what separates
    this from `/user/posts` - TikTok gives them their own tab on the profile.

    It is a public tab: a guest identity reads it, no import needed.

    **Parameters**

    - `platform` - must be `tiktok`.
    - `url` - a link to the author's profile page.
    - `sec_user_id` - the author's stable id, if you already have it.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page. TikTok refuses more than 35.
    - `include_raw` - include each post's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. Requires
      `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The same post shape as `/video`, one entry per repost - each carrying its
    original author, not the account that reposted it - plus the cursor for
    the next page.
    """
    authorize(principal, platform)
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=supported(platform, Operation.AUTHOR_REPOSTS),
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/user/collections",
    summary="The folders an author has organized bookmarked posts into",
    openapi_extra={I18N_KEY: "author_collections", **ASYNC_RESPONSES},
)
async def user_collections(
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
    """One page of bookmark folders.

    The two platforms differ in who you can ask about, and the difference is
    not cosmetic:

    - **TikTok** takes an author. Each folder is public or private on its own
      and a guest identity sees the public ones, so an empty page means "no
      public folders" rather than "no folders".
    - **Douyin** takes no author at all - its endpoint carries no user id and
      answers only about the session sending it. Pass `identity` pointed at an
      imported identity - required, since an unpinned answer would be some
      account's private folders - and passing `url` or `sec_user_id` is an
      error rather than being quietly ignored.
    - **Bilibili** takes an author, by `mid` or space link, and answers with
      every public folder at once: there is one page.

    Either way each folder says whether it is public, and to read what is
    inside one, pass its id to `/collection/posts`.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`.
    - `url` - a link to the author's profile page. **TikTok and Bilibili**;
      Douyin rejects it, because its endpoint cannot be pointed at anyone.
    - `sec_user_id` - the author's stable id, if you already have it - a
      Bilibili `mid`. **TikTok and Bilibili**, for the same reason.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - folders per page.
    - `include_raw` - include each folder's untouched platform payload.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. Requires
      `identity:manage`. **Required on Douyin**, and then never cached.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    Each folder's id, name, cover and item count, plus the cursor for the next
    page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.AUTHOR_COLLECTIONS)
    # Douyin's folder list has no subject but the identity sending it, so an
    # author is neither required nor accepted there; TikTok's needs one.
    params: dict[str, Any] = (
        author_params(platform, url=url, sec_user_id=sec_user_id)
        if _accepts_author(endpoint)
        else _refuse_author(endpoint, url=url, sec_user_id=sec_user_id)
    )
    pinned = await resolve_request_identity(request, principal, identity, platform=platform)
    _require_pin(endpoint, pinned)
    params.update({"cursor": cursor, "count": resolve_count(count), "include_raw": include_raw})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=pinned,
    )


@router.get(
    "/{platform}/user/bookmarks",
    summary="Posts an account has saved",
    openapi_extra={I18N_KEY: "author_bookmarks", **ASYNC_RESPONSES},
)
async def user_bookmarks(
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
    """One page of the posts an account has saved, across every folder.

    **Only ever the account's own list**, on both platforms, so `identity` is
    required: point it at an identity imported from that account's own
    browser session. This is the other half of the saved tab -
    `/user/collections` lists the folders, this lists what is in the tab
    regardless of folder, and an account can have saved posts and no folders.

    - **TikTok** takes the account as well, and answers only that account's own
      session: measured with a guest identity against an account that has
      saved a post, it refuses outright rather than returning an empty page.
    - **Douyin** takes no account at all - its endpoint carries no user id and
      answers about the session sending it. Passing `url` or `sec_user_id` is an
      error rather than being quietly ignored.

    For someone else's public folder, use `/collection/posts` instead.

    **Parameters**

    - `platform` - `douyin` or `tiktok`.
    - `url` - a link to the account's profile page. **TikTok only**.
    - `sec_user_id` - the account's stable id, if you already have it.
      **TikTok only**.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page. TikTok refuses more than 35.
    - `include_raw` - include each post's untouched platform payload.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - **required**. Send the request as this identity and no
      other. Requires `identity:manage`. The answer is never cached, because
      it belongs to that identity alone.
    - `refresh` - ignore any in-flight answer and ask upstream again.

    **Returns**

    The same post shape as `/video`, one entry per saved post, plus the cursor
    for the next page.
    """
    authorize(principal, platform)
    endpoint = supported(platform, Operation.AUTHOR_BOOKMARKS)
    params: dict[str, Any] = (
        author_params(platform, url=url, sec_user_id=sec_user_id)
        if _accepts_author(endpoint)
        else _refuse_author(endpoint, url=url, sec_user_id=sec_user_id)
    )
    pinned = await resolve_request_identity(request, principal, identity, platform=platform)
    _require_pin(endpoint, pinned)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint,
        params=params,
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=pinned,
    )


@router.get(
    "/{platform}/collection",
    summary="One saved folder's own name, cover and size",
    openapi_extra={I18N_KEY: "collection_detail", **ASYNC_RESPONSES},
)
async def collection_detail(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    collection_id: str = COLLECTION_ID_QUERY,
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    proxy: str | None = PROXY_QUERY,
    identity: str | None = IDENTITY_QUERY,
    refresh: bool = REFRESH_QUERY,
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One saved folder's own metadata, asked for by id.

    **TikTok only.** `/user/collections` lists an author's folders, but it has
    to be asked by way of an owner - you give it a `sec_user_id`. This one
    takes the folder's id alone, which is all a shared link carries, and is
    therefore the only way to find out what an unknown collection id is.

    It also tells you **who owns it** and **whether it is public**, neither of
    which you can get from the id by any other route.

    A public folder is readable with a guest identity; a private one needs
    `identity` pointed at an identity imported from its owner's session.

    **Parameters**

    - `platform` - must be `tiktok`.
    - `collection_id` - the folder to describe.
    - `include_raw` - include the untouched platform payload.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. Needed only
      for a folder that is not public. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.author_ttl` is answered from the
      cache and costs nothing.

    **Returns**

    The folder's id, name, cover, item count, owner and whether it is public.
    To read the posts inside it, pass the same id to `/collection/posts`.
    """
    authorize(principal, platform)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=supported(platform, Operation.COLLECTION_DETAIL),
        params={"collection_id": collection_id, "include_raw": include_raw},
        wait=resolve_wait(request, wait),
        proxy=await resolve_request_proxy(request, proxy),
        refresh=refresh,
        explain=await resolve_explain(request, principal, explain),
        identity=await resolve_request_identity(request, principal, identity, platform=platform),
    )


@router.get(
    "/{platform}/collection/posts",
    summary="Posts inside one of an author's saved folders",
    openapi_extra={I18N_KEY: "collection_posts", **ASYNC_RESPONSES},
)
async def collection_posts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    collection_id: str = COLLECTION_ID_QUERY,
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
    """One page of the posts inside a single saved folder.

    **Both platforms.** The request names the folder rather than the account,
    which is why a folder its owner has made public is readable by a guest
    identity on either platform, with no import needed. Measured against a
    public and a private folder on each: the public one answers a guest, the
    private one is refused rather than returned empty.

    On TikTok this is the third of the Saved tab's three views, alongside
    `/user/collections` and `/user/bookmarks`.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`.
    - `collection_id` - the folder to read, as returned by `/user/collections`.
    - `cursor` - the cursor returned by the previous page. Omit it for the
      first page; a response with no cursor is the last page.
    - `count` - posts per page. TikTok refuses more than 35.
    - `include_raw` - include each post's untouched platform payload. A page
      carries one per item, so this multiplies the response and everything
      that stores it; it is off by default for that reason.
    - `wait` - seconds to wait for the result. Omit it to get `202` and a task
      id to poll.
    - `identity` - send the request as this identity and no other. Needed only
      for a folder that is not public. Requires `identity:manage`.
    - `refresh` - ignore any cached or in-flight answer and ask upstream
      again. Without it a repeat inside `cache.list_ttl` (5 minutes by default)
      is answered from the cache and costs nothing; a refresh costs an
      identity and a real request, and its answer is cached in turn.

    **Returns**

    The same post shape as `/video`, one entry per post in the folder, plus the
    cursor for the next page.
    """
    authorize(principal, platform)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=supported(platform, Operation.COLLECTION_POSTS),
        params={
            "collection_id": collection_id,
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
    explain: bool = EXPLAIN_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of the posts collected in a mix.

    A mix is Douyin's series, TikTok's playlist and Bilibili's season - an
    ordered set an author groups their own posts into. `/user/mixes` lists an
    author's, and any post that belongs to one carries its id: Douyin returns
    it as `mix_info`, TikTok as `playlistId`.

    **Parameters**

    - `platform` - `douyin`, `tiktok` or `bilibili`. A Bilibili season comes
      back whole, in one page, whatever `cursor` and `count` say.
    - `mix_id` - the mix, playlist or season to read.
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
    explain: bool = EXPLAIN_QUERY,
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
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
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
    explain: bool = EXPLAIN_QUERY,
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
    params = author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(platform)),
            "include_raw": include_raw,
        }
    )
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


# --------------------------------------------------------------------------
# Parameter assembly
# --------------------------------------------------------------------------


def content_params(platform: Platform, *, url: str | None, aweme_id: str | None) -> dict[str, Any]:
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
        extracted = identifier(kind, ResourceKind.VIDEO)
        chosen = aweme_id or extracted
        # Only what the caller typed is checked. An id this system pulled out of
        # a URL it already recognised has been through the pattern that
        # recognised it, and refusing one here would turn a link the platform
        # would answer into a 400 from us.
        if aweme_id:
            require_content_id(aweme_id, platform=platform)
        return {"aweme_id": chosen, "url": None if extracted else kind.url}
    return {"aweme_id": require_content_id(str(aweme_id), platform=platform), "url": None}


def _accepts_author(endpoint: str) -> bool:
    """Whether this endpoint can be pointed at somebody in particular.

    Derived from the registry rather than branched on the platform name, so a
    platform whose endpoint gains or loses an author parameter needs no edit
    here. Douyin's ``collects/list/`` is the case that forced the question: it
    carries no user id at all and answers only about the session holding it.
    """
    return registry.AUTHOR_ID in registry.ENDPOINTS[endpoint].accepts


def _refuse_author(endpoint: str, *, url: str | None, sec_user_id: str | None) -> dict[str, Any]:
    """Reject an author for an endpoint that has no way to honour one.

    Accepting and ignoring it is the trap: Douyin's ``collects/list/`` would
    cheerfully return the operator's OWN folders under the stranger's id the
    caller asked about, and nothing in the response would say so.
    """
    given = [name for name, value in (("url", url), ("sec_user_id", sec_user_id)) if value]
    if given:
        raise InvalidParam(
            f"{endpoint} answers only about the identity sending it, so it takes no author",
            details={"endpoint": endpoint, "unexpected": given, "hint": "pin identity instead"},
        )
    return {}


def _require_pin(endpoint: str, identity: str | None) -> None:
    """Refuse a session-bound endpoint that names no identity, before it queues.

    The fetch layer refuses it too, and has to - it is where the cache is
    decided. This copy exists so the caller hears it as a 400 now rather than
    as a task that queues, runs and fails.
    """
    if identity is None and registry.ENDPOINTS[endpoint].session_bound:
        raise InvalidParam(
            f"{endpoint} answers only about the identity sending it, so it needs "
            "one pinned with `identity`",
            details={"endpoint": endpoint, "field": "identity"},
        )


def author_params(
    platform: Platform, *, url: str | None, sec_user_id: str | None
) -> dict[str, Any]:
    if not url and not sec_user_id:
        raise InvalidParam(
            "provide either url or sec_user_id",
            details={"fields": ["url", "sec_user_id"]},
        )
    if url:
        kind = vet_url(url, expected=platform)
        extracted = identifier(kind, ResourceKind.USER)
        return {
            "sec_user_id": sec_user_id or extracted,
            "url": None if extracted else kind.url,
        }
    return {"sec_user_id": sec_user_id, "url": None}


__all__ = [
    "author_params",
    "authorize",
    "content_params",
    "identifier",
    "read_scope",
    "router",
    "supported",
    "vet_url",
]
