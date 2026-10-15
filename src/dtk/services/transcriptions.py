"""Durable transcription records and reuse rules."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import InvalidParam, NotConfigured, NotFound
from dtk.db.base import affected
from dtk.db.models import MediaDownload, Transcription
from dtk.ops.capacity import MEDIA_PATH

MODEL: Final = "paraformer-zh-large-onnx-int8"
NORMALIZER: Final = "wetext-zh-itn-0.1.8"
NORMALIZATION_FAILURE_POLICIES: Final = frozenset({"fallback", "fail"})
LIVE_STATES: Final = ("queued", "running")
TERMINAL_STATES: Final = ("done", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class TranscriptionFilter:
    platform: str | None = None
    state: str | None = None


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
) -> None:
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
    await session.execute(
        update(Transcription)
        .where(Transcription.id == transcription_id)
        .values(**values)
    )


async def fail(
    session: AsyncSession,
    transcription_id: uuid.UUID,
    reason: str,
    *,
    normalization_error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
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
    await session.execute(
        update(Transcription)
        .where(Transcription.id == transcription_id)
        .values(**values)
    )
    # Most callers raise immediately afterwards; persist the terminal state
    # before the operation runner rolls its transaction back for that error.
    await session.commit()


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
    "TERMINAL_STATES",
    "TranscriptionFilter",
    "apply_result",
    "as_dict",
    "as_list_dict",
    "cancel",
    "delete_settled",
    "fail",
    "get",
    "mark_running",
    "prepare",
    "relative_path",
    "search",
    "validate_normalization_failure_policy",
]
