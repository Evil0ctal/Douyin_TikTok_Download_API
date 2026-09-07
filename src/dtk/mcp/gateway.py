"""Service-backed implementations of the tool dependencies.

This is the only place in ``dtk.mcp`` that touches PostgreSQL or Redis, and it
does so by calling the services layer directly - never over HTTP against our own
API, which would add a network hop and skip the in-process cache
(docs/design/06-api-auth-mcp.md).

Each call takes its own short-lived session. The one exception is ``wait``,
which holds a session for as long as the tool blocks; it is separated from
``submit`` on purpose so the submitting transaction commits immediately and a
worker in another process can see the row it just queued.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.db import session_scope
from dtk.core.errors import TaskNotFound
from dtk.core.types import Outcome, Platform
from dtk.db.models import ContentSnapshot, RequestLog
from dtk.identity.pool import IdentityPool
from dtk.mcp.context import EndpointHealth, HistoryPoint, PoolSnapshot, TaskOutcome
from dtk.mcp.routing import known_endpoints
from dtk.platforms.registry import available_platforms
from dtk.scheduler import circuit
from dtk.services import tasks

Clock = Callable[[], float]

#: How far back the "last success" lookup scans. Older than this and the answer
#: is "not recently", which is all the prose needs.
LAST_SUCCESS_WINDOW_DAYS = 30


def _task_uuid(task_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(task_id)
    except (ValueError, AttributeError, TypeError):
        # A malformed id is indistinguishable from an expired one as far as the
        # caller is concerned, and TASK_NOT_FOUND already has prose.
        raise TaskNotFound("that is not a valid task id") from None


def _outcome(view: tasks.TaskView) -> TaskOutcome:
    return TaskOutcome(
        task_id=str(view.id),
        state=view.state,
        result=view.result,
        error=view.error,
        endpoint=view.endpoint,
    )


class ServiceTaskGateway:
    """Submits and awaits tasks through :mod:`dtk.services.tasks`.

    No in-flight coalescing here, unlike the REST submission path. Coalescing
    only pays off when several callers ask for the same thing at the same
    instant, and an agent works one tool call at a time; a repeat of an earlier
    call is caught by the response cache instead, which costs the pool nothing
    either. Re-deriving REST's digest to join its claims would mean copying a
    key format across a module boundary, and a copy that silently stopped
    matching would look exactly like coalescing that works.
    """

    __slots__ = ()

    async def submit(self, endpoint: str, params: dict[str, Any]) -> str:
        # Its own session, closed before the wait begins: the row has to be
        # committed before a worker in another process pops the id off the queue.
        async with session_scope() as session:
            task_id = await tasks.submit(session, endpoint, params)
        return str(task_id)

    async def wait(self, task_id: str, seconds: float) -> TaskOutcome | None:
        async with session_scope() as session:
            view = await tasks.wait_for(session, _task_uuid(task_id), seconds)
        return _outcome(view) if view is not None else None

    async def result(self, task_id: str) -> TaskOutcome:
        async with session_scope() as session:
            view = await tasks.get(session, _task_uuid(task_id))
        return _outcome(view)


class ServicePoolReporter:
    """Reads identity counts and endpoint circuit state.

    Counts only: the identity ids, cookies, fingerprints and proxies stay where
    they are. Nothing an agent can reach through MCP may name a credential.
    """

    __slots__ = ("_clock", "_pool")

    def __init__(self, pool: IdentityPool, *, clock: Clock = time.time) -> None:
        self._pool = pool
        self._clock = clock

    async def snapshot(self) -> PoolSnapshot:
        now = self._clock()
        endpoints = known_endpoints()
        async with session_scope() as session:
            identities = {
                platform: await self._pool.counts(session, Platform(platform))
                for platform in available_platforms()
            }
            last_success = await _last_success_map(session, endpoints)
        health = [
            await _health(name, now=now, last_success=last_success.get(name)) for name in endpoints
        ]
        return PoolSnapshot(
            identities=identities,
            endpoints=tuple(health),
            observed_at=datetime.now(UTC),
        )

    async def endpoint(self, endpoint: str) -> EndpointHealth:
        async with session_scope() as session:
            last_success = await _last_success_map(session, (endpoint,))
        return await _health(endpoint, now=self._clock(), last_success=last_success.get(endpoint))


async def _health(endpoint: str, *, now: float, last_success: datetime | None) -> EndpointHealth:
    is_open, retry_after, reason = await circuit.state(endpoint, now=now)
    stats = await circuit.stats(endpoint, now=now)
    return EndpointHealth(
        endpoint=endpoint,
        circuit_open=is_open,
        retry_after_seconds=retry_after or None,
        reason=reason or None,
        total=stats.total,
        ok=stats.ok,
        risk=stats.risk,
        last_success_at=last_success,
    )


async def _last_success_map(session: AsyncSession, endpoints: Sequence[str]) -> dict[str, datetime]:
    """Most recent successful call per endpoint, in one query."""
    if not endpoints:
        return {}
    horizon = datetime.now(UTC) - timedelta(days=LAST_SUCCESS_WINDOW_DAYS)
    rows = await session.execute(
        select(RequestLog.endpoint, func.max(RequestLog.ts))
        .where(
            RequestLog.endpoint.in_(list(endpoints)),
            RequestLog.outcome == Outcome.OK.value,
            RequestLog.ts >= horizon,
        )
        .group_by(RequestLog.endpoint)
    )
    return {endpoint: ts for endpoint, ts in rows.all() if ts is not None}


class ServiceHistoryReader:
    """Reads ``content_snapshots``. Never performs an upstream request."""

    __slots__ = ()

    async def history(
        self,
        platform: Platform,
        content_id: str,
        *,
        since: datetime,
        limit: int,
    ) -> list[HistoryPoint]:
        """The newest ``limit`` snapshots in the window, oldest first.

        The query orders descending and the result is reversed, rather than
        ordering ascending and truncating: when a busy id has more snapshots
        than the ceiling allows, the points worth keeping are the recent ones.
        Ascending plus LIMIT returns the oldest slice, which answers "how is
        this doing now" with data from the start of the window and no hint that
        the newest points were the ones dropped. It also matches the descending
        index on (platform, content_id, ts).
        """
        async with session_scope() as session:
            rows = await session.execute(
                select(ContentSnapshot)
                .where(
                    ContentSnapshot.platform == platform.value,
                    ContentSnapshot.content_id == content_id,
                    ContentSnapshot.ts >= since,
                )
                .order_by(ContentSnapshot.ts.desc())
                .limit(limit)
            )
            newest_first = list(rows.scalars())
        return [
            HistoryPoint(
                ts=row.ts,
                play_count=row.play_count,
                digg_count=row.digg_count,
                comment_count=row.comment_count,
                share_count=row.share_count,
                collect_count=row.collect_count,
                follower_count=row.follower_count,
            )
            for row in reversed(newest_first)
        ]


__all__ = [
    "LAST_SUCCESS_WINDOW_DAYS",
    "ServiceHistoryReader",
    "ServicePoolReporter",
    "ServiceTaskGateway",
]
