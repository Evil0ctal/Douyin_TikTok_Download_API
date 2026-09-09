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
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes import operations
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.schemas import DownloadRequest, PinRequest
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    ok,
)
from dtk.core.config import BootstrapSettings
from dtk.core.errors import InvalidParam, NotConfigured, NotFound, QueueFull
from dtk.core.logging import get_logger
from dtk.core.types import Platform, Scope
from dtk.ops import capacity
from dtk.services import archive, downloads, tasks

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])

#: How long a caller should wait when the disk guard has stopped new jobs. Long
#: enough that retrying is not a spin, short enough that freeing space is
#: noticed within a maintenance tick.
CAPACITY_RETRY_AFTER = 300

PLATFORM_QUERY = Query(default=None, description="Only this platform.")
STATE_QUERY = Query(default=None, description="queued, running, done, partial, failed, cancelled.")
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


@router.post("", summary="Store a post's media", openapi_extra={I18N_KEY: "downloads_create"})
async def start_download(
    request: Request,
    body: DownloadRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Fetch one archived post's media onto this instance's disk.

    The post must already be in the archive - this endpoint takes a content key,
    never a URL, so nothing a caller sends decides what gets fetched. If the
    stored links have gone stale the worker re-parses the post first, which
    spends an identity from the ordinary pool.

    **Parameters**

    - `platform` - douyin or tiktok.
    - `content_id` - the post id, as the archive holds it.

    **Returns**

    `202` with a `download_id` and the `task_id` running it. Poll the download
    or the task; the files land on the operator's media volume and are never
    served back through this API.
    """
    principal.require(Scope.MEDIA_WRITE)
    _configured(request)

    try:
        platform = Platform(body.platform)
    except ValueError as exc:
        raise InvalidParam("platform must be douyin or tiktok") from exc

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
    row = await archive.get(session, platform.value, body.content_id)
    if row is None:
        raise NotFound(
            "this instance has not archived that post; parse it first",
            details={"platform": platform.value, "content_id": body.content_id},
        )

    # Planned here as well as in the worker, so a post with nothing fetchable is
    # a 400 the caller reads now rather than a task that fails in a minute.
    plan = downloads.plan_for(row, max_file_bytes=int(config.get("media.max_file_bytes")))
    if plan.empty:
        raise InvalidParam(
            "; ".join(plan.skipped) or "this post has no downloadable media",
            details={"skipped": list(plan.skipped)},
        )

    download = await downloads.create(session, row, requested_by=principal.api_key_id)
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
    await session.commit()
    await tasks.enqueue(task_id, endpoint=operations.Maintenance.MEDIA_DOWNLOAD.value)

    log.info(
        "media.download.requested",
        download_id=str(download.id),
        platform=platform.value,
        items=len(plan.items),
    )
    return ok(
        request,
        {
            "download_id": str(download.id),
            "task_id": str(task_id),
            "state": state.value,
            "directory": download.directory,
            "planned": [{"name": item.name, "kind": item.kind} for item in plan.items],
            "skipped": list(plan.skipped),
        },
        status_code=202,
    )


@router.get("", summary="List downloads", openapi_extra={I18N_KEY: "downloads_list"})
async def list_downloads(
    request: Request,
    platform: str | None = PLATFORM_QUERY,
    state: str | None = STATE_QUERY,
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
    return ok(
        request,
        {
            "items": [downloads.as_dict(row) for row in rows],
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
