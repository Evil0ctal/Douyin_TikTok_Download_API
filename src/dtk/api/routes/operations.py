"""The async-first submission path shared by every data endpoint.

Submitting and collecting are separate operations (decision D3). A route hands
work to the task queue and answers ``202`` with a ``task_id``; ``?wait=`` moves
the polling loop onto the server without changing that. Nothing here performs
an upstream request - the api container never talks to a platform (doc 01).

Task vocabulary
---------------

``Task.endpoint`` is one of:

* ``parse`` - a URL of unknown shape. ``params`` carries ``url`` and the worker
  expands short links, decides the platform and dispatches internally.
* ``<platform>.<operation>`` - matching the platform adapter's endpoint names
  (``douyin.content_detail``, ``tiktok.author_posts``, ...), so a worker can
  look the endpoint up with ``get_adapter(platform).endpoints[name]``.
* ``identity.mint`` / ``identity.test`` / ``proxy.test`` / ``diagnose`` /
  ``backup`` / ``backup.restore`` / ``notify.test`` - maintenance jobs the
  console triggers.

``Task.params`` uses the public query vocabulary (``url``, ``aweme_id``,
``sec_user_id``, ``comment_id``, ``cursor``, ``count``, ``include_raw``), not a
platform's own argument names. Translating those is the services layer's job;
doing it here would put platform knowledge into ``api/``, which doc 01 forbids.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Final

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.api import envelope
from dtk.api.deps import Principal
from dtk.api.routes.support import language, ok, request_id
from dtk.core.errors import ErrorCode, QueueFull, TaskNotFound
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Platform, RejectReason, TaskState
from dtk.services import cache, tasks

log = get_logger(__name__)

#: How long an in-flight claim survives without being cleared. Short enough
#: that a crashed worker cannot pin new callers to a dead task for long.
INFLIGHT_TTL_SECONDS = 90

#: Namespace prefix for the coalescing digest. Kept distinct from the fetch
#: layer's cache digest so the two can never collide on one Redis key.
INFLIGHT_NAMESPACE = "task"


class Operation(StrEnum):
    """Work a route can hand to the queue."""

    PARSE = "parse"
    CONTENT_DETAIL = "content_detail"
    AUTHOR_PROFILE = "author_profile"
    AUTHOR_POSTS = "author_posts"
    COMMENTS = "comments"
    COMMENT_REPLIES = "comment_replies"
    AUTHOR_LIKES = "author_likes"
    MIX_POSTS = "mix_posts"
    AUTHOR_FOLLOWERS = "author_followers"
    AUTHOR_FOLLOWING = "author_following"


class Maintenance(StrEnum):
    """Console-triggered jobs that are not platform reads."""

    IDENTITY_MINT = "identity.mint"
    IDENTITY_TEST = "identity.test"
    PROXY_TEST = "proxy.test"
    DIAGNOSE = "diagnose"
    BACKUP = "backup"
    #: Kept off the request thread for the same reason as BACKUP, and for one
    #: more: a restore writes rows for as long as the archive is large, and a
    #: browser that gives up mid-way must not be able to abandon it half done.
    BACKUP_RESTORE = "backup.restore"
    NOTIFY_TEST = "notify.test"
    #: Store one archived post's media on the operator's disk. Listed here
    #: because it is dispatched by the same runner, but deliberately NOT exempt
    #: from the queue ceiling below.
    MEDIA_DOWNLOAD = "media.download"


def endpoint_name(platform: Platform, operation: Operation) -> str:
    """``douyin`` + ``author_posts`` -> ``douyin.author_posts``.

    Derived rather than looked up, so adding a platform means adding a
    ``platforms/<name>/`` package and nothing here.
    """
    return f"{platform.value}.{operation.value}"


def _digest(endpoint: str, params: dict[str, Any]) -> str:
    return cache.cache_key(f"{INFLIGHT_NAMESPACE}:{endpoint}", params)


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop absent values so two spellings of one request share a digest."""
    return {k: v for k, v in params.items() if v is not None}


async def _attach(session: AsyncSession, digest: str) -> tuple[uuid.UUID, TaskState] | None:
    """Find the in-flight task for this digest, if it is worth joining.

    A hundred callers asking for the same video should cost the pool one
    upstream request, not a hundred (doc 06). A claim left behind by a task
    that already failed is dropped instead of joined: replaying a failure for
    the rest of the TTL would hide a retry that might well succeed.
    """
    existing = await get_redis().get(cache.INFLIGHT_KEY.format(digest=digest))
    if not existing:
        return None
    try:
        task_id = uuid.UUID(existing)
    except (ValueError, AttributeError):
        return None
    try:
        view = await tasks.get(session, task_id)
    except TaskNotFound:
        # The row is gone - expired result or a purged task. Not joinable.
        await cache.release_inflight(digest, existing)
        return None
    if view.state is TaskState.FAILED:
        await cache.release_inflight(digest, existing)
        return None
    return task_id, view.state


async def submit(
    request: Request,
    principal: Principal,
    *,
    endpoint: str,
    params: dict[str, Any],
    coalesce: bool = True,
) -> tuple[uuid.UUID, TaskState]:
    """Queue one job, joining an identical in-flight one when there is one."""
    session = request.state.db
    cleaned = _clean(params)
    digest = _digest(endpoint, cleaned)

    if coalesce:
        joined = await _attach(session, digest)
        if joined is not None:
            log.debug("task.coalesced", endpoint=endpoint, task_id=str(joined[0]))
            return joined

    # Checked after the coalescing attempt: joining work that is already queued
    # adds nothing to the backlog, and refusing it would turn a full queue into
    # a failure for callers who were about to get an answer for free.
    await _refuse_when_the_queue_is_full(request, endpoint)

    task_id = await tasks.submit(session, endpoint, cleaned, api_key_id=principal.api_key_id)
    # Commit before the worker can pop the id off the queue: the row has to be
    # visible to another process by the time it looks the task up.
    await session.commit()
    if coalesce:
        await cache.claim_inflight(digest, str(task_id), ttl=INFLIGHT_TTL_SECONDS)
    return task_id, TaskState.QUEUED


#: Jobs an operator triggers by hand. Exempt from the queue ceiling: they are
#: rare, they are how someone investigates a backlog, and refusing to run the
#: self check because the queue is deep would withhold the tool at the moment it
#: is wanted.
#:
#: Media downloads are the exception, and the exception is the point of the
#: rule: they are dispatched by the same runner but they are not rare, a caller
#: can start as many as they have archived posts, and each one holds a worker
#: slot for as long as a video takes. Exempting them would let one operator's
#: bulk download starve every read on the instance.
_OPERATOR_TRIGGERED: Final[frozenset[str]] = frozenset(
    member.value for member in Maintenance if member is not Maintenance.MEDIA_DOWNLOAD
)


async def _refuse_when_the_queue_is_full(request: Request, endpoint: str) -> None:
    """Shed load rather than accept work nobody will get to.

    ``sched.queue_max`` was a setting the console exposed and nothing read, and
    RejectReason.QUEUE_FULL was defined and never raised - so the queue grew
    without bound and every caller waited instead of being told to come back.
    Doc 03 is explicit that rejecting early beats queueing: a caller left
    hanging is worse off than one that gets a Retry-After immediately.
    """
    if endpoint in _OPERATOR_TRIGGERED:
        return
    ceiling = int(request.app.state.config.get("sched.queue_max"))
    if ceiling <= 0:
        return
    depth = int(await get_redis().llen(tasks.QUEUE_KEY))
    if depth < ceiling:
        return
    retry_after = max(1, int(request.app.state.config.get("sched.max_wait_seconds")))
    log.warning("task.queue_full", endpoint=endpoint, depth=depth, ceiling=ceiling)
    raise QueueFull(
        f"{depth} tasks are already queued, at the configured ceiling of {ceiling}",
        retry_after=retry_after,
        details={"reject_reason": RejectReason.QUEUE_FULL.value, "queued": depth},
    )


def accepted(request: Request, task_id: uuid.UUID, state: TaskState) -> JSONResponse:
    """The ``202`` every submission answers with when it does not wait."""
    return ok(
        request,
        {"task_id": str(task_id), "state": state.value},
        status_code=202,
    )


def unwrap(result: dict[str, Any] | None) -> tuple[Any, dict[str, Any]]:
    """Split a worker result into payload and metadata.

    Workers may store either the bare payload or ``{"data": ..., "meta": ...}``.
    Accepting both keeps the wire contract stable while the worker evolves.
    """
    if isinstance(result, dict) and "data" in result:
        meta = result.get("meta")
        return result["data"], dict(meta) if isinstance(meta, dict) else {}
    return result, {}


def finished(request: Request, view: tasks.TaskView) -> JSONResponse:
    """Render a completed task as a normal success or failure envelope."""
    if view.state is TaskState.FAILED:
        error = view.error or {}
        raw_code = error.get("code")
        try:
            code = ErrorCode(str(raw_code))
        except ValueError:
            # An unrecognized code from a worker is still a failure; it is just
            # not one this contract names.
            code = ErrorCode.INTERNAL
        details = error.get("details")
        return envelope.failure(
            code,
            request_id(request),
            language=language(request),
            retry_after=error.get("retry_after"),
            details=dict(details) if isinstance(details, dict) else None,
        )

    data, meta = unwrap(view.result)
    cursor = meta.pop("cursor", None)
    return ok(
        request,
        data,
        cached=bool(meta.pop("cached", False)),
        duration_ms=meta.pop("duration_ms", None),
        cursor=cursor if isinstance(cursor, dict) else None,
        extra={"task_id": str(view.id), **meta},
    )


async def submit_and_wait(
    request: Request,
    principal: Principal,
    *,
    endpoint: str,
    params: dict[str, Any],
    wait: float,
    proxy: str | None = None,
    coalesce: bool = True,
) -> JSONResponse:
    """Submit, then optionally hold the connection until the task settles.

    Long polling is a convenience for clients that cannot poll - an iOS
    Shortcut, a curl one-liner - and changes nothing internally: the work still
    runs through the queue (doc 06).

    ``proxy`` is the caller's own egress, already vetted by
    :func:`dtk.api.routes.support.resolve_request_proxy`. It joins the stored
    parameters, which is also what keeps it out of the coalescing digest's blind
    spot: two callers asking for the same post through different proxies are not
    asking the same question, and must not be joined onto one task.
    """
    task_id, state = await submit(
        request,
        principal,
        endpoint=endpoint,
        params={**params, "proxy": proxy} if proxy else params,
        coalesce=coalesce,
    )
    if wait <= 0:
        return accepted(request, task_id, state)

    view = await tasks.wait_for(request.state.db, task_id, wait)
    if view is None:
        return accepted(request, task_id, TaskState.RUNNING)
    return finished(request, view)


__all__ = [
    "INFLIGHT_NAMESPACE",
    "INFLIGHT_TTL_SECONDS",
    "Maintenance",
    "Operation",
    "accepted",
    "endpoint_name",
    "finished",
    "submit",
    "submit_and_wait",
    "unwrap",
]
