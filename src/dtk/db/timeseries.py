"""Writers and readers for the three TimescaleDB hypertables.

These tables are append-only and are written through Core inserts rather than
the ORM: rows are batched, none of them needs identity-map bookkeeping, and on
the request path that bookkeeping is pure overhead.

``request_log`` in particular must never slow a request down. The flusher that
owns it batches rows in memory and writes them from a background task; losing a
few rows on a crash is acceptable, blocking a request to record one is not
(docs/design/05-data-model.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, select

from dtk.core.types import Platform
from dtk.db.base import Repository, utcnow
from dtk.db.models import ContentSnapshot, IdentityEvent, RequestLog

#: Core-level handles for the append-only tables.
_REQUEST_LOG: Table = cast(Table, RequestLog.__table__)
_IDENTITY_EVENTS: Table = cast(Table, IdentityEvent.__table__)
_CONTENT_SNAPSHOTS: Table = cast(Table, ContentSnapshot.__table__)


def request_log_row(
    *,
    ts: datetime,
    request_id: uuid.UUID,
    platform: str,
    endpoint: str,
    outcome: str,
    duration_ms: int,
    task_id: uuid.UUID | None = None,
    identity_id: uuid.UUID | None = None,
    proxy_id: uuid.UUID | None = None,
    api_key_id: uuid.UUID | None = None,
    http_status: int | None = None,
    cache_hit: bool = False,
    signer: str | None = None,
    error_code: str | None = None,
    reject_reason: str | None = None,
) -> dict[str, Any]:
    """Build one fully populated request_log row.

    Every column is present even when the value is None, because a batched
    executemany needs every mapping to have the same keys.
    """
    return {
        "ts": ts,
        "request_id": request_id,
        "task_id": task_id,
        "platform": platform,
        "endpoint": endpoint,
        "identity_id": identity_id,
        "proxy_id": proxy_id,
        "api_key_id": api_key_id,
        "outcome": outcome,
        "http_status": http_status,
        "duration_ms": duration_ms,
        "cache_hit": cache_hit,
        "signer": signer,
        "error_code": error_code,
        "reject_reason": reject_reason,
    }


class RequestLogRepository(Repository):
    """Append-only writes to the hypertable behind every derived metric."""

    async def insert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Insert a batch built with :func:`request_log_row`.

        The background flusher owns this call. Logging must never block a
        request, so failures here are logged and swallowed by the caller, not
        retried on the request path.
        """
        if not rows:
            return 0
        await self.session.execute(_REQUEST_LOG.insert(), [dict(r) for r in rows])
        return len(rows)

    async def by_request_id(self, request_id: uuid.UUID) -> Sequence[RequestLog]:
        """Everything recorded under one API request_id, for a bug report."""
        stmt = (
            select(RequestLog)
            .where(RequestLog.request_id == request_id)
            .order_by(RequestLog.ts.desc())
        )
        return (await self.session.scalars(stmt)).all()


class IdentityEventRepository(Repository):
    """Lifecycle audit for the identity pool."""

    async def record(
        self,
        *,
        identity_id: uuid.UUID,
        event: str,
        detail: Mapping[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        await self.session.execute(
            _IDENTITY_EVENTS.insert(),
            [
                {
                    "ts": ts or utcnow(),
                    "identity_id": identity_id,
                    "event": event,
                    "detail": dict(detail) if detail is not None else None,
                }
            ],
        )

    async def list_for(
        self, identity_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[IdentityEvent]:
        stmt = (
            select(IdentityEvent)
            .where(IdentityEvent.identity_id == identity_id)
            .order_by(IdentityEvent.ts.desc())
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()


class ContentSnapshotRepository(Repository):
    """Metric history. The one capability v5 gets for free from TimescaleDB."""

    async def insert(
        self,
        *,
        platform: Platform,
        content_type: str,
        content_id: str,
        ts: datetime | None = None,
        play_count: int | None = None,
        digg_count: int | None = None,
        comment_count: int | None = None,
        share_count: int | None = None,
        collect_count: int | None = None,
        follower_count: int | None = None,
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        """Write one snapshot.

        Absent metrics stay None. Writing 0 for "the platform did not return
        it" puts a phantom cliff in the curve. Callers must first take the
        Redis dedup lock (``snapshot:{platform}:{content_id}``) so polling
        cannot flood the table.
        """
        await self.session.execute(
            _CONTENT_SNAPSHOTS.insert(),
            [
                {
                    "ts": ts or utcnow(),
                    "platform": platform.value,
                    "content_type": content_type,
                    "content_id": content_id,
                    "play_count": play_count,
                    "digg_count": digg_count,
                    "comment_count": comment_count,
                    "share_count": share_count,
                    "collect_count": collect_count,
                    "follower_count": follower_count,
                    "raw": dict(raw) if raw is not None else None,
                }
            ],
        )

    async def history(
        self,
        *,
        platform: Platform,
        content_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> Sequence[ContentSnapshot]:
        """Oldest first, which is the order a chart wants to plot."""
        stmt = select(ContentSnapshot).where(
            ContentSnapshot.platform == platform.value,
            ContentSnapshot.content_id == content_id,
        )
        if since is not None:
            stmt = stmt.where(ContentSnapshot.ts >= since)
        if until is not None:
            stmt = stmt.where(ContentSnapshot.ts <= until)
        stmt = stmt.order_by(ContentSnapshot.ts.asc()).limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def latest(self, *, platform: Platform, content_id: str) -> ContentSnapshot | None:
        stmt = (
            select(ContentSnapshot)
            .where(
                ContentSnapshot.platform == platform.value,
                ContentSnapshot.content_id == content_id,
            )
            .order_by(ContentSnapshot.ts.desc())
            .limit(1)
        )
        return (await self.session.scalars(stmt)).first()


__all__ = [
    "ContentSnapshotRepository",
    "IdentityEventRepository",
    "RequestLogRepository",
    "request_log_row",
]
