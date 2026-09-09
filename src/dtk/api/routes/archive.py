"""Reading back what this instance has collected.

The archive is the one part of this API that answers from local storage rather
than from a platform: no identity is spent, no rate limit applies upstream, and
a result is available whether or not the post still exists.

Its own scopes, deliberately. `archive:read` is separate from `douyin:read` so
that an operator who opens a platform endpoint to unauthenticated callers has
not thereby opened "everything this instance has ever collected"; and
`archive:export` is separate again, because a bulk export is the single call
that turns a read key into a copy of the database.

Paging is keyset, never OFFSET. The table is written to while a client walks it,
so an offset silently skips and repeats rows - the failure where a caller
believes they have everything and does not.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.schemas import BackfillRequest, RecheckRequest
from dtk.api.routes.support import ok
from dtk.core.db import session_scope
from dtk.core.errors import NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Availability, ContentKind, DurationBucket, Platform, Scope
from dtk.db.models import ArchivedContent
from dtk.services import archive

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/archive", tags=["archive"])

#: Hard ceiling on a single export, so one call cannot run for an hour. A caller
#: who needs more pages through with `cursor` on the list endpoint.
EXPORT_LIMIT = 50_000

#: Which read scope a walk of one platform's history costs. Both of these
#: endpoints look like archive reads and behave like platform reads.
_READ_SCOPE: Final[dict[Platform, Scope]] = {
    Platform.DOUYIN: Scope.DOUYIN_READ,
    Platform.TIKTOK: Scope.TIKTOK_READ,
}

PLATFORM_QUERY = Query(default=None, description="Only this platform.")
AUTHOR_QUERY = Query(default=None, max_length=256, description="Only this author's posts.")
TAG_QUERY = Query(default=None, max_length=128, description="Only posts carrying this tag.")
KIND_QUERY = Query(default=None, description="Only posts of this kind.")
DURATION_QUERY = Query(default=None, description="Only posts in this length class.")
AVAILABILITY_QUERY = Query(default=None, description="Only posts with this upstream status.")
SEARCH_QUERY = Query(
    default=None,
    max_length=200,
    description="Substring of the title or description. Matches in any script.",
)
CURSOR_QUERY = Query(
    default=None, max_length=512, description="Cursor from the previous page; omit for the first."
)
LIMIT_QUERY = Query(default=None, ge=1, le=archive.MAX_PAGE, description="Rows per page.")


def _filter(
    platform: str | None,
    author_uid: str | None,
    tag: str | None,
    kind: str | None,
    duration: str | None,
    availability: str | None,
    query: str | None,
    seen_after: datetime | None = None,
    seen_before: datetime | None = None,
) -> archive.ArchiveFilter:
    return archive.ArchiveFilter(
        platform=platform,
        author_uid=author_uid,
        tag=tag,
        kind=kind,
        duration_bucket=duration,
        availability=availability,
        query=query,
        seen_after=seen_after,
        seen_before=seen_before,
    )


def _row(content: ArchivedContent, *, include_media: bool = True) -> dict[str, Any]:
    """One archived post, shaped the way the API's own content records are."""
    payload: dict[str, Any] = {
        "platform": content.platform,
        "content_id": content.content_id,
        "kind": content.kind,
        "web_url": content.web_url,
        "title": content.title,
        "description": content.description,
        "created_at": content.platform_created_at.isoformat()
        if content.platform_created_at
        else None,
        "duration_ms": content.duration_ms,
        "author": {"uid": content.author_uid, "nickname": content.author_nickname},
        "music": {"music_id": content.music_id, "title": content.music_title},
        "tags": list(content.tags or []),
        "location": content.location,
        "cover_url": content.cover_url,
        "classification": {
            "orientation": content.orientation,
            "duration_bucket": content.duration_bucket,
            "resolution_class": content.resolution_class,
            "script": content.script,
        },
        "availability": content.availability,
        "first_seen_at": content.first_seen_at.isoformat(),
        "last_seen_at": content.last_seen_at.isoformat(),
    }
    if include_media:
        payload["media"] = content.media
    return payload


@router.get("", summary="Search the archive", openapi_extra={I18N_KEY: "archive_list"})
async def list_archive(
    request: Request,
    platform: Platform | None = PLATFORM_QUERY,
    author_uid: str | None = AUTHOR_QUERY,
    tag: str | None = TAG_QUERY,
    kind: ContentKind | None = KIND_QUERY,
    duration_bucket: DurationBucket | None = DURATION_QUERY,
    availability: Availability | None = AVAILABILITY_QUERY,
    q: str | None = SEARCH_QUERY,
    cursor: str | None = CURSOR_QUERY,
    limit: int | None = LIMIT_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One page of what this instance has collected, newest sighting first.

    Answers from local storage: no identity is spent, nothing is fetched, and a
    post that has since been deleted is still here with `availability` saying so.

    **Parameters**

    - `platform`, `author_uid`, `tag`, `kind`, `duration_bucket`, `availability` -
      narrow the result; all optional and combinable.
    - `q` - substring of the title or description. Matched as a substring rather
      than by word, so it behaves the same in Chinese as in English.
    - `cursor` - the cursor from the previous page. Omit it for the first page;
      a response with no cursor was the last page.
    - `limit` - rows per page.

    **Returns**

    The posts, each with its author, tags, derived classification and media
    manifest, plus the cursor for the next page.
    """
    principal.require(Scope.ARCHIVE_READ)
    spec = _filter(platform, author_uid, tag, kind, duration_bucket, availability, q)
    rows, next_cursor = await archive.search(
        request.state.db, spec, limit=limit or archive.DEFAULT_PAGE, cursor=cursor
    )
    return ok(
        request,
        {
            "items": [_row(row) for row in rows],
            "cursor": next_cursor,
            "has_more": bool(next_cursor),
        },
    )


@router.get("/stats", summary="Archive totals", openapi_extra={I18N_KEY: "archive_stats"})
async def archive_stats(
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """How much this instance has stored.

    **Returns**

    Total posts and authors, and a per-platform breakdown.
    """
    principal.require(Scope.ARCHIVE_READ)
    return ok(request, await archive.stats(request.state.db))


@router.get(
    "/export",
    summary="Export the archive",
    openapi_extra={I18N_KEY: "archive_export"},
    # Declared, because this is the one endpoint that does not answer in the
    # uniform envelope. Without saying so the generated document would promise
    # a JSON object and hand back newline-delimited records.
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}, "description": "One post per line."}},
)
async def export_archive(
    request: Request,
    platform: Platform | None = PLATFORM_QUERY,
    author_uid: str | None = AUTHOR_QUERY,
    tag: str | None = TAG_QUERY,
    kind: ContentKind | None = KIND_QUERY,
    duration_bucket: DurationBucket | None = DURATION_QUERY,
    availability: Availability | None = AVAILABILITY_QUERY,
    q: str | None = SEARCH_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> StreamingResponse:
    """Stream every matching post as newline-delimited JSON.

    Streamed a page at a time rather than assembled in memory: an archive is
    meant to grow past what fits in one response, and buffering it would make
    the size of your own data the thing that breaks the export.

    Needs `archive:export`, which ordinary read keys do not carry - this is the
    one call that hands back the whole collection at once.

    **Parameters**

    The same filters as the list endpoint. Export what you can already see.

    **Returns**

    `application/x-ndjson`: one post per line, in the same shape the list
    endpoint returns, without the media manifest - use the list endpoint or
    `/archive/{platform}/{content_id}` when you need the mirrors.
    """
    principal.require(Scope.ARCHIVE_EXPORT)
    spec = _filter(platform, author_uid, tag, kind, duration_bucket, availability, q)

    async def lines() -> AsyncIterator[bytes]:
        # Its own session, not `request.state.db`. The body of a StreamingResponse
        # runs AFTER the route function returns, so the request-scoped session is
        # already being torn down - asyncpg answers that with "another operation
        # is in progress", which is exactly what this endpoint did on its first
        # run against real data.
        cursor: str | None = None
        sent = 0
        async with session_scope() as session:
            while sent < EXPORT_LIMIT:
                rows, cursor = await archive.search(
                    session, spec, limit=archive.MAX_PAGE, cursor=cursor
                )
                if not rows:
                    break
                for row in rows:
                    yield (
                        json.dumps(_row(row, include_media=False), ensure_ascii=False) + "\n"
                    ).encode()
                    sent += 1
                if not cursor:
                    break
        log.info("archive.exported", rows=sent, truncated=sent >= EXPORT_LIMIT)

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="archive.ndjson"'},
    )


@router.post(
    "/recheck", summary="Re-check what still exists", openapi_extra={I18N_KEY: "archive_recheck"}
)
async def recheck(
    request: Request,
    body: RecheckRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Verify that archived posts still exist on the platform.

    This is what makes "which of the things I saved are gone" answerable. A
    deleted post answers the platform's own not-found, which is the finding
    rather than a failure: the record is kept and marked `deleted`, so it stays
    searchable and exportable with the truth attached.

    Each post is one real request through the identity pool, so the batch is
    bounded and the pass runs in the background.

    **Parameters**

    - `limit` - how many posts to verify, least recently checked first.
    - `older_than_days` - only posts whose last check is older than this.

    **Returns**

    `202` with a task id. The result carries how many were checked and how many
    turned out to be gone.
    """
    # Not archive:read. This spends the identity pool on a real request per
    # post, which is exactly what the downloads module says no read scope may
    # imply - and the archive module's own docstring promises that reading it
    # spends no identity. A recheck is a platform read wearing an archive name,
    # so it asks for a platform read scope.
    principal.require(Scope.DOUYIN_READ, Scope.TIKTOK_READ)
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=operations.Maintenance.ARCHIVE_AVAILABILITY.value,
        params={"limit": body.limit, "older_than_days": body.older_than_days},
        wait=0,
        coalesce=True,
    )


@router.post(
    "/backfill",
    summary="Archive an author's history",
    openapi_extra={I18N_KEY: "archive_backfill"},
)
async def backfill(
    request: Request,
    body: BackfillRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Walk one author's posts past the first page and archive every one.

    Separate from the watchlist on purpose. A watchlist entry is for what is
    new and runs forever; a backfill is a one-off with a very different cost,
    and making every scheduled run as deep as the deepest anyone ever wanted
    would be the wrong trade.

    Stops at the first page that returns nothing, at the page ceiling, or when
    the platform says there is no more history - whichever comes first.

    **Parameters**

    - `platform`, `author_id` - whose history to walk.
    - `pages` - how deep to go, capped by the server.

    **Returns**

    `202` with a task id. The result says how many pages were walked, how many
    posts were archived, and why it stopped.
    """
    # The platform's own read scope, for the same reason as the recheck above -
    # and this one names its platform, so it can ask for exactly that one.
    principal.require(_READ_SCOPE[body.platform])
    return await operations.submit_and_wait(
        request,
        principal,
        endpoint=operations.Maintenance.ARCHIVE_BACKFILL.value,
        params={
            "platform": body.platform.value,
            "author_id": body.author_id,
            "pages": body.pages,
        },
        wait=0,
        coalesce=True,
    )


@router.get(
    "/{platform}/{content_id}",
    summary="One archived post",
    openapi_extra={I18N_KEY: "archive_get"},
)
async def get_archived(
    request: Request,
    platform: str = Path(description="douyin or tiktok."),
    content_id: str = Path(max_length=64, description="The post id."),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One post as this instance last saw it.

    **Returns**

    The stored record, media manifest included. The signed CDN links inside it
    expire; `web_url` is the part that survives.
    """
    principal.require(Scope.ARCHIVE_READ)
    row = await archive.get(request.state.db, platform, content_id)
    if row is None:
        raise NotFound("this instance has not archived that post")
    return ok(request, _row(row))


__all__ = ["router"]
