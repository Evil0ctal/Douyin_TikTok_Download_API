"""On-demand transcription of videos already stored on the media volume."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.routes.openapi import ASYNC_RESPONSES, I18N_KEY
from dtk.api.routes.schemas import TranscriptionDeleteRequest, TranscriptionRequest
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    guard,
    ok,
    resolve_wait,
)
from dtk.core.config import BootstrapSettings
from dtk.core.errors import NotConfigured, NotFound
from dtk.core.types import Platform, Scope, TranscriptionState, UserRole
from dtk.services import (
    downloads,
    tasks,
    transcription_progress,
    transcription_requests,
    transcriptions,
)

router = APIRouter(prefix="/api/v1/transcriptions", tags=["transcriptions"])
TRANSCRIPTION_ID = Path(description="The transcription id returned when it was started.")
LIMIT_QUERY = Query(default=None, ge=1, le=MAX_ADMIN_PAGE_SIZE, description="Rows per page.")
OFFSET_QUERY = Query(default=0, ge=0, description="Rows to skip.")


def _configured(request: Request) -> BootstrapSettings:
    settings: BootstrapSettings = request.app.state.settings
    if not settings.transcriber_url:
        raise NotConfigured(
            "this instance has no transcriber; start the transcriber compose profile "
            "and set DTK_TRANSCRIBER_URL"
        )
    transcriptions.validate_normalization_failure_policy(
        settings.transcriber_normalization_failure_policy
    )
    return settings


async def _waited(request: Request, row: Any, seconds: float, reused: str | None) -> Any:
    if seconds > 0 and row.task_id and row.state in transcriptions.LIVE_STATES:
        await tasks.wait_for(request.state.db, row.task_id, seconds)
        await request.state.db.commit()
        row = await transcriptions.get(request.state.db, row.id) or row
    status = 202 if row.state in transcriptions.LIVE_STATES else 200
    return ok(request, transcriptions.as_dict(row, reused=reused), status_code=status)


async def _progress_for(row: Any) -> Any:
    """Best-effort live progress; durable records remain readable without Redis."""
    if row.state not in transcriptions.LIVE_STATES:
        return None
    try:
        return await transcription_progress.read(row.id)
    except Exception:
        return None


def _list_progress(progress: Any) -> dict[str, Any] | None:
    """Progress for a listing row, without the provisional text.

    That text grows with the job and the list never shows it; sent for every
    live row on every poll, it made the listing the heaviest call the page made.
    The detail read still carries it.
    """
    if progress is None:
        return None
    snapshot = asdict(progress)
    snapshot.pop("partial_text", None)
    return snapshot


@router.post(
    "",
    summary="Transcribe a downloaded video",
    openapi_extra={I18N_KEY: "transcriptions_create", **ASYNC_RESPONSES},
)
async def start_transcription(
    request: Request,
    body: TranscriptionRequest,
    wait: float | None = Query(
        default=None,
        description="Wait this many seconds for the transcription before returning 202.",
    ),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Create or reuse a whole-text transcription for a completed downloaded video."""
    principal.require(Scope.TRANSCRIPTION_WRITE)
    settings = _configured(request)
    wait_seconds = resolve_wait(request, wait)
    started = await transcription_requests.start(
        request.state.db,
        config=request.app.state.config,
        normalization_failure_policy=settings.transcriber_normalization_failure_policy,
        download_id=body.download_id,
        normalize=body.normalize,
        refresh=body.refresh,
        requested_by=principal.api_key_id,
        is_demo=principal.role is UserRole.DEMO,
    )
    return await _waited(request, started.row, wait_seconds, started.reused)


@router.get("", summary="List transcriptions", openapi_extra={I18N_KEY: "transcriptions_list"})
async def list_transcriptions(
    request: Request,
    platform: Platform | None = Query(default=None, description="Only this platform."),
    state: TranscriptionState | None = Query(default=None, description="Only this state."),
    limit: int | None = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    _principal: Principal = Depends(
        guard(scopes=(Scope.TRANSCRIPTION_READ, Scope.TRANSCRIPTION_WRITE))
    ),
) -> Any:
    """List durable transcription records newest first without returning full text."""
    page_size = limit or DEFAULT_ADMIN_PAGE_SIZE
    rows, total = await transcriptions.search(
        request.state.db,
        transcriptions.TranscriptionFilter(platform=platform, state=state),
        limit=page_size,
        offset=offset,
    )
    titles = await downloads.titles_for(
        request.state.db, [(row.platform, row.content_id) for row in rows]
    )
    snapshots = await asyncio.gather(*(_progress_for(row) for row in rows))
    return ok(
        request,
        {
            "items": [
                {
                    **transcriptions.as_list_dict(row),
                    "post": titles.get((row.platform, row.content_id)),
                    "progress": _list_progress(progress),
                }
                for row, progress in zip(rows, snapshots, strict=True)
            ],
            "total": total,
            "limit": page_size,
            "offset": offset,
        },
    )


@router.post(
    "/delete",
    summary="Delete settled transcription records",
    openapi_extra={I18N_KEY: "transcriptions_delete"},
)
async def delete_transcriptions(
    request: Request,
    body: TranscriptionDeleteRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Delete settled transcript text and metadata, never source media."""
    principal.require(Scope.TRANSCRIPTION_WRITE)
    deleted = await transcriptions.delete_settled(request.state.db, body.ids)
    await request.state.db.commit()
    return ok(request, {"deleted": deleted})


@router.get(
    "/{transcription_id}",
    summary="Read a transcription",
    openapi_extra={I18N_KEY: "transcriptions_get"},
)
async def get_transcription(
    request: Request,
    transcription_id: uuid.UUID = TRANSCRIPTION_ID,
    _principal: Principal = Depends(
        guard(scopes=(Scope.TRANSCRIPTION_READ, Scope.TRANSCRIPTION_WRITE))
    ),
) -> Any:
    """Read durable text, duration, model, normalization, source and lifecycle metadata."""
    row = await transcriptions.get(request.state.db, transcription_id)
    if row is None:
        raise NotFound("no such transcription")
    titles = await downloads.titles_for(request.state.db, [(row.platform, row.content_id)])
    progress = await _progress_for(row)
    return ok(
        request,
        {
            **transcriptions.as_dict(row),
            "post": titles.get((row.platform, row.content_id)),
            "progress": asdict(progress) if progress else None,
        },
    )


@router.delete(
    "/{transcription_id}",
    summary="Cancel a transcription",
    openapi_extra={I18N_KEY: "transcriptions_cancel"},
)
async def cancel_transcription(
    request: Request,
    transcription_id: uuid.UUID = TRANSCRIPTION_ID,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Cancel queued or running work without deleting any completed result."""
    principal.require(Scope.TRANSCRIPTION_WRITE)
    row = await transcriptions.get(request.state.db, transcription_id)
    if row is None:
        raise NotFound("no such transcription")
    if row.state not in transcriptions.LIVE_STATES:
        return ok(request, transcriptions.as_dict(row))
    await transcriptions.cancel(request.state.db, row.id)
    if row.task_id:
        await tasks.cancel(request.state.db, row.task_id)
    await request.state.db.commit()
    await request.state.db.refresh(row)
    settings: BootstrapSettings = request.app.state.settings
    if settings.transcriber_url:
        from dtk.transcriber import TranscriberClient

        client = TranscriberClient(
            settings.transcriber_url,
            token=settings.transcriber_token,
            timeout_seconds=settings.transcriber_timeout_seconds,
        )
        try:
            await client.cancel(row.id.hex)
        finally:
            await client.aclose()
    with contextlib.suppress(Exception):
        await transcription_progress.clear(row.id)
    return ok(request, transcriptions.as_dict(row))


__all__ = ["router"]
