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

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.operations import Operation, endpoint_name
from dtk.api.routes.schemas import BatchRequest, ParseRequest
from dtk.api.routes.support import (
    MAX_PAGE_SIZE,
    has_scope,
    ok,
    resolve_count,
    resolve_wait,
    validate_callback_url,
)
from dtk.core.errors import DtkError, ForbiddenScope, InvalidParam, InvalidUrl
from dtk.core.logging import get_logger
from dtk.core.types import Platform, Scope
from dtk.urls import ResourceKind, UrlKind, first_url, identify

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
PLATFORM_PATH = Path(description="Platform the request is addressed to.")


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
    openapi_extra={I18N_KEY: "parse"},
)
async def parse(
    request: Request,
    body: ParseRequest,
    wait: float | None = WAIT_QUERY,
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
    )


@router.post("/tasks/batch", summary="Submit many links at once")
async def batch(
    request: Request,
    body: BatchRequest,
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
                },
            )
        except DtkError as exc:
            results.append(
                {
                    "url": item.url,
                    "task_id": None,
                    "error": {"code": exc.code.value, "details": exc.details or None},
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


@router.get("/{platform}/video", summary="One post, video or image album")
async def video(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = Query(default=None, max_length=4096),
    aweme_id: str | None = Query(default=None, max_length=64),
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    authorize(principal, platform)
    params = _content_params(platform, url=url, aweme_id=aweme_id)
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.CONTENT_DETAIL),
        params=params,
        wait=resolve_wait(request, wait),
    )


@router.get("/{platform}/video/comments", summary="Top level comments on a post")
async def comments(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = Query(default=None, max_length=4096),
    aweme_id: str | None = Query(default=None, max_length=64),
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    wait: float | None = WAIT_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    authorize(principal, platform)
    params = _content_params(platform, url=url, aweme_id=aweme_id)
    params.update({"cursor": cursor, "count": resolve_count(count)})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.COMMENTS),
        params=params,
        wait=resolve_wait(request, wait),
    )


@router.get("/{platform}/video/comments/replies", summary="Replies under one comment")
async def comment_replies(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    comment_id: str = Query(min_length=1, max_length=64),
    url: str | None = Query(default=None, max_length=4096),
    aweme_id: str | None = Query(default=None, max_length=64),
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    wait: float | None = WAIT_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    authorize(principal, platform)
    params = _content_params(platform, url=url, aweme_id=aweme_id)
    params.update({"comment_id": comment_id, "cursor": cursor, "count": resolve_count(count)})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.COMMENT_REPLIES),
        params=params,
        wait=resolve_wait(request, wait),
    )


@router.get("/{platform}/user", summary="Author profile")
async def user(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = Query(default=None, max_length=4096),
    sec_user_id: str | None = Query(default=None, max_length=256),
    include_raw: bool = RAW_QUERY,
    wait: float | None = WAIT_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    authorize(principal, platform)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params["include_raw"] = include_raw
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.AUTHOR_PROFILE),
        params=params,
        wait=resolve_wait(request, wait),
    )


@router.get("/{platform}/user/posts", summary="Author post list")
async def user_posts(
    request: Request,
    platform: Platform = PLATFORM_PATH,
    url: str | None = Query(default=None, max_length=4096),
    sec_user_id: str | None = Query(default=None, max_length=256),
    cursor: str | None = CURSOR_QUERY,
    count: int | None = COUNT_QUERY,
    wait: float | None = WAIT_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    authorize(principal, platform)
    params = _author_params(platform, url=url, sec_user_id=sec_user_id)
    params.update({"cursor": cursor, "count": resolve_count(count)})
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=endpoint_name(platform, Operation.AUTHOR_POSTS),
        params=params,
        wait=resolve_wait(request, wait),
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
