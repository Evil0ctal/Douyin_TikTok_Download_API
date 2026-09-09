"""The download index: what this instance has been asked to store, and what it has.

This module is the durable half of media downloads. The Go sidecar moves bytes
and forgets; ``media_downloads`` is what remains - one row per request, holding
the plan, the outcome, the digests and whether the files are still on the disk.

Three decisions are worth stating outright, because each one is the opposite of
what a simpler implementation would do.

**A row outlives its files.** Eviction under the size ceiling sets
``files_removed_at`` and leaves everything else, so "it was collected and later
cleaned up" stays distinguishable from "it was never fetched". Only the first
can be undone by asking again, and an operator who cannot tell them apart
cannot decide anything.

**Nothing is evicted while it is pinned.** A size-based policy without an
exemption eventually deletes the one file somebody meant to keep, and there is
no way to get it back once the platform has taken the post down.

**The caller never supplies a URL.** A download names a post this instance has
already parsed; the mirrors come from the archive, and everything that fails
the media allowlist is dropped with a reason before a job exists. That is the
whole of doc 08's fourth constraint, restated where it is enforced.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.db.base import affected
from dtk.db.models import ArchivedContent, MediaDownload
from dtk.media import domains
from dtk.media import plan as planner

log = get_logger(__name__)

DEFAULT_PAGE: Final = 50
MAX_PAGE: Final = 200

#: States a job can be in. The first two are live, the rest are terminal.
LIVE_STATES: Final[tuple[str, ...]] = ("queued", "running")
TERMINAL_STATES: Final[tuple[str, ...]] = ("done", "partial", "failed", "cancelled")


class DownloadError(RuntimeError):
    """A download cannot be started, with a sentence saying why."""


@dataclass(frozen=True, slots=True)
class DownloadFilter:
    platform: str | None = None
    content_id: str | None = None
    state: str | None = None
    pinned: bool | None = None
    #: True lists only what is still on disk, False only what has been evicted.
    on_disk: bool | None = None


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def mirrors_are_stale(row: ArchivedContent, *, max_age_seconds: int) -> bool:
    """Whether the archived links are too old to hand to the downloader.

    Measured, not assumed: a TikTok video URL carries ``expire=`` and a link
    stored yesterday answered 403 on 2026-09-08. Douyin's lasted longer. Rather
    than encode a per-platform guess, the age of the observation decides, and
    the window is a setting.
    """
    if max_age_seconds <= 0:
        return True
    seen = row.last_seen_at
    if seen is None:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return datetime.now(UTC) - seen > timedelta(seconds=max_age_seconds)


def plan_for(row: ArchivedContent, *, max_file_bytes: int) -> planner.Plan:
    """The files to fetch for one archived post."""
    platform = Platform(row.platform)
    return planner.build(row.media, platform, max_file_bytes=max_file_bytes)


def job_payload(
    download: MediaDownload,
    row: ArchivedContent,
    plan: planner.Plan,
) -> dict[str, Any]:
    """The submission the sidecar receives.

    Note what is not in it: no cookie, no proxy, no key, no database address.
    The sidecar is given mirrors, the host allowlist it may reach for this one
    platform, and a ceiling. Sending the union of both platforms' domains would
    make "a Douyin post reaching a TikTok CDN" indistinguishable from a normal
    job, so the allowlist is narrowed to the platform the post came from.
    """
    platform = Platform(row.platform)
    return {
        "id": download.id.hex,
        "platform": row.platform,
        "author_uid": row.author_uid,
        "content_id": row.content_id,
        "domains": sorted(domains.MEDIA_DOMAINS_BY_PLATFORM.get(platform, frozenset())),
        "user_agent": planner.USER_AGENT,
        "referer": planner.REFERER.get(platform, ""),
        "meta": planner.sidecar(row, plan),
        "items": [item.as_payload() for item in plan.items],
    }


def directory_of(row: ArchivedContent) -> str:
    return f"{row.platform}/{row.author_uid}/{row.content_id}"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def create(
    session: AsyncSession,
    row: ArchivedContent,
    *,
    requested_by: uuid.UUID | None = None,
) -> MediaDownload:
    """Record the intent to download, before anything is fetched.

    The row exists first so a worker that dies between accepting the request
    and submitting the job leaves something to reconcile rather than nothing.
    """
    download = MediaDownload(
        platform=row.platform,
        content_id=row.content_id,
        author_uid=row.author_uid,
        state="queued",
        directory=directory_of(row),
        requested_by=requested_by,
    )
    session.add(download)
    await session.flush()
    return download


async def get(session: AsyncSession, download_id: uuid.UUID) -> MediaDownload | None:
    return await session.get(MediaDownload, download_id)


async def latest_for(session: AsyncSession, platform: str, content_id: str) -> MediaDownload | None:
    """The most recent download of one post, whatever state it is in."""
    statement = (
        select(MediaDownload)
        .where(MediaDownload.platform == platform, MediaDownload.content_id == content_id)
        .order_by(MediaDownload.created_at.desc())
        .limit(1)
    )
    return (await session.execute(statement)).scalars().first()


async def mark_running(session: AsyncSession, download_id: uuid.UUID) -> None:
    await session.execute(
        update(MediaDownload)
        .where(MediaDownload.id == download_id)
        .values(state="running", started_at=func.now())
    )


async def apply_result(
    session: AsyncSession,
    download_id: uuid.UUID,
    job: dict[str, Any],
) -> None:
    """Copy the sidecar's answer onto the row.

    Only the fields the sidecar owns. ``pinned`` and ``files_removed_at`` are
    the operator's and the eviction sweep's respectively, and a job result must
    never be able to unpin something.
    """
    items = job.get("items")
    files = [_file_row(item) for item in items] if isinstance(items, list) else []
    landed = [file for file in files if file["state"] == "done"]
    await session.execute(
        update(MediaDownload)
        .where(MediaDownload.id == download_id)
        .values(
            state=str(job.get("state") or "failed"),
            bytes_total=int(job.get("bytes_total") or 0),
            file_count=len(landed),
            files=files or None,
            error=(str(job.get("error")) or None) if job.get("error") else None,
            finished_at=func.now(),
        )
    )


async def fail(session: AsyncSession, download_id: uuid.UUID, reason: str) -> None:
    await session.execute(
        update(MediaDownload)
        .where(MediaDownload.id == download_id)
        .values(state="failed", error=reason[:500], finished_at=func.now())
    )


async def cancel(session: AsyncSession, download_id: uuid.UUID) -> bool:
    """Mark a download cancelled, if it has not already settled.

    Conditional on the state rather than unconditional: a job that finished
    between the operator pressing the button and this statement running did
    finish, and overwriting that with "cancelled" would lose the files it
    actually stored.
    """
    result = await session.execute(
        update(MediaDownload)
        .where(MediaDownload.id == download_id, MediaDownload.state.in_(LIVE_STATES))
        .values(state="cancelled", finished_at=func.now())
    )
    return bool(affected(result))


async def set_pinned(session: AsyncSession, download_id: uuid.UUID, pinned: bool) -> bool:
    result = await session.execute(
        update(MediaDownload).where(MediaDownload.id == download_id).values(pinned=pinned)
    )
    return bool(affected(result))


async def mark_evicted(session: AsyncSession, ids: Sequence[uuid.UUID]) -> int:
    """Record that the bytes are gone while keeping everything else.

    ``bytes_total`` is zeroed because it describes what is on the disk and the
    disk no longer holds it; ``file_count`` and ``files`` stay, so the row can
    still say what was there. That asymmetry is deliberate: one of those is a
    measurement, the other is a memory.
    """
    if not ids:
        return 0
    result = await session.execute(
        update(MediaDownload)
        .where(MediaDownload.id.in_(list(ids)), MediaDownload.files_removed_at.is_(None))
        .values(files_removed_at=func.now(), bytes_total=0)
    )
    return affected(result)


def _file_row(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"name": "", "state": "failed", "error": "unreadable item"}
    return {
        "name": str(item.get("name") or ""),
        "kind": str(item.get("kind") or ""),
        "state": str(item.get("state") or "failed"),
        "bytes": int(item.get("bytes") or 0),
        "sha256": str(item.get("sha256") or "") or None,
        "content_type": str(item.get("content_type") or "") or None,
        "error": str(item.get("error") or "") or None,
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _apply(statement: Any, spec: DownloadFilter) -> Any:
    if spec.platform:
        statement = statement.where(MediaDownload.platform == spec.platform)
    if spec.content_id:
        statement = statement.where(MediaDownload.content_id == spec.content_id)
    if spec.state:
        statement = statement.where(MediaDownload.state == spec.state)
    if spec.pinned is not None:
        statement = statement.where(MediaDownload.pinned.is_(spec.pinned))
    if spec.on_disk is True:
        statement = statement.where(MediaDownload.files_removed_at.is_(None))
    if spec.on_disk is False:
        statement = statement.where(MediaDownload.files_removed_at.is_not(None))
    return statement


async def search(
    session: AsyncSession,
    spec: DownloadFilter,
    *,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> tuple[list[MediaDownload], int]:
    """One page of downloads, newest first, with the total.

    Offset paging rather than the archive's keyset, and for a reason that does
    not apply there: this table is small by construction - one row per deliberate
    operator action, not one per parsed post - and the page it feeds shows a
    total and lets someone jump around. The archive's table is the one that
    grows without bound while a client walks it.
    """
    rows = (
        (
            await session.execute(
                _apply(select(MediaDownload), spec)
                .order_by(MediaDownload.created_at.desc(), MediaDownload.id.desc())
                .limit(max(1, min(limit, MAX_PAGE)))
                .offset(max(0, offset))
            )
        )
        .scalars()
        .all()
    )
    total = int(
        (
            await session.execute(_apply(select(func.count()).select_from(MediaDownload), spec))
        ).scalar_one()
    )
    return list(rows), total


async def stats(session: AsyncSession) -> dict[str, Any]:
    """Totals for the console's storage panel."""
    totals = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(MediaDownload.bytes_total), 0),
                func.count().filter(MediaDownload.pinned.is_(True)),
                func.count().filter(MediaDownload.files_removed_at.is_not(None)),
                func.count().filter(MediaDownload.state.in_(LIVE_STATES)),
            )
        )
    ).one()
    by_state = (
        await session.execute(
            select(MediaDownload.state, func.count()).group_by(MediaDownload.state)
        )
    ).all()
    return {
        "downloads": int(totals[0]),
        "bytes_total": int(totals[1]),
        "pinned": int(totals[2]),
        "evicted": int(totals[3]),
        "in_flight": int(totals[4]),
        "by_state": {str(state): int(count) for state, count in by_state},
    }


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Eviction:
    """What one sweep would remove, decided before anything is deleted."""

    paths: tuple[str, ...] = ()
    ids: tuple[uuid.UUID, ...] = ()
    #: Which rows live on each directory. Several rows routinely share one -
    #: the layout has nothing per-download in it - and every one of them loses
    #: its files when that directory goes, so the caller needs the grouping to
    #: mark the right rows after a partial delete.
    ids_by_path: Mapping[str, tuple[uuid.UUID, ...]] = field(default_factory=dict)
    bytes_freed: int = 0
    #: Bytes still on the volume that the sweep cannot touch, because they are
    #: pinned. Surfaced rather than swallowed: an operator who pins more than
    #: the ceiling has quietly disabled their own cleanup, and should be told.
    pinned_bytes: int = 0
    over_by: int = 0


def choose_evictions(rows: Sequence[Any], *, over_by: int) -> Eviction:
    """Pick which downloads to remove, given how far over the ceiling we are.

    Pure, and kept apart from the query for exactly that reason: this is the
    part with a policy in it, and a policy that deletes files should be
    testable without a database.

    ``rows`` arrives oldest first.

    **The unit is the directory, not the row.** ``directory_of`` is
    ``platform/author/content`` with nothing per-download in it, and nothing
    stops the same post being downloaded twice, so several rows routinely share
    one directory - and what the sidecar deletes is a directory, recursively.
    Deciding row by row got both halves wrong: it scheduled a directory for
    deletion because *one* of its rows was unpinned, destroying the pinned
    row's files while reporting those same bytes as protected; and it added the
    same directory's bytes to ``bytes_freed`` once per row, so the sweep
    believed it had freed twice what it had and stopped short of the ceiling.

    So: group by directory, refuse the whole directory if **any** row on it is
    pinned, and count its bytes once. A pin protects the files, which is what
    the pin endpoint promises and what an operator would assume.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault(
            row.directory, {"pinned": False, "bytes": 0, "ids": [], "order": len(groups)}
        )
        group["ids"].append(row.id)
        group["pinned"] = group["pinned"] or bool(row.pinned)
        # The largest claim wins rather than the sum: two rows for one post
        # describe the same files, and adding them counts the bytes twice.
        group["bytes"] = max(int(group["bytes"]), int(row.bytes_total))

    freed = 0
    paths: list[str] = []
    ids: list[uuid.UUID] = []
    by_path: dict[str, tuple[uuid.UUID, ...]] = {}
    pinned_bytes = 0
    for directory, group in sorted(groups.items(), key=lambda item: item[1]["order"]):
        if group["pinned"]:
            pinned_bytes += int(group["bytes"])
            continue
        if freed >= over_by:
            continue
        paths.append(directory)
        # Every row on the directory is marked evicted, because every one of
        # them loses its files when the directory goes.
        ids.extend(group["ids"])
        by_path[directory] = tuple(group["ids"])
        freed += int(group["bytes"])
    return Eviction(
        paths=tuple(paths),
        ids=tuple(ids),
        ids_by_path=by_path,
        bytes_freed=freed,
        pinned_bytes=pinned_bytes,
        over_by=over_by,
    )


async def plan_eviction(session: AsyncSession, *, total_bytes: int, ceiling_bytes: int) -> Eviction:
    """Choose what to delete to get back under the ceiling.

    Oldest first, pinned never, and only as much as it takes. The total comes
    from the volume rather than from this table: files can be removed by hand,
    a restore can bring some back, and a sweep that trusted its own bookkeeping
    would eventually be deleting to satisfy a number nobody else agrees with.

    Nothing is deleted here. The caller does that, and only after this returned
    a list - so "what would be removed" is answerable without removing it.
    """
    if ceiling_bytes <= 0 or total_bytes <= ceiling_bytes:
        return Eviction(over_by=0)

    rows = (
        (
            await session.execute(
                select(MediaDownload)
                .where(
                    MediaDownload.files_removed_at.is_(None),
                    MediaDownload.bytes_total > 0,
                )
                .order_by(
                    MediaDownload.pinned.asc(),
                    func.coalesce(MediaDownload.finished_at, MediaDownload.created_at).asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return choose_evictions(list(rows), over_by=total_bytes - ceiling_bytes)


def as_dict(row: MediaDownload) -> dict[str, Any]:
    """One download, shaped for the API and the console."""
    return {
        "id": str(row.id),
        "platform": row.platform,
        "content_id": row.content_id,
        "author_uid": row.author_uid,
        "state": row.state,
        "directory": row.directory,
        "bytes_total": int(row.bytes_total),
        "file_count": int(row.file_count),
        "files": row.files or [],
        "pinned": bool(row.pinned),
        "on_disk": row.files_removed_at is None,
        "files_removed_at": row.files_removed_at.isoformat() if row.files_removed_at else None,
        "error": row.error,
        "task_id": str(row.task_id) if row.task_id else None,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


__all__ = [
    "DEFAULT_PAGE",
    "LIVE_STATES",
    "MAX_PAGE",
    "TERMINAL_STATES",
    "DownloadError",
    "DownloadFilter",
    "Eviction",
    "apply_result",
    "as_dict",
    "cancel",
    "choose_evictions",
    "create",
    "directory_of",
    "fail",
    "get",
    "job_payload",
    "latest_for",
    "mark_evicted",
    "mark_running",
    "mirrors_are_stale",
    "plan_eviction",
    "plan_for",
    "search",
    "set_pinned",
    "stats",
]
