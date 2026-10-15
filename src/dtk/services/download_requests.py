"""Shared orchestration for starting durable media downloads."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import Internal, InvalidParam, NotConfigured, QueueFull
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import DownloadProfile, Platform, RejectReason, TaskState
from dtk.ops import capacity
from dtk.services import archive, downloads, tasks
from dtk.urls import ResourceKind, first_url, identify, require_content_id
from dtk.worker import serialize_error

log = get_logger(__name__)

ENDPOINT: Final = "media.download"
CAPACITY_RETRY_AFTER: Final = 300


@dataclass(frozen=True, slots=True)
class DownloadStart:
    """One newly accepted or reused download request."""

    download_id: uuid.UUID
    task_id: uuid.UUID | None
    state: str
    directory: str
    profile: str
    planned: tuple[dict[str, str], ...] = ()
    skipped: tuple[str, ...] = ()
    archived: bool = True
    reused: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "download_id": str(self.download_id),
            "task_id": str(self.task_id) if self.task_id else None,
            "state": self.state,
            "directory": self.directory,
            "profile": self.profile,
            "planned": list(self.planned),
            "skipped": list(self.skipped),
            "archived": self.archived,
            "reused": self.reused,
        }


def resolve_target(
    *, url: str | None, platform: Platform | None, content_id: str | None
) -> tuple[Platform, str]:
    """Resolve the REST and MCP input forms to one platform post key."""
    if url:
        kind = identify(url)
        if not kind.allowed:
            candidate = first_url(url)
            if candidate is not None:
                kind = identify(candidate)
        if not kind.allowed or kind.platform is None:
            raise InvalidParam(
                "that link is not a supported Douyin or TikTok post",
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
    return platform, require_content_id(content_id, platform=platform)


async def start(
    session: AsyncSession,
    *,
    config: Any,
    downloader_configured: bool,
    url: str | None,
    platform: Platform | None,
    content_id: str | None,
    profile: DownloadProfile | str = DownloadProfile.STANDARD,
    skip_existing: bool = False,
    requested_by: uuid.UUID | None = None,
    is_demo: bool = False,
) -> DownloadStart:
    """Validate, create or reuse, commit, then publish one download task."""
    profile = DownloadProfile(profile)
    if not downloader_configured:
        raise NotConfigured(
            "this instance has no media downloader; start the downloader compose "
            "profile and set DTK_DOWNLOADER_URL"
        )
    if not bool(config.get("media.enabled")):
        raise NotConfigured("media downloads are disabled in the settings")

    target_platform, target_id = resolve_target(url=url, platform=platform, content_id=content_id)
    verdict = capacity.evaluate(
        warn_percent=float(config.get("capacity.warn_percent")),
        hard_stop_percent=float(config.get("capacity.hard_stop_percent")),
    )
    if verdict.paused:
        raise QueueFull(
            f"new downloads are paused: {verdict.detail}",
            retry_after=CAPACITY_RETRY_AFTER,
        )

    live, stored = await downloads.existing_for(session, target_platform.value, target_id, profile)
    if live is not None:
        if live.profile != profile.value:
            raise QueueFull(
                "another download profile for this post is in flight; try again when it settles",
                retry_after=1,
            )
        return _existing(live, reason="in_flight")
    if skip_existing and stored is not None:
        return _existing(stored, reason="already_stored")

    row = await archive.get(session, target_platform.value, target_id)
    planned: tuple[dict[str, str], ...] = ()
    skipped: tuple[str, ...] = ()
    try:
        if row is None:
            download = await downloads.create_pending(
                session,
                platform=target_platform.value,
                content_id=target_id,
                requested_by=requested_by,
                profile=profile,
            )
        else:
            plan = downloads.plan_for(
                row,
                max_file_bytes=int(config.get("media.max_file_bytes")),
                profile=profile,
            )
            if plan.empty:
                raise InvalidParam(
                    "; ".join(plan.skipped) or "this post has no downloadable media",
                    details={"skipped": list(plan.skipped)},
                )
            planned = tuple({"name": item.name, "kind": item.kind} for item in plan.items)
            skipped = tuple(plan.skipped)
            download = await downloads.create(
                session, row, requested_by=requested_by, profile=profile
            )
    except IntegrityError:
        return await _joined(session, target_platform.value, target_id, profile)

    await _refuse_when_queue_full(config)
    task_id = await tasks.create(
        session,
        ENDPOINT,
        {"download_id": str(download.id)},
        api_key_id=requested_by,
        is_demo=is_demo,
    )
    download.task_id = task_id
    try:
        await session.commit()
    except IntegrityError:
        return await _joined(session, target_platform.value, target_id, profile)

    try:
        await tasks.enqueue(task_id, endpoint=ENDPOINT)
    except Exception as exc:
        log.warning("task.enqueue_failed", task_id=str(task_id), error=type(exc).__name__)
        await tasks.finish(
            session,
            task_id,
            error=serialize_error(Internal("the task could not be queued for a worker")),
        )
        await session.commit()
        raise

    log.info(
        "media.download.requested",
        download_id=str(download.id),
        platform=target_platform.value,
        items=len(planned),
        archived=row is not None,
    )
    return DownloadStart(
        download_id=download.id,
        task_id=task_id,
        state=TaskState.QUEUED.value,
        directory=download.directory,
        profile=download.profile,
        planned=planned,
        skipped=skipped,
        archived=row is not None,
    )


async def _joined(
    session: AsyncSession, platform: str, content_id: str, profile: DownloadProfile
) -> DownloadStart:
    await session.rollback()
    live, _ = await downloads.existing_for(session, platform, content_id, profile)
    if live is None:
        raise QueueFull("another download of this post was in flight; try again", retry_after=1)
    if live.profile != profile.value:
        raise QueueFull(
            "another download profile for this post is in flight; try again when it settles",
            retry_after=1,
        )
    return _existing(live, reason="in_flight")


def _existing(download: Any, *, reason: str) -> DownloadStart:
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
        profile=download.profile,
        reused=reason,
    )


async def _refuse_when_queue_full(config: Any) -> None:
    ceiling = int(config.get("sched.queue_max"))
    if ceiling <= 0:
        return
    depth = int(await get_redis().llen(tasks.QUEUE_KEY))
    if depth < ceiling:
        return
    retry_after = max(1, int(config.get("sched.max_wait_seconds")))
    raise QueueFull(
        f"{depth} tasks are already queued, at the configured ceiling of {ceiling}",
        retry_after=retry_after,
        details={"reject_reason": RejectReason.QUEUE_FULL.value, "queued": depth},
    )


__all__ = ["CAPACITY_RETRY_AFTER", "DownloadStart", "resolve_target", "start"]
