"""Durable transcription records and reuse rules."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import delete, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import InvalidParam, NotConfigured, NotFound
from dtk.db.base import affected
from dtk.db.models import ArchivedContent, MediaDownload, Transcription
from dtk.ops.capacity import MEDIA_PATH
from dtk.services.archive import LIKE_ESCAPE, escape_like

MODEL: Final = "paraformer-zh-large-onnx-int8"
NORMALIZER: Final = "wetext-zh-itn-0.1.8"
NORMALIZATION_FAILURE_POLICIES: Final = frozenset({"fallback", "fail"})
LIVE_STATES: Final = ("queued", "running")
TERMINAL_STATES: Final = ("done", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class TranscriptionFilter:
    platform: str | None = None
    state: str | None = None
    #: A substring of the transcript, of the post's title as the archive holds
    #: it, or of the content id.
    query: str | None = None


def _search_clause(term: str) -> Any:
    """Match a search term against the transcript and the post it came from.

    The title is read from the archive - the listing shows it from there - by
    EXISTS rather than a join, so the count query stays the same statement.
    Escaped and substring-matched for the reasons the archive search is: the
    caller typed text, not a LIKE pattern, and Chinese has no word boundaries.
    """
    pattern = f"%{escape_like(term)}%"
    return or_(
        Transcription.content_id.ilike(pattern, escape=LIKE_ESCAPE),
        Transcription.text.ilike(pattern, escape=LIKE_ESCAPE),
        select(literal(1))
        .where(
            ArchivedContent.platform == Transcription.platform,
            ArchivedContent.content_id == Transcription.content_id,
            ArchivedContent.title.ilike(pattern, escape=LIKE_ESCAPE),
        )
        .exists(),
    )


def _sidecar_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("transcriber timestamp must be an ISO 8601 string")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("transcriber timestamp must include a timezone")
    return timestamp


def validate_normalization_failure_policy(value: str) -> str:
    policy = value.strip().lower()
    if policy not in NORMALIZATION_FAILURE_POLICIES:
        raise NotConfigured("DTK_TRANSCRIBER_NORMALIZATION_FAILURE_POLICY must be fallback or fail")
    return policy


def _source(download: MediaDownload) -> dict[str, Any]:
    if download.state not in ("done", "partial"):
        raise InvalidParam("the download has no completed video to transcribe")
    if download.files_removed_at is not None:
        raise InvalidParam("the download's files have been cleaned up")
    entry = next(
        (
            item
            for item in (download.files or [])
            if isinstance(item, dict)
            and item.get("kind") == "video"
            and item.get("state") == "done"
            and item.get("name")
            and item.get("sha256")
        ),
        None,
    )
    if entry is None:
        raise InvalidParam("the download contains no completed video file")
    root = Path(MEDIA_PATH).resolve()
    try:
        path = (root / download.directory / str(entry["name"])).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError):
        raise InvalidParam("the downloaded video is no longer available on disk") from None
    if not path.is_file():
        raise InvalidParam("the downloaded video is no longer available on disk")
    return {
        "name": str(entry["name"]),
        "sha256": str(entry["sha256"]),
        "bytes": int(entry.get("bytes") or path.stat().st_size),
    }


async def prepare(
    session: AsyncSession,
    download_id: uuid.UUID,
    *,
    normalize: bool,
    normalization_failure_policy: str,
    refresh: bool,
    requested_by: uuid.UUID | None,
) -> tuple[Transcription, str | None]:
    download = await session.get(MediaDownload, download_id)
    if download is None:
        raise NotFound("no such download", details={"download_id": str(download_id)})
    source = _source(download)
    policy = validate_normalization_failure_policy(normalization_failure_policy)
    normalizer = NORMALIZER if normalize else None
    persisted_policy = policy if normalize else None
    matching = (
        (
            await session.execute(
                select(Transcription)
                .where(
                    Transcription.download_id == download_id,
                    Transcription.source_sha256 == source["sha256"],
                    Transcription.model == MODEL,
                    Transcription.normalization_requested.is_(normalize),
                    Transcription.normalizer.is_(None)
                    if normalizer is None
                    else Transcription.normalizer == normalizer,
                    Transcription.normalization_failure_policy.is_(None)
                    if persisted_policy is None
                    else Transcription.normalization_failure_policy == persisted_policy,
                )
                .order_by(Transcription.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    live = next((row for row in matching if row.state in LIVE_STATES), None)
    if live is not None:
        return live, "in_flight"
    if not refresh:
        completed = next((row for row in matching if row.state == "done"), None)
        if completed is not None:
            return completed, "completed"
    row = Transcription(
        download_id=download.id,
        platform=download.platform,
        content_id=download.content_id,
        model=MODEL,
        normalization_requested=normalize,
        normalization_applied=False,
        normalizer=normalizer,
        normalization_failure_policy=persisted_policy,
        source_name=source["name"],
        source_sha256=source["sha256"],
        source_bytes=source["bytes"],
        requested_by=requested_by,
    )
    session.add(row)
    await session.flush()
    return row, None


async def get(session: AsyncSession, transcription_id: uuid.UUID) -> Transcription | None:
    return await session.get(Transcription, transcription_id)


async def search(
    session: AsyncSession,
    spec: TranscriptionFilter,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Transcription], int]:
    """Return one newest-first page without loading transcript text."""

    def apply(statement: Any) -> Any:
        if spec.platform:
            statement = statement.where(Transcription.platform == spec.platform)
        if spec.state:
            statement = statement.where(Transcription.state == spec.state)
        if spec.query and spec.query.strip():
            statement = statement.where(_search_clause(spec.query.strip()))
        return statement

    rows: Sequence[Transcription] = (
        (
            await session.execute(
                apply(select(Transcription))
                .order_by(Transcription.created_at.desc(), Transcription.id.desc())
                .limit(max(1, min(limit, 200)))
                .offset(max(0, offset))
            )
        )
        .scalars()
        .all()
    )
    total = int(
        (await session.execute(apply(select(func.count()).select_from(Transcription)))).scalar_one()
    )
    return list(rows), total


#: The states that make a transcription worth opening rather than starting.
#: A failed or cancelled one is not: asking again is what the reader wants
#: there, and the API would start a new one anyway.
OPENABLE_STATES: Final = (*LIVE_STATES, "done")


async def latest_for_downloads(
    session: AsyncSession, download_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """The newest openable transcription of each of these downloads, in one query.

    What lets a page that lists downloads say "this one already has a
    transcript" - the Transcribe button became a request whose only possible
    outcome was being handed back the record the page could have linked to.
    Newest wins, so a refresh still running is preferred over the text it is
    replacing. Ids only, never text: a listing has no business carrying a
    transcript, and this is attached to listings.
    """
    unique = list(dict.fromkeys(download_ids))
    if not unique:
        return {}
    rows = (
        await session.execute(
            select(Transcription.download_id, Transcription.id, Transcription.state)
            .where(
                Transcription.download_id.in_(unique),
                Transcription.state.in_(OPENABLE_STATES),
            )
            .order_by(Transcription.created_at.desc(), Transcription.id.desc())
        )
    ).all()
    found: dict[uuid.UUID, dict[str, Any]] = {}
    for download_id, transcription_id, state in rows:
        if download_id is None or download_id in found:
            continue
        found[download_id] = {"transcription_id": str(transcription_id), "state": state}
    return found


async def delete_settled(session: AsyncSession, ids: Sequence[uuid.UUID]) -> int:
    """Atomically delete settled rows, never their downloads or media files."""
    unique = tuple(dict.fromkeys(ids))
    rows = (
        (
            await session.execute(
                select(Transcription).where(Transcription.id.in_(unique)).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    found = {row.id for row in rows}
    missing = [str(identifier) for identifier in unique if identifier not in found]
    if missing:
        raise NotFound("no such transcription", details={"transcription_ids": missing})
    unsettled = [str(row.id) for row in rows if row.state not in TERMINAL_STATES]
    if unsettled:
        raise InvalidParam(
            "running transcriptions must be cancelled before deletion",
            details={"transcription_ids": unsettled},
        )
    result = await session.execute(delete(Transcription).where(Transcription.id.in_(unique)))
    return affected(result)


async def mark_running(session: AsyncSession, transcription_id: uuid.UUID) -> None:
    await session.execute(
        update(Transcription)
        .where(Transcription.id == transcription_id, Transcription.state == "queued")
        .values(state="running", started_at=func.now())
    )


async def apply_result(
    session: AsyncSession, transcription_id: uuid.UUID, result: dict[str, Any]
) -> bool:
    """Store a finished transcript, unless the row has already settled.

    The settling writes here are guarded on a live state, and each says whether
    it changed anything. A task can run on two workers at once - the stale sweep
    re-queues one that has run past its threshold - and the loser of that race
    used to overwrite the winner's transcript, or a caller's cancellation.
    """
    values: dict[str, Any] = {
        "state": "done",
        "text": str(result.get("text") or ""),
        "duration_ms": int(result.get("duration_ms") or 0) or None,
        "normalization_applied": bool(result.get("normalization_applied")),
        "normalization_error": (str(result.get("normalization_error") or "")[:500] or None),
        "error": None,
        "finished_at": _sidecar_timestamp(result.get("finished_at")) or func.now(),
    }
    started_at = _sidecar_timestamp(result.get("started_at"))
    if started_at is not None:
        values["started_at"] = started_at
    outcome = await session.execute(
        update(Transcription)
        .where(Transcription.id == transcription_id, Transcription.state.in_(LIVE_STATES))
        .values(**values)
    )
    return bool(affected(outcome))


async def fail(
    session: AsyncSession,
    transcription_id: uuid.UUID,
    reason: str,
    *,
    normalization_error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> bool:
    """Record a failure, unless the row has already settled; see :func:`apply_result`."""
    values: dict[str, Any] = {
        "state": "failed",
        "error": reason[:500],
        "normalization_applied": False,
        "normalization_error": (normalization_error or "")[:500] or None,
        "finished_at": _sidecar_timestamp(finished_at) or func.now(),
    }
    actual_started_at = _sidecar_timestamp(started_at)
    if actual_started_at is not None:
        values["started_at"] = actual_started_at
    result = await session.execute(
        update(Transcription)
        .where(Transcription.id == transcription_id, Transcription.state.in_(LIVE_STATES))
        .values(**values)
    )
    # Most callers raise immediately afterwards; persist the terminal state
    # before the operation runner rolls its transaction back for that error.
    await session.commit()
    return bool(affected(result))


async def cancel(session: AsyncSession, transcription_id: uuid.UUID) -> bool:
    result = await session.execute(
        update(Transcription)
        .where(Transcription.id == transcription_id, Transcription.state.in_(LIVE_STATES))
        .values(state="cancelled", finished_at=func.now())
    )
    return bool(affected(result))


def relative_path(row: Transcription, download: MediaDownload) -> str:
    return f"{download.directory}/{row.source_name}"


def as_dict(row: Transcription, *, reused: str | None = None) -> dict[str, Any]:
    return {
        "transcription_id": str(row.id),
        "download_id": str(row.download_id) if row.download_id else None,
        "task_id": str(row.task_id) if row.task_id else None,
        "platform": row.platform,
        "content_id": row.content_id,
        "state": row.state,
        "text_preview": (row.text or "")[:160],
        "text": row.text,
        "duration_ms": row.duration_ms,
        "model": row.model,
        "normalization": {
            "requested": bool(row.normalization_requested),
            "applied": bool(row.normalization_applied),
            "engine": row.normalizer,
            "failure_policy": row.normalization_failure_policy,
            "error": row.normalization_error,
        },
        "source": {
            "name": row.source_name,
            "sha256": row.source_sha256,
            "bytes": int(row.source_bytes),
        },
        "reused": reused,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def as_list_dict(row: Transcription) -> dict[str, Any]:
    """A listing row: lifecycle and source metadata, never the full text."""
    return {
        "transcription_id": str(row.id),
        "download_id": str(row.download_id) if row.download_id else None,
        "task_id": str(row.task_id) if row.task_id else None,
        "platform": row.platform,
        "content_id": row.content_id,
        "state": row.state,
        "text_preview": (row.text or "")[:160],
        "duration_ms": row.duration_ms,
        "model": row.model,
        "normalization": {
            "requested": bool(row.normalization_requested),
            "applied": bool(row.normalization_applied),
            "engine": row.normalizer,
            "failure_policy": row.normalization_failure_policy,
            "error": row.normalization_error,
        },
        "source": {
            "name": row.source_name,
            "sha256": row.source_sha256,
            "bytes": int(row.source_bytes),
        },
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


__all__ = [
    "LIVE_STATES",
    "MODEL",
    "NORMALIZER",
    "OPENABLE_STATES",
    "TERMINAL_STATES",
    "TranscriptionFilter",
    "apply_result",
    "as_dict",
    "as_list_dict",
    "cancel",
    "delete_settled",
    "fail",
    "get",
    "latest_for_downloads",
    "mark_running",
    "prepare",
    "relative_path",
    "search",
    "validate_normalization_failure_policy",
]
