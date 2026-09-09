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
* **orphaned tasks are re-queued.** The sweep above looks only at ``running``
  rows, and for a long time nothing looked at the other shape: a row still
  ``queued`` whose id is no longer on the Redis list. Every predicate of the
  stale sweep fails on it - wrong state, and a NULL ``started_at`` that fails
  both remaining comparisons - so a task dropped before it ever started was
  unreachable by any code path in the system. Measured on 2026-09-08: three
  such rows, the oldest twenty hours old.
* **expiring logins are announced early.** An imported session cookie dies at a
  known time (:func:`dtk.identity.importing.session_expiry`). Warning days ahead
  turns a sudden mass failure into a scheduled chore.
* **stored media stays under its ceiling.** The only job here that deletes
  anything a user can see, and the only one that had to: an unattended instance
  fetching video fills a partition otherwise. Oldest first, pinned never, files
  only - the `media_downloads` row survives its files, so "collected and later
  cleaned up" stays a different fact from "never fetched".
* **abandoned downloads stop claiming to be in flight.** The task that runs a
  download is re-queued like any other, but a task that exhausts its attempts
  leaves the download row saying `running` for good. Nothing else would ever
  correct it, and a console that shows a transfer in flight forever is worse
  than one that shows a failure.

See docs/design/15-operations.md and docs/design/05-data-model.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update

from dtk.core.crypto import Cipher
from dtk.core.db import get_engine, session_scope
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import IdentitySource, IdentityState, TaskState
from dtk.db.base import affected
from dtk.db.models import CONTINUOUS_AGGREGATES
from dtk.db.models import Identity as IdentityRow
from dtk.db.models import MediaDownload as MediaDownloadRow
from dtk.db.models import Task as TaskRow
from dtk.identity.importing import session_expiry
from dtk.identity.pool import purge_retired
from dtk.media import DownloaderClient, DownloaderUnavailable
from dtk.ops import capacity, retention
from dtk.services import downloads, tasks
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
    #: A task still ``queued`` this long after it was created, with no entry on
    #: the Redis list, was dropped rather than merely waiting. Generous: a deep
    #: backlog is legitimately queued for minutes, and its id IS on the list, so
    #: this window only has to outlast the gap between commit and publish.
    orphan_task_seconds: int = 300
    session_expiry_warning_days: int = 3
    #: A download still queued or running this long after it was created is
    #: presumed abandoned. Generous: a 250 MB video over a slow link is a
    #: legitimate half hour, and failing a transfer that is still going would
    #: be a worse error than leaving a stale row for another tick.
    stale_download_seconds: int = 7200
    #: How far back a manual aggregate refresh reaches. Wider than the chunk
    #: interval, so a refresh that was missed once still catches up.
    aggregate_refresh_window: str = "1 day"


@dataclass(slots=True)
class MaintenanceReport:
    retention: retention.RetentionReport | None = None
    retired_identities_purged: int = 0
    requeued_tasks: int = 0
    #: Tasks that were queued in the database but on no queue.
    requeued_orphans: int = 0
    #: The disk verdict this tick. Background writers read `paused` from it.
    capacity: capacity.CapacityReport | None = None
    aggregates_refreshed: list[str] = field(default_factory=list)
    expiring_sessions: int = 0
    #: Downloads whose files were removed to stay under the media ceiling.
    media_evicted: int = 0
    media_freed_bytes: int = 0
    #: Downloads that were still claiming to be in flight long after any real
    #: transfer could have been.
    stale_downloads_failed: int = 0
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
        downloader: DownloaderClient | None = None,
    ) -> None:
        self._cipher = cipher
        self._config: Callable[[], Any] = config if callable(config) else (lambda: config)
        self._options = options or MaintenanceConfig()
        self._session_factory = session_factory
        self._engine_factory = engine_factory
        self._alerter = alerter
        self._downloader = downloader
        self._timescale: bool | None = None

    # -- entry point -------------------------------------------------------

    async def tick(self) -> MaintenanceReport:
        """Run every job. One failing job never prevents the others."""
        report = MaintenanceReport()
        for name, job in (
            ("apply_retention", self.apply_retention),
            ("purge_retired_identities", self.purge_retired_identities),
            ("requeue_stale_tasks", self.requeue_stale_tasks),
            ("requeue_orphaned_tasks", self.requeue_orphaned_tasks),
            ("refresh_aggregates", self.refresh_aggregates),
            ("warn_expiring_sessions", self.warn_expiring_sessions),
            ("check_capacity", self.check_capacity),
            ("enforce_media_ceiling", self.enforce_media_ceiling),
            ("fail_stale_downloads", self.fail_stale_downloads),
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
            requeued_orphans=report.requeued_orphans,
            aggregates_refreshed=len(report.aggregates_refreshed),
            expiring_sessions=report.expiring_sessions,
            media_evicted=report.media_evicted,
            stale_downloads_failed=report.stale_downloads_failed,
            errors=len(report.errors),
        )
        return report

    # -- capacity ----------------------------------------------------------

    async def check_capacity(self, report: MaintenanceReport) -> None:
        """Measure the disk and alert, without deleting anything.

        Nothing here frees space. It reports, and past the hard stop it sets the
        flag background writers consult - collection and downloads stand down,
        interactive reads carry on. A full disk should degrade the instance, not
        take it offline, and certainly not destroy what it was collecting.
        """
        config = self._config()
        verdict = capacity.evaluate(
            warn_percent=float(config.get("capacity.warn_percent")),
            hard_stop_percent=float(config.get("capacity.hard_stop_percent")),
        )
        report.capacity = verdict
        if verdict.state is capacity.CapacityState.OK:
            return

        event = (
            NotifyEvent.CAPACITY_PAUSED
            if verdict.state is capacity.CapacityState.FULL
            else NotifyEvent.CAPACITY_WARNING
        )
        log.warning(
            "worker.maintenance.capacity",
            state=verdict.state.value,
            worst_path=verdict.worst_path,
            worst_percent=verdict.worst_percent,
        )
        await raise_alert(
            self._alerter,
            event,
            detail=verdict.detail,
            worst_path=verdict.worst_path or "",
            hard_stop=int(config.get("capacity.hard_stop_percent")),
        )

    # -- media -------------------------------------------------------------

    async def enforce_media_ceiling(self, report: MaintenanceReport) -> None:
        """Delete stored media, oldest first, until the volume is under its ceiling.

        The only job in this class that removes something a user can see, so
        every guard is explicit:

        * the total comes from the volume, not from this database. Files can be
          removed by hand or restored from a backup, and a sweep that trusted
          its own bookkeeping would delete to satisfy a number nothing else
          agrees with;
        * pinned downloads are skipped, always. Without an exemption a
          size-based policy eventually removes the one file somebody meant to
          keep, and a deleted post cannot be fetched again;
        * only files go. The row, its digests and its file list stay, so the
          library still shows what was collected and says it was evicted;
        * every sweep that removes something raises an alert. This is the only
          notice an operator gets that their disk policy just ran.

        A ceiling of 0 disables the whole thing, which is a choice an operator
        is allowed to make.
        """
        client = self._downloader
        if client is None or not client.configured:
            return
        ceiling = int(self._config().get("media.max_bytes"))
        if ceiling <= 0:
            return

        try:
            volume = await client.files()
        except DownloaderUnavailable as exc:
            # An absent sidecar is not an error here. The profile may simply not
            # be running, and a maintenance pass must not start failing because
            # an optional container is down.
            log.debug("worker.maintenance.media_unavailable", error=str(exc)[:200])
            return

        total = int(volume.get("total_bytes") or 0)
        if total <= ceiling:
            return

        async with self._session_factory() as session:
            eviction = await downloads.plan_eviction(
                session, total_bytes=total, ceiling_bytes=ceiling
            )
        if not eviction.paths:
            # Over the ceiling with nothing evictable means everything on the
            # volume is pinned, or was written by something other than a
            # recorded download. Saying so beats sweeping silently forever.
            log.warning(
                "worker.maintenance.media_over_ceiling",
                total_bytes=total,
                ceiling_bytes=ceiling,
                pinned_bytes=eviction.pinned_bytes,
                action="nothing evictable",
            )
            return

        removed = await client.delete(list(eviction.paths))
        freed = int(removed.get("freed_bytes") or 0)
        confirmed = {str(path) for path in removed.get("removed") or []}
        # Only what the sidecar confirmed it removed is marked evicted. Marking
        # optimistically would produce a row claiming the file is gone while it
        # is still on the disk, and the next sweep would then never reclaim it.
        # Grouped by directory rather than zipped against it: several rows can
        # share one directory, so the two tuples are not parallel and zipping
        # them raised as soon as a post had been downloaded twice.
        evicted_ids = [
            download_id
            for path in eviction.paths
            if path in confirmed
            for download_id in eviction.ids_by_path.get(path, ())
        ]
        async with self._session_factory() as session:
            report.media_evicted = await downloads.mark_evicted(session, evicted_ids)
        report.media_freed_bytes = freed

        log.warning(
            "worker.maintenance.media_evicted",
            downloads=report.media_evicted,
            freed_bytes=freed,
            total_bytes=total,
            ceiling_bytes=ceiling,
        )
        await raise_alert(
            self._alerter,
            NotifyEvent.MEDIA_EVICTED,
            files=report.media_evicted,
            freed=_human_bytes(freed),
            ceiling=_human_bytes(ceiling),
        )

    async def fail_stale_downloads(self, report: MaintenanceReport) -> None:
        """Settle downloads whose worker never came back.

        Only rows old enough that no transfer could still be running, and only
        the two live states. Nothing on disk is touched: files that did land
        stay where they are, and the row keeps whatever it managed to record.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._options.stale_download_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                update(MediaDownloadRow)
                .where(
                    MediaDownloadRow.state.in_(downloads.LIVE_STATES),
                    MediaDownloadRow.created_at < cutoff,
                )
                .values(
                    state="failed",
                    error="the worker never reported an outcome for this download",
                    finished_at=func.now(),
                )
            )
        report.stale_downloads_failed = affected(result)
        if report.stale_downloads_failed:
            log.warning("worker.maintenance.stale_downloads", count=report.stale_downloads_failed)

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

    async def requeue_orphaned_tasks(self, report: MaintenanceReport) -> None:
        """Recover tasks that are queued in the database but on no queue.

        The shape this catches: ``state='queued'``, ``started_at`` NULL, and the
        id absent from the Redis list. A task in that state was published and
        then lost - historically because the id was pushed before the row was
        committed, so a worker popped it, found nothing and dropped it - and
        nothing else in this system can see it. :meth:`requeue_stale_tasks`
        filters on ``state == running``, which this fails on all three of its
        predicates.

        The list is read BEFORE the query, deliberately. A task popped between
        the two reads is merely pushed again and refused by
        ``DatabaseTaskStore.start`` if it has since finished; the other order
        would let a task submitted in that window look like an orphan when the
        query ran first and the list read second missed its entry.
        """
        redis = get_redis()
        queued_ids = {str(value) for value in await redis.lrange(tasks.QUEUE_KEY, 0, -1)}

        cutoff = datetime.now(UTC) - timedelta(seconds=self._options.orphan_task_seconds)
        orphans: list[str] = []
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TaskRow)
                        .where(
                            TaskRow.state == TaskState.QUEUED.value,
                            TaskRow.created_at < cutoff,
                        )
                        .order_by(TaskRow.created_at)
                        .limit(self._options.requeue_limit)
                    )
                )
                .scalars()
                .all()
            )
            orphans = [str(row.id) for row in rows if str(row.id) not in queued_ids]

        for task_id in orphans:
            await redis.rpush(tasks.QUEUE_KEY, task_id)
            report.requeued_orphans += 1
        if orphans:
            log.warning(
                "worker.maintenance.requeued_orphans",
                count=report.requeued_orphans,
                oldest=orphans[0],
            )

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


def _human_bytes(value: int) -> str:
    """Bytes as something an operator reads in an alert rather than counts.

    "1.9 GiB" in a notification is the number someone acts on; 2040109465 is a
    number they have to divide twice before they can.
    """
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


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
