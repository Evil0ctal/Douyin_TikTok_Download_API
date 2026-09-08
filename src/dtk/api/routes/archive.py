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
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import ok
from dtk.core.db import session_scope
from dtk.core.errors import NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Scope
from dtk.db.models import ArchivedContent
from dtk.services import archive

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/archive", tags=["archive"])

#: Hard ceiling on a single export, so one call cannot run for an hour. A caller
#: who needs more pages through with `cursor` on the list endpoint.
EXPORT_LIMIT = 50_000

PLATFORM_QUERY = Query(default=None, description="Only this platform.")
AUTHOR_QUERY = Query(default=None, max_length=256, description="Only this author's posts.")
TAG_QUERY = Query(default=None, max_length=128, description="Only posts carrying this tag.")
KIND_QUERY = Query(default=None, description="video or image_album.")
DURATION_QUERY = Query(default=None, description="short, medium, long or unknown.")
AVAILABILITY_QUERY = Query(default=None, description="live, deleted, private or unknown.")
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
    platform: str | None = PLATFORM_QUERY,
    author_uid: str | None = AUTHOR_QUERY,
    tag: str | None = TAG_QUERY,
    kind: str | None = KIND_QUERY,
    duration_bucket: str | None = DURATION_QUERY,
    availability: str | None = AVAILABILITY_QUERY,
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


@router.get("/export", summary="Export the archive", openapi_extra={I18N_KEY: "archive_export"})
async def export_archive(
    request: Request,
    platform: str | None = PLATFORM_QUERY,
    author_uid: str | None = AUTHOR_QUERY,
    tag: str | None = TAG_QUERY,
    kind: str | None = KIND_QUERY,
    duration_bucket: str | None = DURATION_QUERY,
    availability: str | None = AVAILABILITY_QUERY,
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
