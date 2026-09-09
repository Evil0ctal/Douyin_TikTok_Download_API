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
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.openapi import ACCEPTED_RESPONSES, CREATED_RESPONSES, I18N_KEY
from dtk.api.routes.schemas import (
    ArchiveDelete,
    BackfillRequest,
    CollectionCreate,
    CollectionUpdate,
    ContentSelection,
    RecheckRequest,
)
from dtk.api.routes.support import ok
from dtk.core.db import session_scope
from dtk.core.errors import DownloaderDown, NotConfigured, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Availability, ContentKind, DurationBucket, Platform, Scope
from dtk.db.models import ArchivedContent
from dtk.services import archive, collections, downloads

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
STORED_QUERY = Query(
    default=None,
    description=(
        "True for posts whose media this instance is holding right now, false "
        "for the rest. Like `collection` this describes what was kept rather "
        "than the post: an evicted download does not count, because there is "
        "nothing left to play."
    ),
)
COLLECTION_QUERY = Query(
    default=None,
    description=(
        "Only posts in this collection, by id. Unlike every other filter here "
        "this one is not a property of the post: somebody put them in it."
    ),
)


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
    collection_id: uuid.UUID | None = None,
    stored: bool | None = None,
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
        collection_id=collection_id,
        stored=stored,
    )


def _row(
    content: ArchivedContent,
    *,
    include_media: bool = True,
    stored: dict[str, Any] | None = None,
    in_collections: list[str] | None = None,
) -> dict[str, Any]:
    """One archived post, shaped the way the API's own content records are.

    ``stored`` is what this instance has on its own disk for the post, or None.
    It is not part of the archive - the archive is what was seen, the download
    is what was kept - but a reader looking at a list of posts wants to know
    which of them they already have, and the console cannot answer that without
    it. The names it carries are what `GET /downloads/{id}/files/{name}` takes.
    """
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
    payload["stored"] = stored
    if in_collections is not None:
        payload["collections"] = in_collections
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
    collection: uuid.UUID | None = COLLECTION_QUERY,
    stored: bool | None = STORED_QUERY,
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
    - `collection` - only posts in this collection. The one filter here that is
      not a property of the post: somebody put them in it.
    - `stored` - true for the posts whose media is on this instance's disk right
      now. An evicted download does not count: the record is kept on purpose,
      and the bytes are not there to play.
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
    spec = _filter(
        platform,
        author_uid,
        tag,
        kind,
        duration_bucket,
        availability,
        q,
        collection_id=collection,
        stored=stored,
    )
    rows, next_cursor = await archive.search(
        request.state.db, spec, limit=limit or archive.DEFAULT_PAGE, cursor=cursor
    )
    # One query for the whole page rather than one per row: the console renders
    # this as a grid of covers and asks the same question of every card.
    #
    # Named `on_disk` rather than `stored`, which is now the query parameter
    # asking to be filtered by it.
    on_disk = (
        await downloads.stored_for(
            request.state.db, [(row.platform, row.content_id) for row in rows]
        )
        if principal.permits(Scope.MEDIA_READ, Scope.ADMIN)
        else {}
    )
    # Same shape of question, same treatment: one query for the page rather
    # than one per card.
    member_of = await collections.memberships(
        request.state.db, [(row.platform, row.content_id) for row in rows]
    )
    return ok(
        request,
        {
            "items": [
                _row(
                    row,
                    stored=on_disk.get((row.platform, row.content_id)),
                    in_collections=member_of.get((row.platform, row.content_id), []),
                )
                for row in rows
            ],
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

    Total posts and authors, a per-platform breakdown, and how many of them
    this instance is holding the media for - in total and per platform. The
    archive is what was seen and a download is what was kept, so the two counts
    are different questions and reporting only the first made "46 posts" read
    as 46 videos on the disk.
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
    "/recheck",
    summary="Re-check what still exists",
    openapi_extra={I18N_KEY: "archive_recheck", **ACCEPTED_RESPONSES},
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
    openapi_extra={I18N_KEY: "archive_backfill", **ACCEPTED_RESPONSES},
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


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------
#
# Registered above `/{platform}/{content_id}`, which would otherwise swallow
# `/collections/{id}` - FastAPI matches in registration order and a two-segment
# template does not care that the first segment is a word rather than a
# platform.


@router.get(
    "/collections", summary="List collections", openapi_extra={I18N_KEY: "collections_list"}
)
async def list_collections(
    request: Request, principal: Principal = Depends(enforce_rate_limit)
) -> Any:
    """Every collection, with how many posts is in each.

    A collection is the one grouping the library offers that is not derived
    from the posts: author, platform and collection date all come out of the
    record, and this one comes out of somebody deciding.

    **Returns**

    Each collection's id, name, note, item count and timestamps, newest first.
    """
    principal.require(Scope.ARCHIVE_READ)
    return ok(request, {"items": await collections.listing(request.state.db)})


@router.post(
    "/collections",
    summary="Create a collection",
    openapi_extra={I18N_KEY: "collections_create", **CREATED_RESPONSES},
    status_code=201,
)
async def create_collection(
    request: Request,
    body: CollectionCreate,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Make a new named set. Empty until posts are added to it.

    **Parameters**

    - `name` - unique, case-insensitively. Whitespace is collapsed.
    - `note` - free text the console shows under the name. Never parsed.

    **Returns**

    The collection.
    """
    principal.require(Scope.MEDIA_WRITE, Scope.ADMIN)
    row = await collections.create(
        request.state.db, name=body.name, note=body.note, created_by=principal.user_id
    )
    await request.state.db.commit()
    return ok(request, collections.as_dict(row, items=0), status_code=201)


@router.patch(
    "/collections/{collection_id}",
    summary="Rename a collection",
    openapi_extra={I18N_KEY: "collections_update"},
)
async def update_collection(
    request: Request,
    body: CollectionUpdate,
    collection_id: uuid.UUID = Path(description="The collection to change."),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Change the name, the note, or both. Membership is untouched.

    **Parameters**

    - `name` - the new name, if it is changing.
    - `note` - the new note. Send it as null to clear it; leave it out
      entirely to keep whatever is there.

    **Returns**

    The collection.
    """
    principal.require(Scope.MEDIA_WRITE, Scope.ADMIN)
    row = await collections.rename(
        request.state.db,
        collection_id,
        name=body.name,
        note=body.note,
        # Sent-and-null and not-sent are the same value and different requests.
        note_given="note" in body.model_fields_set,
    )
    await request.state.db.commit()
    return ok(request, collections.as_dict(row))


@router.delete(
    "/collections/{collection_id}",
    summary="Delete a collection",
    openapi_extra={I18N_KEY: "collections_delete"},
)
async def delete_collection(
    request: Request,
    collection_id: uuid.UUID = Path(description="The collection to delete."),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Remove the collection itself.

    The posts stay in the archive and the files stay on disk: a collection is a
    label, and deleting a label is not deleting what it was on.

    **Returns**

    `{"deleted": true}`.
    """
    principal.require(Scope.MEDIA_WRITE, Scope.ADMIN)
    await collections.remove(request.state.db, collection_id)
    await request.state.db.commit()
    return ok(request, {"deleted": True})


@router.post(
    "/collections/{collection_id}/items",
    summary="Add posts to a collection",
    openapi_extra={I18N_KEY: "collections_add"},
)
async def add_to_collection(
    request: Request,
    body: ContentSelection,
    collection_id: uuid.UUID = Path(description="The collection to add to."),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Put posts in a collection.

    Adding a post that is already in it is a no-op rather than an error: the
    console sends whatever is selected, and part of a selection is routinely
    already there. A post that is not in the archive is skipped, so one stale
    card does not refuse the other nineteen.

    **Parameters**

    - `items` - the posts, each as a platform and a content id.

    **Returns**

    `added`, the number that were not already in it.
    """
    principal.require(Scope.MEDIA_WRITE, Scope.ADMIN)
    added = await collections.add_items(
        request.state.db, collection_id, collections.clean_keys(body.items)
    )
    await request.state.db.commit()
    return ok(request, {"added": added})


@router.post(
    "/collections/{collection_id}/items/remove",
    summary="Take posts out of a collection",
    openapi_extra={I18N_KEY: "collections_remove"},
)
async def remove_from_collection(
    request: Request,
    body: ContentSelection,
    collection_id: uuid.UUID = Path(description="The collection to remove from."),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Take posts out of a collection, leaving them in the archive.

    A POST rather than a DELETE because it carries a body, and a DELETE with a
    body is the kind of thing intermediaries drop.

    **Returns**

    `removed`, the number of memberships that went.
    """
    principal.require(Scope.MEDIA_WRITE, Scope.ADMIN)
    removed = await collections.remove_items(
        request.state.db, collection_id, collections.clean_keys(body.items)
    )
    await request.state.db.commit()
    return ok(request, {"removed": removed})


@router.post("/delete", summary="Delete archived posts", openapi_extra={I18N_KEY: "archive_delete"})
async def delete_archived(
    request: Request,
    body: ArchiveDelete,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Remove posts from the archive, and by default their stored media too.

    This is the one call in the archive that destroys something. It is not
    reversible and there is no trash: the row goes, its collection memberships
    go with it, and unless `media` is false the sidecar is asked to remove the
    directories on disk as well.

    The files are removed first and the rows second. The other order can leave a
    record pointing at a directory that is already gone, which reads as a
    download the console can offer and cannot deliver; this order can at worst
    leave bytes with no record, which the storage panel already reports.

    **Parameters**

    - `items` - the posts, each as a platform and a content id.
    - `media` - also delete what is on disk. True by default, because that is
      what "delete this" means about a video the instance is holding.

    **Returns**

    How many archive rows went, how many download records went, and how many
    bytes the sidecar reported freeing.
    """
    # Deleting is not a read, and it reaches further than one download: the same
    # scope that may start a download and cancel one may also remove what it
    # produced, and nothing weaker can.
    principal.require(Scope.MEDIA_WRITE, Scope.ADMIN)
    keys = collections.clean_keys(body.items)

    directories, download_ids = await downloads.directories_for(request.state.db, keys)
    freed = 0
    if body.media and directories:
        settings = request.app.state.settings
        if not settings.downloader_url:
            # There are bytes on the volume and no way to reach them. Deleting
            # the rows anyway would leave files nothing can account for and
            # nothing can remove; `media=false` is the call that says to keep
            # them on purpose.
            raise NotConfigured(
                "this post has stored media and this instance has no media "
                "downloader to remove it; start the downloader compose profile, "
                "or send media=false to delete the record and keep the files"
            )
        from dtk.media import DownloaderClient, DownloaderUnavailable

        client = DownloaderClient(settings.downloader_url, token=settings.downloader_token)
        try:
            removed = await client.delete(directories)
            freed = int(removed.get("freed_bytes") or 0)
        except DownloaderUnavailable as exc:
            # Same reasoning, one step later: the sidecar exists and did not
            # answer. Refusing leaves everything as it was, which is recoverable;
            # proceeding would not be.
            log.warning("archive.delete.downloader_failed", error=str(exc)[:200])
            raise DownloaderDown(
                "the media downloader did not answer, so nothing was deleted"
            ) from exc
        finally:
            await client.aclose()

    forgotten = await downloads.forget(request.state.db, download_ids) if body.media else 0
    deleted = await archive.remove(request.state.db, keys)
    await request.state.db.commit()

    log.info(
        "archive.deleted",
        posts=deleted,
        downloads=forgotten,
        freed_bytes=freed,
        actor=str(principal.user_id) if principal.user_id else None,
    )
    return ok(request, {"deleted": deleted, "downloads_removed": forgotten, "freed_bytes": freed})


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
