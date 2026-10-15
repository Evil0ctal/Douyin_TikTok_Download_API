"""Ephemeral progress snapshots for an in-flight transcription.

The durable transcription row contains only the authoritative final text.
Provisional ASR fragments cross the worker/API process boundary through Redis
and expire quickly if either process disappears.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Final

from dtk.core.redis import get_redis

KEY: Final = "transcriptions:progress:{transcription_id}"
TTL_SECONDS: Final = 120


@dataclass(frozen=True, slots=True)
class TranscriptionProgress:
    state: str
    stage: str
    revision: int
    processed_ms: int
    duration_ms: int | None
    progress_percent: float | None
    partial_text: str


def _identifier(value: uuid.UUID | str) -> str:
    return str(value)


def from_job(job: dict[str, Any]) -> TranscriptionProgress:
    """Reduce the sidecar's internal job response to the safe progress contract."""
    duration = int(job.get("media_duration_ms") or 0) or None
    percent = job.get("progress_percent")
    return TranscriptionProgress(
        state=str(job.get("state") or "running"),
        stage=str(job.get("stage") or "running"),
        revision=max(0, int(job.get("progress_revision") or 0)),
        processed_ms=max(0, int(job.get("processed_ms") or 0)),
        duration_ms=duration,
        progress_percent=float(percent) if percent is not None else None,
        partial_text=str(job.get("partial_text") or ""),
    )


async def publish(
    transcription_id: uuid.UUID | str,
    job: dict[str, Any],
    *,
    ttl: int = TTL_SECONDS,
) -> None:
    snapshot = from_job(job)
    await get_redis().set(
        KEY.format(transcription_id=_identifier(transcription_id)),
        json.dumps(asdict(snapshot), ensure_ascii=False, separators=(",", ":")),
        ex=max(1, ttl),
    )


async def read(transcription_id: uuid.UUID | str) -> TranscriptionProgress | None:
    raw = await get_redis().get(KEY.format(transcription_id=_identifier(transcription_id)))
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        return TranscriptionProgress(
            state=str(value.get("state") or "running"),
            stage=str(value.get("stage") or "running"),
            revision=max(0, int(value.get("revision") or 0)),
            processed_ms=max(0, int(value.get("processed_ms") or 0)),
            duration_ms=(int(value["duration_ms"]) if value.get("duration_ms") else None),
            progress_percent=(
                float(value["progress_percent"])
                if value.get("progress_percent") is not None
                else None
            ),
            partial_text=str(value.get("partial_text") or ""),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


async def clear(transcription_id: uuid.UUID | str) -> None:
    await get_redis().delete(KEY.format(transcription_id=_identifier(transcription_id)))


__all__ = [
    "KEY",
    "TTL_SECONDS",
    "TranscriptionProgress",
    "clear",
    "from_job",
    "publish",
    "read",
]
