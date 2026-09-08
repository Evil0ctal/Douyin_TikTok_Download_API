"""Periodic housekeeping the worker owns.

Five jobs, each cheap, each with a failure mode that stays invisible until it is
expensive:

* **retention follows the settings.** Doc 15 makes the windows runtime settings
  because a Raspberry Pi and a 2TB server should not share a policy, and
  TimescaleDB keeps its own policy objects - a changed setting has to be pushed
  into them. The work itself lives in :mod:`dtk.ops.retention`; this job is the
  scheduler that calls it, since nothing else runs on a timer.
* **retired identities are eventually deleted.** Retirement keeps the row for
  its statistics, and a pool that mints a replacement for every identity it
  loses would otherwise grow one row per loss forever. No TimescaleDB policy
  can bound it: ``identities`` is a plain table.
* **continuous aggregates stay fresh.** They normally have a refresh policy of
  their own. A deployment that lost one - restored from a dump, upgraded across
  a version that renamed the job - would show identity health frozen at the last
  refresh, with nothing in the logs to say so. When no policy is present they
  are refreshed by hand.
* **stale tasks are re-queued.** A worker killed between claiming and finishing
  leaves a task ``running`` forever. This sweep is what makes "a crash never
  loses a task" true rather than aspirational; the attempt counter in
  :mod:`dtk.worker.main` bounds how often one task may come back.
* **expiring logins are announced early.** An imported session cookie dies at a
  known time (:func:`dtk.identity.importing.session_expiry`). Warning days ahead
  turns a sudden mass failure into a scheduled chore.

See docs/design/15-operations.md and docs/design/05-data-model.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from dtk.core.crypto import Cipher
from dtk.core.db import get_engine, session_scope
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import IdentitySource, IdentityState, TaskState
from dtk.db.models import CONTINUOUS_AGGREGATES
from dtk.db.models import Identity as IdentityRow
from dtk.db.models import Task as TaskRow
from dtk.identity.importing import session_expiry
from dtk.identity.pool import purge_retired
from dtk.ops import retention
from dtk.services import tasks
from dtk.worker.alerts import Alerter, NotifyEvent, raise_alert

log = get_logger(__name__)

SessionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class MaintenanceConfig:
    interval_seconds: float = 300.0
    #: A task still ``running`` this long after it started is presumed orphaned.
    #: Comfortably above the request timeout plus the scheduler's max wait, so a
    #: slow-but-alive task is never taken away from the worker running it.
    stale_task_seconds: int = 900
    #: How many stale tasks one sweep may re-queue, so a pathological backlog
    #: cannot be pushed back onto the queue all at once.
    requeue_limit: int = 200
    session_expiry_warning_days: int = 3
    #: How far back a manual aggregate refresh reaches. Wider than the chunk
    #: interval, so a refresh that was missed once still catches up.
    aggregate_refresh_window: str = "1 day"


@dataclass(slots=True)
class MaintenanceReport:
    retention: retention.RetentionReport | None = None
    retired_identities_purged: int = 0
    requeued_tasks: int = 0
    aggregates_refreshed: list[str] = field(default_factory=list)
    expiring_sessions: int = 0
    errors: list[str] = field(default_factory=list)


class Maintenance:
    def __init__(
        self,
        *,
        cipher: Cipher,
        config: Callable[[], Any] | Any,
        options: MaintenanceConfig | None = None,
        session_factory: SessionFactory = session_scope,
        engine_factory: Callable[[], Any] = get_engine,
        alerter: Alerter | None = None,
    ) -> None:
        self._cipher = cipher
        self._config: Callable[[], Any] = config if callable(config) else (lambda: config)
        self._options = options or MaintenanceConfig()
        self._session_factory = session_factory
        self._engine_factory = engine_factory
        self._alerter = alerter
        self._timescale: bool | None = None

    # -- entry point -------------------------------------------------------

    async def tick(self) -> MaintenanceReport:
        """Run every job. One failing job never prevents the others."""
        report = MaintenanceReport()
        for name, job in (
            ("apply_retention", self.apply_retention),
            ("purge_retired_identities", self.purge_retired_identities),
            ("requeue_stale_tasks", self.requeue_stale_tasks),
            ("refresh_aggregates", self.refresh_aggregates),
            ("warn_expiring_sessions", self.warn_expiring_sessions),
        ):
            try:
                await job(report)
            except Exception as exc:
                message = f"{name}: {type(exc).__name__}: {exc}"[:200]
                report.errors.append(message)
                log.warning("worker.maintenance.job_failed", job=name, error=message)
        log.info(
            "worker.maintenance.done",
            retired_identities_purged=report.retired_identities_purged,
            requeued_tasks=report.requeued_tasks,
            aggregates_refreshed=len(report.aggregates_refreshed),
            expiring_sessions=report.expiring_sessions,
            errors=len(report.errors),
        )
        return report

    # -- retention ---------------------------------------------------------

    async def apply_retention(self, report: MaintenanceReport) -> None:
        """Re-apply the configured windows and evict aged task payloads."""
        async with self._session_factory() as session:
            report.retention = await retention.run_maintenance(session, self._config())

    async def purge_retired_identities(self, report: MaintenanceReport) -> None:
        """Bound the identities table.

        A TimescaleDB policy cannot: ``identities`` is a plain table, and the
        rows a pool sheds over months are what make it grow without limit. Its
        own job rather than part of the retention pass, so a server without
        TimescaleDB - where every policy call fails - still gets the sweep.
        """
        days = int(self._config().get(retention.RETIRED_IDENTITY_SETTING))
        async with self._session_factory() as session:
            report.retired_identities_purged = await purge_retired(session, days=days)

    # -- tasks -------------------------------------------------------------

    async def requeue_stale_tasks(self, report: MaintenanceReport) -> None:
        """Give tasks orphaned by a dead worker back to the queue.

        Pushed to the tail, not the head: they have already had their turn, and
        a task that keeps killing its worker must not sit in front of healthy
        work. The attempt counter fails it for good soon enough.

        The queue is written only after the transaction has committed. Pushing
        first would hand the id to another worker while the row still says
        ``running``, and a commit that then failed would leave the task both
        queued and claimable again on the next sweep - the one way this job
        could turn one task into two upstream requests.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._options.stale_task_seconds)
        requeued: list[str] = []
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TaskRow)
                        .where(
                            TaskRow.state == TaskState.RUNNING.value,
                            TaskRow.started_at.is_not(None),
                            TaskRow.started_at < cutoff,
                        )
                        .order_by(TaskRow.started_at)
                        .limit(self._options.requeue_limit)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return
            for row in rows:
                row.state = TaskState.QUEUED.value
                row.started_at = None
                requeued.append(str(row.id))

        redis = get_redis()
        for task_id in requeued:
            await redis.rpush(tasks.QUEUE_KEY, task_id)
            report.requeued_tasks += 1
        log.warning("worker.maintenance.requeued_stale", count=report.requeued_tasks)

    # -- time series -------------------------------------------------------

    async def _has_timescale(self, session: Any) -> bool:
        if self._timescale is None:
            row = await session.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
            )
            self._timescale = row.first() is not None
            if not self._timescale:
                log.info("worker.maintenance.no_timescaledb", action="skipping aggregate refresh")
        return self._timescale

    async def refresh_aggregates(self, report: MaintenanceReport) -> None:
        """Refresh continuous aggregates that have no refresh policy.

        ``refresh_continuous_aggregate`` refuses to run inside a transaction, so
        it goes through an autocommit connection rather than the session.
        """
        async with self._session_factory() as session:
            if not await self._has_timescale(session):
                return
            unpolicied = [
                v for v in CONTINUOUS_AGGREGATES if not await self._has_policy(session, v)
            ]

        if not unpolicied:
            return

        window = self._options.aggregate_refresh_window
        engine = self._engine_factory()
        async with engine.connect() as connection:
            autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
            for view in unpolicied:
                # The name is a module constant, never caller input.
                await autocommit.execute(
                    text(
                        f"CALL refresh_continuous_aggregate('{view}', "
                        f"now() - INTERVAL '{window}', now() - INTERVAL '1 minute')"
                    )
                )
                report.aggregates_refreshed.append(view)
                log.info("worker.maintenance.aggregate_refreshed", view=view)

    async def _has_policy(self, session: Any, view: str) -> bool:
        """Whether ``view`` already has a refresh policy.

        TimescaleDB has spelled ``jobs.hypertable_name`` for a refresh policy
        both ways across versions - the view itself on current releases, the
        materialization hypertable on older ones - so both are accepted. Reading
        it wrong in the "no policy" direction would mean refreshing by hand on
        every pass, which is a real cost on a large aggregate.
        """
        result = await session.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.jobs j "
                "LEFT JOIN timescaledb_information.continuous_aggregates c "
                "  ON c.materialization_hypertable_name = j.hypertable_name "
                " AND c.materialization_hypertable_schema = j.hypertable_schema "
                "WHERE j.proc_name = 'policy_refresh_continuous_aggregate' "
                "AND (j.hypertable_name = :view OR c.view_name = :view)"
            ),
            {"view": view},
        )
        return int(result.scalar_one()) > 0

    # -- identities --------------------------------------------------------

    async def warn_expiring_sessions(self, report: MaintenanceReport) -> None:
        """Warn about imported logins whose session is about to expire."""
        now = datetime.now(UTC)
        horizon = now + timedelta(days=self._options.session_expiry_warning_days)
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(IdentityRow).where(
                            IdentityRow.source == IdentitySource.IMPORTED.value,
                            IdentityRow.authenticated.is_(True),
                            IdentityRow.state != IdentityState.RETIRED.value,
                        )
                    )
                )
                .scalars()
                .all()
            )

        for row in rows:
            expires_at = self._expiry_of(row)
            if expires_at is None or expires_at > horizon:
                continue
            report.expiring_sessions += 1
            remaining = max(0.0, (expires_at - now).total_seconds())
            log.warning(
                "worker.identity.session_expiring",
                identity_id=str(row.id),
                platform=row.platform,
                expires_at=expires_at.isoformat(),
                seconds_remaining=int(remaining),
            )
            await raise_alert(
                self._alerter,
                NotifyEvent.COOKIE_EXPIRING,
                identity_id=str(row.id),
                days=round(remaining / 86400, 1),
            )

    def _expiry_of(self, row: Any) -> datetime | None:
        if not row.cookies_encrypted:
            return None
        try:
            header = self._cipher.decrypt(row.cookies_encrypted, aad=str(row.id))
        except Exception:
            # A row this key cannot open is not this job's problem to report.
            return None
        return session_expiry(cookies_from_header(header))


def cookies_from_header(header: str) -> dict[str, str]:
    """Split a ``a=b; c=d`` cookie header back into a mapping."""
    cookies: dict[str, str] = {}
    for chunk in header.split(";"):
        name, separator, value = chunk.partition("=")
        if separator:
            cookies[name.strip()] = value.strip()
    return cookies


__all__ = [
    "Maintenance",
    "MaintenanceConfig",
    "MaintenanceReport",
    "cookies_from_header",
]
