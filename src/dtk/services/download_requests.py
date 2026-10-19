"""Starting a media download: one path for REST and MCP.

This used to live in ``api/routes/downloads.py``. It moved here when MCP gained
``download_media``, because a second copy of these checks in the MCP layer is
exactly how the two entry points would have come to disagree about who may
download what, and when (CLAUDE.md: one logic, three entry points).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import InvalidParam, NotConfigured, QueueFull
from dtk.core.logging import get_logger
from dtk.core.types import Platform, TaskState
from dtk.ops import capacity
from dtk.services import archive, downloads, tasks
from dtk.urls import ResourceKind, first_url, identify, require_content_id

log = get_logger(__name__)

ENDPOINT: Final = "media.download"
#: How long a caller should wait when the disk guard has stopped new jobs. Long
#: enough that retrying is not a spin, short enough that freeing space is
#: noticed within a maintenance tick.
CAPACITY_RETRY_AFTER: Final = 300


@dataclass(frozen=True, slots=True)
class DownloadStart:
    """One newly accepted or reused download request."""

    download_id: uuid.UUID
    task_id: uuid.UUID | None
    state: str
    directory: str
    planned: tuple[dict[str, str], ...] = ()
    skipped: tuple[str, ...] = ()
    #: False means the worker has to fetch the post before it can download
    #: anything, which is a slower first response.
    archived: bool = True
    reused: str | None = None
    #: Which part of a video uploaded in several; None is the first.
    part: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "download_id": str(self.download_id),
            "part": self.part,
            "task_id": str(self.task_id) if self.task_id else None,
            "state": self.state,
            "directory": self.directory,
            "planned": list(self.planned),
            "skipped": list(self.skipped),
            "archived": self.archived,
            "reused": self.reused,
        }


def ensure_configured(*, downloader_configured: bool, config: Any) -> None:
    """Refuse early when no downloader is running.

    A 501 rather than a 500: the sidecar is an opt-in compose profile, and an
    instance without it is correctly configured for someone who never wanted
    it. The message says how to turn it on rather than implying a fault.
    """
    if not downloader_configured:
        raise NotConfigured(
            "this instance has no media downloader; start the downloader compose "
            "profile and set DTK_DOWNLOADER_URL"
        )
    if not bool(config.get("media.enabled")):
        raise NotConfigured("media downloads are disabled in the settings")


def resolve_target(
    *, url: str | None, platform: Platform | None, content_id: str | None
) -> tuple[Platform, str]:
    """Work out which post is meant, from a link or a key.

    `identify` is the allowlist: an unrecognised host yields nothing and is
    refused here, so no URL a caller sends ever becomes a request.
    """
    if url:
        kind = identify(url)
        if not kind.allowed:
            # Both apps put a caption and a numeric code on the clipboard
            # beside the link, and that whole string is what gets pasted.
            candidate = first_url(url)
            if candidate is not None:
                kind = identify(candidate)
        if not kind.allowed or kind.platform is None:
            raise InvalidParam(
                "that link is not a supported Douyin, TikTok or Bilibili post",
                details={"field": "url"},
            )
        if kind.needs_expansion:
            raise InvalidParam(
                "a short link has to be expanded before it names a post; send it "
                "to parse_url over MCP or /api/v1/parse over REST first",
                details={"field": "url", "needs_expansion": True},
            )
        if kind.resource is not ResourceKind.VIDEO or not kind.resource_id:
            raise InvalidParam(
                "that link names a profile rather than a post",
                details={"field": "url", "resource": kind.resource.value},
            )
        if platform is not None and platform is not kind.platform:
            # Guessing which one was meant is how you download the wrong post
            # from the wrong platform and call it a feature.
            raise InvalidParam(
                "the link and the platform disagree",
                details={"url_platform": kind.platform.value, "platform": platform.value},
            )
        if content_id and content_id != kind.resource_id:
            raise InvalidParam(
                "the link and the content_id name different posts",
                details={"url_content_id": kind.resource_id, "content_id": content_id},
            )
        return kind.platform, kind.resource_id

    if platform is None or not content_id:
        raise InvalidParam(
            "provide either url, or platform and content_id",
            details={"fields": ["url", "platform", "content_id"]},
        )
    # Only a typed id is checked: one extracted from a link came out of the
    # pattern that recognised the link.
    return platform, require_content_id(content_id, platform=platform)


#: The platforms whose videos come in parts.
PART_PLATFORMS: Final[frozenset[Platform]] = frozenset({Platform.BILIBILI})


def resolve_part(platform: Platform, part: int | None, url: str | None) -> int | None:
    """Which part is meant: the one asked for, or the one the link plays.

    A Bilibili link to a later part carries it as ``?p=3``, and the URL layer
    keeps that parameter for exactly this; pasting the link a player is on
    should download what that player plays. The first part is None, whichever
    way it was spelt, because that is what every download made before parts
    existed is - and the in-flight index treats the two as one.
    """
    from_link = _part_in(url) if url and platform in PART_PLATFORMS else None
    if part is not None and from_link is not None and part != from_link:
        raise InvalidParam(
            "the link and part name different parts",
            details={"field": "part", "part": part, "url_part": from_link},
        )
    wanted = part if part is not None else from_link
    if wanted is None:
        return None
    if platform not in PART_PLATFORMS:
        raise InvalidParam(
            f"{platform.value} videos do not come in parts",
            details={"field": "part", "platform": platform.value},
        )
    if wanted < 1:
        raise InvalidParam("part counts from 1", details={"field": "part", "value": wanted})
    return wanted if wanted > 1 else None


def _part_in(url: str) -> int | None:
    candidate = first_url(url) or url
    values = parse_qs(urlsplit(candidate).query).get("p") or []
    text = values[0].strip() if values else ""
    return int(text) if text.isdigit() else None


async def start(
    session: AsyncSession,
    *,
    config: Any,
    downloader_configured: bool,
    url: str | None,
    platform: Platform | None,
    content_id: str | None,
    skip_existing: bool = False,
    requested_by: uuid.UUID | None = None,
    is_demo: bool = False,
    part: int | None = None,
) -> DownloadStart:
    """Validate, create or reuse, commit, then publish one download task."""
    ensure_configured(downloader_configured=downloader_configured, config=config)

    target_platform, target_id = resolve_target(url=url, platform=platform, content_id=content_id)
    part = resolve_part(target_platform, part, url)
    verdict = capacity.evaluate(
        warn_percent=float(config.get("capacity.warn_percent")),
        hard_stop_percent=float(config.get("capacity.hard_stop_percent")),
    )
    if verdict.paused:
        raise QueueFull(
            f"new downloads are paused: {verdict.detail}",
            retry_after=CAPACITY_RETRY_AFTER,
        )

    # Two reasons not to start a second one, and only one of them is optional.
    live, stored = await downloads.existing_for(session, target_platform.value, target_id, part)
    if live is not None:
        # Never optional. Two downloads of a post write to one directory - it is
        # keyed by platform, author and post - and the sidecar renames
        # `name.part` to `name` on the way out, so the loser renames a file the
        # winner has already moved and comes back `partial`. Measured here:
        # three requests for one post finished within 10ms and one lost a cover
        # that way. Joining is also what the caller wanted; they are waiting for
        # this post's media, and it is already on its way.
        return _existing(live, reason="in_flight")
    if skip_existing and stored is not None:
        return _existing(stored, reason="already_stored")

    row = await archive.get(session, target_platform.value, target_id)
    planned: tuple[dict[str, str], ...] = ()
    skipped: tuple[str, ...] = ()
    try:
        if row is None:
            # Never seen. The worker fetches it before downloading, so there is
            # nothing to plan from yet and nothing to refuse on.
            download = await downloads.create_pending(
                session,
                platform=target_platform.value,
                content_id=target_id,
                part=part,
                requested_by=requested_by,
            )
        elif part is not None:
            # The archive holds the first part's file, so it says nothing about
            # this one; the worker asks for this part's and plans from that.
            download = await downloads.create(session, row, part=part, requested_by=requested_by)
        else:
            # Planned here as well as in the worker, so a post that IS archived
            # and has nothing fetchable is a 400 the caller reads now rather
            # than a task that fails in a minute.
            plan = downloads.plan_for(row, max_file_bytes=int(config.get("media.max_file_bytes")))
            if plan.empty:
                raise InvalidParam(
                    "; ".join(plan.skipped) or "this post has no downloadable media",
                    details={"skipped": list(plan.skipped)},
                )
            planned = tuple({"name": item.name, "kind": item.kind} for item in plan.items)
            skipped = tuple(plan.skipped)
            download = await downloads.create(session, row, requested_by=requested_by)
    except IntegrityError:
        return await _joined(session, target_platform.value, target_id, part)

    # Never coalesced: two requests for the same post are two deliberate acts,
    # and the second one is usually "the first did not work" - joining them onto
    # one task would answer it with the failure it was retrying.
    await tasks.refuse_when_queue_full(config, endpoint=ENDPOINT)
    task_id = await tasks.create(
        session,
        ENDPOINT,
        {"download_id": str(download.id)},
        api_key_id=requested_by,
        is_demo=is_demo,
    )
    # One commit for both rows, and only then the publish. Otherwise a worker
    # can start before the download carries its task id, and a request that dies
    # in the gap leaves a download nothing points at.
    download.task_id = task_id
    try:
        await session.commit()
    except IntegrityError:
        return await _joined(session, target_platform.value, target_id, part)

    try:
        await tasks.enqueue(task_id, endpoint=ENDPOINT)
    except Exception as exc:
        # Redis went away between the commit and the publish. Settle both rows:
        # a failed task beside a download still reading `queued` held the
        # one-live-download index for two hours, until the stale sweep, and
        # answered every retry of this post with "still running".
        log.warning("task.enqueue_failed", task_id=str(task_id), error=type(exc).__name__)
        reason = "the task could not be queued for a worker"
        # The row first, for the reason transcription_requests gives: it needs
        # no Redis, and the task's finish does.
        await downloads.fail(session, download.id, reason)
        await tasks.settle_unpublished(session, task_id, reason)
        raise

    log.info(
        "media.download.requested",
        download_id=str(download.id),
        platform=target_platform.value,
        items=len(planned),
        archived=row is not None,
        part=part,
    )
    return DownloadStart(
        download_id=download.id,
        task_id=task_id,
        state=TaskState.QUEUED.value,
        directory=download.directory,
        planned=planned,
        skipped=skipped,
        archived=row is not None,
        part=part,
    )


async def _joined(
    session: AsyncSession, platform: str, content_id: str, part: int | None = None
) -> DownloadStart:
    """Answer a request the in-flight index refused.

    `ux_media_downloads_in_flight` fires when another request for this post got
    there first, in the window between our check and our insert. A check cannot
    close that window - simultaneous requests all read "none in flight" before
    any of them writes - so the index is the guarantee and this is how it reads
    to a caller: the same answer the check would have given.

    The insert raises at the flush inside `create`, not at the commit, so this
    is called from three places rather than wrapped once around the end.
    """
    await session.rollback()
    live, _ = await downloads.existing_for(session, platform, content_id, part)
    if live is None:
        # The other request settled and cleared the way between the refusal and
        # this lookup. Nothing to join, and pretending otherwise would hand back
        # a download that is already over.
        raise QueueFull("another download of this post was in flight; try again", retry_after=1)
    return _existing(live, reason="in_flight")


def _existing(download: Any, *, reason: str) -> DownloadStart:
    """Answer with a download that already exists, saying which one and why."""
    log.info(
        "media.download.reused",
        download_id=str(download.id),
        platform=download.platform,
        reason=reason,
    )
    return DownloadStart(
        download_id=download.id,
        task_id=download.task_id,
        state=download.state,
        directory=download.directory,
        reused=reason,
        part=download.part,
    )


__all__ = [
    "CAPACITY_RETRY_AFTER",
    "DownloadStart",
    "ensure_configured",
    "resolve_part",
    "resolve_target",
    "start",
]
