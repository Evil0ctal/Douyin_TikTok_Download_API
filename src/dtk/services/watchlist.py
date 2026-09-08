"""What this instance re-collects on a timer, and when it is next due.

The watchlist exists for one table: ``content_snapshots``. Until something
collects on a schedule, that hypertable holds whatever a human happened to
parse, at whatever moments they happened to do it - a scatter of unrelated
observations rather than a series. Doc 05 describes it as a time series; this
is what makes that true.

**Nothing here fetches.** An entry that comes due is submitted as an ordinary
task and the existing worker does the rest: same queue, same scheduler, same
identity pool, same archive and snapshot writes at the end. Scheduled
collection therefore queues *behind* interactive requests, which is the correct
priority - somebody is waiting on those. A second collection path would be a
second set of rate limits to get wrong, and doc 03's whole argument is that
there should be one.

Backoff is on the entry, not on the task. A watched author whose id was mistyped
fails every run forever otherwise; doubling the interval up to a ceiling turns
that into a few attempts a day and leaves the row visible, with its error, for
whoever comes to fix it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.db.base import affected
from dtk.db.models import WatchlistEntry

log = get_logger(__name__)

#: What may be watched. An author is watched for new posts and follower counts;
#: a post for how its own metrics move. Both end in the same two tables.
KINDS: Final[tuple[str, ...]] = ("author", "content")

#: The capability each kind collects with. Author entries deliberately collect
#: the post list rather than the profile: the list carries the author record on
#: every item anyway, so one request answers both questions.
CAPABILITY: Final[dict[str, str]] = {
    "author": "author_posts",
    "content": "content_detail",
}

#: How the target id is spelled in the task's parameters.
PARAMETER: Final[dict[str, str]] = {
    "author": "author_id",
    "content": "content_id",
}

#: Ceiling on the backoff. Eight hours after a run of failures is often enough
#: to notice a target that came back, and rare enough to stop being a drain.
MAX_BACKOFF_SECONDS: Final = 8 * 3600

#: Failures before the interval starts doubling. One transient network error
#: should not change an entry's schedule.
BACKOFF_AFTER_FAILURES: Final = 2


@dataclass(frozen=True, slots=True)
class WatchFilter:
    platform: str | None = None
    kind: str | None = None
    enabled: bool | None = None


def validate(
    platform: str, kind: str, target_id: str, interval_seconds: int, *, floor: int
) -> None:
    """Refuse an entry that cannot work, by name.

    The interval floor is the one that matters: an entry set to 10 seconds does
    not collect a better time series, it burns the identity pool on one target
    and starves everything else. Refusing it beats accepting it and quietly
    substituting a different number.
    """
    try:
        Platform(platform)
    except ValueError as exc:
        raise InvalidParam(f"unknown platform: {platform}") from exc
    if kind not in KINDS:
        raise InvalidParam(f"kind must be one of {', '.join(KINDS)}")
    if not target_id.strip():
        raise InvalidParam("target_id is required")
    if interval_seconds < floor:
        raise InvalidParam(
            f"interval must be at least {floor} seconds",
            details={"minimum_seconds": floor},
        )


async def add(
    session: AsyncSession,
    *,
    platform: str,
    kind: str,
    target_id: str,
    interval_seconds: int,
    label: str | None = None,
    pages: int = 1,
    created_by: uuid.UUID | None = None,
    floor: int,
) -> WatchlistEntry:
    """Start watching one target.

    Due immediately: somebody who just added an author wants to see it collect,
    and spreading the first run out by a random offset would only make the
    feature look broken for six hours.
    """
    validate(platform, kind, target_id, interval_seconds, floor=floor)
    target = target_id.strip()

    # Checked before the insert rather than only caught after it. A failed
    # flush poisons the session for everything downstream - the request would
    # then end in PendingRollbackError and reach the caller as a 500 instead of
    # "you are already watching that", which is what it actually is.
    duplicate = await session.scalar(
        select(WatchlistEntry.id).where(
            WatchlistEntry.platform == platform,
            WatchlistEntry.kind == kind,
            WatchlistEntry.target_id == target,
        )
    )
    if duplicate is not None:
        raise InvalidParam(
            "this target is already on the watchlist",
            details={"platform": platform, "kind": kind, "target_id": target},
        )

    entry = WatchlistEntry(
        platform=platform,
        kind=kind,
        target_id=target,
        label=(label or None),
        interval_seconds=interval_seconds,
        pages=max(1, min(pages, 10)),
        next_run_at=datetime.now(UTC),
        created_by=created_by,
    )
    session.add(entry)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The unique index, as the backstop for two callers racing between the
        # check above and this insert. Rolled back first, because the session
        # is unusable afterwards and the error handler still has to answer.
        await session.rollback()
        raise InvalidParam(
            "this target is already on the watchlist",
            details={"platform": platform, "kind": kind, "target_id": target},
        ) from exc
    return entry


async def get(session: AsyncSession, entry_id: uuid.UUID) -> WatchlistEntry | None:
    return await session.get(WatchlistEntry, entry_id)


async def remove(session: AsyncSession, entry_id: uuid.UUID) -> bool:
    entry = await session.get(WatchlistEntry, entry_id)
    if entry is None:
        return False
    await session.delete(entry)
    return True


async def update_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    interval_seconds: int | None = None,
    enabled: bool | None = None,
    pages: int | None = None,
    floor: int,
) -> WatchlistEntry | None:
    entry = await session.get(WatchlistEntry, entry_id)
    if entry is None:
        return None
    if interval_seconds is not None:
        if interval_seconds < floor:
            raise InvalidParam(
                f"interval must be at least {floor} seconds",
                details={"minimum_seconds": floor},
            )
        entry.interval_seconds = interval_seconds
        # A shortened interval takes effect now rather than after the old one
        # elapses, which is what someone shortening it is asking for.
        entry.next_run_at = min(
            entry.next_run_at, datetime.now(UTC) + timedelta(seconds=interval_seconds)
        )
    if pages is not None:
        entry.pages = max(1, min(pages, 10))
    if enabled is not None:
        entry.enabled = enabled
        if enabled:
            # Re-enabling clears the backoff: whatever was wrong, the operator
            # is asserting it is fixed, and making them wait eight hours to
            # find out would be its own bug.
            entry.consecutive_failures = 0
            entry.last_error = None
            entry.next_run_at = datetime.now(UTC)
    return entry


async def due(
    session: AsyncSession, *, limit: int, now: datetime | None = None
) -> list[WatchlistEntry]:
    """Entries whose next run has arrived, oldest first.

    ``limit`` is what keeps a large watchlist from becoming a thundering herd
    on the minute: the rest stay due and are picked up on the following tick,
    which spreads a hundred entries over a few minutes rather than queueing
    them all at once behind whoever is waiting on an interactive request.
    """
    stamp = now or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(WatchlistEntry)
                .where(WatchlistEntry.enabled.is_(True), WatchlistEntry.next_run_at <= stamp)
                .order_by(WatchlistEntry.next_run_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def task_for(entry: WatchlistEntry) -> tuple[str, dict[str, Any]]:
    """The endpoint and parameters one run submits."""
    endpoint = f"{entry.platform}.{CAPABILITY[entry.kind]}"
    params: dict[str, Any] = {PARAMETER[entry.kind]: entry.target_id}
    if entry.kind == "author":
        # The page size the platform's own web client asks for. Larger pages
        # are not obviously cheaper - they are one request either way - and a
        # page that is too large is a shape the platform does not normally see.
        params["count"] = 20
    return endpoint, params


def backoff_seconds(entry: WatchlistEntry) -> int:
    """How long to wait after a failure.

    The configured interval until failures pile up, then doubling to a ceiling.
    A watched target whose id is wrong should be retried less and less often,
    and should still be retried - a platform outage ends.
    """
    if entry.consecutive_failures < BACKOFF_AFTER_FAILURES:
        return entry.interval_seconds
    doublings = min(entry.consecutive_failures - BACKOFF_AFTER_FAILURES + 1, 10)
    return min(entry.interval_seconds * (2**doublings), MAX_BACKOFF_SECONDS)


async def mark_submitted(
    session: AsyncSession, entry_id: uuid.UUID, task_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """Record that a run was queued and schedule the next one.

    Scheduled from submission rather than from completion, deliberately: a task
    that never finishes would otherwise stop the entry forever, and "collect
    every six hours" should keep meaning that even when one run is lost.
    """
    stamp = now or datetime.now(UTC)
    entry = await session.get(WatchlistEntry, entry_id)
    if entry is None:
        return
    entry.last_run_at = stamp
    entry.last_task_id = task_id
    entry.runs += 1
    entry.next_run_at = stamp + timedelta(seconds=entry.interval_seconds)


async def mark_failed(
    session: AsyncSession, entry_id: uuid.UUID, reason: str, *, now: datetime | None = None
) -> None:
    """Record that a run could not even be queued, and back off."""
    stamp = now or datetime.now(UTC)
    entry = await session.get(WatchlistEntry, entry_id)
    if entry is None:
        return
    entry.consecutive_failures += 1
    entry.last_error = reason[:500]
    entry.next_run_at = stamp + timedelta(seconds=backoff_seconds(entry))


async def unreconciled(session: AsyncSession, *, limit: int = 50) -> list[WatchlistEntry]:
    """Entries whose last submitted run has not been read back yet.

    Expressed as "ran more recently than we last heard back" rather than as a
    separate flag, so there is no second piece of state to keep in step with
    the first.
    """
    rows = (
        (
            await session.execute(
                select(WatchlistEntry)
                .where(
                    WatchlistEntry.last_task_id.is_not(None),
                    WatchlistEntry.last_run_at.is_not(None),
                    (WatchlistEntry.last_result_at.is_(None))
                    | (WatchlistEntry.last_result_at < WatchlistEntry.last_run_at),
                )
                .order_by(WatchlistEntry.last_run_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def mark_result(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    label: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    """Record how the submitted task turned out.

    ``label`` is filled from the first successful run so an operator who pasted
    a sec_user_id sees a nickname afterwards. It is only ever written, never
    cleared: a rename upstream should update it, a failed run should not blank it.

    A failure here also backs the entry off. That is the case worth having -
    an id that does not exist fails at the platform, not at submission, so a
    backoff that only counted submission errors would never fire.
    """
    stamp = now or datetime.now(UTC)
    entry = await session.get(WatchlistEntry, entry_id)
    if entry is None:
        return
    entry.last_result_at = stamp
    if error:
        entry.consecutive_failures += 1
        entry.last_error = error[:500]
        entry.next_run_at = stamp + timedelta(seconds=backoff_seconds(entry))
        return
    entry.consecutive_failures = 0
    entry.last_error = None
    if label:
        entry.label = label


def label_from_result(payload: Any) -> str | None:
    """A display name for the entry, dug out of a stored task result.

    Reads the stored dictionary rather than a parsed model, because that is
    what survives in ``tasks.result`` by the time the watcher looks. Best effort
    throughout: the label is a convenience, and a shape this does not recognise
    should cost nothing rather than fail a reconciliation.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    def nickname_of(candidate: Any) -> str | None:
        if not isinstance(candidate, dict):
            return None
        author = candidate.get("author")
        if isinstance(author, dict) and author.get("nickname"):
            return str(author["nickname"])
        if candidate.get("nickname"):
            return str(candidate["nickname"])
        return None

    direct = nickname_of(data)
    if direct:
        return direct
    items = data.get("items")
    if isinstance(items, list):
        for item in items:
            found = nickname_of(item)
            if found:
                return found
    return None


def _apply(statement: Any, spec: WatchFilter) -> Any:
    if spec.platform:
        statement = statement.where(WatchlistEntry.platform == spec.platform)
    if spec.kind:
        statement = statement.where(WatchlistEntry.kind == spec.kind)
    if spec.enabled is not None:
        statement = statement.where(WatchlistEntry.enabled.is_(spec.enabled))
    return statement


async def search(
    session: AsyncSession, spec: WatchFilter, *, limit: int = 200, offset: int = 0
) -> tuple[list[WatchlistEntry], int]:
    rows = (
        (
            await session.execute(
                _apply(select(WatchlistEntry), spec)
                .order_by(WatchlistEntry.next_run_at.asc())
                .limit(max(1, min(limit, 500)))
                .offset(max(0, offset))
            )
        )
        .scalars()
        .all()
    )
    total = int(
        (
            await session.execute(_apply(select(func.count()).select_from(WatchlistEntry), spec))
        ).scalar_one()
    )
    return list(rows), total


async def stats(session: AsyncSession) -> dict[str, Any]:
    totals = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(WatchlistEntry.enabled.is_(True)),
                func.count().filter(WatchlistEntry.consecutive_failures > 0),
                func.coalesce(func.sum(WatchlistEntry.runs), 0),
            )
        )
    ).one()
    return {
        "entries": int(totals[0]),
        "enabled": int(totals[1]),
        "failing": int(totals[2]),
        "runs": int(totals[3]),
    }


async def pause_all(session: AsyncSession) -> int:
    """Disable every entry. The console's stop button."""
    result = await session.execute(
        update(WatchlistEntry).where(WatchlistEntry.enabled.is_(True)).values(enabled=False)
    )
    return affected(result)


def as_dict(entry: WatchlistEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "platform": entry.platform,
        "kind": entry.kind,
        "target_id": entry.target_id,
        "label": entry.label,
        "interval_seconds": int(entry.interval_seconds),
        "enabled": bool(entry.enabled),
        "pages": int(entry.pages),
        "next_run_at": entry.next_run_at.isoformat(),
        "last_run_at": entry.last_run_at.isoformat() if entry.last_run_at else None,
        "last_task_id": str(entry.last_task_id) if entry.last_task_id else None,
        "last_error": entry.last_error,
        "consecutive_failures": int(entry.consecutive_failures),
        "runs": int(entry.runs),
        "created_at": entry.created_at.isoformat(),
    }


__all__ = [
    "BACKOFF_AFTER_FAILURES",
    "CAPABILITY",
    "KINDS",
    "MAX_BACKOFF_SECONDS",
    "WatchFilter",
    "add",
    "as_dict",
    "backoff_seconds",
    "due",
    "get",
    "label_from_result",
    "mark_failed",
    "mark_result",
    "mark_submitted",
    "pause_all",
    "remove",
    "search",
    "stats",
    "task_for",
    "unreconciled",
    "update_entry",
    "validate",
]
