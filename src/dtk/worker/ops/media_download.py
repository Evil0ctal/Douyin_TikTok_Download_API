"""Store one post's media on the operator's disk.

Submitted by ``POST /api/v1/downloads`` with ``{"download_id": ...}`` and read
back by ``web/src/pages/Downloads.tsx``, so the result shape is a contract with
that page: ``download_id``, ``state``, ``bytes_total``, ``file_count``,
``files``, ``skipped`` and ``detail``.

The job runs here rather than in the API process for the usual reason - the api
container never opens a socket to a platform (doc 01) - and for one more: this
is the only operation that may re-parse a post before it acts, because signed
CDN links expire. Measured on 2026-09-08: a TikTok video URL stored the day
before answered 403, a Douyin one from the same archive still served. Rather
than guess a per-platform lifetime, the age of the observation decides, and the
window is ``media.mirror_max_age_seconds``.

What this module never does:

* accept a URL from a caller - it takes a ``download_id``, reads the archive,
  and every mirror is checked against the media allowlist before a job exists;
* hand the sidecar a cookie, a proxy, a key or a database address;
* delete anything. Eviction is the maintenance sweep's, and it is separate.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from dtk.core.errors import Internal, InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.db.models import ArchivedContent
from dtk.media import DownloaderBusy, DownloaderUnavailable
from dtk.services import archive, downloads
from dtk.services.fetch import FetchContext
from dtk.worker import registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)

#: How often the sidecar is asked how a job is going. Short, because the answer
#: is a map lookup on the other side and a long poll would make a two-second
#: image download look like a ten-second one in the console.
POLL_INTERVAL_SECONDS: Final = 2.0

#: Ceiling on one job. Long: a 250 MB video over a slow link is a legitimate
#: half hour. What bounds a runaway is the per-file byte ceiling, not this.
POLL_TIMEOUT_SECONDS: Final = 3600.0


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Fetch the media for one archived post and record what landed."""
    client = deps.downloader
    if client is None or not client.configured:
        raise Internal(
            "this instance has no media downloader; start the downloader compose "
            "profile and set DTK_DOWNLOADER_URL"
        )

    download_id = _download_id(params)
    download = await downloads.get(session, download_id)
    if download is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})

    config = deps.config()
    if not bool(config.get("media.enabled")):
        await downloads.fail(session, download_id, "media downloads are disabled")
        raise InvalidParam("media downloads are disabled")

    row = await _content(deps, session, download, config)
    if row is None:
        await downloads.fail(session, download_id, "this instance has not archived that post")
        raise NotFound(
            "this instance has not archived that post",
            details={"platform": download.platform, "content_id": download.content_id},
        )

    # A download created before its post was archived carries a placeholder
    # author, and the author is half the directory. Now that the fetch has
    # come back, the row can say where its files really go - before the job is
    # built from it, which is what reads `directory`.
    await downloads.adopt(session, download, row)

    plan = downloads.plan_for(row, max_file_bytes=int(config.get("media.max_file_bytes")))
    if plan.empty:
        detail = "; ".join(plan.skipped) or "this post has no downloadable media"
        await downloads.fail(session, download_id, detail)
        return {
            "download_id": str(download_id),
            "state": "failed",
            "bytes_total": 0,
            "file_count": 0,
            "files": [],
            "skipped": list(plan.skipped),
            "detail": detail,
        }

    await downloads.mark_running(session, download_id)
    await session.commit()

    payload = downloads.job_payload(download, row, plan)
    try:
        await client.submit(payload)
    except DownloaderBusy as exc:
        # Not a failure of this post. The row goes back to queued so the same
        # task can be resubmitted, rather than recording a permanent failure
        # for a transient queue.
        await downloads.fail(session, download_id, f"{exc}; try again shortly")
        raise Internal(str(exc)) from exc
    except DownloaderUnavailable as exc:
        await downloads.fail(session, download_id, str(exc))
        raise Internal(str(exc)) from exc

    job = await _await_job(client, payload["id"])
    if job is None:
        detail = "the downloader stopped reporting on this job"
        await downloads.fail(session, download_id, detail)
        return {
            "download_id": str(download_id),
            "state": "failed",
            "bytes_total": 0,
            "file_count": 0,
            "files": [],
            "skipped": list(plan.skipped),
            "detail": detail,
        }

    await downloads.apply_result(session, download_id, job)
    stored = await downloads.get(session, download_id)
    log.info(
        "media.download.finished",
        download_id=str(download_id),
        platform=download.platform,
        state=job.get("state"),
        bytes=job.get("bytes_total"),
    )
    return {
        "download_id": str(download_id),
        "state": str(job.get("state") or "failed"),
        "bytes_total": int(job.get("bytes_total") or 0),
        "file_count": int(stored.file_count) if stored else 0,
        "files": list(stored.files or []) if stored else [],
        "skipped": list(plan.skipped),
        "detail": str(job.get("error") or ""),
    }


async def _content(
    deps: OperationDeps,
    session: AsyncSession,
    download: Any,
    config: Any,
) -> ArchivedContent | None:
    """The archive row: fetched if we have never seen it, refreshed if stale."""
    row = await archive.get(session, download.platform, download.content_id)
    if row is None:
        # Never archived. Fetch it now rather than refusing - a downloader that
        # can only save what you happened to parse first is two steps where
        # people expect one. The fetch goes through the same FetchService as
        # every other read, so it spends an identity from the ordinary pool,
        # obeys the scheduler, and writes a request_log row.
        return await _reparse(deps, session, download)
    max_age = int(config.get("media.mirror_max_age_seconds"))
    if not downloads.mirrors_are_stale(row, max_age_seconds=max_age):
        return row

    refreshed = await _reparse(deps, session, download)
    if refreshed is not None:
        return refreshed
    # A re-parse that failed is not fatal: the stored mirrors may still work,
    # and finding out costs one request that the operator already asked for.
    log.info(
        "media.download.reparse_failed",
        platform=download.platform,
        action="using the archived mirrors",
    )
    return row


async def _reparse(
    deps: OperationDeps, session: AsyncSession, download: Any
) -> ArchivedContent | None:
    """Fetch the post again so the mirrors are fresh, and re-archive it.

    Uses the same FetchService the ordinary read path uses, so this spends an
    identity from the same pool under the same scheduler and writes the same
    request_log row - a download is not a way around rate limiting, and it is
    not a way to make a platform request that nothing records.
    """
    fetch = deps.fetch
    if fetch is None:
        return None

    config = deps.config()
    parsed: list[Any] = []
    try:
        platform = Platform(download.platform)
        call = registry.resolve(
            f"{platform.value}.content_detail", {"content_id": download.content_id}, config
        )

        def capture(payload: dict[str, Any]) -> Any:
            model = call.parse(payload)
            parsed.append(model)
            return model

        await fetch.fetch(
            session,
            call.platform,
            call.endpoint,
            call.params,
            parse=capture,
            cache_ttl=0,
            ctx=FetchContext(),
        )
    except Exception as exc:
        log.warning(
            "media.download.reparse_error",
            platform=download.platform,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return None

    if not parsed:
        return None
    # cache_ttl is 0 above on purpose: a cache hit returns the stored dict
    # without invoking the parser, so there would be no model to archive and
    # the mirrors would be exactly as old as the ones being replaced.
    await archive.record(session, (parsed[-1],), store_raw=bool(config.get("archive.store_raw")))
    await session.commit()
    return await archive.get(session, download.platform, download.content_id)


async def _await_job(client: Any, job_id: str) -> dict[str, Any] | None:
    """Poll until the sidecar says the job is over.

    A missing job is treated as over rather than retried forever: the sidecar
    keeps a bounded history and forgets everything on restart, and a poller
    that waited for a job nobody remembers would hold a worker slot until the
    timeout.
    """
    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        job = await client.job(job_id)
        if job is None:
            return None
        state = str(job.get("state") or "")
        if state in downloads.TERMINAL_STATES:
            return job
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return None


def _download_id(params: Mapping[str, Any]) -> uuid.UUID:
    raw = params.get("download_id")
    if not isinstance(raw, str):
        raise InvalidParam("download_id is required")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise InvalidParam("download_id must be a UUID") from exc


__all__ = ["run"]
