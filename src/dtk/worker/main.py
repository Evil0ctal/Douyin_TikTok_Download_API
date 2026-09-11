"""The task worker: claim, run, finish, and never lose a task on the way.

The loop itself is small. What matters is what happens around it:

* **Nothing vanishes.** A task is either finished with a result, finished with a
  serialized error, or put back on the queue. An unexpected exception is an
  explicit failure, not a task that stays ``running`` forever, and a task
  claimed while shutting down goes back rather than being dropped.
* **A crash is survivable.** The process can be killed between claiming and
  finishing; the maintenance sweep re-queues tasks left ``running`` past the
  stale threshold (see :mod:`dtk.worker.maintenance`). Because that re-queue can
  in principle repeat, each task carries an attempt counter and is failed
  explicitly once it is exhausted - an infinitely retried task is a slower way
  of losing it.
* **SIGTERM drains.** Claiming stops immediately, in-flight work is given time
  to finish, and only then does the process exit. Killing a request mid-flight
  wastes an identity's quota and leaves an inflight lock to expire on its TTL.

The worker performs no platform-specific work of its own: the endpoint name on
the task resolves through :mod:`dtk.worker.registry` to an adapter call, a parse
function and a cache TTL, and :class:`dtk.services.fetch.FetchService` runs it.

See docs/design/01-architecture.md.
"""

# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from dtk import __version__
from dtk.core.config import BootstrapSettings, Config, extra_url_hosts
from dtk.core.db import session_scope
from dtk.core.errors import DtkError, ErrorCode, Internal, InvalidParam
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Platform, TaskState
from dtk.db.models import Task as TaskRow
from dtk.models import Author, Content, Page
from dtk.ops import webhooks
from dtk.services import archive, snapshots, tasks
from dtk.services.fetch import Explanation, FetchContext, FetchResult, FetchService
from dtk.worker import parsing, registry
from dtk.worker.ops import OperationRunner

log = get_logger(__name__)

#: Redis counter for how often one task has been picked up. Bounded so a task
#: that kills its worker cannot be re-queued forever.
ATTEMPTS_KEY = "worker:task:attempts:{task_id}"

SessionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class WorkerOptions:
    """Knobs that belong to the process rather than to the runtime config."""

    #: How many tasks one worker runs at once. Real concurrency is bounded by
    #: the identity pool, not by this: extra slots just wait on the scheduler.
    concurrency: int = 4
    #: BLPOP timeout. Also the worst-case delay between SIGTERM and the loop
    #: noticing it.
    claim_timeout_seconds: int = 5
    #: Pause after a failed claim, so an unreachable Redis is not hammered.
    claim_backoff_seconds: float = 1.0
    #: Attempts allowed before a task is failed instead of re-queued.
    max_attempts: int = 3
    attempt_ttl_seconds: int = 86400
    #: How long in-flight work gets to finish after SIGTERM.
    drain_timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class TaskRun:
    """The part of a queued task the worker actually needs."""

    id: uuid.UUID
    endpoint: str
    params: dict[str, Any]
    api_key_id: uuid.UUID | None = None
    attempt: int = 1
    #: Started by the public demo account. Carried on the row rather than
    #: recomputed, because by the time the worker runs there is no request and
    #: no principal left to ask. It suppresses two writes - the archive row and
    #: the request-log row - and nothing else: the fetch itself, the identity
    #: it spends and the health it folds back are all unchanged, because a demo
    #: that behaved differently would not be demonstrating this instance.
    is_demo: bool = False


class TaskStore(Protocol):
    """Queue and task-record operations, behind one seam.

    A Protocol rather than direct calls into :mod:`dtk.services.tasks`, so the
    loop's guarantees - drain rather than drop, fail rather than vanish - can be
    tested without Redis or PostgreSQL.
    """

    async def claim(self, timeout: int) -> uuid.UUID | None: ...

    async def requeue(self, task_id: uuid.UUID) -> None: ...

    async def attempt(self, task_id: uuid.UUID) -> int: ...

    async def start(self, task_id: uuid.UUID) -> TaskRun | None: ...

    async def complete(self, task_id: uuid.UUID, result: dict[str, Any]) -> None: ...

    async def fail(
        self, task_id: uuid.UUID, error: dict[str, Any], meta: dict[str, Any] | None = None
    ) -> None: ...


def explain_meta(explanation: Explanation) -> dict[str, Any]:
    """The request this instance made, in the shape the API hands back.

    One function because a failed task carries the same block a successful one
    does, and the case a reader actually has - "it was refused, show me what we
    sent" - is the failed one.
    """
    return {
        "method": explanation.method,
        "url": explanation.url,
        "headers": dict(explanation.headers),
        "cookie_header": explanation.cookie_header,
        "identity_id": explanation.identity_id,
        "signer": explanation.signer,
        "endpoint": explanation.endpoint,
        "proxy": explanation.proxy,
    }


def serialize_error(exc: BaseException) -> dict[str, Any]:
    """Render an exception into the stored ``tasks.error`` payload.

    The shape matches :mod:`dtk.api.envelope`: a stable ``code``, an English
    message for logs and the CLI, and the arguments that message was built
    from. ``retry_after`` stays beside ``details`` rather than inside it for
    exactly that reason - ``envelope.failure`` folds the two together the same
    way, and ``dtk.api.routes.tasks`` re-renders the sentence from them in
    whatever language the caller asked for, because the worker has no caller to
    ask. ``retryable`` is spelled out because an agent reading the task result
    should not have to carry the non-retryable table around.
    """
    if isinstance(exc, DtkError):
        payload: dict[str, Any] = {
            "code": exc.code.value,
            "message": str(exc),
            "retryable": exc.retryable,
        }
        if exc.retry_after is not None:
            payload["retry_after"] = exc.retry_after
        if exc.details:
            payload["details"] = dict(exc.details)
        return payload
    # Never leak a traceback or an internal type name into a stored result.
    return {
        "code": ErrorCode.INTERNAL.value,
        "message": "internal error while running the task",
        "retryable": True,
        "details": {"error": type(exc).__name__},
    }


class DatabaseTaskStore:
    """The production :class:`TaskStore`: Redis queue plus the tasks table."""

    __slots__ = ("_attempt_ttl", "_session_factory")

    def __init__(
        self,
        *,
        session_factory: SessionFactory = session_scope,
        attempt_ttl_seconds: int = 86400,
    ) -> None:
        self._session_factory = session_factory
        self._attempt_ttl = attempt_ttl_seconds

    async def claim(self, timeout: int) -> uuid.UUID | None:
        return await tasks.claim(timeout=timeout)

    async def requeue(self, task_id: uuid.UUID) -> None:
        """Put a task back at the head of the queue and un-mark it running.

        The head, not the tail: it was already waiting once, and sending it to
        the back would let a shutdown reorder the whole backlog. A task that
        finished after all - cancelled in the instant between the write and the
        return - is left alone rather than queued a second time.
        """
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task_id)
            if row is not None and row.state in (TaskState.DONE.value, TaskState.FAILED.value):
                return
            if row is not None and row.state == TaskState.RUNNING.value:
                row.state = TaskState.QUEUED.value
                row.started_at = None
        await get_redis().lpush(tasks.QUEUE_KEY, str(task_id))
        log.info("worker.task.requeued", task_id=str(task_id))

    async def attempt(self, task_id: uuid.UUID) -> int:
        redis = get_redis()
        key = ATTEMPTS_KEY.format(task_id=task_id)
        count = int(await redis.incr(key))
        await redis.expire(key, self._attempt_ttl)
        return count

    async def start(self, task_id: uuid.UUID) -> TaskRun | None:
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task_id)
            if row is None:
                # Two very different situations used to share one log line, and
                # that conflation is why the commit race went unnoticed for so
                # long: a cancelled task legitimately leaves its id on the queue
                # and lands in the branch below, so the warning was routine.
                # This branch is not routine. It means the queue held an id with
                # no row behind it - a rolled-back submission, or a retention
                # pass that reached a row it should not have.
                log.error("worker.task.unavailable", task_id=str(task_id), reason="missing")
                return None
            if row.state in (TaskState.DONE.value, TaskState.FAILED.value):
                # Already finished, most likely by a worker that was re-queued
                # after a slow finish, or cancelled while queued. Running it
                # again would burn quota twice.
                log.info("worker.task.unavailable", task_id=str(task_id), reason=row.state)
                return None
            await tasks.mark_running(session, task_id)
            return TaskRun(
                id=task_id,
                endpoint=row.endpoint,
                params=dict(row.params or {}),
                api_key_id=row.api_key_id,
                is_demo=bool(row.is_demo),
            )

    async def complete(self, task_id: uuid.UUID, result: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            await tasks.finish(session, task_id, result=result)
        await self._clear_attempts(task_id)

    async def fail(
        self, task_id: uuid.UUID, error: dict[str, Any], meta: dict[str, Any] | None = None
    ) -> None:
        # A failed task has no data and may still have metadata worth keeping.
        # `finish` decides the state from `error` alone, so storing a result
        # beside it leaves the task FAILED - which is what makes the same
        # scope-gated `explain` block available on both outcomes.
        async with self._session_factory() as session:
            # `{"data": ..., "meta": ...}` even though the data is nothing:
            # that is the shape `dtk.api.routes.operations.unwrap` reads, and a
            # failed task genuinely has no payload.
            await tasks.finish(
                session,
                task_id,
                result={"data": None, "meta": meta} if meta else None,
                error=error,
            )
        await self._clear_attempts(task_id)

    async def _clear_attempts(self, task_id: uuid.UUID) -> None:
        with contextlib.suppress(Exception):
            await get_redis().delete(ATTEMPTS_KEY.format(task_id=task_id))


class TaskWorker:
    """Claims tasks and runs them through the fetch pipeline."""

    def __init__(
        self,
        *,
        fetch: FetchService,
        store: TaskStore,
        config: Callable[[], Config] | Config,
        options: WorkerOptions | None = None,
        session_factory: SessionFactory = session_scope,
        egress: parsing.ProxySource | None = None,
        operations: OperationRunner | None = None,
    ) -> None:
        self._fetch = fetch
        self._store = store
        # Console-triggered maintenance. Absent in a worker built for platform
        # reads alone, and in most tests, which is why it is optional rather
        # than a required collaborator.
        self._operations = operations
        self._config: Callable[[], Config] = config if callable(config) else (lambda: config)
        self._options = options or WorkerOptions()
        self._session_factory = session_factory
        # Where the "parse" endpoint's short-link hop leaves from. Only that hop
        # uses it, and a worker built without one refuses to expand rather than
        # falling back to this host's own address - see dtk.worker.parsing.
        self._egress: parsing.ProxySource = egress or parsing.no_egress
        self._stop = asyncio.Event()
        self._gate = asyncio.Semaphore(self._options.concurrency)
        self._inflight: set[asyncio.Task[None]] = set()

    # -- lifecycle ---------------------------------------------------------

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        """Stop claiming. In-flight tasks keep running until they finish."""
        if not self._stop.is_set():
            log.info("worker.stopping", inflight=len(self._inflight))
        self._stop.set()

    async def run(self) -> None:
        log.info(
            "worker.started",
            version=__version__,
            concurrency=self._options.concurrency,
            endpoints=len(registry.ENDPOINTS),
        )
        try:
            while not self._stop.is_set():
                await self._claim_and_dispatch()
        finally:
            await self.drain()
            log.info("worker.stopped")

    async def _acquire_slot(self) -> bool:
        """Wait for a free concurrency slot, or give up when shutdown starts.

        Waiting on the semaphore alone would make SIGTERM depend on a task
        finishing: with every slot held by a hung request the loop would never
        reach the drain, and the drain is the only thing with a deadline.
        """
        acquire = asyncio.ensure_future(self._gate.acquire())
        stopping = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({acquire, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopping.cancel()

        if acquire.done() and not acquire.cancelled() and acquire.exception() is None:
            if self._stop.is_set():
                self._gate.release()
                return False
            return True

        # Cancelling a granted acquire returns the slot to the semaphore, so
        # this cannot leak capacity.
        acquire.cancel()
        with contextlib.suppress(BaseException):
            await acquire
        return False

    async def _claim_and_dispatch(self) -> None:
        if not await self._acquire_slot():
            return

        try:
            task_id = await self._store.claim(self._options.claim_timeout_seconds)
        except asyncio.CancelledError:
            self._gate.release()
            raise
        except Exception as exc:
            # Redis is unreachable. Backing off beats spinning on the failure.
            self._gate.release()
            log.warning("worker.claim_failed", error=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(self._options.claim_backoff_seconds)
            return

        if task_id is None:
            self._gate.release()
            return

        if self._stop.is_set():
            # Claimed during shutdown: hand it back instead of dropping it.
            self._gate.release()
            with contextlib.suppress(Exception):
                await self._store.requeue(task_id)
            return

        runner = asyncio.create_task(self._run_one(task_id), name=f"task-{task_id}")
        self._inflight.add(runner)
        runner.add_done_callback(self._inflight.discard)

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for in-flight tasks, then give up on the stragglers.

        A task cancelled at the deadline re-queues itself, so the work survives
        even when this process does not.
        """
        if not self._inflight:
            return
        limit = self._options.drain_timeout_seconds if timeout is None else timeout
        pending = set(self._inflight)
        log.info("worker.draining", inflight=len(pending), timeout_seconds=limit)
        done, still_running = await asyncio.wait(pending, timeout=limit)
        if still_running:
            log.warning("worker.drain_timeout", abandoned=len(still_running))
            for task in still_running:
                task.cancel()
            await asyncio.gather(*still_running, return_exceptions=True)
        log.info("worker.drained", finished=len(done))

    # -- one task ----------------------------------------------------------

    async def _run_one(self, task_id: uuid.UUID) -> None:
        # Bound before the try, because the handlers below use it: a failure in
        # attempt() or start() leaves nothing to notify about, and a NameError
        # there would replace a real error with a confusing one.
        run: TaskRun | None = None
        # Filled in by `_execute` when the caller asked what request was made
        # and the request failed. Owned here because the handlers below are
        # where a failure is stored, and `_execute` no longer exists by then.
        diagnostics: dict[str, Any] = {}
        try:
            attempt = await self._store.attempt(task_id)
            if attempt > self._options.max_attempts:
                # Explicit failure. A task that keeps taking its worker down
                # must stop rather than cycle through the pool forever.
                log.error("worker.task.exhausted", task_id=str(task_id), attempts=attempt)
                await self._store.fail(
                    task_id,
                    serialize_error(
                        Internal(
                            "the task was retried too many times without finishing",
                            details={"attempts": attempt},
                        )
                    ),
                )
                return

            run = await self._store.start(task_id)
            if run is None:
                # The store logs which of the two cases this was. Nothing is
                # re-queued here on purpose: a blind requeue would resurrect
                # every cancelled task, and a row that is genuinely missing has
                # nothing to run. A row that exists but was never claimed is
                # the orphan sweep's job (`requeue_orphaned_tasks`).
                return

            result = await self._execute(run, diagnostics)
            await self._store.complete(task_id, result)
            await self._notify(run, state=TaskState.DONE.value)
            # A maintenance job may store its payload bare - the task contract
            # accepts both shapes - and only the fetch path always carries
            # meta. Indexing it would raise after the task was already stored
            # as done, and the handler below would then overwrite that row with
            # a failure the job never had.
            meta = result.get("meta") or {}
            log.info(
                "worker.task.done",
                task_id=str(task_id),
                endpoint=run.endpoint,
                cached=meta.get("cached"),
                duration_ms=meta.get("duration_ms"),
            )
        except asyncio.CancelledError:
            # Shutdown reached the drain deadline while this task was running.
            # If even the re-queue cannot finish, the row stays RUNNING and the
            # maintenance sweep picks it up; either way the work survives.
            with contextlib.suppress(Exception):
                await self._store.requeue(task_id)
            raise
        except DtkError as exc:
            log.info("worker.task.failed", task_id=str(task_id), code=exc.code.value)
            await self._finish_failed(task_id, exc, diagnostics)
            await self._notify(run, state=TaskState.FAILED.value, error=serialize_error(exc))
        except Exception as exc:
            log.exception("worker.task.crashed", task_id=str(task_id), error=type(exc).__name__)
            await self._finish_failed(task_id, exc, diagnostics)
            await self._notify(run, state=TaskState.FAILED.value, error=serialize_error(exc))
        finally:
            self._gate.release()

    async def _notify(
        self, run: TaskRun | None, *, state: str, error: dict[str, Any] | None = None
    ) -> None:
        """Deliver the task callback, if the caller asked for one.

        This is the missing half of a parameter this API has accepted,
        validated and stored since the first release without ever sending
        anything to it. Doc 06 promises webhooks; nothing made the request.

        After the task is stored, always. The caller's data is safe on the row
        before any outbound request is attempted, so a webhook endpoint that is
        down, slow or hostile cannot turn a successful fetch into a failed task
        - and `deliver` never raises, which is what makes that a guarantee
        rather than an intention.
        """
        if run is None:
            return
        url = run.params.get("callback_url")
        if not isinstance(url, str) or not url:
            return
        try:
            config = self._config()
            if not bool(config.get("security.enable_task_webhook")):
                # Turned off between submission and completion. The caller was
                # told yes at the time and is told nothing now, which is the
                # safe direction: an operator switching this off means it.
                log.info("worker.webhook.disabled", task_id=str(run.id))
                return
            body = webhooks.payload_for(
                str(run.id), endpoint=run.endpoint, state=state, error=error
            )
            secret = str(config.get("security.webhook_secret") or "")
            await webhooks.deliver(url, body, secret=secret)
        except Exception as exc:
            # `deliver` promises not to raise, and this catches it anyway. The
            # call site sits inside `_run_one`'s try, so anything escaping here
            # would be caught as a task failure and would overwrite a row
            # already stored as done - turning a successful fetch into a
            # failure because a webhook misbehaved. Belt and braces, on the one
            # path where the braces failing is silent data loss for the caller.
            log.warning(
                "worker.webhook.crashed",
                task_id=str(run.id),
                error=f"{type(exc).__name__}: {exc}"[:200],
            )

    async def _finish_failed(
        self, task_id: uuid.UUID, exc: BaseException, meta: dict[str, Any] | None = None
    ) -> None:
        try:
            await self._store.fail(task_id, serialize_error(exc), meta or None)
        except Exception as inner:
            # The database is unreachable; the task stays RUNNING and the
            # maintenance sweep re-queues it. Saying so beats a silent drop.
            log.error(
                "worker.task.finish_failed",
                task_id=str(task_id),
                error=f"{type(inner).__name__}: {inner}",
            )

    #: The logical endpoint a caller submits when they have only a link. Both
    #: POST /api/v1/parse and the MCP parse_url tool queue this, so it must be
    #: understood here or every "just give it a URL" path fails.
    PARSE_ENDPOINT = "parse"

    async def _resolve_endpoint(self, run: TaskRun) -> tuple[str, dict[str, Any]]:
        """Bind a submitted task to a concrete platform endpoint.

        Everything except ``parse`` already names its endpoint. ``parse`` names
        only a URL, so the link is expanded and identified first; that expansion
        is a network call, which is why it happens here in the worker rather
        than at submission time, where it would block the API.
        """
        if run.endpoint != self.PARSE_ENDPOINT:
            return run.endpoint, dict(run.params)

        url = str(run.params.get("url") or "").strip()
        if not url:
            raise InvalidParam("parse requires a url", details={"endpoint": run.endpoint})

        # One list for both halves: the fetcher re-checks each hop it is handed,
        # and a fetcher that had not heard of the operator's hosts would refuse
        # the hop expansion had just allowed.
        hosts = extra_url_hosts(self._config())
        endpoint, resolved = await parsing.plan(
            url, parsing.egress_fetcher(self._egress, extra_hosts=hosts), extra_hosts=hosts
        )
        # Pass through anything the caller also set, such as include_raw.
        extra = {k: v for k, v in run.params.items() if k != "url"}
        return endpoint, {**extra, **resolved}

    async def _resolve_author_handle(
        self, endpoint: str, params: dict[str, Any], run: TaskRun
    ) -> dict[str, Any]:
        """Turn an @handle into the id the target endpoint actually keys on.

        A TikTok profile link is ``/@handle`` and carries nothing else, so every
        URL-shaped author lookup arrives holding a handle. Only
        ``author_profile`` accepts one; ``author_posts``, ``author_likes``,
        ``followers`` and ``following`` all key on ``secUid`` and refuse a
        handle by name rather than sending it somewhere it silently cannot work
        (TikTok answers a handle in the ``secUid`` slot with an empty user and a
        200, which classifies as success).

        Refusing was correct and also a dead end: the caller was told to look
        the author up first, and there was no way to do that and come back with
        an id, because the profile they would get did not carry one either. With
        ``Author.sec_uid`` carried through, the lookup is now a real answer, so
        do it for them instead of handing back homework.

        Douyin never reaches here - its profile links contain the
        ``sec_user_id`` literally, so the extracted identifier is already the
        right one.

        The lookup costs one upstream call and one identity, behind
        ``author_profile``'s own cache, so a second page of the same author is
        free. Failure is left to `registry.resolve` below: if the profile cannot
        be read, the caller gets the same InvalidParam naming the handle that it
        got before, rather than a second error about a call they did not make.
        """
        value = params.get(registry.AUTHOR_ID) or params.get("sec_user_id")
        if not isinstance(value, str) or not value or registry.looks_like_sec_uid(value):
            return params

        definition = registry.definition_for(endpoint)
        if registry.UNIQUE_ID in definition.accepts:
            # The endpoint takes the handle as it stands; `_route_handle` will
            # put it in the right slot and no lookup is needed.
            return params

        profile_endpoint = registry.endpoint_name(
            definition.platform, registry.Capability.AUTHOR_PROFILE
        )
        handle = value.lstrip("@")
        lookup = registry.resolve(profile_endpoint, {registry.UNIQUE_ID: handle}, self._config())

        try:
            async with self._session_factory() as session:
                result = await self._fetch.fetch(
                    session,
                    lookup.platform,
                    lookup.endpoint,
                    lookup.params,
                    parse=lookup.parse,
                    cache_ttl=lookup.cache_ttl,
                    ctx=FetchContext(
                        task_id=run.id,
                        api_key_id=run.api_key_id,
                        # This call exists to find an id. Nothing about the
                        # caller's own request - raw payloads, a pinned
                        # identity, explain - belongs on it, and a pinned
                        # identity especially: the id is the same whoever asks.
                        is_demo=run.is_demo,
                    ),
                )
        except DtkError as exc:
            log.info(
                "worker.author_handle.lookup_failed",
                endpoint=endpoint,
                handle=handle,
                error=f"{type(exc).__name__}",
            )
            return params

        # `FetchResult.payload` is the *dumped* model, not the upstream body -
        # `fetch` parses, serialises, caches the serialised form and returns
        # that, on both paths. So the id is a key here, and reading it this way
        # is the only version that survives a cache hit: the `parse` callback
        # fires on a miss and never on a hit, and re-parsing the payload as if
        # it were an upstream response raises UpstreamChanged. Both of those
        # were tried, in that order, and the first looked like it worked for
        # exactly as long as the cache was cold.
        sec_uid = result.payload.get("sec_uid") if isinstance(result.payload, dict) else None
        if not isinstance(sec_uid, str) or not sec_uid:
            log.info("worker.author_handle.no_id", endpoint=endpoint, handle=handle)
            return params

        log.info(
            "worker.author_handle.resolved",
            endpoint=endpoint,
            handle=handle,
            cached=result.cached,
        )
        resolved = dict(params)
        resolved.pop("sec_user_id", None)
        resolved[registry.AUTHOR_ID] = sec_uid
        return resolved

    async def _execute(
        self, run: TaskRun, diagnostics: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = self._config()
        # Maintenance first: these endpoints are local jobs with no platform,
        # no signature and no token bucket, so the endpoint registry has nothing
        # to say about them and would reject them as unknown.
        if self._operations is not None and self._operations.handles(run.endpoint):
            return await self._operations.run(run.endpoint, dict(run.params))

        endpoint, params = await self._resolve_endpoint(run)
        params = await self._resolve_author_handle(endpoint, params, run)
        call = registry.resolve(endpoint, params, config)
        parsed: list[Any] = []

        def parse(payload: dict[str, Any]) -> Any:
            model = call.parse(payload)
            parsed.append(model)
            return model

        ctx = FetchContext(
            task_id=run.id,
            api_key_id=run.api_key_id,
            include_raw=bool(run.params.get("include_raw")),
            # Validated at the edge by dtk.api.request_proxy, which is also
            # where the operator's setting is read. By the time it reaches a
            # stored task it has already been permitted.
            request_proxy=run.params.get("proxy") or None,
            # Likewise checked at the edge for existence, retirement and
            # platform. The scheduler checks again because a row can be retired
            # between submission and execution, and refuses rather than
            # substituting.
            identity_id=run.params.get("identity") or None,
            refresh=bool(run.params.get("refresh")),
            # Permitted at the edge, where the scope check and the audit line
            # are. By the time it reaches a stored task it has been allowed.
            explain=bool(run.params.get("explain")),
            is_demo=run.is_demo,
        )

        async with self._session_factory() as session:
            try:
                result = await self._fetch.fetch(
                    session,
                    call.platform,
                    call.endpoint,
                    call.params,
                    parse=parse,
                    cache_ttl=call.cache_ttl,
                    ctx=ctx,
                )
            except Exception:
                # The request log rows written before the failure are the only
                # record that the attempt happened; commit them before the
                # session scope rolls the transaction back.
                with contextlib.suppress(Exception):
                    await session.commit()
                # And hand back the explanation on the way out. The caller
                # cannot read it off the result - there is no result - and it
                # must not travel inside the error, which is rendered to every
                # reader while this is gated on `identity:manage`.
                if diagnostics is not None and ctx.explained is not None:
                    diagnostics["explain"] = explain_meta(ctx.explained)
                raise
            # Neither write happens for the demo account. A snapshot is a
            # counter reading of somebody else's post and an archive row is a
            # copy of it, and a public instance that kept both would grow a
            # database out of whatever strangers happened to paste.
            if parsed and not run.is_demo:
                await self._record_snapshots(session, parsed[-1], config)
                await self._archive(session, parsed[-1], config)

        return _result_payload(
            call.definition.name, call.platform, result, pinned=ctx.identity_id is not None
        )

    async def _record_snapshots(self, session: AsyncSession, parsed: Any, config: Config) -> None:
        """Fold a successful parse into the metric history.

        Free data: the request has already been paid for. Failures here are
        logged and dropped - a snapshot is never worth failing a task over.
        """
        interval = int(config.get("snapshot.min_interval_seconds"))
        try:
            for item in _snapshot_targets(parsed):
                if isinstance(item, Content):
                    await snapshots.record_content(session, item, min_interval=interval)
                elif isinstance(item, Author):
                    await snapshots.record_author(session, item, min_interval=interval)
        except Exception as exc:
            log.warning("worker.snapshot_failed", error=f"{type(exc).__name__}: {exc}")

    async def _archive(self, session: AsyncSession, parsed: Any, config: Config) -> None:
        """Keep the parsed content after the task result expires.

        Beside the snapshot writer and with the same discipline, for the same
        reason: the caller asked for data and got it, so losing an archive row is
        a smaller harm than turning a successful fetch into an error.

        Two settings, both defaulting to the conservative side. `archive.enabled`
        because an operator who wants a stateless instance should be able to have
        one, and `archive.store_raw` because the parsers fill `raw` on every
        object and doc 18 measured what keeping all of it costs.
        """
        if not bool(config.get("archive.enabled")):
            return
        try:
            written = await archive.record(
                session, (parsed,), store_raw=bool(config.get("archive.store_raw"))
            )
        except Exception as exc:
            log.warning("worker.archive_failed", error=f"{type(exc).__name__}: {exc}")
            return
        if written:
            log.debug("worker.archived", contents=written)


def _snapshot_targets(parsed: Any) -> tuple[Any, ...]:
    if isinstance(parsed, Content | Author):
        return (parsed,)
    if isinstance(parsed, Page):
        return tuple(i for i in parsed.items if isinstance(i, Content | Author))
    return ()


def _result_payload(
    endpoint: str, platform: Platform, result: FetchResult, *, pinned: bool = False
) -> dict[str, Any]:
    """The stored task result: the data plus the metadata the API echoes back."""
    meta: dict[str, Any] = {
        "endpoint": endpoint,
        "platform": platform.value,
        "cached": result.cached,
        "duration_ms": result.duration_ms,
        "request_id": str(result.request_id),
    }
    if result.explain is not None:
        # Under its own key rather than folded into the metadata above, because
        # `dtk.api.routes.tasks` strips it for any reader who could not have
        # revealed the jar directly - and stripping one key is a rule that can
        # be written down, where stripping fields out of a merged dict is not.
        meta["explain"] = explain_meta(result.explain)
    if pinned and result.identity_id:
        # Echoed only when the caller named it, and then only back to them.
        # The identity id is not a secret - it is logged in the clear on every
        # request - but the ordinary data path deliberately does not hand one
        # out, and the MCP surface hides it from agents entirely. Confirming a
        # value the caller supplied is a different act from disclosing one.
        meta["identity_id"] = result.identity_id
    data = result.payload
    if isinstance(data, dict) and "items" in data and "has_more" in data:
        meta["cursor"] = {"next": data.get("cursor"), "has_more": bool(data.get("has_more"))}
    return {"data": data, "meta": meta}


async def run(settings: BootstrapSettings | None = None) -> None:  # pragma: no cover
    """Run one worker process until SIGTERM or SIGINT.

    Re-exported from :mod:`dtk.worker.runtime`, which owns the wiring, so that
    ``dtk.worker.main`` stays the stable name for callers - the CLI's
    ``dtk worker run`` among them. Imported lazily because the runtime module
    imports this one.
    """
    from dtk.worker.runtime import run as _run

    await _run(settings)


def main() -> None:  # pragma: no cover - console entry point
    """The worker container's entry point. Wiring lives in :mod:`dtk.worker.runtime`."""
    from dtk.worker.runtime import main as _main

    _main()


def endpoint_params(endpoint: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Translate caller parameters for ``endpoint``. Kept for the CLI and MCP."""
    return registry.definition_for(endpoint).platform_params(params)


__all__ = [
    "ATTEMPTS_KEY",
    "DatabaseTaskStore",
    "SessionFactory",
    "TaskRun",
    "TaskStore",
    "TaskWorker",
    "WorkerOptions",
    "endpoint_params",
    "main",
    "run",
    "serialize_error",
]
