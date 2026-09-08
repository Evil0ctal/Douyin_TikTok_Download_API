"""The loop that turns a watchlist into a time series.

It does very little on purpose. Once a minute it asks which entries are due,
submits each as an ordinary task, and writes back when the next run is. The
fetching, signing, identity selection, archiving and snapshot writing all
happen where they already happened - in :class:`dtk.worker.main.TaskWorker`,
through the same queue an API caller's request goes through.

That is the whole design. Scheduled collection queues *behind* interactive
requests because somebody is waiting on those; it spends the same identity pool
under the same scheduler, so it cannot outrun the rate limits; and every run is
a task row an operator can open in the console like any other. A second
collection path would have been a second set of limits to get wrong.

Three things it refuses to do:

* **collect while the disk is full.** The capacity guard's whole purpose is to
  stand down the writers nobody is waiting on, and this is the clearest example
  of one. Interactive reads carry on.
* **queue everything at once.** A hundred entries that all come due on the
  minute would be a hundred tasks in front of whoever is using the API. A batch
  ceiling spreads them over the following ticks; they stay due, they are not
  dropped.
* **retry a broken entry at full rate.** A watched id that was mistyped fails
  every run forever otherwise. The interval doubles to a ceiling and the row
  keeps its error where someone will see it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from dtk.core.db import session_scope
from dtk.core.logging import get_logger
from dtk.core.types import TaskState
from dtk.db.models import Task as TaskRow
from dtk.ops import capacity
from dtk.services import tasks, watchlist

log = get_logger(__name__)

SessionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class WatcherConfig:
    #: A minute. The entries carry their own intervals, so this only bounds how
    #: late a run can be, and a shorter tick would be a query per second for a
    #: table that changes hourly.
    interval_seconds: float = 60.0
    #: How often to queue an availability recheck. Six hours, because the pass
    #: itself only verifies `archive.recheck_batch` posts and the question it
    #: answers - has this been deleted - moves on the scale of days.
    availability_every_seconds: float = 6 * 3600


@dataclass(slots=True)
class WatcherReport:
    due: int = 0
    submitted: int = 0
    reconciled: int = 0
    availability_queued: bool = False
    skipped_capacity: bool = False
    disabled: bool = False
    errors: list[str] = field(default_factory=list)
    submitted_ids: list[str] = field(default_factory=list)


class Watcher:
    """Submits due watchlist entries as tasks."""

    def __init__(
        self,
        *,
        config: Callable[[], Any] | Any,
        options: WatcherConfig | None = None,
        session_factory: SessionFactory = session_scope,
    ) -> None:
        self._config: Callable[[], Any] = config if callable(config) else (lambda: config)
        self._options = options or WatcherConfig()
        self._session_factory = session_factory
        #: Monotonic stamp of the last availability sweep queued by this
        #: process. In memory rather than in the database because a missed
        #: sweep costs nothing - the posts stay due - and a restart re-checking
        #: a few hours early is not a problem worth a row to prevent.
        self._last_availability: float | None = None

    async def tick(self) -> WatcherReport:
        report = WatcherReport()
        config = self._config()

        if not bool(config.get("watchlist.enabled")):
            report.disabled = True
            return report

        # Read back last tick's outcomes first, so an entry that failed at the
        # platform is backed off before it is considered for another run.
        await self.reconcile(report)

        verdict = capacity.evaluate(
            warn_percent=float(config.get("capacity.warn_percent")),
            hard_stop_percent=float(config.get("capacity.hard_stop_percent")),
        )
        if verdict.paused:
            # Nothing is waiting on this. Standing down is the whole point of
            # the guard, and the alert has already been raised by maintenance.
            report.skipped_capacity = True
            log.warning(
                "worker.watchlist.paused",
                reason=verdict.detail,
                action="scheduled collection stands down",
            )
            return report

        await self.queue_availability(report, config)

        batch = int(config.get("watchlist.batch_size"))
        async with self._session_factory() as session:
            entries = await watchlist.due(session, limit=batch)
            report.due = len(entries)
            for entry in entries:
                try:
                    endpoint, params = watchlist.task_for(entry)
                except KeyError as exc:
                    # A kind this build does not know, from a row written by a
                    # newer version. Backed off rather than retried in a loop.
                    await watchlist.mark_failed(session, entry.id, f"unknown kind: {exc}")
                    report.errors.append(str(exc))
                    continue
                try:
                    task_id = await tasks.submit(session, endpoint, params)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"[:200]
                    await watchlist.mark_failed(session, entry.id, message)
                    report.errors.append(message)
                    log.warning(
                        "worker.watchlist.submit_failed",
                        entry_id=str(entry.id),
                        platform=entry.platform,
                        error=message,
                    )
                    continue
                await watchlist.mark_submitted(session, entry.id, task_id)
                report.submitted += 1
                report.submitted_ids.append(str(entry.id))

        if report.submitted:
            log.info(
                "worker.watchlist.submitted",
                due=report.due,
                submitted=report.submitted,
                batch=batch,
            )
        return report

    async def queue_availability(self, report: WatcherReport, config: Any) -> None:
        """Queue a recheck pass, at most one every few hours.

        Submitted as a task like everything else rather than run inline: the
        pass makes one real request per post, and a maintenance tick is not the
        place to hold a worker for a minute.

        Only ever one in flight from this process. Queueing a second while the
        first is still walking would double the request rate against a limit
        the operator set once.
        """
        if int(config.get("archive.recheck_after_days")) <= 0:
            return
        now = monotonic()
        if (
            self._last_availability is not None
            and now - self._last_availability < self._options.availability_every_seconds
        ):
            return
        async with self._session_factory() as session:
            await tasks.submit(session, "archive.availability", {})
        self._last_availability = now
        report.availability_queued = True
        log.info("worker.watchlist.availability_queued")

    async def reconcile(self, report: WatcherReport) -> None:
        """Read back how each submitted run turned out.

        Here rather than in the task worker on purpose: the worker knows
        nothing about watchlists and should not have to. An entry records which
        task it queued, and this reads that task's own row - which is also why
        a run whose result has already been evicted is treated as a success
        rather than a failure. The row surviving without its payload means the
        task finished long enough ago for retention to reach it, and inventing
        a failure from that would back off an entry that is working.
        """
        async with self._session_factory() as session:
            entries = await watchlist.unreconciled(session)
            for entry in entries:
                task = await session.get(TaskRow, entry.last_task_id)
                if task is None:
                    # The task row itself is gone. Nothing to learn, and leaving
                    # the entry unreconciled forever would re-query it every
                    # tick, so it is settled without a verdict.
                    await watchlist.mark_result(session, entry.id)
                    report.reconciled += 1
                    continue
                if task.state not in (TaskState.DONE.value, TaskState.FAILED.value):
                    continue
                error = None
                if task.state == TaskState.FAILED.value:
                    detail = task.error if isinstance(task.error, dict) else {}
                    error = str(detail.get("code") or detail.get("message") or "task failed")
                await watchlist.mark_result(
                    session,
                    entry.id,
                    label=watchlist.label_from_result(task.result),
                    error=error,
                )
                report.reconciled += 1


__all__ = ["Watcher", "WatcherConfig", "WatcherReport"]
