"""Starting a transcription: one path for REST and MCP.

The first version had two. The REST route went through the operations submit
and so through the queue ceiling; the MCP gateway called ``tasks.create``
itself, skipped the ceiling, retried a lost insert race recursively without a
bound, and stored the task id only after a commit had already published the row
without one. Every one of those was a difference nobody chose.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.logging import get_logger
from dtk.db.models import Transcription
from dtk.services import tasks, transcriptions

log = get_logger(__name__)

ENDPOINT: Final = "media.transcribe"


@dataclass(frozen=True, slots=True)
class TranscriptionStart:
    """A transcription that was just queued, or one that is being reused."""

    row: Transcription
    #: None for new work; "in_flight" or "completed" for a reused row.
    reused: str | None


async def start(
    session: AsyncSession,
    *,
    config: Any,
    normalization_failure_policy: str,
    download_id: uuid.UUID,
    normalize: bool,
    refresh: bool,
    requested_by: uuid.UUID | None = None,
    is_demo: bool = False,
) -> TranscriptionStart:
    """Create or reuse a transcription, then commit and publish its task."""
    try:
        row, reused = await transcriptions.prepare(
            session,
            download_id,
            normalize=normalize,
            normalization_failure_policy=normalization_failure_policy,
            refresh=refresh,
            requested_by=requested_by,
        )
    except IntegrityError:
        # `ux_transcriptions_in_flight` refused the insert: another request for
        # the same source and settings got there between our lookup and our
        # flush. Postgres only raises once that row is committed, so one more
        # lookup finds it. Once - a second refusal is not a race this explains.
        await session.rollback()
        row, reused = await transcriptions.prepare(
            session,
            download_id,
            normalize=normalize,
            normalization_failure_policy=normalization_failure_policy,
            refresh=False,
            requested_by=requested_by,
        )
    if reused is not None:
        return TranscriptionStart(row=row, reused=reused)

    await tasks.refuse_when_queue_full(config, endpoint=ENDPOINT)
    task_id = await tasks.create(
        session,
        ENDPOINT,
        {"transcription_id": str(row.id)},
        api_key_id=requested_by,
        is_demo=is_demo,
    )
    # One commit for both rows. A transcription committed before it carries its
    # task id is one a caller can read and nothing can collect.
    row.task_id = task_id
    await session.commit()

    try:
        await tasks.enqueue(task_id, endpoint=ENDPOINT)
    except Exception as exc:
        # Redis went away between the commit and the publish. Settle both rows
        # rather than leave a live transcription holding the in-flight index
        # with nothing behind it.
        log.warning("task.enqueue_failed", task_id=str(task_id), error=type(exc).__name__)
        reason = "the task could not be queued for a worker"
        # The row first: it commits on its own and needs no Redis. Finishing the
        # task ends by waking its waiters through Redis - the thing that just
        # failed - and when it ran first it raised and took the row with it.
        await transcriptions.fail(session, row.id, reason)
        await tasks.settle_unpublished(session, task_id, reason)
        raise

    log.info(
        "media.transcription.requested",
        transcription_id=str(row.id),
        download_id=str(download_id),
        normalize=normalize,
    )
    return TranscriptionStart(row=row, reused=None)


__all__ = ["ENDPOINT", "TranscriptionStart", "start"]
