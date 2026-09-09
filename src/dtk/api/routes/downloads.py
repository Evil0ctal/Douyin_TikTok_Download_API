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
from pathlib import Path as FsPath
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.openapi import ACCEPTED_RESPONSES, I18N_KEY
from dtk.api.routes.schemas import DedupeRequest, DownloadRequest, PinRequest
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    ok,
)
from dtk.core.config import BootstrapSettings
from dtk.core.errors import DownloaderDown, InvalidParam, NotConfigured, NotFound, QueueFull
from dtk.core.logging import get_logger
from dtk.core.types import DownloadState, Platform, Scope
from dtk.ops import capacity
from dtk.services import archive, downloads, tasks
from dtk.urls import ResourceKind, first_url, identify, require_content_id

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])

#: How long a caller should wait when the disk guard has stopped new jobs. Long
#: enough that retrying is not a spin, short enough that freeing space is
#: noticed within a maintenance tick.
CAPACITY_RETRY_AFTER = 300

PLATFORM_QUERY = Query(default=None, description="Only this platform.")
STATE_QUERY = Query(default=None, description="Only downloads in this state.")
PINNED_QUERY = Query(default=None, description="Only pinned downloads, or only unpinned.")
ON_DISK_QUERY = Query(default=None, description="True for downloads whose files are still stored.")
LIMIT_QUERY = Query(default=None, ge=1, le=MAX_ADMIN_PAGE_SIZE, description="Rows per page.")
OFFSET_QUERY = Query(default=0, ge=0, description="Rows to skip.")
DOWNLOAD_ID = Path(description="The download id returned when it was started.")


def _configured(request: Request) -> None:
    """Refuse early when no downloader is running.

    A 501 rather than a 500: the sidecar is an opt-in compose profile, and an
    instance without it is correctly configured for someone who never wanted
    it. The message says how to turn it on rather than implying a fault.
    """
    settings: BootstrapSettings = request.app.state.settings
    if not settings.downloader_url:
        raise NotConfigured(
            "this instance has no media downloader; start the downloader compose "
            "profile and set DTK_DOWNLOADER_URL"
        )
    if not bool(request.app.state.config.get("media.enabled")):
        raise NotConfigured("media downloads are disabled in the settings")


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
    _configured(request)

    platform, content_id = _resolve_target(body)
    config = request.app.state.config
    verdict = capacity.evaluate(
        warn_percent=float(config.get("capacity.warn_percent")),
        hard_stop_percent=float(config.get("capacity.hard_stop_percent")),
    )
    if verdict.paused:
        raise QueueFull(
            f"new downloads are paused: {verdict.detail}",
            retry_after=CAPACITY_RETRY_AFTER,
        )

    session = request.state.db

    # Two reasons not to start a second one, and only one of them is optional.
    live, stored = await downloads.existing_for(session, platform.value, content_id)
    if live is not None:
        # Never optional. Two downloads of a post write to one directory - it is
        # keyed by platform, author and post - and the sidecar renames
        # `name.part` to `name` on the way out, so the loser renames a file the
        # winner has already moved and comes back `partial`. Measured here:
        # three requests for one post finished within 10ms and one lost a cover
        # that way. Joining is also what the caller wanted; they are waiting for
        # this post's media, and it is already on its way.
        return _existing(request, live, reason="in_flight")
    if body.skip_existing and stored is not None:
        return _existing(request, stored, reason="already_stored")

    row = await archive.get(session, platform.value, content_id)
    planned: list[dict[str, str]] = []
    skipped: list[str] = []
    if row is None:
        # Never seen. The worker fetches it before downloading, so there is
        # nothing to plan from yet and nothing to refuse on.
        try:
            download = await downloads.create_pending(
                session,
                platform=platform.value,
                content_id=content_id,
                requested_by=principal.api_key_id,
            )
        except IntegrityError:
            return await _joined(request, session, platform.value, content_id)
    else:
        # Planned here as well as in the worker, so a post that IS archived and
        # has nothing fetchable is a 400 the caller reads now rather than a task
        # that fails in a minute.
        plan = downloads.plan_for(row, max_file_bytes=int(config.get("media.max_file_bytes")))
        if plan.empty:
            raise InvalidParam(
                "; ".join(plan.skipped) or "this post has no downloadable media",
                details={"skipped": list(plan.skipped)},
            )
        planned = [{"name": item.name, "kind": item.kind} for item in plan.items]
        skipped = list(plan.skipped)
        try:
            download = await downloads.create(session, row, requested_by=principal.api_key_id)
        except IntegrityError:
            return await _joined(request, session, platform.value, content_id)
    task_id, state = await operations.submit(
        request,
        principal,
        endpoint=operations.Maintenance.MEDIA_DOWNLOAD.value,
        params={"download_id": str(download.id)},
        # Never coalesced. Two requests for the same post are two deliberate
        # acts, and the second one is usually "the first did not work" - joining
        # them onto one task would answer it with the failure it was retrying.
        coalesce=False,
        # Held back until the download row carries the task id. Otherwise a
        # worker can start before that link is written, and a request that dies
        # in the gap leaves a download nothing points at.
        publish=False,
    )
    download.task_id = task_id
    try:
        await session.commit()
    except IntegrityError:
        return await _joined(request, session, platform.value, content_id)
    await tasks.enqueue(task_id, endpoint=operations.Maintenance.MEDIA_DOWNLOAD.value)

    log.info(
        "media.download.requested",
        download_id=str(download.id),
        platform=platform.value,
        items=len(planned),
        archived=row is not None,
    )
    return ok(
        request,
        {
            "download_id": str(download.id),
            "task_id": str(task_id),
            "state": state.value,
            "directory": download.directory,
            "planned": planned,
            "skipped": skipped,
            "reused": None,
            # False means the worker has to fetch the post before it can
            # download anything, which is a slower first response.
            "archived": row is not None,
        },
        status_code=202,
    )


async def _joined(request: Request, session: Any, platform: str, content_id: str) -> Any:
    """Answer a request the in-flight index refused.

    `ux_media_downloads_in_flight` fires when another request for this post got
    there first, in the window between our check and our insert. A check cannot
    close that window - simultaneous requests all read "none in flight" before
    any of them writes - so the index is the guarantee and this is how it reads
    to a caller: the same answer the check would have given.

    The insert raises at the flush inside `create`, not at the commit, so this
    is called from three places rather than wrapped once around the end.
    """
    await session.rollback()
    live, _ = await downloads.existing_for(session, platform, content_id)
    if live is None:
        # The other request settled and cleared the way between the refusal and
        # this lookup. Nothing to join, and pretending otherwise would hand back
        # a download that is already over.
        raise QueueFull(
            "another download of this post was in flight; try again",
            retry_after=1,
        )
    return _existing(request, live, reason="in_flight")


def _existing(request: Request, download: Any, *, reason: str) -> Any:
    """Answer with a download that already exists, saying which one and why."""
    log.info(
        "media.download.reused",
        download_id=str(download.id),
        platform=download.platform,
        reason=reason,
    )
    return ok(
        request,
        {
            "download_id": str(download.id),
            "task_id": str(download.task_id) if download.task_id else None,
            "state": download.state,
            "directory": download.directory,
            "planned": [],
            "skipped": [],
            "archived": True,
            # 200 rather than 202: nothing was accepted for processing, and the
            # caller is being handed something that already exists.
            "reused": reason,
        },
    )


def _resolve_target(body: DownloadRequest) -> tuple[Platform, str]:
    """Work out which post is meant, from a link or a key.

    `identify` is the allowlist: an unrecognised host yields nothing and is
    refused here, so no URL a caller sends ever becomes a request.
    """
    if body.url:
        kind = identify(body.url)
        if not kind.allowed:
            # Both apps put a caption and a numeric code on the clipboard
            # beside the link, and that whole string is what gets pasted.
            candidate = first_url(body.url)
            if candidate is not None:
                kind = identify(candidate)
        if not kind.allowed or kind.platform is None:
            raise InvalidParam(
                "that link is not a supported Douyin or TikTok post",
                details={"field": "url"},
            )
        if kind.needs_expansion:
            raise InvalidParam(
                "a short link has to be expanded before it names a post; send it "
                "to /api/v1/parse, which follows it in the background",
                details={"field": "url", "needs_expansion": True},
            )
        if kind.resource is not ResourceKind.VIDEO or not kind.resource_id:
            raise InvalidParam(
                "that link names a profile rather than a post",
                details={"field": "url", "resource": kind.resource.value},
            )
        if body.platform is not None and body.platform is not kind.platform:
            # Guessing which one was meant is how you download the wrong post
            # from the wrong platform and call it a feature.
            raise InvalidParam(
                "the link and the platform disagree",
                details={"url_platform": kind.platform.value, "platform": body.platform.value},
            )
        if body.content_id and body.content_id != kind.resource_id:
            raise InvalidParam(
                "the link and the content_id name different posts",
                details={"url_content_id": kind.resource_id, "content_id": body.content_id},
            )
        return kind.platform, kind.resource_id

    if body.platform is None or not body.content_id:
        raise InvalidParam(
            "provide either url, or platform and content_id",
            details={"fields": ["url", "platform", "content_id"]},
        )
    # Only a typed id is checked: one extracted from a link came out of the
    # pattern that recognised the link.
    return body.platform, require_content_id(body.content_id, platform=body.platform)


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
    limit: int | None = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """What this instance has been asked to store, newest first.

    A row survives its files: one whose `on_disk` is false was collected and
    later removed to stay under the media ceiling, which is a different fact
    from never having been fetched - and unlike that one, it can be undone by
    asking again.

    **Returns**

    The downloads with their per-file digests and sizes, plus the total.
    """
    principal.require(Scope.MEDIA_READ)
    spec = downloads.DownloadFilter(platform=platform, state=state, pinned=pinned, on_disk=on_disk)
    rows, total = await downloads.search(
        request.state.db,
        spec,
        limit=limit or DEFAULT_ADMIN_PAGE_SIZE,
        offset=offset,
    )
    # One statement for the page. A table of content ids and byte counts is a
    # table of receipts; the archive already knows what was kept.
    titles = await downloads.titles_for(
        request.state.db, [(row.platform, row.content_id) for row in rows]
    )
    return ok(
        request,
        {
            "items": [
                {**downloads.as_dict(row), "post": titles.get((row.platform, row.content_id))}
                for row in rows
            ],
            "total": total,
            "limit": limit or DEFAULT_ADMIN_PAGE_SIZE,
            "offset": offset,
        },
    )


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
    whether the files are still on the disk.
    """
    principal.require(Scope.MEDIA_READ)
    row = await downloads.get(request.state.db, download_id)
    if row is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})
    return ok(request, downloads.as_dict(row))


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

    return FileResponse(
        target,
        media_type=str(entry.get("content_type") or "application/octet-stream"),
        filename=name,
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
    "/{download_id}", summary="Cancel a download", openapi_extra={I18N_KEY: "downloads_cancel"}
)
async def cancel_download(
    request: Request,
    download_id: uuid.UUID = DOWNLOAD_ID,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Stop a download that has not finished.

    Cancels the transfer; it does not delete anything. Files already complete
    stay where they are, and the record stays either way - removing files is
    the cleanup sweep's job and follows the operator's ceiling, not a DELETE.

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
