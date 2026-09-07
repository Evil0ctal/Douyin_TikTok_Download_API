"""Task inspection: polling and the SSE stream.

Polling is the baseline every client can implement. The stream exists because
the console would otherwise poll a hundred rows at a time, and a server-sent
event costs far less than that (doc 06).

The stream deliberately opens its own database session. The request-scoped
session belongs to :class:`dtk.api.middleware.DatabaseSessionMiddleware`, which
closes it when the handler returns - and a streaming handler returns before its
body has been produced.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.operations import unwrap
from dtk.api.routes.support import authenticated, iso, ok
from dtk.core.db import session_scope
from dtk.core.errors import TaskNotFound
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import TaskState
from dtk.services import tasks as task_service

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

#: Upper bound on one event stream. A console tab left open for a day should
#: reconnect rather than pin a worker forever; EventSource reconnects on its own.
SSE_MAX_SECONDS = 300
#: Silence longer than this and a proxy in the middle starts dropping the
#: connection, so a comment frame goes out instead.
SSE_HEARTBEAT_SECONDS = 15
#: How long one wait on the completion signal blocks before looping.
SSE_TICK_SECONDS = 5

TERMINAL = (TaskState.DONE, TaskState.FAILED)


def _view_payload(view: task_service.TaskView, *, include_result: bool = True) -> dict[str, Any]:
    """The wire shape of a task.

    ``data`` rather than ``result`` because that is what doc 06 documents, and
    it stays absent until the task is finished so a caller cannot mistake a
    queued task for an empty answer.
    """
    payload: dict[str, Any] = {
        "task_id": str(view.id),
        "state": view.state.value,
        "endpoint": view.endpoint,
        "created_at": iso(view.created_at),
        "finished_at": iso(view.finished_at),
    }
    if include_result and view.state is TaskState.DONE:
        data, meta = unwrap(view.result)
        payload["data"] = data
        if meta:
            payload["result_meta"] = meta
    if view.state is TaskState.FAILED:
        payload["error"] = view.error
    return payload


@router.get("/{task_id}", summary="Fetch a task", openapi_extra={I18N_KEY: "task_get"})
async def get_task(
    request: Request,
    task_id: uuid.UUID,
    principal: Principal = Depends(authenticated),
) -> Any:
    """Idempotent while the result lives.

    Results are evicted after ``retention.task_result_hours``; the row survives
    for the statistics, and a later lookup answers ``TASK_NOT_FOUND`` so the
    caller re-submits rather than waiting for something that will never come.
    """
    view = await task_service.get(request.state.db, task_id)
    if view.state is TaskState.DONE and view.result is None:
        # The row outlives its payload by design; say so rather than handing
        # back a success with nothing in it.
        raise TaskNotFound("this task finished but its result has expired")
    return ok(request, _view_payload(view))


@router.get("/{task_id}/events", summary="Stream a task's progress")
async def task_events(
    request: Request,
    task_id: uuid.UUID,
    timeout: float = Query(default=SSE_MAX_SECONDS, ge=1, le=SSE_MAX_SECONDS),
    principal: Principal = Depends(authenticated),
) -> StreamingResponse:
    """Server-sent events until the task settles or the deadline passes.

    Authorization happens before the response starts; once the stream is open
    there is nothing left to authorize.
    """
    # Fail fast on an unknown id so the caller gets a 404 envelope instead of
    # an event stream that says nothing.
    await task_service.get(request.state.db, task_id)

    async def stream() -> AsyncIterator[bytes]:
        redis = get_redis()
        signal_key = task_service.SIGNAL_KEY.format(task_id=task_id)
        last_state: str | None = None
        elapsed = 0.0
        silent = 0.0
        try:
            while elapsed < timeout:
                if await request.is_disconnected():
                    break
                async with session_scope() as session:
                    try:
                        view = await task_service.get(session, task_id)
                    except TaskNotFound:
                        yield _event("error", {"code": "TASK_NOT_FOUND"})
                        return
                if view.state.value != last_state:
                    last_state = view.state.value
                    silent = 0.0
                    yield _event("state", _view_payload(view, include_result=False))
                if view.state in TERMINAL:
                    yield _event("result", _view_payload(view))
                    yield _event("end", {"task_id": str(task_id)})
                    return
                if silent >= SSE_HEARTBEAT_SECONDS:
                    silent = 0.0
                    yield b": keep-alive\n\n"

                waited = min(SSE_TICK_SECONDS, max(1.0, timeout - elapsed))
                # Blocks on the same signal the task queue pushes on
                # completion, so a finished task wakes the stream at once.
                await redis.blpop([signal_key], timeout=int(waited))
                elapsed += waited
                silent += waited
            yield _event("timeout", {"task_id": str(task_id), "state": last_state})
        except asyncio.CancelledError:  # client went away mid-stream
            raise
        finally:
            log.debug("task.stream_closed", task_id=str(task_id))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers proxied responses by default, which would hold
            # every event back until the stream ends.
            "X-Accel-Buffering": "no",
        },
    )


def _event(name: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {name}\ndata: {body}\n\n".encode()


__all__ = [
    "SSE_HEARTBEAT_SECONDS",
    "SSE_MAX_SECONDS",
    "SSE_TICK_SECONDS",
    "router",
]
