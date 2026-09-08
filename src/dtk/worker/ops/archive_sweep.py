"""Two jobs that keep the archive honest about what still exists.

**availability** re-checks archived posts that this instance still believes are
live. Until now nothing did: a deleted post answers BUSINESS_ERROR, the fetch
raises, the archive write never happens, and the row keeps saying `live`
forever. So "which of the things I saved are gone" - the question doc 18 names
for this workstream - had no answer at all.

**backfill** walks an author's history past the first page, which the watchlist
deliberately does not. A watchlist is for what is new and runs forever; a
backfill is a one-off with a very different cost, and conflating them would
make every scheduled run as expensive as the deepest one anybody ever wanted.

Both run through :class:`dtk.services.fetch.FetchService` - the same pipeline,
pool and scheduler as everything else - and both are bounded by an explicit
ceiling rather than by how much history happens to exist.

A NOT_FOUND here is the *finding*, not a failure: it is what "this post is
gone" looks like from the outside, and the sweep records it and moves on. Any
other error leaves the row untouched, because a risk-control response or a
network fault says nothing about whether the post exists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from dtk.core.errors import ContentPrivate, DtkError, ErrorCode, Internal, InvalidParam
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.services import archive
from dtk.services.fetch import FetchContext
from dtk.worker import registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)

#: Pause between checks, when the setting does not say.
#:
#: Three seconds, and the number is measured rather than chosen. At 0.5s the
#: first live sweep sent 20 TikTok requests in 13 seconds and every one came
#: back rejected, while a single manual request a minute later succeeded - the
#: scheduler paces per identity, and rotating nine cookie jars does nothing
#: about a limit that counts requests per address. Nobody is waiting on this
#: pass, so slow is free: 25 posts at 3s is 75 seconds, four times a day.
PAUSE_SECONDS: Final = 3.0

#: Hard ceiling on one sweep, whatever the caller asks for. A sweep that ran
#: for an hour would hold a worker slot for an hour.
MAX_BATCH: Final = 200

#: Hard ceiling on one backfill, in pages. Twenty pages of twenty posts is four
#: hundred posts, which is more history than most accounts have.
MAX_PAGES: Final = 20


def _capturer(call: Any, sink: list[Any]) -> Any:
    """A parse callback bound to one resolved call and one result list."""

    def capture(payload: dict[str, Any]) -> Any:
        model = call.parse(payload)
        sink.append(model)
        return model

    return capture


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Re-check the archive's oldest unverified posts."""
    fetch = deps.fetch
    if fetch is None:
        raise Internal("this worker was started without a fetch pipeline")

    config = deps.config()
    days = int(params.get("older_than_days") or config.get("archive.recheck_after_days"))
    limit = min(int(params.get("limit") or config.get("archive.recheck_batch")), MAX_BATCH)
    if days <= 0:
        return _empty("availability rechecks are disabled (archive.recheck_after_days is 0)")

    pause = float(config.get("archive.recheck_pause_seconds"))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = await archive.stale_availability(session, older_than=cutoff, limit=limit)
    if not rows:
        return _empty("nothing was due for a recheck")

    checked = 0
    gone = 0
    private = 0
    errors: list[str] = []

    stopped = ""
    for index, row in enumerate(rows):
        if index:
            await asyncio.sleep(pause)
        verdict, failure, halt = await _check(deps, session, row)
        checked += 1
        if failure:
            errors.append(failure)
            if halt:
                # An open circuit is an endpoint-level fact: every remaining
                # post would get the same answer without a request being made,
                # and the rows would be marked unchecked anyway. Measured on a
                # live sweep that spent two of five checks discovering this.
                stopped = failure
                break
            continue
        if verdict == "deleted":
            gone += 1
        elif verdict == "private":
            private += 1
        await archive.mark_availability(session, row.platform, row.content_id, availability=verdict)
        await session.commit()

    log.info(
        "archive.availability.swept",
        checked=checked,
        gone=gone,
        private=private,
        errors=len(errors),
        stopped=stopped,
    )
    return {
        "checked": checked,
        "gone": gone,
        "private": private,
        "errors": errors[:20],
        "detail": f"stopped early: {stopped}" if stopped else "",
    }


async def _check(
    deps: OperationDeps, session: AsyncSession, row: Any
) -> tuple[str | None, str | None, bool]:
    """One post. Returns (verdict, failure, halt); exactly one of the first two is set.

    ``halt`` says the whole pass should stop rather than continue: the only
    thing that earns it is an open circuit, because that is a fact about the
    endpoint rather than about this post.

    ``None`` as the verdict means "still there, and the archive write that just
    happened already refreshed the row" - so the caller stamps the check and
    changes nothing else. Writing `live` here as well would be a second opinion
    about a row the observation just settled.
    """
    fetch = deps.fetch
    assert fetch is not None
    config = deps.config()
    parsed: list[Any] = []
    try:
        call = registry.resolve(
            f"{row.platform}.content_detail", {"content_id": row.content_id}, config
        )
        capture = _capturer(call, parsed)

        await fetch.fetch(
            session,
            call.platform,
            call.endpoint,
            call.params,
            parse=capture,
            # Never cached: a cache hit would answer with what was stored when
            # the post was alive, which is the one answer this job must not get.
            cache_ttl=0,
            ctx=FetchContext(),
        )
    except ContentPrivate:
        return "private", None, False
    except DtkError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            # The finding, not a failure. This is what "gone" looks like.
            return "deleted", None, False
        # Risk control, a network fault, a changed payload: none of them say
        # anything about whether the post exists, so the row is left alone.
        halt = exc.code is ErrorCode.ENDPOINT_CIRCUIT_OPEN
        return None, f"{row.content_id}: {exc.code.value}", halt
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"{row.content_id}: {type(exc).__name__}", False

    if parsed:
        await archive.record(
            session, (parsed[-1],), store_raw=bool(config.get("archive.store_raw"))
        )
    return None, None, False


async def backfill(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Walk one author's history and archive every page.

    Stops at the first page that returns nothing new, at the page ceiling, or
    when the platform says there is no more - whichever comes first. The first
    of those matters most: an author whose whole history is already archived
    costs one request, not twenty.
    """
    fetch = deps.fetch
    if fetch is None:
        raise Internal("this worker was started without a fetch pipeline")

    platform = _platform(params)
    author_id = str(params.get("author_id") or "").strip()
    if not author_id:
        raise InvalidParam("author_id is required")
    pages = min(max(int(params.get("pages") or 5), 1), MAX_PAGES)

    config = deps.config()
    cursor: str | None = None
    archived = 0
    walked = 0
    stopped = "page limit"

    pause = float(config.get("archive.recheck_pause_seconds"))
    for page in range(pages):
        if page:
            await asyncio.sleep(pause)
        parsed: list[Any] = []
        request: dict[str, Any] = {"author_id": author_id, "count": 20}
        if cursor:
            request["cursor"] = cursor
        call = registry.resolve(f"{platform.value}.author_posts", request, config)
        # Bound as defaults rather than closed over: the loop rebinds both on
        # every page, and a closure over the loop variable would parse page
        # three's payload with page one's parser the moment this became async.
        capture = _capturer(call, parsed)

        try:
            await fetch.fetch(
                session,
                call.platform,
                call.endpoint,
                call.params,
                parse=capture,
                cache_ttl=0,
                ctx=FetchContext(),
            )
        except DtkError as exc:
            stopped = exc.code.value
            break

        walked += 1
        if not parsed:
            stopped = "no payload"
            break
        page_model = parsed[-1]
        written = await archive.record(
            session, (page_model,), store_raw=bool(config.get("archive.store_raw"))
        )
        await session.commit()
        archived += written

        cursor = getattr(page_model, "cursor", None)
        if not getattr(page_model, "has_more", False) or not cursor:
            stopped = "end of history"
            break

    log.info(
        "archive.backfill.done",
        platform=platform.value,
        pages=walked,
        archived=archived,
        stopped=stopped,
    )
    return {
        "platform": platform.value,
        "author_id": author_id,
        "pages": walked,
        "archived": archived,
        "stopped": stopped,
    }


def _platform(params: Mapping[str, Any]) -> Platform:
    raw = str(params.get("platform") or "")
    try:
        return Platform(raw)
    except ValueError as exc:
        raise InvalidParam("platform must be douyin or tiktok") from exc


def _empty(detail: str) -> dict[str, Any]:
    return {"checked": 0, "gone": 0, "private": 0, "errors": [], "detail": detail}


__all__ = ["backfill", "run"]
