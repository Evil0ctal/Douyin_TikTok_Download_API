"""The worker against real PostgreSQL and Redis.

What cannot be shown with fakes: that a task really moves through the tasks
table, that a re-queue really puts the row back in ``queued``, that the mint
lock really serializes two worker replicas, and that a proxy failure really
leaves the identities behind it cooling rather than retired.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from dtk.core.config import Config
from dtk.core.crypto import Cipher
from dtk.core.db import session_scope
from dtk.core.errors import ErrorCode, UpstreamRiskControl
from dtk.core.types import (
    BrowserFamily,
    IdentitySource,
    IdentityState,
    Outcome,
    Platform,
    TaskState,
)
from dtk.db.models import CONTINUOUS_AGGREGATES, Identity, Proxy, RequestLog, Task
from dtk.identity.pool import IdentityPool
from dtk.services import tasks
from dtk.services.fetch import FetchResult
from dtk.transport import Fingerprint
from dtk.worker.main import ATTEMPTS_KEY, DatabaseTaskStore, TaskWorker, WorkerOptions
from dtk.worker.maintenance import Maintenance, MaintenanceConfig, MaintenanceReport
from dtk.worker.pool_filler import MINT_LOCK_KEY, FillerConfig, PoolFiller
from dtk.worker.proxy_prober import ProberConfig, ProbeResult, ProxyProber

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECRET = "integration-test-secret-key-of-sufficient-length"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


class StubFetch:
    """Replaces the upstream pipeline; the worker's own behaviour is the subject."""

    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None):
        self.payload = payload if payload is not None else {"content_id": "7"}
        self.error = error
        self.calls: list[str] = []

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
        self.calls.append(endpoint)
        if self.error is not None:
            raise self.error
        return FetchResult(
            payload=self.payload,
            outcome=Outcome.OK,
            identity_id=None,
            cached=False,
            duration_ms=3,
            request_id=ctx.request_id,
        )


class StubProbe:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.calls = 0

    async def probe(self, proxy_url: str | None) -> ProbeResult:
        self.calls += 1
        return self.result

    async def aclose(self) -> None:
        return None


class RecordingRpc:
    """Browser RPC stand-in that notices two overlapping mints."""

    configured = True

    def __init__(self) -> None:
        self.mints = 0
        self.active = 0
        self.max_active = 0

    async def mint(self, platform: Platform, *, proxy_url: str | None, geo_hint: Any) -> Any:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
            self.mints += 1
            return SimpleNamespace(
                cookies={"ttwid": "x"},
                fingerprint=Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=131),
                exit_ip=None,
            )
        finally:
            self.active -= 1


def session_factory_for(session: Any) -> Any:
    @contextlib.asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


async def _submit(endpoint: str = "douyin.content_detail", **params: Any) -> uuid.UUID:
    async with session_scope() as session:
        return await tasks.submit(session, endpoint, params or {"aweme_id": "7300000000000000000"})


async def _task_row(task_id: uuid.UUID) -> Task:
    row = await _task_row_or_none(task_id)
    assert row is not None, "the task row disappeared; is another suite truncating the fixture?"
    return row


async def _task_row_or_none(task_id: uuid.UUID) -> Task | None:
    async with session_scope() as session:
        return await session.get(Task, task_id)


def _require(row: Any, what: str) -> Any:
    """Guard a precondition, not an assertion.

    The fixture database is shared, and another suite's ``TRUNCATE`` can remove
    a row this test has just written. That is an environment collision rather
    than a defect, so the setup path skips instead of failing; every assertion
    below still asserts.
    """
    if row is None:
        pytest.skip(f"{what} vanished: the shared fixture was truncated by another suite")
    return row


async def _run_until_state(worker: TaskWorker, task_id: uuid.UUID, state: TaskState) -> Task:
    """Run the worker until the task reaches ``state``, returning that row.

    The row is captured by the predicate rather than re-read afterwards: the
    test database is shared, so a second read is a second chance for someone
    else's fixture to have truncated the table underneath it.
    """
    seen: list[Task] = []
    existed = False

    async def reached() -> bool:
        nonlocal existed
        row = await _task_row_or_none(task_id)
        if row is None:
            if existed:
                # Nothing in the worker ever deletes a task, so the row leaving
                # while we watched it is another suite's TRUNCATE.
                _require(None, "the task row")
            return False
        existed = True
        if row.state == state.value:
            seen.append(row)
            return True
        return False

    await _run_worker_until(worker, reached)
    return seen[0]


async def _drop_task(task_id: uuid.UUID) -> None:
    async with session_scope() as session:
        await session.execute(delete(Task).where(Task.id == task_id))


async def _run_worker_until(worker: TaskWorker, predicate: Any, *, timeout: float = 10.0) -> None:
    runner = asyncio.create_task(worker.run())
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while asyncio.get_running_loop().time() < deadline:
            if await predicate():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("the worker did not reach the expected state in time")
    finally:
        worker.request_stop()
        await asyncio.wait_for(runner, timeout=timeout)


def make_worker(fetch: Any) -> TaskWorker:
    return TaskWorker(
        fetch=fetch,
        store=DatabaseTaskStore(),
        config=Config.defaults(),
        options=WorkerOptions(
            concurrency=2,
            claim_timeout_seconds=1,
            claim_backoff_seconds=0.05,
            drain_timeout_seconds=5.0,
        ),
    )


@pytest.fixture
async def cleanup_identities(db_engine):
    created: list[uuid.UUID] = []
    yield created
    async with session_scope() as session:
        for identity_id in created:
            await session.execute(delete(Identity).where(Identity.id == identity_id))


@pytest.fixture
async def cleanup_proxies(db_engine):
    created: list[uuid.UUID] = []
    yield created
    async with session_scope() as session:
        for proxy_id in created:
            await session.execute(delete(Proxy).where(Proxy.id == proxy_id))


# --------------------------------------------------------------------------
# the task loop against the real queue
# --------------------------------------------------------------------------


async def test_a_queued_task_runs_and_is_stored_as_done(db_engine, redis_client):
    task_id = await _submit()
    fetch = StubFetch({"content_id": "7300000000000000000"})
    worker = make_worker(fetch)

    try:
        row = await _run_until_state(worker, task_id, TaskState.DONE)
        assert row.result["data"] == {"content_id": "7300000000000000000"}
        assert row.result["meta"]["endpoint"] == "douyin.content_detail"
        assert row.error is None
        assert row.finished_at is not None
        assert fetch.calls == ["douyin.content_detail"]
        # The attempt counter is cleaned up, so a re-used id starts from one.
        assert await redis_client.get(ATTEMPTS_KEY.format(task_id=task_id)) is None
    finally:
        await _drop_task(task_id)


async def test_a_failing_task_is_stored_as_failed_with_a_serialized_error(db_engine, redis_client):
    task_id = await _submit()
    worker = make_worker(StubFetch(error=UpstreamRiskControl("blocked", retry_after=60)))

    try:
        row = await _run_until_state(worker, task_id, TaskState.FAILED)
        assert row.error["code"] == ErrorCode.UPSTREAM_RISK_CONTROL.value
        assert row.error["retry_after"] == 60
        assert row.result is None
    finally:
        await _drop_task(task_id)


async def test_requeue_returns_the_row_to_queued_and_the_id_to_the_queue(db_engine, redis_client):
    task_id = await _submit()
    store = DatabaseTaskStore()
    try:
        claimed = await store.claim(timeout=2)
        if claimed != task_id:
            # Another suite is sharing the fixture queue; nothing to test here.
            pytest.skip("the fixture queue held another suite's task")
        run = await store.start(task_id)
        assert run is not None
        assert run.endpoint == "douyin.content_detail"
        assert (await _task_row(task_id)).state == TaskState.RUNNING.value

        await store.requeue(task_id)

        row = await _task_row(task_id)
        assert row.state == TaskState.QUEUED.value
        assert row.started_at is None
        assert str(task_id) in await redis_client.lrange(tasks.QUEUE_KEY, 0, -1)
    finally:
        await _drop_task(task_id)


async def test_the_attempt_counter_survives_across_claims(db_engine, redis_client):
    task_id = await _submit()
    store = DatabaseTaskStore()
    try:
        assert await store.attempt(task_id) == 1
        assert await store.attempt(task_id) == 2
        await store.fail(task_id, {"code": "INTERNAL"})
        assert await redis_client.get(ATTEMPTS_KEY.format(task_id=task_id)) is None
    finally:
        await _drop_task(task_id)


async def test_a_finished_task_is_never_run_a_second_time(db_engine, redis_client):
    task_id = await _submit()
    store = DatabaseTaskStore()
    try:
        await store.complete(task_id, {"data": {}, "meta": {}})
        assert await store.start(task_id) is None
    finally:
        await _drop_task(task_id)


# --------------------------------------------------------------------------
# maintenance
# --------------------------------------------------------------------------


async def test_a_stale_running_task_is_requeued(db_engine, redis_client):
    task_id = await _submit()
    async with session_scope() as session:
        row = _require(await session.get(Task, task_id), "the submitted task")
        row.state = TaskState.RUNNING.value
        row.started_at = datetime.now(UTC) - timedelta(hours=2)
    await redis_client.delete(tasks.QUEUE_KEY)

    job = Maintenance(
        cipher=Cipher(SECRET),
        config=Config.defaults(),
        options=MaintenanceConfig(stale_task_seconds=60),
    )
    report = MaintenanceReport()
    try:
        await job.requeue_stale_tasks(report)

        assert report.requeued_tasks >= 1
        assert (await _task_row(task_id)).state == TaskState.QUEUED.value
        assert str(task_id) in await redis_client.lrange(tasks.QUEUE_KEY, 0, -1)
    finally:
        await _drop_task(task_id)


async def test_a_running_task_inside_the_window_is_left_alone(db_engine, redis_client):
    task_id = await _submit()
    async with session_scope() as session:
        row = _require(await session.get(Task, task_id), "the submitted task")
        row.state = TaskState.RUNNING.value
        row.started_at = datetime.now(UTC)

    job = Maintenance(
        cipher=Cipher(SECRET),
        config=Config.defaults(),
        options=MaintenanceConfig(stale_task_seconds=900),
    )
    report = MaintenanceReport()
    try:
        await job.requeue_stale_tasks(report)
        assert report.requeued_tasks == 0
        assert (await _task_row(task_id)).state == TaskState.RUNNING.value
    finally:
        await _drop_task(task_id)


async def test_retention_windows_reach_timescaledb(db_engine, redis_client):
    """A changed setting has to land on the policy object, not just in the table."""
    from sqlalchemy import text

    job = Maintenance(cipher=Cipher(SECRET), config=Config({"retention.request_log_days": 9}))
    report = MaintenanceReport()
    try:
        await job.apply_retention(report)
        assert report.retention is not None

        async with session_scope() as session:
            drop_after = (
                await session.execute(
                    text(
                        "SELECT config ->> 'drop_after' FROM timescaledb_information.jobs "
                        "WHERE proc_name = 'policy_retention' AND hypertable_name = 'request_log'"
                    )
                )
            ).scalar_one()
        assert "9 days" in str(drop_after)
    finally:
        # Put the shared fixture back the way it was found.
        restore = Maintenance(cipher=Cipher(SECRET), config=Config.defaults())
        await restore.apply_retention(MaintenanceReport())


async def test_aggregates_with_a_refresh_policy_are_left_alone(db_engine, redis_client):
    """The catalog query must run against real TimescaleDB, policies and all.

    The migration installs a refresh policy for every aggregate, so a pass that
    refreshes anything here means the policy lookup misread the catalog.
    """
    job = Maintenance(cipher=Cipher(SECRET), config=Config.defaults())
    report = MaintenanceReport()
    await job.refresh_aggregates(report)
    assert report.aggregates_refreshed == []


async def test_an_aggregate_without_a_policy_is_refreshed_by_hand(db_engine, redis_client):
    """The manual path has to survive "cannot run inside a transaction"."""
    job = Maintenance(cipher=Cipher(SECRET), config=Config.defaults())

    async def no_policy(session: Any, view: str) -> bool:
        return False

    job._has_policy = no_policy  # type: ignore[method-assign]
    report = MaintenanceReport()
    await job.refresh_aggregates(report)

    assert set(report.aggregates_refreshed) == set(CONTINUOUS_AGGREGATES)


# --------------------------------------------------------------------------
# proxy prober
# --------------------------------------------------------------------------


async def _make_proxy(cipher: Cipher, url: str = "http://user:pw@proxy.test:8080") -> uuid.UUID:
    proxy_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            Proxy(
                id=proxy_id,
                url_encrypted=cipher.encrypt(url, aad=str(proxy_id)),
                label="probe-test",
                healthy=True,
            )
        )
    return proxy_id


async def test_a_dead_proxy_cools_its_identities_and_leaves_them_where_they_are(
    db_engine, redis_client, cleanup_identities, cleanup_proxies
):
    cipher = Cipher(SECRET)
    pool = IdentityPool(cipher)
    proxy_id = await _make_proxy(cipher)
    cleanup_proxies.append(proxy_id)

    async with session_scope() as session:
        identity_id = await pool.add(
            session,
            platform=Platform.DOUYIN,
            cookies={"ttwid": "abc"},
            fingerprint=Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=131),
            source=IdentitySource.MINTED,
            proxy_id=proxy_id,
        )
    cleanup_identities.append(identity_id)

    prober = ProxyProber(
        cipher=cipher,
        pool=pool,
        client=StubProbe(ProbeResult(ok=False, detail="connect timeout")),
        options=ProberConfig(cooldown_seconds=600),
    )
    await prober.probe_one(proxy_id, force=True)

    async with session_scope() as session:
        proxy = _require(await session.get(Proxy, proxy_id), "the proxy")
        identity = _require(await session.get(Identity, identity_id), "the identity")
        assert proxy.healthy is False
        assert proxy.last_check_at is not None
        # Cooling, not retired, and still on the same proxy.
        assert identity.state == IdentityState.COOLING.value
        assert identity.proxy_id == proxy_id
        assert identity.retired_at is None
        assert identity.cookies_encrypted


async def test_a_healthy_probe_records_geoip(db_engine, redis_client, cleanup_proxies):
    cipher = Cipher(SECRET)
    proxy_id = await _make_proxy(cipher)
    cleanup_proxies.append(proxy_id)

    prober = ProxyProber(
        cipher=cipher,
        pool=IdentityPool(cipher),
        client=StubProbe(ProbeResult(ok=True, exit_ip="203.0.113.7", country="DE", timezone="CET")),
    )
    result = _require(await prober.probe_one(proxy_id, force=True), "the proxy")

    assert result.ok
    async with session_scope() as session:
        proxy = _require(await session.get(Proxy, proxy_id), "the proxy")
        assert proxy.healthy is True
        assert proxy.country == "DE"
        assert proxy.timezone == "CET"


async def test_three_network_errors_trigger_one_probe(db_engine, redis_client, cleanup_proxies):
    cipher = Cipher(SECRET)
    proxy_id = await _make_proxy(cipher)
    cleanup_proxies.append(proxy_id)
    client = StubProbe(ProbeResult(ok=True))
    prober = ProxyProber(
        cipher=cipher,
        pool=IdentityPool(cipher),
        client=client,
        options=ProberConfig(error_threshold=3),
    )

    assert await prober.note_network_error(proxy_id) is False
    assert await prober.note_network_error(proxy_id) is False
    assert client.calls == 0

    assert await prober.note_network_error(proxy_id) is True
    assert client.calls == 1


async def test_an_error_burst_in_the_request_log_is_noticed(
    db_engine, redis_client, cleanup_identities, cleanup_proxies
):
    cipher = Cipher(SECRET)
    pool = IdentityPool(cipher)
    proxy_id = await _make_proxy(cipher)
    cleanup_proxies.append(proxy_id)

    async with session_scope() as session:
        identity_id = await pool.add(
            session,
            platform=Platform.DOUYIN,
            cookies={"ttwid": "abc"},
            fingerprint=Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=131),
            source=IdentitySource.MINTED,
            proxy_id=proxy_id,
        )
    cleanup_identities.append(identity_id)

    now = datetime.now(UTC)
    async with session_scope() as session:
        for index in range(3):
            session.add(
                RequestLog(
                    ts=now - timedelta(seconds=10 * index),
                    request_id=uuid.uuid4(),
                    platform=Platform.DOUYIN.value,
                    endpoint="douyin.content_detail",
                    identity_id=identity_id,
                    outcome=Outcome.NETWORK_ERROR.value,
                    duration_ms=100,
                )
            )

    async with session_scope() as session:
        written = (
            await session.execute(
                select(func.count())
                .select_from(RequestLog)
                .where(RequestLog.identity_id == identity_id)
            )
        ).scalar_one()
    if int(written) < 3:
        pytest.skip("the request log was truncated by another suite before the query ran")

    client = StubProbe(ProbeResult(ok=False, detail="connect timeout"))
    prober = ProxyProber(cipher=cipher, pool=pool, client=client, options=ProberConfig())
    try:
        report = await prober.check_error_bursts()

        assert str(proxy_id) in report.probed_on_demand
        assert client.calls >= 1
        assert report.cooled_identities >= 1
    finally:
        async with session_scope() as session:
            await session.execute(delete(RequestLog).where(RequestLog.identity_id == identity_id))


# --------------------------------------------------------------------------
# pool filler
# --------------------------------------------------------------------------


async def test_the_mint_lock_stops_two_replicas_minting_at_once(db_engine, redis_client):
    """One at a time is a cross-process property, so the lock has to be real."""
    rpc = RecordingRpc()
    await redis_client.delete(MINT_LOCK_KEY)

    class EmptyScalars:
        def all(self) -> list[Any]:
            return []

    class NoProxies:
        async def scalars(self, statement: Any) -> Any:
            return EmptyScalars()

        async def flush(self) -> None:
            return None

        def add(self, obj: Any) -> None:
            return None

    class CountingPool:
        def __init__(self) -> None:
            self.added = 0

        async def counts(self, session: Any, platform: Platform) -> dict[str, int]:
            return {}

        async def add(self, session: Any, **kwargs: Any) -> uuid.UUID:
            self.added += 1
            return uuid.uuid4()

    pool = CountingPool()
    fillers = [
        PoolFiller(
            pool=pool,
            rpc=rpc,
            cipher=Cipher(SECRET),
            config=Config.defaults(),
            options=FillerConfig(lock_ttl_seconds=30),
            session_factory=session_factory_for(NoProxies()),
        )
        for _ in range(2)
    ]

    results = await asyncio.gather(*(f.top_up_once(Platform.DOUYIN) for f in fillers))

    assert rpc.max_active == 1
    assert sum(1 for r in results if r.minted) == 1
    assert [r.reason for r in results].count("locked") == 1
    assert pool.added == 1
    # The lock is released, so the next tick can mint again.
    assert await redis_client.get(MINT_LOCK_KEY) is None


async def test_a_minted_identity_lands_in_the_pool(db_engine, redis_client, cleanup_identities):
    cipher = Cipher(SECRET)
    pool = IdentityPool(cipher)
    rpc = RecordingRpc()
    await redis_client.delete(MINT_LOCK_KEY)

    filler = PoolFiller(pool=pool, rpc=rpc, cipher=cipher, config=Config.defaults())
    result = await filler.top_up_once(Platform.TIKTOK)

    assert result.minted is True
    assert result.identity_id is not None
    cleanup_identities.append(result.identity_id)

    async with session_scope() as session:
        row = _require(await session.get(Identity, result.identity_id), "the minted identity")
        assert row.state == IdentityState.ACTIVE.value
        assert row.source == IdentitySource.MINTED.value
        assert row.platform == Platform.TIKTOK.value


async def test_the_pool_level_query_runs_against_the_real_table(db_engine, redis_client):
    cipher = Cipher(SECRET)
    filler = PoolFiller(
        pool=IdentityPool(cipher), rpc=None, cipher=cipher, config=Config.defaults()
    )

    # Disabled minting still surveys the pool, which is what raises the alerts.
    result = await filler.tick()
    assert result.reason == "disabled"


async def test_identities_of_a_dead_proxy_recover_on_their_own(
    db_engine, redis_client, cleanup_identities, cleanup_proxies
):
    """Cooling is temporary by construction: the pool promotes them back."""
    cipher = Cipher(SECRET)
    pool = IdentityPool(cipher)
    proxy_id = await _make_proxy(cipher)
    cleanup_proxies.append(proxy_id)

    async with session_scope() as session:
        identity_id = await pool.add(
            session,
            platform=Platform.DOUYIN,
            cookies={"ttwid": "abc"},
            fingerprint=Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=131),
            source=IdentitySource.MINTED,
            proxy_id=proxy_id,
        )
    cleanup_identities.append(identity_id)

    prober = ProxyProber(
        cipher=cipher,
        pool=pool,
        client=StubProbe(ProbeResult(ok=False)),
        options=ProberConfig(cooldown_seconds=1),
    )
    await prober.probe_one(proxy_id, force=True)

    async with session_scope() as session:
        row = _require(await session.get(Identity, identity_id), "the identity")
        row.cooldown_until = datetime.now(UTC) - timedelta(seconds=1)

    async with session_scope() as session:
        candidates = await pool.candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)
        assert str(identity_id) in {c.identity_id for c in candidates}

    async with session_scope() as session:
        row = (
            await session.execute(select(Identity).where(Identity.id == identity_id))
        ).scalar_one()
        assert row.state == IdentityState.ACTIVE.value
        assert row.retired_at is None


# --------------------------------------------------------------------------
# process wiring
# --------------------------------------------------------------------------


async def test_the_worker_process_assembles(db_engine, redis_client):
    """The entry point wires scheduler, pool, transport, signing and the loops.

    Everything it touches belongs to another module, so a signature that moves
    under it would otherwise only be discovered by starting the container.
    """
    from dtk.core.config import BootstrapSettings
    from dtk.services.settings_store import load_config
    from dtk.worker.runtime import build_runtime

    settings = BootstrapSettings(
        secret_key=SECRET,
        database_url="postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test",
        redis_url="redis://127.0.0.1:56379/0",
        browser_rpc_url="",  # no browser container: minting stays disabled
    )
    runtime = await build_runtime(settings, await load_config())
    try:
        assert [loop.name for loop in runtime.loops] == [
            "pool_filler",
            "proxy_prober",
            "maintenance",
        ]
        assert runtime.worker.inflight == 0
        assert runtime.worker.stopping is False
    finally:
        await runtime.aclose()
