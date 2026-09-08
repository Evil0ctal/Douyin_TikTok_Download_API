"""Async task queue.

Submit and collect are separate operations. The scheduler may have no identity
available at any moment, so an endpoint that promises an immediate answer would
hold connections open under pressure - and held connections occupy the very
workers needed to drain the backlog, which is how a slowdown turns into a
collapse. See docs/design/06-api-auth-mcp.md.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import ErrorCode, TaskNotFound
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import TaskState
from dtk.db.models import Task

log = get_logger(__name__)

QUEUE_KEY = "tasks:queue"
DONE_CHANNEL = "tasks:done:{task_id}"
#: Long-polling waiters block on this list, so a finished task wakes them
#: immediately instead of after the next poll tick.
SIGNAL_KEY = "tasks:signal:{task_id}"
SIGNAL_TTL = 120


@dataclass(frozen=True, slots=True)
class TaskView:
    id: uuid.UUID
    state: TaskState
    endpoint: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


async def submit(
    session: AsyncSession,
    endpoint: str,
    params: dict[str, Any],
    *,
    api_key_id: uuid.UUID | None = None,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    session.add(
        Task(
            id=task_id,
            api_key_id=api_key_id,
            endpoint=endpoint,
            params=params,
            state=TaskState.QUEUED.value,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    await get_redis().rpush(QUEUE_KEY, str(task_id))
    log.info("task.submitted", task_id=str(task_id), endpoint=endpoint)
    return task_id


async def claim(timeout: int = 5) -> uuid.UUID | None:
    """Block until a task is available, for a worker loop."""
    popped = await get_redis().blpop([QUEUE_KEY], timeout=timeout)
    if not popped:
        return None
    _key, value = popped
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def mark_running(session: AsyncSession, task_id: uuid.UUID) -> None:
    task = await session.get(Task, task_id)
    if task is not None:
        task.state = TaskState.RUNNING.value
        task.started_at = datetime.now(UTC)


async def finish(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    task = await session.get(Task, task_id)
    if task is None:
        return
    task.state = (TaskState.FAILED if error else TaskState.DONE).value
    task.result = result
    task.error = error
    task.finished_at = datetime.now(UTC)
    await session.flush()
    await _signal(task_id)
    log.info("task.finished", task_id=str(task_id), state=task.state)


async def _signal(task_id: uuid.UUID) -> None:
    redis = get_redis()
    key = SIGNAL_KEY.format(task_id=task_id)
    await redis.rpush(key, "1")
    await redis.expire(key, SIGNAL_TTL)


async def cancel(session: AsyncSession, task_id: uuid.UUID) -> str:
    """Give up on a task that has not finished. Returns what happened.

    Only a queued task is actually stopped. A running one is left alone and
    reported as such rather than marked failed: the request is already in
    flight against a platform, the identity's quota is already spent, and
    recording a failure the worker did not have would make the endpoint's risk
    rate lie - which is what the circuit breaker reads.

    The id stays on the Redis queue. Removing an element from the middle of a
    list is O(n) and racy, and the worker skips a task that is no longer queued
    when it pops one, which costs a single lookup.
    """
    task = await session.get(Task, task_id)
    if task is None:
        raise TaskNotFound("no such task, or its result has expired")
    if task.state != TaskState.QUEUED.value:
        return task.state
    task.state = TaskState.FAILED.value
    task.error = {"code": ErrorCode.INVALID_PARAM.value, "message": "cancelled by the caller"}
    task.finished_at = datetime.now(UTC)
    await session.flush()
    await _signal(task_id)
    log.info("task.cancelled", task_id=str(task_id))
    return TaskState.FAILED.value


async def get(session: AsyncSession, task_id: uuid.UUID) -> TaskView:
    task = await session.get(Task, task_id)
    if task is None:
        raise TaskNotFound("no such task, or its result has expired")
    return TaskView(
        id=task.id,
        state=TaskState(task.state),
        endpoint=task.endpoint,
        result=task.result,
        error=task.error,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )


async def wait_for(session: AsyncSession, task_id: uuid.UUID, seconds: float) -> TaskView | None:
    """Server-side long poll.

    Moves the polling loop off the caller without changing the semantics: the
    work still runs through the same asynchronous path. Simple clients - an iOS
    Shortcut, a curl one-liner - cannot write a polling loop at all, which is
    the reason this exists.
    """
    view = await get(session, task_id)
    if view.state in (TaskState.DONE, TaskState.FAILED):
        return view

    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    redis = get_redis()
    key = SIGNAL_KEY.format(task_id=task_id)
    while datetime.now(UTC) < deadline:
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            break
        await redis.blpop([key], timeout=max(1, int(min(remaining, 5))))
        await session.commit()
        view = await get(session, task_id)
        if view.state in (TaskState.DONE, TaskState.FAILED):
            return view
    return None


async def expire_results(session: AsyncSession, *, hours: int) -> int:
    """Blank out payloads past their retention window, keeping the metadata."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await session.execute(
            select(Task).where(
                Task.finished_at.is_not(None),
                Task.finished_at < cutoff,
                Task.result.is_not(None),
            )
        )
    ).scalars()
    count = 0
    for task in rows:
        task.result = None
        task.error = None
        count += 1
    return count


def serialize(view: TaskView) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {
                "task_id": str(view.id),
                "state": view.state.value,
                "endpoint": view.endpoint,
                "result": view.result,
                "error": view.error,
                "created_at": view.created_at,
                "finished_at": view.finished_at,
            },
            default=str,
        )
    )


__all__ = [
    "DONE_CHANNEL",
    "QUEUE_KEY",
    "SIGNAL_KEY",
    "TaskView",
    "cancel",
    "claim",
    "expire_results",
    "finish",
    "get",
    "mark_running",
    "serialize",
    "submit",
    "wait_for",
]
