"""Transcribe one completed downloaded video through the offline sidecar."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from dtk.core.errors import InvalidParam, NotFound, TranscriberDown
from dtk.core.logging import get_logger
from dtk.db.models import MediaDownload
from dtk.services import transcription_progress, transcriptions
from dtk.transcriber import TranscriberUnavailable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

POLL_SECONDS: Final = 1.0
log = get_logger(__name__)


async def _publish_progress(transcription_id: uuid.UUID, job: dict[str, Any]) -> None:
    """Make progress observable without ever making transcription depend on it."""
    if str(job.get("state") or "") not in ("queued", "running"):
        return
    try:
        await transcription_progress.publish(transcription_id, job)
    except Exception as exc:  # progress is best-effort; the durable job is authoritative
        log.warning(
            "transcription.progress_publish_failed",
            transcription_id=str(transcription_id),
            error=repr(exc),
        )


async def _clear_progress(transcription_id: uuid.UUID) -> None:
    try:
        await transcription_progress.clear(transcription_id)
    except Exception as exc:  # the Redis TTL is the final cleanup guard
        log.warning(
            "transcription.progress_clear_failed",
            transcription_id=str(transcription_id),
            error=repr(exc),
        )


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        transcription_id = uuid.UUID(str(params.get("transcription_id") or ""))
    except ValueError:
        raise InvalidParam("transcription_id must be a UUID") from None
    row = await transcriptions.get(session, transcription_id)
    if row is None:
        raise NotFound("no such transcription")
    if row.state == "done":
        await _clear_progress(row.id)
        return transcriptions.as_dict(row, reused="completed")
    if row.state == "cancelled":
        await _clear_progress(row.id)
        return transcriptions.as_dict(row)
    if row.download_id is None:
        await transcriptions.fail(session, row.id, "the source download was deleted")
        raise NotFound("the source download was deleted")
    download = await session.get(MediaDownload, row.download_id)
    if download is None or download.files_removed_at is not None:
        await transcriptions.fail(session, row.id, "the source video is no longer on disk")
        raise NotFound("the source video is no longer on disk")
    client = deps.transcriber
    if client is None or not client.configured:
        await transcriptions.fail(session, row.id, "the transcriber is not configured")
        raise TranscriberDown("the transcriber is not configured")

    await transcriptions.mark_running(session, row.id)
    await session.commit()
    payload = {
        "id": row.id.hex,
        "download_id": str(row.download_id),
        "path": transcriptions.relative_path(row, download),
        "normalize": row.normalization_requested,
        "normalization_failure_policy": row.normalization_failure_policy or "fallback",
        "model": row.model,
    }
    try:
        # A worker may restart after the sidecar already finished. Read first so
        # recovery reuses that durable-in-process job instead of submitting a
        # newer payload an older sidecar version may not understand during a
        # rolling upgrade. A missing job is reported through the same internal
        # exception as other 404 responses, so only then do we submit it.
        try:
            job = await client.job(row.id.hex)
        except TranscriberUnavailable:
            job = await client.submit(payload)
        await _publish_progress(row.id, job)
        deadline = asyncio.get_running_loop().time() + client.timeout_seconds
        while str(job.get("state") or "") in ("queued", "running"):
            if asyncio.get_running_loop().time() >= deadline:
                raise TranscriberUnavailable("transcription timed out")
            await asyncio.sleep(POLL_SECONDS)
            await session.refresh(row)
            if row.state == "cancelled":
                await client.cancel(row.id.hex)
                await _clear_progress(row.id)
                return transcriptions.as_dict(row)
            job = await client.job(row.id.hex)
            await _publish_progress(row.id, job)
    except TranscriberUnavailable as exc:
        await _clear_progress(row.id)
        await transcriptions.fail(session, row.id, str(exc))
        raise TranscriberDown(str(exc)) from exc

    state = str(job.get("state") or "failed")
    if state == "done":
        await transcriptions.apply_result(session, row.id, job)
    elif state == "cancelled":
        await transcriptions.cancel(session, row.id)
    else:
        await transcriptions.fail(
            session,
            row.id,
            str(job.get("error") or "transcription failed"),
            normalization_error=str(job.get("normalization_error") or "") or None,
            started_at=job.get("started_at"),
            finished_at=job.get("finished_at"),
        )
    # Commit the durable result before acknowledging the sidecar copy. If the
    # process stops before DELETE, the bounded terminal cache supports restart
    # recovery; if the commit fails, the recoverable copy is left intact.
    await session.commit()
    # SQLAlchemy bulk UPDATE expires attributes on an object already present in
    # the identity map. A subsequent get() returns that same expired object;
    # serializing it then attempts implicit async IO outside greenlet_spawn.
    # Refresh explicitly while we are still in the awaited session context.
    await session.refresh(row)
    await client.cancel(row.id.hex)
    await _clear_progress(row.id)
    return transcriptions.as_dict(row)
