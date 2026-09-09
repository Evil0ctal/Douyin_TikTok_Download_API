"""Unit tests for the worker package.

The guarantees under test are the ones that are expensive to discover in
production: a task is never dropped, minting never happens twice at once, and a
dead proxy cools its identities instead of retiring them.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from dtk.api.routes.tasks import _localized_error, _view_payload
from dtk.core.config import Config
from dtk.core.errors import (
    DtkError,
    ErrorCode,
    InvalidParam,
    NotFound,
    RateLimited,
    UpstreamChanged,
    UpstreamRiskControl,
)
from dtk.core.types import IdentityState, Language, Outcome, Platform, Scope, TaskState
from dtk.ops import webhooks
from dtk.platforms import get_adapter
from dtk.platforms.tiktok.params import DEVICE_ID_DIGITS
from dtk.services.fetch import FetchResult
from dtk.services.tasks import TaskView
from dtk.worker import maintenance as maintenance_module
from dtk.worker import registry
from dtk.worker.loop import PeriodicLoop
from dtk.worker.main import TaskRun, TaskWorker, WorkerOptions, serialize_error
from dtk.worker.pool_filler import FillerConfig, PoolFiller
from dtk.worker.proxy_prober import ProberConfig, ProbeResult, ProxyProber

# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeResult:
    """Stands in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0] if self._rows else 0


class FakeSession:
    """Minimal AsyncSession: scripted SELECT results, writes report one row."""

    def __init__(
        self,
        *,
        select_results: list[list[Any]] | None = None,
        scalars_results: list[list[Any]] | None = None,
    ) -> None:
        self._selects = list(select_results or [])
        self._scalars = list(scalars_results or [])
        self.statements: list[str] = []
        self.committed = 0

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        rendered = str(statement)
        self.statements.append(rendered)
        if rendered.lstrip().upper().startswith("SELECT"):
            return FakeResult(self._selects.pop(0) if self._selects else [])
        return FakeResult([], rowcount=1)

    async def scalars(self, statement: Any) -> FakeResult:
        self.statements.append(str(statement))
        return FakeResult(self._scalars.pop(0) if self._scalars else [])

    async def get(self, model: Any, key: Any) -> Any:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed += 1

    def add(self, obj: Any) -> None:
        return None


def session_factory_for(session: Any) -> Any:
    @contextlib.asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


class FakeCipher:
    def __init__(self, value: str = "http://user:secret@eu-1.example:8080") -> None:
        self.value = value

    def decrypt(self, blob: bytes, *, aad: str) -> str:
        return self.value


class FakeAlerter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, event: Any, /, **args: Any) -> None:
        self.sent.append((event.value, args))


class FakeStore:
    """In-memory :class:`dtk.worker.main.TaskStore`."""

    def __init__(self, runs: dict[uuid.UUID, TaskRun] | None = None) -> None:
        self.runs = dict(runs or {})
        self.queue: list[uuid.UUID] = list(self.runs)
        self.completed: dict[uuid.UUID, dict[str, Any]] = {}
        self.failed: dict[uuid.UUID, dict[str, Any]] = {}
        self.requeued: list[uuid.UUID] = []
        self.started: list[uuid.UUID] = []
        self.attempts: dict[uuid.UUID, int] = {}
        self.claim_error: Exception | None = None
        self.claim_errors_raised = 0

    async def claim(self, timeout: int) -> uuid.UUID | None:
        if self.claim_error is not None:
            self.claim_errors_raised += 1
            error, self.claim_error = self.claim_error, None
            raise error
        if self.queue:
            return self.queue.pop(0)
        await asyncio.sleep(0.005)
        return None

    async def requeue(self, task_id: uuid.UUID) -> None:
        self.requeued.append(task_id)

    async def attempt(self, task_id: uuid.UUID) -> int:
        self.attempts[task_id] = self.attempts.get(task_id, 0) + 1
        return self.attempts[task_id]

    async def start(self, task_id: uuid.UUID) -> TaskRun | None:
        self.started.append(task_id)
        return self.runs.get(task_id)

    async def complete(self, task_id: uuid.UUID, result: dict[str, Any]) -> None:
        self.completed[task_id] = result

    async def fail(self, task_id: uuid.UUID, error: dict[str, Any]) -> None:
        self.failed[task_id] = error


@dataclass
class FakeFetch:
    payload: dict[str, Any] = field(default_factory=lambda: {"content_id": "1"})
    error: Exception | None = None
    parse_payload: dict[str, Any] | None = None
    gate: asyncio.Event | None = None
    entered: asyncio.Event | None = None
    calls: list[Any] = field(default_factory=list)

    async def fetch(
        self,
        session: Any,
        platform: Platform,
        endpoint: str,
        params: dict[str, Any],
        *,
        parse: Any,
        cache_ttl: int,
        ctx: Any,
    ) -> FetchResult:
        self.calls.append(
            SimpleNamespace(
                platform=platform, endpoint=endpoint, params=params, cache_ttl=cache_ttl, ctx=ctx
            )
        )
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        if self.parse_payload is not None:
            parse(self.parse_payload)
        return FetchResult(
            payload=self.payload,
            outcome=Outcome.OK,
            identity_id="11111111-1111-1111-1111-111111111111",
            cached=False,
            duration_ms=7,
            request_id=uuid.uuid4(),
        )


def make_worker(
    store: FakeStore, fetch: FakeFetch, **options: Any
) -> tuple[TaskWorker, FakeSession]:
    session = FakeSession()
    worker = TaskWorker(
        fetch=fetch,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        config=Config.defaults(),
        options=WorkerOptions(
            concurrency=options.pop("concurrency", 2),
            claim_timeout_seconds=options.pop("claim_timeout_seconds", 1),
            claim_backoff_seconds=options.pop("claim_backoff_seconds", 0.01),
            max_attempts=options.pop("max_attempts", 3),
            drain_timeout_seconds=options.pop("drain_timeout_seconds", 5.0),
        ),
        session_factory=session_factory_for(session),
    )
    return worker, session


def a_run(endpoint: str = "douyin.content_detail", **params: Any) -> TaskRun:
    return TaskRun(
        id=uuid.uuid4(), endpoint=endpoint, params=params or {"aweme_id": "7300000000000000000"}
    )


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def test_registry_covers_every_p0_endpoint() -> None:
    """Every platform serves the core set; beyond it they may differ."""
    for platform in (Platform.DOUYIN, Platform.TIKTOK):
        assert registry.missing_capabilities(platform) == ()
        names = {d.name for d in registry.definitions_for(platform)}
        required = {
            f"{platform.value}.{capability.value}" for capability in registry.P0_CAPABILITIES
        }
        allowed = required | {
            f"{platform.value}.{capability.value}" for capability in registry.OPTIONAL_CAPABILITIES
        }
        assert required <= names <= allowed


def test_optional_capabilities_are_the_ones_outside_the_core() -> None:
    """The two lists partition the enum, so a new capability lands in exactly one."""
    assert set(registry.P0_CAPABILITIES) | set(registry.OPTIONAL_CAPABILITIES) == set(
        registry.Capability
    )
    assert not set(registry.P0_CAPABILITIES) & set(registry.OPTIONAL_CAPABILITIES)


def test_registry_names_match_the_adapter_tables() -> None:
    from dtk.platforms import get_adapter

    for definition in registry.ENDPOINTS.values():
        assert definition.name in get_adapter(definition.platform).endpoints


def test_every_definition_has_a_cache_ttl_and_a_scope() -> None:
    config = Config.defaults()
    for definition in registry.ENDPOINTS.values():
        assert definition.cache_ttl(config) > 0
        assert definition.scope is not None
        assert definition.scope.value.startswith(definition.platform.value)


def test_cache_ttl_follows_the_config() -> None:
    definition = registry.definition_for("douyin.content_detail")
    assert definition.cache_ttl(Config.defaults()) == 1800
    assert definition.cache_ttl(Config({"cache.content_ttl": 30})) == 30


def test_platform_params_translate_aliases_per_platform() -> None:
    douyin = registry.definition_for("douyin.content_detail")
    tiktok = registry.definition_for("tiktok.content_detail")
    assert douyin.platform_params({"content_id": "7"}) == {"aweme_id": "7"}
    assert douyin.platform_params({"item_id": "7"}) == {"aweme_id": "7"}
    assert tiktok.platform_params({"aweme_id": "7"}) == {"item_id": "7"}


def test_ids_are_stringified_and_counts_are_integers() -> None:
    definition = registry.definition_for("douyin.author_posts")
    built = definition.platform_params({"sec_user_id": 12345, "count": "20"})
    assert built == {"sec_user_id": "12345", "count": 20}


def test_a_missing_required_parameter_is_rejected() -> None:
    definition = registry.definition_for("douyin.comment_replies")
    with pytest.raises(InvalidParam) as excinfo:
        definition.platform_params({"content_id": "7"})
    assert excinfo.value.details["missing"] == ["comment_id"]


def test_an_unknown_parameter_is_rejected_rather_than_ignored() -> None:
    definition = registry.definition_for("douyin.author_posts")
    with pytest.raises(InvalidParam) as excinfo:
        definition.platform_params({"author_id": "u", "max_cursor": "1"})
    assert excinfo.value.details["unknown"] == ["max_cursor"]


def test_a_numeric_uid_is_rejected_rather_than_read_as_a_sec_id() -> None:
    """``uid`` is a different identifier, not another spelling of ``sec_user_id``.

    Douyin's numeric uid rotates and is not the author's stable key, and
    TikTok's uid is numeric while its author key is ``secUid``. Accepting either
    as an alias would put the caller's number in the sec-id slot and return an
    empty page with nothing to say the id had been misread.
    """
    for endpoint, spelling in (
        ("douyin.author_profile", "uid"),
        ("douyin.author_posts", "user_id"),
        ("tiktok.author_posts", "uid"),
        ("douyin.content_detail", "video_id"),
    ):
        with pytest.raises(InvalidParam) as excinfo:
            registry.definition_for(endpoint).platform_params({spelling: "1234567890"})
        assert excinfo.value.details["unknown"] == [spelling]


def test_an_endpoint_of_an_unscoped_platform_requires_admin() -> None:
    """An authorization default may only ever fail closed."""

    class Unscoped:
        value = "brand-new-platform"

    assert registry._scope_for(Unscoped()) is Scope.ADMIN  # type: ignore[arg-type]


def test_envelope_parameters_pass_through_untouched() -> None:
    definition = registry.definition_for("douyin.content_detail")
    built = definition.platform_params(
        {"content_id": "7", "include_raw": True, "url": "https://v.douyin.com/x/", "lang": "zh"}
    )
    assert built == {"aweme_id": "7"}


def test_absent_values_are_dropped_not_coerced() -> None:
    definition = registry.definition_for("douyin.author_posts")
    author = "MS4wLjABAAAAexample"
    assert definition.platform_params({"author_id": author, "cursor": None}) == {
        "sec_user_id": author
    }


def test_a_bad_count_names_the_endpoint() -> None:
    definition = registry.definition_for("douyin.comments")
    with pytest.raises(InvalidParam):
        definition.platform_params({"content_id": "7", "count": "many"})


def test_unknown_endpoints_raise_invalid_param() -> None:
    with pytest.raises(InvalidParam):
        registry.definition_for("douyin.not_an_endpoint")


def test_parse_dispatches_with_the_identifiers_the_payload_omits(monkeypatch: Any) -> None:
    calls: list[tuple[str, Any, Any]] = []

    class StubAdapter:
        def parse_content(self, payload: Any, *, fetched_at: Any) -> str:
            calls.append(("content", fetched_at, None))
            return "content"

        def parse_author(self, payload: Any) -> str:
            calls.append(("author", None, None))
            return "author"

        def parse_author_posts(self, payload: Any, *, fetched_at: Any) -> str:
            calls.append(("author_posts", fetched_at, None))
            return "posts"

        def parse_comments(self, payload: Any, *, content_id: Any = None) -> str:
            calls.append(("comments", content_id, None))
            return "comments"

        def parse_comment_replies(
            self, payload: Any, *, content_id: Any = None, parent_id: Any = None
        ) -> str:
            calls.append(("replies", content_id, parent_id))
            return "replies"

    monkeypatch.setattr(registry, "get_adapter", lambda platform: StubAdapter())

    assert registry.definition_for("douyin.content_detail").parse({}, params={"aweme_id": "7"})
    comments = registry.definition_for("douyin.comments")
    assert comments.parse({}, params={"aweme_id": "7"}) == "comments"
    replies = registry.definition_for("douyin.comment_replies")
    assert replies.parse({}, params={"item_id": "7", "comment_id": "9"}) == "replies"

    assert ("comments", "7", None) in calls
    assert ("replies", "7", "9") in calls


def test_resolve_binds_params_ttl_and_parser() -> None:
    call = registry.resolve("tiktok.comments", {"aweme_id": "7", "count": 20}, Config.defaults())
    assert call.platform is Platform.TIKTOK
    assert call.endpoint == "tiktok.comments"
    assert call.params == {"aweme_id": "7", "count": 20}
    assert call.cache_ttl == 300
    assert callable(call.parse)


# --------------------------------------------------------------------------
# error serialization
# --------------------------------------------------------------------------


def test_domain_errors_serialize_with_their_code() -> None:
    payload = serialize_error(
        UpstreamRiskControl("blocked", retry_after=60, details={"endpoint": "douyin.comments"})
    )
    assert payload["code"] == ErrorCode.UPSTREAM_RISK_CONTROL.value
    assert payload["retry_after"] == 60
    assert payload["retryable"] is True
    assert payload["details"] == {"endpoint": "douyin.comments"}


def test_non_retryable_errors_say_so() -> None:
    payload = serialize_error(InvalidParam("bad"))
    assert payload["retryable"] is False


def test_unexpected_errors_never_leak_their_message() -> None:
    payload = serialize_error(RuntimeError("connection string postgres://user:pw@host/db"))
    assert payload["code"] == ErrorCode.INTERNAL.value
    assert "postgres://" not in json.dumps(payload)
    assert payload["details"] == {"error": "RuntimeError"}


# --------------------------------------------------------------------------
# error localization at the API boundary
# --------------------------------------------------------------------------
#
# The worker stores one English sentence because it has no caller to ask. What
# makes that acceptable is that everything needed to say it again in another
# language is stored beside it, and that dtk.api.routes.tasks actually does so.


def test_a_stored_error_is_re_rendered_in_the_callers_language() -> None:
    stored = serialize_error(RateLimited("request rate limit exceeded", retry_after=60))
    english = _localized_error(stored, Language.EN)
    chinese = _localized_error(stored, Language.ZH)

    assert english is not None and chinese is not None
    assert english["code"] == chinese["code"] == ErrorCode.RATE_LIMITED.value
    assert english["message"] != chinese["message"]
    # The stored sentence is for the worker log; neither caller sees it.
    assert english["message"] != stored["message"]
    # retry_after lives beside details, not in it, so folding it into the
    # template arguments is the only thing that puts the number on screen.
    assert "60" in english["message"]
    assert "60" in chinese["message"]


def test_stored_details_reach_the_rendered_message() -> None:
    stored = serialize_error(UpstreamChanged("aweme_detail.author"))
    rendered = _localized_error(stored, Language.ZH)

    assert rendered is not None
    assert rendered["details"] == {"path": "aweme_detail.author"}
    assert "aweme_detail.author" in rendered["message"]


def test_re_rendering_keeps_the_rest_of_the_stored_payload() -> None:
    stored = serialize_error(InvalidParam("bad", details={"field": "count"}))
    rendered = _localized_error(stored, Language.ZH)

    assert rendered is not None
    assert rendered["retryable"] is False
    assert rendered["details"] == {"field": "count"}
    # An unfilled placeholder reads as a broken product, not as a missing word.
    assert "{" not in rendered["message"]


def test_a_code_this_process_does_not_know_still_renders() -> None:
    rendered = _localized_error({"code": "FROM_A_NEWER_WORKER", "message": "raw"}, Language.EN)

    assert rendered is not None
    assert rendered["code"] == ErrorCode.INTERNAL.value
    assert rendered["message"] != "raw"


def test_a_failed_task_with_no_stored_error_renders_nothing() -> None:
    assert _localized_error(None, Language.EN) is None


def test_the_task_payload_carries_the_localized_error() -> None:
    """The regression: the wire payload used to hand back view.error verbatim."""
    stored = serialize_error(RateLimited("request rate limit exceeded", retry_after=30))
    view = TaskView(
        id=uuid.uuid4(),
        state=TaskState.FAILED,
        endpoint="douyin.comments",
        result=None,
        error=stored,
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )

    payload = _view_payload(view, Language.ZH)

    assert payload["error"]["code"] == ErrorCode.RATE_LIMITED.value
    assert payload["error"]["message"] != stored["message"]
    assert "30" in payload["error"]["message"]


# --------------------------------------------------------------------------
# the task loop
# --------------------------------------------------------------------------


async def test_a_successful_task_stores_data_and_meta() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    fetch = FakeFetch(payload={"items": [], "cursor": "2", "has_more": True})
    worker, _ = make_worker(store, fetch)

    runner = asyncio.create_task(worker.run())
    await _until(lambda: run.id in store.completed)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    result = store.completed[run.id]
    assert result["data"]["cursor"] == "2"
    assert result["meta"]["endpoint"] == "douyin.content_detail"
    assert result["meta"]["cursor"] == {"next": "2", "has_more": True}
    assert fetch.calls[0].params == {"aweme_id": "7300000000000000000"}
    assert fetch.calls[0].ctx.task_id == run.id
    assert store.failed == {}


async def test_a_task_that_raises_is_failed_not_lost() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch(error=UpstreamRiskControl("blocked", retry_after=60)))

    runner = asyncio.create_task(worker.run())
    await _until(lambda: run.id in store.failed)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    assert store.failed[run.id]["code"] == ErrorCode.UPSTREAM_RISK_CONTROL.value
    assert run.id not in store.completed
    assert store.requeued == []


async def test_an_unexpected_crash_still_finishes_the_task() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    worker, session = make_worker(store, FakeFetch(error=RuntimeError("boom")))

    runner = asyncio.create_task(worker.run())
    await _until(lambda: run.id in store.failed)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    assert store.failed[run.id]["code"] == ErrorCode.INTERNAL.value
    # The request log rows written before the failure are committed rather than
    # rolled back with the transaction.
    assert session.committed == 1


async def test_an_unknown_endpoint_fails_the_task_with_invalid_param() -> None:
    run = TaskRun(id=uuid.uuid4(), endpoint="douyin.nope", params={})
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch())

    runner = asyncio.create_task(worker.run())
    await _until(lambda: run.id in store.failed)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    assert store.failed[run.id]["code"] == ErrorCode.INVALID_PARAM.value


async def test_shutdown_drains_in_flight_work_instead_of_dropping_it() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    gate = asyncio.Event()
    entered = asyncio.Event()
    worker, _ = make_worker(store, FakeFetch(gate=gate, entered=entered))

    runner = asyncio.create_task(worker.run())
    await asyncio.wait_for(entered.wait(), timeout=5)

    worker.request_stop()
    assert worker.inflight == 1
    gate.set()  # the upstream call comes back after shutdown was requested
    await asyncio.wait_for(runner, timeout=5)

    assert run.id in store.completed
    assert store.requeued == []


async def test_a_task_claimed_during_shutdown_goes_back_on_the_queue() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    fetch = FakeFetch()
    worker, _ = make_worker(store, fetch)

    original_claim = store.claim

    async def claim_then_stop(timeout: int) -> uuid.UUID | None:
        task_id = await original_claim(timeout)
        if task_id is not None:
            worker.request_stop()
        return task_id

    store.claim = claim_then_stop  # type: ignore[method-assign]

    await asyncio.wait_for(worker.run(), timeout=5)

    assert store.requeued == [run.id]
    assert store.completed == {}
    assert fetch.calls == []


async def test_work_that_outlives_the_drain_deadline_is_requeued() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    entered = asyncio.Event()
    worker, _ = make_worker(
        store, FakeFetch(gate=asyncio.Event(), entered=entered), drain_timeout_seconds=0.05
    )

    runner = asyncio.create_task(worker.run())
    await asyncio.wait_for(entered.wait(), timeout=5)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    # Cancelled at the deadline, so the work goes back rather than disappearing.
    assert store.requeued == [run.id]
    assert store.completed == {}


async def test_shutdown_is_not_blocked_by_a_busy_slot() -> None:
    """SIGTERM must not wait on a hung request to free its concurrency slot."""
    first, second = a_run(), a_run()
    store = FakeStore({first.id: first, second.id: second})
    entered = asyncio.Event()
    worker, _ = make_worker(
        store,
        FakeFetch(gate=asyncio.Event(), entered=entered),
        concurrency=1,
        drain_timeout_seconds=0.05,
    )

    runner = asyncio.create_task(worker.run())
    await asyncio.wait_for(entered.wait(), timeout=5)
    worker.request_stop()

    # Without a stop-aware slot wait this never returns: the only slot is held
    # by a request that never completes.
    await asyncio.wait_for(runner, timeout=3)
    assert store.requeued == [first.id]


async def test_a_task_that_exhausts_its_attempts_is_failed_explicitly() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    store.attempts[run.id] = 3
    worker, _ = make_worker(store, FakeFetch(), max_attempts=3)

    runner = asyncio.create_task(worker.run())
    await _until(lambda: run.id in store.failed)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    assert store.failed[run.id]["code"] == ErrorCode.INTERNAL.value
    assert store.started == []  # never handed to the pipeline a fourth time


async def test_a_claim_failure_does_not_end_the_loop() -> None:
    run = a_run()
    store = FakeStore({run.id: run})
    store.claim_error = ConnectionError("redis is gone")
    worker, _ = make_worker(store, FakeFetch())

    runner = asyncio.create_task(worker.run())
    await _until(lambda: run.id in store.completed)
    worker.request_stop()
    await asyncio.wait_for(runner, timeout=5)

    assert store.claim_errors_raised == 1
    assert run.id in store.completed


# --------------------------------------------------------------------------
# periodic loop
# --------------------------------------------------------------------------


async def test_a_failing_tick_never_ends_the_loop() -> None:
    calls = {"n": 0}

    async def tick() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")

    loop = PeriodicLoop("test", 0.0, tick, jitter=0.0)
    runner = asyncio.create_task(loop.run())
    await _until(lambda: calls["n"] >= 3)
    loop.request_stop()
    await asyncio.wait_for(runner, timeout=5)
    assert calls["n"] >= 3


async def test_stopping_interrupts_the_sleep() -> None:
    async def tick() -> None:
        return None

    loop = PeriodicLoop("test", 3600.0, tick, jitter=0.0)
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    loop.request_stop()
    await asyncio.wait_for(runner, timeout=2)


# --------------------------------------------------------------------------
# pool filler
# --------------------------------------------------------------------------


class FakeRpc:
    """Browser RPC stand-in that reports whether two mints ever overlapped."""

    def __init__(self, *, configured: bool = True, error: Exception | None = None) -> None:
        self.configured = configured
        self.error = error
        self.mints = 0
        self.active = 0
        self.max_active = 0
        self.delay = 0.01
        self.proxies: list[str | None] = []

    async def mint(self, platform: Platform, *, proxy_url: str | None, geo_hint: Any) -> Any:
        from dtk.core.types import BrowserFamily
        from dtk.transport import Fingerprint

        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            self.mints += 1
            self.proxies.append(proxy_url)
            return SimpleNamespace(
                cookies={"ttwid": "x"},
                fingerprint=Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=131),
                exit_ip="203.0.113.7",
            )
        finally:
            self.active -= 1


class FakePool:
    def __init__(self, counts: dict[str, int] | None = None, *, usable: int | None = None) -> None:
        self._counts = dict(counts or {})
        #: How many of those are still under the failure-streak threshold.
        #: Defaults to all of the live ones, which is what a pool that is
        #: working looks like and keeps every existing test meaning what it did.
        self._usable = usable
        self.added: list[dict[str, Any]] = []
        self.cooled: list[tuple[uuid.UUID, int]] = []
        self.retired: list[str] = []

    async def counts(self, session: Any, platform: Platform) -> dict[str, int]:
        return dict(self._counts)

    async def usable_count(self, session: Any, platform: Platform, *, max_fail_streak: int) -> int:
        if self._usable is not None:
            return self._usable
        return int(self._counts.get("active", 0)) + int(self._counts.get("cooling", 0))

    async def add(self, session: Any, **kwargs: Any) -> uuid.UUID:
        self.added.append(kwargs)
        return uuid.uuid4()

    async def cool_all_on_proxy(self, session: Any, proxy_id: uuid.UUID, *, seconds: int) -> int:
        self.cooled.append((proxy_id, seconds))
        return 3

    async def retire(self, session: Any, identity_id: str, reason: str) -> None:
        self.retired.append(identity_id)


def make_filler(
    rpc: Any, pool: FakePool, *, session: FakeSession | None = None, **kwargs: Any
) -> PoolFiller:
    return PoolFiller(
        pool=pool,  # type: ignore[arg-type]
        rpc=rpc,
        cipher=FakeCipher(),
        config=Config.defaults(),
        options=FillerConfig(**kwargs.pop("options", {})),
        session_factory=session_factory_for(session or FakeSession(scalars_results=[[], []])),
        distributed_lock=False,
        **kwargs,
    )


async def test_minting_is_skipped_entirely_without_browser_rpc() -> None:
    rpc = FakeRpc(configured=False)
    filler = make_filler(rpc, FakePool())
    assert filler.enabled is False

    result = await filler.tick()
    assert result.reason == "disabled"
    assert rpc.mints == 0

    filler_without_client = make_filler(None, FakePool())
    assert filler_without_client.enabled is False
    assert (await filler_without_client.tick()).reason == "disabled"


async def test_one_tick_mints_exactly_one_identity_however_large_the_deficit() -> None:
    rpc = FakeRpc()
    pool = FakePool()  # empty pool, target 8
    filler = make_filler(rpc, pool)

    result = await filler.tick()

    assert result.minted is True
    assert rpc.mints == 1
    assert len(pool.added) == 1
    assert pool.added[0]["platform"] in tuple(Platform)


async def test_concurrent_top_ups_never_overlap() -> None:
    rpc = FakeRpc()
    pool = FakePool()
    filler = make_filler(rpc, pool)

    results = await asyncio.gather(
        filler.top_up_once(Platform.DOUYIN), filler.top_up_once(Platform.DOUYIN)
    )

    assert rpc.max_active == 1
    assert sum(1 for r in results if r.minted) == 1
    assert [r.reason for r in results].count("busy") == 1


async def test_a_satisfied_pool_mints_nothing() -> None:
    rpc = FakeRpc()
    filler = make_filler(rpc, FakePool({IdentityState.ACTIVE.value: 8}))
    result = await filler.tick()
    assert result.reason == "satisfied"
    assert rpc.mints == 0


async def test_cooling_identities_count_towards_the_pool_level() -> None:
    rpc = FakeRpc()
    filler = make_filler(
        rpc,
        FakePool({IdentityState.ACTIVE.value: 1, IdentityState.COOLING.value: 7}),
    )
    # Minting a replacement for a cooling identity during a risk-control event
    # is the worst possible timing, so the level counts them.
    assert (await filler.tick()).reason == "satisfied"


async def test_repeated_mint_failures_back_off() -> None:
    from dtk.identity.minting import BrowserRpcUnavailable

    now = {"t": 1000.0}
    rpc = FakeRpc(error=BrowserRpcUnavailable("browser is down"))
    filler = make_filler(rpc, FakePool(), clock=lambda: now["t"])

    first = await filler.tick()
    assert first.minted is False
    assert filler.failures == 1
    assert filler.backing_off is True
    assert (await filler.tick()).reason == "backoff"

    now["t"] += FillerConfig().backoff_initial_seconds + 1
    assert filler.backing_off is False
    await filler.tick()
    assert filler.failures == 2


async def test_minting_waits_rather_than_doubling_up_on_one_proxy() -> None:
    rpc = FakeRpc()
    pool = FakePool()
    proxy = SimpleNamespace(id=uuid.uuid4(), url_encrypted=b"x", country="DE", timezone=None)
    # No unbound proxy, but proxies do exist: two identities behind one exit is
    # the recombination doc 02 forbids.
    session = FakeSession(scalars_results=[[], [proxy]])
    filler = make_filler(rpc, pool, session=session)

    result = await filler.tick()

    assert result.reason == "no_free_proxy"
    assert rpc.mints == 0


async def test_minting_uses_a_free_proxy_and_its_geo() -> None:
    rpc = FakeRpc()
    pool = FakePool()
    proxy = SimpleNamespace(
        id=uuid.uuid4(), url_encrypted=b"x", country="DE", timezone="Europe/Berlin"
    )
    session = FakeSession(scalars_results=[[proxy]])
    filler = make_filler(rpc, pool, session=session)

    result = await filler.tick()

    assert result.minted is True
    assert rpc.proxies == ["http://user:secret@eu-1.example:8080"]
    assert pool.added[0]["proxy_id"] == proxy.id


async def test_a_full_pool_of_broken_identities_is_refilled() -> None:
    """The gap this closes: the level used to be "how many identities exist".

    An identity that fails every request stays live - a risk-control hit cools
    it, the backoff elapses, it is promoted back and fails again - so a pool can
    sit permanently at its target while serving nothing, and the filler, which
    was counting rows, had nothing to do. Measured on a reference instance:
    eighteen live TikTok identities, thirteen of them at a zero success rate,
    and a filler that had not minted in a day.
    """
    rpc = FakeRpc()
    pool = FakePool({"active": 12}, usable=1)
    filler = make_filler(rpc, pool)

    result = await filler.tick()

    assert result.minted is True


async def test_a_pool_that_is_entirely_broken_is_not_refilled() -> None:
    """Nothing usable and plenty live means everything is failing at once.

    That is a platform-wide event, and minting into it adds fresh identities to
    be burned by whatever is burning the others - while five new visitors
    appearing from one deployment during an incident is the loudest signal this
    thing can send. The alerts still fire; the browser stays put.
    """
    rpc = FakeRpc()
    pool = FakePool({"active": 12}, usable=0)
    filler = make_filler(rpc, pool)

    result = await filler.tick()

    assert result.minted is False
    assert rpc.mints == 0


async def test_a_genuinely_empty_pool_is_still_refilled() -> None:
    """The guard above must not swallow the case it looks like."""
    rpc = FakeRpc()
    pool = FakePool({}, usable=0)
    filler = make_filler(rpc, pool)

    result = await filler.tick()

    assert result.minted is True


async def test_an_empty_pool_alerts_even_when_minting_is_disabled() -> None:
    alerter = FakeAlerter()
    filler = make_filler(FakeRpc(configured=False), FakePool(), alerter=alerter)

    await filler.tick()

    events = {event for event, _ in alerter.sent}
    assert "pool_empty" in events


async def test_pool_alerts_are_delivered_with_no_transaction_open() -> None:
    """Delivering an alert means POSTing to someone else's webhook.

    Doing that with a session open pins a pooled database connection for as long
    as the far end takes to answer, which on a hung webhook is the timeout.
    """
    open_sessions = {"n": 0}
    session = FakeSession()

    @contextlib.asynccontextmanager
    async def counting_factory() -> Any:
        open_sessions["n"] += 1
        try:
            yield session
        finally:
            open_sessions["n"] -= 1

    class WatchingAlerter(FakeAlerter):
        def __init__(self) -> None:
            super().__init__()
            self.open_while_sending: list[int] = []

        async def notify(self, event: Any, /, **args: Any) -> None:
            self.open_while_sending.append(open_sessions["n"])
            await super().notify(event, **args)

    alerter = WatchingAlerter()
    filler = PoolFiller(
        pool=FakePool(),  # type: ignore[arg-type]
        rpc=FakeRpc(configured=False),  # type: ignore[arg-type]
        cipher=FakeCipher(),  # type: ignore[arg-type]
        config=Config.defaults(),
        session_factory=counting_factory,
        distributed_lock=False,
        alerter=alerter,
    )

    await filler.tick()

    assert alerter.sent, "the empty pool should have alerted"
    assert alerter.open_while_sending == [0] * len(alerter.sent)


# --------------------------------------------------------------------------
# proxy prober
# --------------------------------------------------------------------------


class FakeProbeClient:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.calls: list[str | None] = []

    async def probe(self, proxy_url: str | None) -> ProbeResult:
        self.calls.append(proxy_url)
        return self.result

    async def aclose(self) -> None:
        return None


def a_proxy(**kwargs: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "url_encrypted": b"blob",
        "label": "eu-1",
        "country": None,
        "timezone": None,
        "healthy": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_prober(
    result: ProbeResult, pool: FakePool, session: FakeSession, alerter: FakeAlerter | None = None
) -> ProxyProber:
    return ProxyProber(
        cipher=FakeCipher(),  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        client=FakeProbeClient(result),
        options=ProberConfig(cooldown_seconds=900),
        session_factory=session_factory_for(session),
        alerter=alerter,
    )


async def test_a_failed_proxy_cools_its_identities_and_never_retires_them() -> None:
    proxy = a_proxy()
    pool = FakePool()
    session = FakeSession(select_results=[[proxy]])
    alerter = FakeAlerter()
    prober = make_prober(ProbeResult(ok=False, detail="timeout"), pool, session, alerter)

    report = await prober.sweep()

    assert report.unhealthy == 1
    assert pool.cooled == [(proxy.id, 900)]
    assert pool.retired == []
    # Nothing re-points an identity at another proxy.
    assert not any("proxy_id" in statement for statement in session.statements)
    event, args = alerter.sent[0]
    assert event == "proxy_unhealthy"
    assert args["proxy"] == "eu-1"
    assert args["failures"] == 1


async def test_a_failed_probe_marks_the_proxy_unhealthy() -> None:
    proxy = a_proxy()
    session = FakeSession(select_results=[[proxy]])
    prober = make_prober(ProbeResult(ok=False, detail="timeout"), FakePool(), session)

    await prober.sweep()

    updates = [s for s in session.statements if s.lstrip().upper().startswith("UPDATE PROXIES")]
    assert updates and "healthy" in updates[0]


async def test_a_healthy_probe_writes_back_geoip() -> None:
    proxy = a_proxy()
    pool = FakePool()
    session = FakeSession(select_results=[[proxy]])
    prober = make_prober(
        ProbeResult(ok=True, exit_ip="203.0.113.7", country="DE", timezone="Europe/Berlin"),
        pool,
        session,
    )

    report = await prober.sweep()

    assert report.healthy == 1
    assert pool.cooled == []
    assert any("country" in statement for statement in session.statements)


async def test_a_proxy_is_not_reprobed_inside_its_floor() -> None:
    proxy = a_proxy()
    session = FakeSession(select_results=[[proxy], [proxy]])
    prober = make_prober(ProbeResult(ok=True), FakePool(), session)

    first = await prober.sweep()
    second = await prober.sweep()

    assert first.checked == 1
    assert second.checked == 0


class ExplodingProbeClient:
    """Fails the first probe outright, the way a missing optional dependency does.

    ``httpx`` raises ``ImportError`` for a ``socks5://`` proxy when the socks
    extra is not installed, and that scheme is one the console and the CLI both
    accept.
    """

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[str | None] = []

    async def probe(self, proxy_url: str | None) -> ProbeResult:
        self.calls.append(proxy_url)
        if len(self.calls) == 1:
            raise self.error
        return ProbeResult(ok=True)

    async def aclose(self) -> None:
        return None


async def test_one_unprobeable_proxy_does_not_abort_the_sweep() -> None:
    first, second = a_proxy(label="socks-1"), a_proxy(label="http-2")
    pool = FakePool()
    session = FakeSession(select_results=[[first, second]])
    prober = ProxyProber(
        cipher=FakeCipher(),  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        client=ExplodingProbeClient(  # type: ignore[arg-type]
            ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")
        ),
        session_factory=session_factory_for(session),
    )

    report = await prober.sweep()

    # The second proxy is still probed and still gets its health written back.
    assert report.checked == 1
    assert report.healthy == 1
    # A broken probe client says nothing about the egress, so nothing is cooled.
    assert pool.cooled == []


async def test_a_probe_error_never_carries_the_proxy_credentials() -> None:
    from dtk.worker.proxy_prober import _without_credentials

    url = "http://user:secret@eu-1.example:8080"
    message = f"ValueError: Unknown scheme for proxy URL '{url}'"
    scrubbed = _without_credentials(message, url)
    assert scrubbed is not None
    assert "secret" not in scrubbed
    assert "user:secret" not in scrubbed
    assert "eu-1.example:8080" in scrubbed


async def test_an_undecryptable_proxy_is_reported_not_cooled() -> None:
    class BrokenCipher:
        def decrypt(self, blob: bytes, *, aad: str) -> str:
            raise ValueError("wrong key")

    proxy = a_proxy()
    pool = FakePool()
    session = FakeSession(select_results=[[proxy]])
    prober = ProxyProber(
        cipher=BrokenCipher(),  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        client=FakeProbeClient(ProbeResult(ok=False)),
        session_factory=session_factory_for(session),
    )

    report = await prober.sweep()

    assert report.checked == 0
    assert pool.cooled == []
    assert pool.retired == []


# --------------------------------------------------------------------------
# maintenance
# --------------------------------------------------------------------------


def _sid_guard(expires_in: timedelta) -> str:
    issued = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    max_age = int(timedelta(days=1).total_seconds() + expires_in.total_seconds())
    return f"sid_guard=session|{issued}|{max_age}|Mon%2C+01-Jan-2035+00%3A00%3A00+GMT"


class ExpiryCipher:
    def __init__(self, headers: dict[uuid.UUID, str]) -> None:
        self._headers = headers

    def decrypt(self, blob: bytes, *, aad: str) -> str:
        return self._headers[uuid.UUID(aad)]


async def test_sessions_close_to_expiry_are_warned_about() -> None:
    soon = SimpleNamespace(
        id=uuid.uuid4(), platform="douyin", cookies_encrypted=b"a", state="active"
    )
    later = SimpleNamespace(
        id=uuid.uuid4(), platform="douyin", cookies_encrypted=b"b", state="active"
    )
    cipher = ExpiryCipher(
        {
            soon.id: _sid_guard(timedelta(hours=12)),
            later.id: _sid_guard(timedelta(days=30)),
        }
    )
    session = FakeSession(select_results=[[soon, later]])
    alerter = FakeAlerter()
    job = maintenance_module.Maintenance(
        cipher=cipher,  # type: ignore[arg-type]
        config=Config.defaults(),
        session_factory=session_factory_for(session),
        alerter=alerter,
    )

    report = maintenance_module.MaintenanceReport()
    await job.warn_expiring_sessions(report)

    assert report.expiring_sessions == 1
    assert [event for event, _ in alerter.sent] == ["cookie_expiring"]
    assert alerter.sent[0][1]["identity_id"] == str(soon.id)


async def test_an_identity_without_a_session_cookie_is_not_warned_about() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(), platform="douyin", cookies_encrypted=b"a", state="active"
    )
    session = FakeSession(select_results=[[row]])
    job = maintenance_module.Maintenance(
        cipher=ExpiryCipher({row.id: "ttwid=abc; msToken=def"}),  # type: ignore[arg-type]
        config=Config.defaults(),
        session_factory=session_factory_for(session),
    )

    report = maintenance_module.MaintenanceReport()
    await job.warn_expiring_sessions(report)

    assert report.expiring_sessions == 0


async def test_one_failing_job_does_not_stop_the_others() -> None:
    job = maintenance_module.Maintenance(
        cipher=FakeCipher(),  # type: ignore[arg-type]
        config=Config.defaults(),
        session_factory=session_factory_for(FakeSession()),
    )
    ran: list[str] = []

    async def boom(report: Any) -> None:
        raise RuntimeError("database is gone")

    def recorder(name: str) -> Any:
        async def job_fn(report: Any) -> None:
            ran.append(name)

        return job_fn

    job.apply_retention = boom  # type: ignore[method-assign]
    job.requeue_stale_tasks = recorder("requeue_stale_tasks")  # type: ignore[method-assign]
    job.refresh_aggregates = recorder("refresh_aggregates")  # type: ignore[method-assign]
    job.warn_expiring_sessions = recorder("warn_expiring_sessions")  # type: ignore[method-assign]

    report = await job.tick()

    assert any("apply_retention" in error for error in report.errors)
    # The point of the try/except per job: the three after the failure still ran.
    assert ran == ["requeue_stale_tasks", "refresh_aggregates", "warn_expiring_sessions"]


async def test_stale_tasks_reach_the_queue_only_after_the_commit(monkeypatch: Any) -> None:
    """A queued id whose row still says ``running`` is a task run twice.

    The push has to follow the commit: a rollback after the push would leave
    the id claimable while the row is untouched, so the next sweep would push
    it again and two workers would fetch the same thing upstream.
    """
    events: list[str] = []
    rows = [
        SimpleNamespace(
            id=uuid.uuid4(),
            state="running",
            started_at=datetime.now(UTC) - timedelta(hours=2),
        )
    ]
    session = FakeSession(select_results=[rows])

    @contextlib.asynccontextmanager
    async def committing_factory() -> Any:
        yield session
        events.append("commit")

    class RecordingRedis:
        def __init__(self) -> None:
            self.pushed: list[str] = []

        async def rpush(self, key: str, value: str) -> int:
            events.append("push")
            self.pushed.append(value)
            return 1

    redis = RecordingRedis()
    job = maintenance_module.Maintenance(
        cipher=FakeCipher(),  # type: ignore[arg-type]
        config=Config.defaults(),
        options=maintenance_module.MaintenanceConfig(stale_task_seconds=60),
        session_factory=committing_factory,
    )
    monkeypatch.setattr(maintenance_module, "get_redis", lambda: redis)
    report = maintenance_module.MaintenanceReport()
    await job.requeue_stale_tasks(report)

    assert report.requeued_tasks == 1
    assert redis.pushed == [str(rows[0].id)]
    assert rows[0].state == "queued"
    assert rows[0].started_at is None
    assert events == ["commit", "push"]


def test_cookie_headers_round_trip() -> None:
    assert maintenance_module.cookies_from_header("a=1; b=2 ; junk") == {"a": "1", "b": "2"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _until(predicate: Any, timeout: float = 5.0) -> None:
    """Wait for a condition without sleeping a fixed amount."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met in time")


def test_dtk_errors_carry_a_stable_code() -> None:
    # Guards the assumption serialize_error makes about the error hierarchy.
    assert issubclass(InvalidParam, DtkError)
    assert ErrorCode.INVALID_PARAM.value == "INVALID_PARAM"


def test_the_socket_outlives_the_longest_blocking_command() -> None:
    """A dependency default that moved under us, pinned so it cannot move again.

    redis-py 8 changed the default socket_timeout from None to 5 seconds, and
    `tasks.claim` passes exactly 5 to BLPOP. The read then expired at the same
    moment the command was due to return empty, so every idle poll raised: the
    worker logged a Redis timeout every few seconds and only picked tasks up on
    a later attempt. The socket must be allowed to outlast the command.
    """
    from dtk.core.redis import SOCKET_TIMEOUT_SECONDS
    from dtk.services.tasks import claim
    from dtk.worker.main import WorkerOptions

    longest_block = max(
        WorkerOptions().claim_timeout_seconds,
        inspect.signature(claim).parameters["timeout"].default,
    )
    assert longest_block < SOCKET_TIMEOUT_SECONDS, (
        f"socket_timeout {SOCKET_TIMEOUT_SECONDS}s does not outlast a "
        f"{longest_block}s blocking command; every idle poll will raise"
    )


class TestTikTokDeviceId:
    """`device_id` is required, and its absence is silent.

    Measured against live TikTok on 2026-09-08. Without this parameter every
    endpoint answers 200 with an EMPTY BODY and the header `tt_orcas_res: 1` -
    no status code, no message. Every TikTok call this project made was
    returning nothing for exactly this reason, and it survived a long hunt
    through signatures, cookies, TLS profiles and identity pools because an
    empty 200 reads as risk control.

    Adding only `device_id` took /api/item/detail/ from 0 bytes to a full
    itemStruct; adding only `odinId` or only `WebIdLastTime` left it gated.
    """

    def test_every_tiktok_request_carries_a_device_id(self) -> None:
        adapter = get_adapter(Platform.TIKTOK)
        for endpoint, params in (
            ("tiktok.content_detail", {"item_id": "7218694761253735723"}),
            ("tiktok.author_profile", {"unique_id": "owlcitymusic"}),
            ("tiktok.comments", {"aweme_id": "7218694761253735723"}),
            ("tiktok.author_posts", {"sec_uid": "MS4wLjABAAAA"}),
        ):
            built = adapter.build_request(endpoint, **params)
            device_id = built["params"].get("device_id")
            assert device_id, f"{endpoint} sends no device_id"
            assert device_id.isdigit(), f"{endpoint} device_id is not numeric: {device_id!r}"
            assert len(device_id) == DEVICE_ID_DIGITS, (
                f"{endpoint} device_id is {len(device_id)} digits"
            )
            assert not device_id.startswith("0"), "a leading zero is not a plausible device id"

    def test_two_requests_do_not_share_one_device_id(self) -> None:
        """Sharing one value across identities would link them to each other.

        Per-call is a compromise - a real device id is stable for the life of a
        browser - but the alternative available at this layer is one constant
        for every identity, which is worse. See params.DEVICE_ID_DIGITS.
        """
        adapter = get_adapter(Platform.TIKTOK)
        seen = {
            adapter.build_request("tiktok.content_detail", item_id="7")["params"]["device_id"]
            for _ in range(20)
        }
        assert len(seen) > 1, "device_id is constant across requests"


# --------------------------------------------------------------------------
# @handle routing
#
# A TikTok profile link is "/@handle", so every URL-shaped author lookup
# resolves to a handle rather than to a secUid. Sent as author_id it reaches
# the platform in the secUid slot, where TikTok answers 200 with statusCode
# 10221 and an empty user - a success-shaped response carrying nothing, which
# surfaced as "this author has no data" rather than as a bad request.
# --------------------------------------------------------------------------


def test_a_tiktok_handle_is_routed_to_the_parameter_that_accepts_one() -> None:
    definition = registry.definition_for("tiktok.author_profile")
    assert definition.platform_params({"author_id": "chainzone_led"}) == {
        "unique_id": "chainzone_led"
    }


def test_a_leading_at_sign_is_stripped_from_a_handle() -> None:
    definition = registry.definition_for("tiktok.author_profile")
    assert definition.platform_params({"author_id": "@chainzone_led"}) == {
        "unique_id": "chainzone_led"
    }


def test_a_sec_uid_still_goes_to_the_stable_id_parameter() -> None:
    """The fix must not divert the identifier that already worked."""
    definition = registry.definition_for("tiktok.author_profile")
    author = "MS4wLjABAAAASDkj_zPPnsTc11swAcHDf470jLCB3tx49m4OJluW3Tb"
    assert definition.platform_params({"author_id": author}) == {"sec_uid": author}


def test_an_endpoint_with_no_handle_parameter_refuses_the_handle_by_name() -> None:
    """Better a named error than a request that cannot work being sent anyway."""
    definition = registry.definition_for("tiktok.author_posts")
    with pytest.raises(InvalidParam, match="stable id"):
        definition.platform_params({"author_id": "chainzone_led"})


# --------------------------------------------------------------------------
# raw payloads
#
# `raw` exists so a caller can read fields the normalized model does not carry;
# doc 11 promises them that. What it must not do is get STORED for a caller who
# asked not to receive it - which is what happened, because
# model_dump(exclude={"raw"}) drops only the top-level field and every list item
# carries its own.
# --------------------------------------------------------------------------


def _page_with_raw():
    from dtk.models.content import Author, Comment, Page

    author = Author(platform="douyin", uid="9", nickname="n")
    comment = Comment(
        platform="douyin",
        comment_id="1",
        content_id="2",
        text="hi",
        author=author,
        raw={"platform_only_field": "value"},
    )
    return Page[Comment](items=[comment], cursor=None, has_more=False)


def test_include_raw_still_returns_every_per_item_payload() -> None:
    """The half that must not regress: asking for raw gets ALL of it."""
    from dtk.services.fetch import _dump

    dumped = _dump(_page_with_raw(), include_raw=True)

    assert dumped["items"][0]["raw"] == {"platform_only_field": "value"}
    # And nothing else was flattened on the way through.
    assert dumped["items"][0]["author"]["nickname"] == "n"


def test_declining_raw_drops_it_at_every_depth() -> None:
    """A page of twenty comments used to keep twenty untouched payloads."""
    from dtk.services.fetch import _dump

    dumped = _dump(_page_with_raw(), include_raw=False)

    assert "raw" not in dumped
    assert "raw" not in dumped["items"][0]
    assert dumped["items"][0]["author"]["nickname"] == "n"


def test_the_snapshot_dedup_key_separates_a_video_from_an_author() -> None:
    """Sharing a namespace suppressed one of the two writes for a whole window."""
    from dtk.services.snapshots import DEDUP_KEY

    video = DEDUP_KEY.format(platform="douyin", content_type="video", content_id="7")
    user = DEDUP_KEY.format(platform="douyin", content_type="user", content_id="7")

    assert video != user


# --------------------------------------------------------------------------
# Task callbacks
#
# `callback_url` was declared, validated and stored from the first release, and
# nothing ever posted to it. These pin down the wiring that closes that, plus
# the two properties that make it safe: delivery happens after the task is
# stored, and it can never fail the task.
# --------------------------------------------------------------------------


def _callback_config(enabled: bool = True, secret: str = "") -> Config:
    return Config(
        {
            **Config.defaults().as_dict(),
            "security.enable_task_webhook": enabled,
            "security.webhook_secret": secret,
        }
    )


async def test_a_finished_task_posts_to_its_callback(monkeypatch: Any) -> None:
    sent: list[tuple[str, dict[str, Any], str]] = []

    async def fake_deliver(url: str, body: dict[str, Any], *, secret: str = "", **_: Any) -> Any:
        sent.append((url, body, secret))
        return None

    monkeypatch.setattr(webhooks, "deliver", fake_deliver)

    run = a_run(callback_url="https://example.com/hook", aweme_id="7300000000000000000")
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch())
    worker._config = lambda: _callback_config(secret="s")

    await worker._run_one(run.id)

    assert len(sent) == 1
    url, body, secret = sent[0]
    assert url == "https://example.com/hook"
    assert body["event"] == "task.completed"
    assert body["task_id"] == str(run.id)
    assert secret == "s"
    # The result is stored before anything is posted, so a webhook that never
    # answers cannot cost the caller their data.
    assert run.id in store.completed


async def test_a_failed_task_posts_the_reason(monkeypatch: Any) -> None:
    sent: list[dict[str, Any]] = []

    async def fake_deliver(url: str, body: dict[str, Any], **_: Any) -> Any:
        sent.append(body)
        return None

    monkeypatch.setattr(webhooks, "deliver", fake_deliver)

    run = a_run(callback_url="https://example.com/hook", aweme_id="7300000000000000000")
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch(error=NotFound("gone")))
    worker._config = lambda: _callback_config()

    await worker._run_one(run.id)

    assert sent and sent[0]["event"] == "task.failed"
    assert sent[0]["error"]["code"] == "NOT_FOUND"


async def test_a_dead_callback_never_fails_the_task(monkeypatch: Any) -> None:
    """The contract. The caller asked for data and got it."""

    async def exploding(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the receiver is on fire")

    monkeypatch.setattr(webhooks, "deliver", exploding)

    run = a_run(callback_url="https://example.com/hook", aweme_id="7300000000000000000")
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch())
    worker._config = lambda: _callback_config()

    await worker._run_one(run.id)

    # Stored as done, and the exception from delivery did not turn it into a
    # failure - `_run_one` catches it, which is why this asserts both.
    assert run.id in store.completed
    assert run.id not in store.failed


async def test_no_callback_means_no_outbound_request(monkeypatch: Any) -> None:
    async def never(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("a task with no callback_url must not post anywhere")

    monkeypatch.setattr(webhooks, "deliver", never)

    run = a_run()
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch())
    worker._config = lambda: _callback_config()

    await worker._run_one(run.id)
    assert run.id in store.completed


async def test_turning_the_setting_off_stops_delivery(monkeypatch: Any) -> None:
    """An operator switching this off between submission and completion means
    it; the caller was told yes at the time and is told nothing now."""

    async def never(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("delivery must not happen while the setting is off")

    monkeypatch.setattr(webhooks, "deliver", never)

    run = a_run(callback_url="https://example.com/hook", aweme_id="7300000000000000000")
    store = FakeStore({run.id: run})
    worker, _ = make_worker(store, FakeFetch())
    worker._config = lambda: _callback_config(enabled=False)

    await worker._run_one(run.id)
    assert run.id in store.completed
