"""Scheduled collection: what this instance re-collects, and how often.

Administrative rather than a public data endpoint, and deliberately so. An
entry here is not a request for data - it is a standing instruction that will
spend the identity pool every few hours for as long as it exists, which is the
same kind of decision as adding a proxy or an identity. It sits beside those,
under the same guards.

Nothing here fetches or schedules. The row says when the next run is due and
the worker's watchlist loop submits it as an ordinary task, so scheduled
collection queues behind interactive requests and spends the same pool under
the same scheduler (docs/design/18, §5).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request

from dtk.api.deps import Principal
from dtk.api.routes.schemas import WatchCreate, WatchUpdate
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    audit,
    manage_pool,
    ok,
    read_admin,
)
from dtk.core.errors import NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Platform, WatchKind
from dtk.services import watchlist

log = get_logger(__name__)

router = APIRouter(prefix="/watchlist", tags=["admin"])

ENTRY_ID = Path(description="The watchlist entry id.")
PLATFORM_QUERY = Query(default=None, description="Only this platform.")
KIND_QUERY = Query(default=None, description="Only entries of this kind.")
ENABLED_QUERY = Query(default=None, description="Only enabled entries, or only paused ones.")
LIMIT_QUERY = Query(default=None, ge=1, le=MAX_ADMIN_PAGE_SIZE, description="Rows per page.")
OFFSET_QUERY = Query(default=0, ge=0, description="Rows to skip.")


def _floor(request: Request) -> int:
    return int(request.app.state.config.get("watchlist.min_interval_seconds"))


@router.get("", summary="List watched targets")
async def list_watchlist(
    request: Request,
    platform: Platform | None = PLATFORM_QUERY,
    kind: WatchKind | None = KIND_QUERY,
    enabled: bool | None = ENABLED_QUERY,
    limit: int | None = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    principal: Principal = Depends(read_admin),
) -> Any:
    """Every target this instance collects on a timer, soonest due first."""
    spec = watchlist.WatchFilter(platform=platform, kind=kind, enabled=enabled)
    rows, total = await watchlist.search(
        request.state.db, spec, limit=limit or DEFAULT_ADMIN_PAGE_SIZE, offset=offset
    )
    return ok(
        request,
        {
            "items": [watchlist.as_dict(row) for row in rows],
            "total": total,
            "stats": await watchlist.stats(request.state.db),
            "min_interval_seconds": _floor(request),
            "default_interval_seconds": int(
                request.app.state.config.get("watchlist.default_interval_seconds")
            ),
            "enabled": bool(request.app.state.config.get("watchlist.enabled")),
        },
    )


@router.post("", summary="Watch a target", status_code=201)
async def add_watch(
    request: Request,
    body: WatchCreate,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Start collecting one author or post on a schedule.

    Due immediately: someone who just added an author wants to see it collect,
    and holding the first run back by the interval would only make the feature
    look broken for six hours.
    """
    session = request.state.db
    entry = await watchlist.add(
        session,
        platform=body.platform.value,
        kind=body.kind,
        target_id=body.target_id,
        interval_seconds=body.interval_seconds
        or int(request.app.state.config.get("watchlist.default_interval_seconds")),
        label=body.label,
        pages=body.pages,
        created_by=principal.user_id,
        floor=_floor(request),
    )
    await audit(
        request,
        principal,
        "watchlist.added",
        target_type="watchlist",
        target_id=str(entry.id),
        detail={"platform": entry.platform, "kind": entry.kind},
    )
    await session.commit()
    log.info(
        "watchlist.added",
        entry_id=str(entry.id),
        platform=entry.platform,
        kind=entry.kind,
        interval_seconds=entry.interval_seconds,
    )
    return ok(request, watchlist.as_dict(entry), status_code=201)


@router.patch("/{entry_id}", summary="Change or pause a watched target")
async def update_watch(
    request: Request,
    body: WatchUpdate,
    entry_id: uuid.UUID = ENTRY_ID,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Change the interval, the page depth, or whether it runs at all.

    Re-enabling clears the failure backoff: the operator is asserting that
    whatever was wrong is fixed, and making them wait out an eight-hour backoff
    to find out would be its own bug.
    """
    session = request.state.db
    entry = await watchlist.update_entry(
        session,
        entry_id,
        interval_seconds=body.interval_seconds,
        enabled=body.enabled,
        pages=body.pages,
        floor=_floor(request),
    )
    if entry is None:
        raise NotFound("no such watchlist entry", details={"entry_id": str(entry_id)})
    await audit(
        request,
        principal,
        "watchlist.updated",
        target_type="watchlist",
        target_id=str(entry_id),
        detail={"enabled": entry.enabled, "interval_seconds": entry.interval_seconds},
    )
    await session.commit()
    return ok(request, watchlist.as_dict(entry))


@router.delete("/{entry_id}", summary="Stop watching a target")
async def remove_watch(
    request: Request,
    entry_id: uuid.UUID = ENTRY_ID,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Stop collecting this target.

    Removes the schedule and nothing else. Everything already collected stays
    in the archive and in the snapshot history - deleting the instruction is
    not a request to delete what it produced.
    """
    session = request.state.db
    if not await watchlist.remove(session, entry_id):
        raise NotFound("no such watchlist entry", details={"entry_id": str(entry_id)})
    await audit(
        request,
        principal,
        "watchlist.removed",
        target_type="watchlist",
        target_id=str(entry_id),
    )
    await session.commit()
    log.info("watchlist.removed", entry_id=str(entry_id))
    return ok(request, {"removed": True, "entry_id": str(entry_id)})


@router.post("/pause", summary="Pause every watched target")
async def pause_watchlist(
    request: Request,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Stop all scheduled collection without losing the list.

    The button for "something is wrong and I want the pool back". Turning the
    `watchlist.enabled` setting off does the same thing globally; this leaves
    that setting alone and pauses the entries themselves, so re-enabling is
    per entry and deliberate.
    """
    session = request.state.db
    paused = await watchlist.pause_all(session)
    await audit(
        request,
        principal,
        "watchlist.paused",
        target_type="watchlist",
        detail={"entries": paused},
    )
    await session.commit()
    log.warning("watchlist.paused_all", entries=paused)
    return ok(request, {"paused": paused})


__all__ = ["router"]
