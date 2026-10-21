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

from fastapi import APIRouter, Depends, Request

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.content import (
    AWEME_ID_QUERY,
    COUNT_QUERY,
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
    supported,
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
from dtk.core.types import Platform
from dtk.platforms.paging import max_page_size

router = APIRouter(prefix="/api/v1", tags=["content"])


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


__all__ = ["router"]
