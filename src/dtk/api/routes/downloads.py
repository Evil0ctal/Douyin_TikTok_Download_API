"""Storing media on this instance's own disk, and reading back what is there.

The one part of this API with a lasting effect on the host: everything else
answers a question, and this writes files to a volume the operator has to live
with. So the whole surface is shaped around the two things that could go wrong.

**It is a sink, not a relay.** A download names a post this instance has
already archived; a caller never supplies a URL, the mirrors come from the
archive, and no endpoint here streams a stored byte back. README lists "proxy
video traffic through the server" as a non-goal and that stays true - the
difference is who the bytes are for. Doc 18 argues the distinction in full.

**It can fill a disk, so it is bounded from three directions.** A per-file
ceiling counted on bytes written, a total the volume may hold with an eviction
sweep behind it, and the capacity guard - past the hard stop, new jobs are
refused with `Retry-After` while every read here carries on. A full disk should
degrade an instance, not take it offline.

Its own scopes. `media:read` is separate from `archive:read` because seeing
what was collected and seeing what is on the operator's filesystem are
different questions; `media:write` is separate again because starting a
download spends an identity and consumes disk, which no read scope should imply.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path as FsPath
from typing import Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import transcriptions as transcription_routes
from dtk.api.routes.openapi import ACCEPTED_RESPONSES, I18N_KEY
from dtk.api.routes.schemas import DedupeRequest, DownloadRequest, PinRequest, RetryRequest
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    ok,
)
from dtk.core.config import BootstrapSettings
from dtk.core.errors import DownloaderDown, InvalidParam, NotConfigured, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import DownloadState, Platform, Scope, UserRole
from dtk.media import plan
from dtk.ops import capacity
from dtk.services import download_requests, downloads, transcriptions

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])

PLATFORM_QUERY = Query(default=None, description="Only this platform.")
STATE_QUERY = Query(default=None, description="Only downloads in this state.")
PINNED_QUERY = Query(default=None, description="Only pinned downloads, or only unpinned.")
ON_DISK_QUERY = Query(default=None, description="True for downloads whose files are still stored.")
LIMIT_QUERY = Query(default=None, ge=1, le=MAX_ADMIN_PAGE_SIZE, description="Rows per page.")
OFFSET_QUERY = Query(default=0, ge=0, description="Rows to skip.")
SEARCH_QUERY = Query(
    default=None,
    max_length=200,
    description=(
        "Substring of the post's title or author, as the archive holds them, or of its content id."
    ),
)
DOWNLOAD_ID = Path(description="The download id returned when it was started.")


async def _transcriptions_for(
    request: Request, principal: Principal, rows: Sequence[Any]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Which of these downloads already have a transcript, for a caller who may open one."""
    if not rows or not transcription_routes.may_read(principal):
        return {}
    return await transcriptions.latest_for_downloads(request.state.db, [row.id for row in rows])


@router.post(
    "",
    summary="Store a post's media",
    openapi_extra={I18N_KEY: "downloads_create", **ACCEPTED_RESPONSES},
)
async def start_download(
    request: Request,
    body: DownloadRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Fetch one post's media onto this instance's disk.

    Takes a post id or a link. A link is not a URL this endpoint will fetch:
    it goes through `dtk.urls.identify`, which is the allowlist doc 08 puts in
    front of every caller-supplied URL, and only the post id it yields is used.
    The request then goes to the platform's own endpoint table exactly as if the
    id had been typed.

    The post does not have to be archived. If this instance has never seen it,
    the worker fetches it first - through the same pool, scheduler and request
    log as any other read - and downloads what comes back. A downloader that
    could only save what you happened to parse first is two steps where people
    expect one.

    Short links are the exception: resolving `v.douyin.com/xxxx` means following
    it, and this endpoint does not make network calls. Send those to `/parse`,
    which expands them in the background, and the post is archived by the time
    you come back.

    **Parameters**

    - `url` - a share link, or the share text around one.
    - `platform`, `content_id` - the post key, if you already have it. A link
      supplies both; giving both a link and a key that disagree is refused
      rather than guessed at.
    - `skip_existing` - hand back the download this instance already has rather
      than fetching the post again. What re-running a feed wants: the author
      added three posts and the other forty are on the disk already.
    - `part` - Bilibili only: which part of a video uploaded in several, from
      1. A link's `?p=` says the same thing; omitted, it is the first part.
      Each part is its own download, stored in its own directory, and is
      transcribed as one.

    A download already in flight for the same post is joined whatever
    `skip_existing` says, and that is not a preference. Two downloads of one
    post write into one directory, and the sidecar renames `name.part` to
    `name` as it finishes - so the loser of that race renames a file the winner
    has already moved, and comes back `partial` with a file missing.

    **Returns**

    `202` with a `download_id` and the `task_id` running it. Poll the download
    or the task; the files land on the operator's media volume and are never
    served back through this API.
    """
    principal.require(Scope.MEDIA_WRITE)
    result = await download_requests.start(
        request.state.db,
        config=request.app.state.config,
        downloader_configured=bool(request.app.state.settings.downloader_url),
        url=body.url,
        platform=body.platform,
        content_id=body.content_id,
        skip_existing=body.skip_existing,
        requested_by=principal.api_key_id,
        is_demo=principal.role is UserRole.DEMO,
        part=body.part,
    )
    return ok(request, result.as_dict(), status_code=200 if result.reused else 202)


#: How many live downloads to ask the sidecar about on one listing. The number
#: in flight is normally one or two - the sidecar has a small worker pool - and
#: a page that made forty extra calls to draw forty progress bars would cost
#: more than the bars are worth.
PROGRESS_LOOKUP_LIMIT: Final = 8


async def _live_progress(request: Request, rows: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Ask the sidecar how far along the in-flight downloads are.

    Read from the sidecar rather than stored, because the durable record is
    written once when the job settles and progress is only interesting before
    then. The job id is the download id, so nothing has to be kept to find it.

    Best effort throughout. A sidecar that is not configured, is down, or has
    forgotten a job - it keeps a bounded history and loses everything on
    restart - means the row draws without a bar, which is what it did before.
    A progress bar is not worth failing a listing over.
    """
    live = [row for row in rows if row.state in downloads.LIVE_STATES][:PROGRESS_LOOKUP_LIMIT]
    settings: BootstrapSettings = request.app.state.settings
    if not live or not settings.downloader_url:
        return {}

    from dtk.media import DownloaderClient, DownloaderUnavailable

    client = DownloaderClient(settings.downloader_url, token=settings.downloader_token)
    found: dict[str, dict[str, Any]] = {}
    try:
        for row in live:
            try:
                job = await client.job(row.id.hex)
            except DownloaderUnavailable:
                break
            if not job:
                continue
            found[str(row.id)] = progress_of(job)
    finally:
        await client.aclose()
    return found


def progress_of(job: Mapping[str, Any]) -> dict[str, Any]:
    """One in-flight job, as the listing reports it.

    ``bytes`` is summed over the files rather than read from the job's
    ``bytes_total``, which the sidecar writes only when the job settles. Read
    from there it stayed at zero for the whole of a 100 MB Bilibili video and
    then jumped, so the one number meant to keep moving never did.
    """
    items = [item for item in (job.get("items") or []) if isinstance(item, dict)]
    files = [
        {
            "name": str(item.get("name") or ""),
            "kind": str(item.get("kind") or ""),
            "state": str(item.get("state") or ""),
            "bytes": int(item.get("bytes") or 0),
            "bytes_expected": int(item.get("bytes_expected") or 0) or None,
            "bytes_per_second": int(item.get("bytes_per_second") or 0),
        }
        for item in items
    ]
    return {
        "bytes": sum(file["bytes"] for file in files),
        "bytes_expected": _expected_total(files),
        "bytes_per_second": sum(file["bytes_per_second"] for file in files),
        "files_done": sum(1 for file in files if file["state"] == "done"),
        "files_total": len(files),
        "state": str(job.get("state") or ""),
        # Per file, so a stalled cover next to a finished video is visible as
        # that rather than as one number not moving.
        "items": files,
    }


def _expected_total(files: Sequence[Mapping[str, Any]]) -> int | None:
    """What the whole download will weigh, or None while any part is unknown.

    A finished file counts what landed and a failed one nothing. A running one
    counts the size its server declared - for display only; the ceiling is
    enforced on bytes written, in the sidecar. A file not started yet, or one
    whose server declared nothing, makes the total unknown, and the page falls
    back to counting files rather than drawing a bar against a guess.
    """
    total = 0
    for file in files:
        if file["state"] == "done":
            total += file["bytes"]
        elif file["state"] == "failed":
            continue
        elif file["bytes_expected"]:
            total += max(file["bytes_expected"], file["bytes"])
        else:
            return None
    return total


@router.post(
    "/retry",
    summary="Try a download again",
    openapi_extra={I18N_KEY: "downloads_retry", **ACCEPTED_RESPONSES},
)
async def retry_download(
    request: Request,
    body: RetryRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Start a settled download over.

    There is no resume, and there could not usefully be one. The sidecar writes
    to `<name>.part` and truncates it on every attempt, and the reason it is
    built that way is upstream: media URLs are signed and expire, so bytes
    fetched an hour ago cannot be continued against a link that now answers 403.
    What works instead is what this does - ask again, which re-parses the post
    for fresh mirrors first if the stored ones have gone stale.

    Refused while the download is still going. A stuck one is failed by the
    maintenance pass after `maintenance.stale_download_seconds`, and until then
    there is nothing to retry: a second attempt would race the first into the
    same directory.

    **Parameters**

    - `download_id` - the settled download to try again.

    **Returns**

    `202` with the new `download_id` and the task running it.
    """
    principal.require(Scope.MEDIA_WRITE)
    # Before the lookup, as it always was: on an instance with no downloader
    # the answer to every retry is "not configured", not a 404 or a 400 about
    # a row that could not be retried anyway.
    download_requests.ensure_configured(
        downloader_configured=bool(request.app.state.settings.downloader_url),
        config=request.app.state.config,
    )

    session = request.state.db
    row = await downloads.get(session, body.download_id)
    if row is None:
        raise NotFound("no such download", details={"download_id": str(body.download_id)})
    if row.state in downloads.LIVE_STATES:
        raise InvalidParam(
            "that download is still running; cancel it first, or wait for it to settle",
            details={"download_id": str(body.download_id), "state": row.state},
        )

    return await start_download(
        request,
        DownloadRequest(platform=Platform(row.platform), content_id=row.content_id),
        principal,
    )


@router.post(
    "/deduplicate",
    summary="Remove duplicate downloads",
    openapi_extra={I18N_KEY: "downloads_dedupe"},
)
async def deduplicate(
    request: Request,
    body: DedupeRequest | None = None,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Keep one copy of each post and remove the rest.

    Re-downloading a post is a normal thing to do - the first attempt failed,
    the mirrors went stale, the file was evicted - and each attempt leaves a
    row. The successful ones leave a complete second copy of the same video,
    which is the disk filling with bytes nobody asked for twice.

    The newest copy that still has its files is kept: newest because it came
    from the freshest mirrors, still-present because keeping an evicted row over
    a present one would delete the only copy there is.

    A directory is only removed when no surviving row shares it. Two downloads
    of one post land in the same directory - it is keyed by platform, author and
    post - so deleting the older row's path by id would delete the file the
    newer one just wrote. In that ordinary case the rows go and the bytes stay,
    which is the correct outcome: there was only ever one copy on disk.

    **Parameters**

    - `dry_run` - report what would go and change nothing.

    **Returns**

    How many duplicate records were found, how many were removed, the bytes the
    sidecar reported freeing, and the posts involved.
    """
    principal.require(Scope.MEDIA_WRITE)
    dry_run = body.dry_run if body is not None else False

    session = request.state.db
    duplicates = await downloads.find_duplicates(session)
    posts = [
        {
            "platform": item.platform,
            "content_id": item.content_id,
            "copies": len(item.drop) + 1,
            "removing": len(item.drop),
            "paths": list(item.paths),
        }
        for item in duplicates
    ]
    summary: dict[str, Any] = {
        "duplicates": sum(len(item.drop) for item in duplicates),
        "posts": posts,
        "dry_run": dry_run,
        "removed": 0,
        "freed_bytes": 0,
    }
    if dry_run or not duplicates:
        return ok(request, summary)

    directories = sorted({path for item in duplicates for path in item.paths})
    freed = 0
    if directories:
        settings: BootstrapSettings = request.app.state.settings
        if not settings.downloader_url:
            raise NotConfigured(
                "some duplicates own files this instance cannot reach; start the "
                "downloader compose profile, or the records would be removed and "
                "the bytes left behind"
            )
        from dtk.media import DownloaderClient, DownloaderUnavailable

        client = DownloaderClient(settings.downloader_url, token=settings.downloader_token)
        try:
            removed = await client.delete(directories)
            freed = int(removed.get("freed_bytes") or 0)
        except DownloaderUnavailable as exc:
            # Same rule as deleting an archived post: refuse rather than leave
            # bytes nothing points at.
            log.warning("media.dedupe.downloader_failed", error=str(exc)[:200])
            raise DownloaderDown(
                "the media downloader did not answer, nothing was removed"
            ) from exc
        finally:
            await client.aclose()

    summary["removed"] = await downloads.forget(
        session, [download_id for item in duplicates for download_id in item.drop]
    )
    summary["freed_bytes"] = freed
    await session.commit()
    log.info(
        "media.dedupe",
        posts=len(duplicates),
        removed=summary["removed"],
        freed_bytes=freed,
        directories=len(directories),
    )
    return ok(request, summary)


@router.get("", summary="List downloads", openapi_extra={I18N_KEY: "downloads_list"})
async def list_downloads(
    request: Request,
    platform: Platform | None = PLATFORM_QUERY,
    state: DownloadState | None = STATE_QUERY,
    pinned: bool | None = PINNED_QUERY,
    on_disk: bool | None = ON_DISK_QUERY,
    q: str | None = SEARCH_QUERY,
    limit: int | None = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """What this instance has been asked to store, newest first.

    A row survives its files: one whose `on_disk` is false was collected and
    later removed to stay under the media ceiling, which is a different fact
    from never having been fetched - and unlike that one, it can be undone by
    asking again.

    **Parameters**

    - `platform`, `state`, `pinned`, `on_disk` - narrow the result.
    - `q` - substring of the post's title or author as the archive holds them,
      or of its content id. Substring rather than word matching, so it behaves
      the same in Chinese as in English.
    - `limit`, `offset` - paging. `total` counts every match, not the page.

    **Returns**

    The downloads with their per-file digests and sizes, plus the total. A
    download still in flight also carries `progress`, read live from the
    downloader: `bytes` written so far, `bytes_expected` once every file's size
    is known (null until then), `bytes_per_second` over the last few seconds,
    and the same per file. It is null once the download settles.

    `transcription` is the newest queued, running or finished transcription of
    the download - its id and state - or null. Null as well for a caller who
    may not read transcriptions.
    """
    principal.require(Scope.MEDIA_READ)
    spec = downloads.DownloadFilter(
        platform=platform, state=state, pinned=pinned, on_disk=on_disk, query=q
    )
    rows, total = await downloads.search(
        request.state.db,
        spec,
        limit=limit or DEFAULT_ADMIN_PAGE_SIZE,
        offset=offset,
    )
    return ok(
        request,
        {
            "items": await _described(request, principal, rows),
            "total": total,
            "limit": limit or DEFAULT_ADMIN_PAGE_SIZE,
            "offset": offset,
        },
    )


async def _described(
    request: Request, principal: Principal, rows: Sequence[Any]
) -> list[dict[str, Any]]:
    """Download rows with what the console shows beside them.

    Shared by the listing and the single read, so a drawer opened from a link -
    `/downloads?id=` - shows the same title, progress and transcript link as
    one opened from the table.
    """
    # One statement for the page. A table of content ids and byte counts is a
    # table of receipts; the archive already knows what was kept.
    titles = await downloads.titles_for(
        request.state.db, [(row.platform, row.content_id) for row in rows]
    )
    # Only the in-flight rows cost anything here, and only while there are any.
    progress = await _live_progress(request, rows)
    transcribed = await _transcriptions_for(request, principal, rows)
    return [
        {
            **downloads.as_dict(row),
            "post": titles.get((row.platform, row.content_id)),
            "progress": progress.get(str(row.id)),
            "transcription": transcribed.get(row.id),
        }
        for row in rows
    ]


@router.get(
    "/storage", summary="Media storage usage", openapi_extra={I18N_KEY: "downloads_storage"}
)
async def storage(
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """How much disk stored media occupies, and how much it is allowed to.

    The measured total comes from the volume rather than from the database, so
    it accounts for files removed by hand and for anything a restore put back.

    **Returns**

    Totals per state, the measured volume usage, the configured ceiling, and
    whether the downloader is reachable at all.
    """
    principal.require(Scope.MEDIA_READ)
    config = request.app.state.config
    settings: BootstrapSettings = request.app.state.settings

    totals = await downloads.stats(request.state.db)
    health: dict[str, Any] = {"available": False, "detail": "not configured"}
    if settings.downloader_url:
        from dtk.media import DownloaderClient

        client = DownloaderClient(settings.downloader_url, token=settings.downloader_token)
        try:
            probed = await client.health()
            health = {
                "available": probed.available,
                "version": probed.version,
                "workers": probed.workers,
                "queued": probed.queued,
                "running": probed.running,
                "volume_bytes": probed.total_bytes,
                "detail": probed.detail,
            }
        finally:
            await client.aclose()

    return ok(
        request,
        {
            **totals,
            "enabled": bool(config.get("media.enabled")),
            "max_bytes": int(config.get("media.max_bytes")),
            "max_file_bytes": int(config.get("media.max_file_bytes")),
            "downloader": health,
        },
    )


@router.get("/{download_id}", summary="One download", openapi_extra={I18N_KEY: "downloads_get"})
async def get_download(
    request: Request,
    download_id: uuid.UUID = DOWNLOAD_ID,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """One download and every file in it.

    **Returns**

    The stored record: per-file name, size, sha256 and content type, plus
    whether the files are still on the disk - and, as in the listing, the
    post's title, live `progress` while it runs, and its `transcription`.
    """
    principal.require(Scope.MEDIA_READ)
    row = await downloads.get(request.state.db, download_id)
    if row is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})
    [described] = await _described(request, principal, [row])
    return ok(request, described)


@router.get(
    "/{download_id}/files/{name}",
    summary="Download one stored file",
    openapi_extra={I18N_KEY: "downloads_file"},
    # Bytes, not the envelope. Declared so the generated document does not
    # promise a JSON object and then hand back an mp4.
    response_class=FileResponse,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "The stored file."}
    },
)
async def get_file(
    request: Request,
    download_id: uuid.UUID = DOWNLOAD_ID,
    name: str = Path(description="The file's name, exactly as the download record lists it."),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Serve one file this instance has already stored, to a browser.

    The path is rebuilt from the stored row - the download's own directory plus
    a name that has to appear in its file list - so the only thing a caller
    chooses is *which* record, never where on the disk to read. A name that is
    not in the record is a 404 whether or not something of that name exists.

    Nothing is fetched: if the files were evicted to stay under the media
    ceiling the record survives without them, and this answers 404 rather than
    going back to the platform. Ask for the download again to restore it.

    **Parameters**

    - `download_id` - the record, from `GET /downloads`.
    - `name` - one of the names in that record's `files`.

    **Returns**

    The file, with the content type the downloader recorded for it and a
    Content-Disposition that makes a browser save rather than render it.
    """
    principal.require(Scope.MEDIA_READ)
    row = await downloads.get(request.state.db, download_id)
    if row is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})
    if row.files_removed_at is not None:
        raise NotFound(
            "this download's files were removed to stay under the media ceiling",
            details={"download_id": str(download_id), "reason": "evicted"},
        )

    entry = next(
        (item for item in (row.files or []) if isinstance(item, dict) and item.get("name") == name),
        None,
    )
    if entry is None or entry.get("state") != "done":
        raise NotFound(
            "this download has no completed file by that name",
            details={"download_id": str(download_id), "name": name},
        )

    # Two independent checks, because one of them being enough is exactly the
    # assumption path traversal is built on. The name came out of the stored
    # record rather than off the wire, and the resolved path still has to land
    # inside the media root.
    root = FsPath(capacity.MEDIA_PATH).resolve()
    try:
        target = (root / str(row.directory) / name).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        log.warning("media.path_escaped", download_id=str(download_id))
        raise NotFound("no such file", details={"download_id": str(download_id)}) from None
    if not target.is_file():
        raise NotFound(
            "the record lists this file but it is not on the volume",
            details={"download_id": str(download_id), "name": name},
        )

    # Not `name`: on the volume that is `video.mp4`, which is fine inside the
    # post's own directory and useless in a browser's downloads folder. The
    # naming policy lives with the rest of it in dtk.media.plan.
    return FileResponse(
        target,
        media_type=str(entry.get("content_type") or "application/octet-stream"),
        filename=plan.export_name(str(row.platform), str(row.content_id), name),
    )


@router.post(
    "/{download_id}/pin", summary="Pin or unpin", openapi_extra={I18N_KEY: "downloads_pin"}
)
async def pin_download(
    request: Request,
    body: PinRequest,
    download_id: uuid.UUID = DOWNLOAD_ID,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Exempt one download from the size-based cleanup, or stop exempting it.

    This is the only way to keep a file the ceiling would otherwise remove.
    Without it, a policy that deletes oldest-first eventually takes the one
    post somebody meant to keep - and a post the platform has since removed
    cannot be fetched again.

    **Parameters**

    - `pinned` - true to keep it, false to let the sweep have it.
    """
    principal.require(Scope.MEDIA_WRITE)
    if not await downloads.set_pinned(request.state.db, download_id, body.pinned):
        raise NotFound("no such download", details={"download_id": str(download_id)})
    await request.state.db.commit()
    log.info("media.download.pinned", download_id=str(download_id), pinned=body.pinned)
    return ok(request, {"download_id": str(download_id), "pinned": body.pinned})


@router.delete(
    "/{download_id}/files",
    summary="Delete a download's files",
    openapi_extra={I18N_KEY: "downloads_delete_files"},
)
async def delete_download_files(
    request: Request,
    download_id: uuid.UUID = DOWNLOAD_ID,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Remove what a download stored, and keep the record that it was stored.

    The record reads as cleaned up afterwards, exactly as when the size ceiling
    removes it: the downloads page can still tell "had this and removed it" from
    "never fetched it", and asking for the download again brings the files back.
    The console's Delete on the downloads page always promised this and was
    wired to the cancel below instead, which leaves a finished download as it
    is - so it reported success and removed nothing.

    The downloads of one post share a directory, so this removes that directory
    and marks every record in it. It is refused, and nothing is removed, while
    any of them is still running - that one is writing into the directory - or
    pinned, because a pin is the promise that these files stay. Removing the
    post and its records altogether is `POST /api/v1/archive/delete`.

    **Returns**

    How many records were marked and how many bytes the sidecar freed. Files
    already gone answer the same, with nothing marked and nothing freed.
    """
    principal.require(Scope.MEDIA_WRITE)
    session = request.state.db
    row = await downloads.get(session, download_id)
    if row is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})
    if row.files_removed_at is not None or not row.directory:
        return ok(request, {"download_id": str(download_id), "downloads": 0, "freed_bytes": 0})

    sharing = await downloads.sharing_directory(session, row.directory)
    running = [str(other.id) for other in sharing if other.state in downloads.LIVE_STATES]
    if running:
        raise InvalidParam(
            "a download of this post is still running; cancel it first, or wait for it to settle",
            details={"download_id": str(download_id), "running": running},
        )
    pinned = [str(other.id) for other in sharing if other.pinned]
    if pinned:
        raise InvalidParam(
            "this post is pinned, which is the promise that its files stay; unpin it first",
            details={"download_id": str(download_id), "pinned": pinned},
        )

    settings: BootstrapSettings = request.app.state.settings
    if not settings.downloader_url:
        # The bytes are on the volume and only the sidecar can reach them.
        # Marking the rows anyway would leave files nothing accounts for.
        raise NotConfigured(
            "this instance has no media downloader to remove the files; "
            "start the downloader compose profile"
        )
    from dtk.media import DownloaderClient, DownloaderUnavailable

    client = DownloaderClient(settings.downloader_url, token=settings.downloader_token)
    try:
        removed = await client.delete([row.directory])
    except DownloaderUnavailable as exc:
        log.warning("media.download.delete_failed", download_id=str(download_id), error=str(exc))
        raise DownloaderDown("the media downloader did not answer, so nothing was deleted") from exc
    finally:
        await client.aclose()
    # Only what the sidecar confirmed is marked, as the eviction sweep does: a
    # row that says its files are gone while they are on the disk is a row no
    # sweep would ever reclaim.
    if row.directory not in {str(path) for path in removed.get("removed") or []}:
        raise DownloaderDown("the media downloader did not confirm removing the files")

    marked = await downloads.mark_evicted(session, [other.id for other in sharing])
    await session.commit()
    freed = int(removed.get("freed_bytes") or 0)
    log.info(
        "media.download.files_deleted",
        download_id=str(download_id),
        downloads=marked,
        freed_bytes=freed,
        actor=str(principal.user_id) if principal.user_id else None,
    )
    return ok(request, {"download_id": str(download_id), "downloads": marked, "freed_bytes": freed})


@router.delete(
    "/{download_id}", summary="Cancel a download", openapi_extra={I18N_KEY: "downloads_cancel"}
)
async def cancel_download(
    request: Request,
    download_id: uuid.UUID = DOWNLOAD_ID,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Stop a download that has not finished.

    Cancels the transfer; it does not delete anything. Files already complete
    stay where they are, and the record stays either way. Removing files is
    `DELETE /api/v1/downloads/{download_id}/files`, or the cleanup sweep once
    the operator's ceiling is reached.

    **Returns**

    The download's state after the request.
    """
    principal.require(Scope.MEDIA_WRITE)
    session = request.state.db
    row = await downloads.get(session, download_id)
    if row is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})
    if row.state in downloads.TERMINAL_STATES:
        return ok(request, {"download_id": str(download_id), "state": row.state})

    settings: BootstrapSettings = request.app.state.settings
    if settings.downloader_url:
        from dtk.media import DownloaderClient

        client = DownloaderClient(settings.downloader_url, token=settings.downloader_token)
        try:
            await client.cancel(row.id.hex)
        finally:
            await client.aclose()

    cancelled = await downloads.cancel(session, download_id)
    await session.commit()
    refreshed = await downloads.get(session, download_id)
    state = refreshed.state if refreshed else "cancelled"
    log.info("media.download.cancelled", download_id=str(download_id), applied=cancelled)
    return ok(request, {"download_id": str(download_id), "state": state})


__all__ = ["router"]
