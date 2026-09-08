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

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.operations import unwrap
from dtk.api.routes.support import authenticated, iso, language, ok
from dtk.core.db import session_scope
from dtk.core.errors import ErrorCode, TaskNotFound
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Language, TaskState
from dtk.i18n.messages import render
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


def _localized_error(error: dict[str, Any] | None, language: Language) -> dict[str, Any] | None:
    """Re-render a stored task error in the caller's language.

    :func:`dtk.worker.main.serialize_error` writes an English sentence for the
    worker log and the CLI; what actually travels is the code beside it and the
    arguments it was raised with. Rendering here rather than at write time is
    what lets one failed task answer a Chinese console and an English agent,
    and the arguments are assembled exactly as :func:`dtk.api.envelope.failure`
    assembles them - ``retry_after`` folded in among the details, because that
    number is the entire content of "retry in {retry_after} seconds".
    """
    if not error:
        return None
    try:
        code = ErrorCode(str(error.get("code")))
    except ValueError:
        # A code from a worker newer than this process is still a failure, just
        # not one this contract can describe; INTERNAL at least renders.
        code = ErrorCode.INTERNAL
    details = error.get("details")
    args: dict[str, Any] = dict(details) if isinstance(details, dict) else {}
    retry_after = error.get("retry_after")
    if retry_after is not None:
        args["retry_after"] = retry_after
    return {**error, "code": code.value, "message": render(code, language, **args)}


def _localized_data(endpoint: str, data: Any, language: Language) -> Any:
    """Re-render a stored result's prose in the reader's language.

    The worker produces a result long before anyone's language is known - the
    console polls for it afterwards - so a job whose output is prose stores
    codes and arguments and the sentence is built here. Only the self check
    needs it today; the other maintenance jobs return codes and numbers.
    """
    if endpoint != "diagnose" or not isinstance(data, dict):
        return data
    from dtk.ops.diagnose import localize_report

    return localize_report(data, language)


def _view_payload(
    view: task_service.TaskView, language: Language, *, include_result: bool = True
) -> dict[str, Any]:
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
        payload["data"] = _localized_data(view.endpoint, data, language)
        if meta:
            payload["result_meta"] = meta
    if view.state is TaskState.FAILED:
        payload["error"] = _localized_error(view.error, language)
    return payload


TASK_ID_PATH = Path(description="The task id returned when the work was submitted.")


@router.get("/{task_id}", summary="Fetch a task", openapi_extra={I18N_KEY: "task_get"})
async def get_task(
    request: Request,
    task_id: uuid.UUID = TASK_ID_PATH,
    principal: Principal = Depends(authenticated),
) -> Any:
    """The state of one task, and its result once it has finished.

    Safe to poll, and safe to call twice: the answer does not change until the
    task does.

    **Parameters**

    - `task_id` - the id returned when the work was submitted.

    **Returns**

    The task's state, timestamps and endpoint. A finished task also carries its
    result. Results are kept for a limited time; once one has expired the
    lookup answers `TASK_NOT_FOUND`, which means submit the work again rather
    than keep polling.
    """
    view = await task_service.get(request.state.db, task_id)
    if view.state is TaskState.DONE and view.result is None:
        # The row outlives its payload by design; say so rather than handing
        # back a success with nothing in it.
        raise TaskNotFound("this task finished but its result has expired")
    return ok(request, _view_payload(view, language(request)))


@router.delete("/{task_id}", summary="Cancel a task", openapi_extra={I18N_KEY: "task_cancel"})
async def cancel_task(
    request: Request,
    task_id: uuid.UUID = TASK_ID_PATH,
    principal: Principal = Depends(authenticated),
) -> Any:
    """Give up on a task that has not started.

    Only a queued task is stopped. One that is already running is left alone
    and reported as running: the request is in flight against a platform and
    the identity's quota is already spent, so recording a failure the worker
    never had would make the endpoint's risk rate lie - and the circuit breaker
    reads that rate.

    Nothing is deleted. The row stays, with its state, the way every other
    finished task does.

    **Returns**

    The task's state after the request: `failed` if it was cancelled, or
    whatever it already was.
    """
    session = request.state.db
    state = await task_service.cancel(session, task_id)
    await session.commit()
    return ok(request, {"task_id": str(task_id), "state": state})


@router.get("/{task_id}/events", summary="Stream a task's progress")
async def task_events(
    request: Request,
    task_id: uuid.UUID = TASK_ID_PATH,
    timeout: float = Query(
        default=SSE_MAX_SECONDS,
        ge=1,
        le=SSE_MAX_SECONDS,
        description="Seconds to hold the stream open before closing it.",
    ),
    principal: Principal = Depends(authenticated),
) -> StreamingResponse:
    """Server-sent events until the task settles or the deadline passes.

    Authorization happens before the response starts; once the stream is open
    there is nothing left to authorize.
    """
    # Fail fast on an unknown id so the caller gets a 404 envelope instead of
    # an event stream that says nothing.
    await task_service.get(request.state.db, task_id)

    # Bound before the stream opens, like the session below: the negotiated
    # language belongs to the request, and the request is over by the time the
    # body is produced.
    caller_language = language(request)

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
                        yield _event(
                            "error",
                            {
                                "code": ErrorCode.TASK_NOT_FOUND.value,
                                "message": render(ErrorCode.TASK_NOT_FOUND, caller_language),
                            },
                        )
                        return
                if view.state.value != last_state:
                    last_state = view.state.value
                    silent = 0.0
                    yield _event(
                        "state", _view_payload(view, caller_language, include_result=False)
                    )
                if view.state in TERMINAL:
                    yield _event("result", _view_payload(view, caller_language))
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
