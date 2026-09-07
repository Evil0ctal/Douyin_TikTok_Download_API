"""Health checks.

The load-bearing test in this file is the first one: liveness must not touch
the database. Doc 15 explains the outage it prevents - a database blip failing
every liveness probe at once, the orchestrator restarting every API container,
and the restart storm keeping the database down. The test therefore makes any
database access explode rather than merely asserting on the returned payload.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dtk import __version__
from dtk.core.types import IdentityState
from dtk.identity.minting.client import RpcHealth
from dtk.ops import health


class Exploding:
    """Any attribute access is a test failure."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"liveness touched a dependency: {name}")


class _AsyncCtx:
    def __init__(self, value: Any, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    async def __aenter__(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._value

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FakeConnection:
    async def execute(self, _statement: Any, _params: Any = None) -> None:
        return None


class FakeEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def connect(self) -> _AsyncCtx:
        return _AsyncCtx(FakeConnection(), self._error)


class FakeRedis:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def ping(self) -> bool:
        if self._error is not None:
            raise self._error
        return True


class FakeRpc:
    def __init__(self, health_result: RpcHealth, configured: bool = True) -> None:
        self._health = health_result
        self.configured = configured

    async def health(self) -> RpcHealth:
        return self._health


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.returns_rows = True

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeSession:
    """Answers by matching a fragment of the statement text."""

    def __init__(self, answers: dict[str, list[tuple[Any, ...]]]) -> None:
        self._answers = answers
        self.statements: list[str] = []

    def begin_nested(self) -> _AsyncCtx:
        return _AsyncCtx(None)

    async def execute(self, statement: Any, _params: Any = None) -> FakeResult:
        rendered = str(statement)
        self.statements.append(rendered)
        for fragment, rows in self._answers.items():
            if fragment in rendered:
                return FakeResult(rows)
        raise RuntimeError("no answer configured for this statement")


# --------------------------------------------------------------------------
# liveness
# --------------------------------------------------------------------------


def test_liveness_never_touches_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "get_engine", Exploding().__getattr__)
    monkeypatch.setattr(health, "get_redis", Exploding().__getattr__)

    report = health.liveness()

    assert report.status == "alive"
    assert report.version == __version__
    assert report.uptime_seconds >= 0
    assert report.pid > 0


def test_liveness_is_synchronous_and_has_no_dependencies() -> None:
    """A coroutine here would let a future edit await a query into it."""
    import inspect

    assert not inspect.iscoroutinefunction(health.liveness)
    assert inspect.signature(health.liveness).parameters == {}


async def test_readiness_fails_while_liveness_still_passes() -> None:
    """The whole point of splitting the two probes."""
    report = await health.readiness(
        engine=FakeEngine(RuntimeError("connection refused")), redis=FakeRedis()
    )

    assert report.ready is False
    postgres = report.component(health.POSTGRES)
    assert postgres is not None and postgres.ok is False
    assert "connection refused" in (postgres.detail or "")
    assert health.liveness().status == "alive"


async def test_readiness_ignores_browser_rpc() -> None:
    """browser-rpc down must not take the instance out of rotation."""
    rpc = FakeRpc(RpcHealth(available=False, detail="connection refused"))

    report = await health.readiness(engine=FakeEngine(), redis=FakeRedis(), rpc=rpc)

    assert report.ready is True
    browser = report.component(health.BROWSER_RPC)
    assert browser is not None and browser.ok is False


async def test_readiness_requires_redis() -> None:
    report = await health.readiness(engine=FakeEngine(), redis=FakeRedis(RuntimeError("no redis")))
    assert report.ready is False


# --------------------------------------------------------------------------
# component probes
# --------------------------------------------------------------------------


async def test_postgres_probe_reports_latency() -> None:
    component = await health.check_postgres(FakeEngine())
    assert component.ok is True
    assert component.latency_ms is not None and component.latency_ms >= 0


async def test_browser_rpc_reports_both_majors_side_by_side() -> None:
    """Doc 04's version drift has to be visible without doing arithmetic."""
    rpc = FakeRpc(RpcHealth(available=True, warm_contexts=2, chromium_major=149))

    component = await health.check_browser_rpc(rpc)

    assert component.ok is True
    assert component.extra["chromium_major"] == 149
    assert component.extra["wreq_profile_major"] == health.wreq_profile_major()
    assert component.extra["version_drift"] in {"ok", "warn", "fail"}
    assert component.extra["warm_contexts"] == 2


async def test_browser_rpc_still_reports_the_local_profile_when_down() -> None:
    component = await health.check_browser_rpc(None)

    assert component.ok is False
    assert component.detail == "not configured"
    assert component.extra["chromium_major"] is None
    assert component.extra["wreq_profile_major"] == health.wreq_profile_major()


async def test_browser_rpc_drift_is_graded() -> None:
    far_behind = FakeRpc(RpcHealth(available=True, chromium_major=200))
    component = await health.check_browser_rpc(far_behind)
    assert component.extra["version_drift"] == "fail"


def test_commit_is_none_outside_a_build(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in health.COMMIT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert health.commit() is None

    monkeypatch.setenv("DTK_COMMIT", "abc1234def5678901")
    assert health.commit() == "abc1234def56"


class HangingRedis:
    """Accepts the PING and never answers, like a wedged server."""

    async def ping(self) -> bool:
        await asyncio.sleep(3600)
        return True


class HangingEngine:
    def connect(self) -> Any:
        class _Never:
            async def __aenter__(self) -> Any:
                await asyncio.sleep(3600)
                return FakeConnection()

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Never()


async def test_a_wedged_dependency_fails_the_probe_instead_of_hanging() -> None:
    """PROBE_TIMEOUT_SECONDS has to be enforced, not merely declared.

    A dependency that accepts the connection and never answers is the exact
    case readiness exists for; waiting on it forever gives the orchestrator the
    one answer it cannot act on.
    """
    redis = await health.check_redis(HangingRedis(), timeout=0.05)  # type: ignore[arg-type]
    postgres = await health.check_postgres(HangingEngine(), timeout=0.05)  # type: ignore[arg-type]

    assert redis.ok is False
    assert "no answer" in (redis.detail or "")
    assert postgres.ok is False
    assert "no answer" in (postgres.detail or "")


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


async def test_pool_counts_split_by_platform() -> None:
    session = FakeSession(
        {
            "FROM identities": [
                ("douyin", IdentityState.ACTIVE.value, 12),
                ("douyin", IdentityState.COOLING.value, 3),
                ("tiktok", IdentityState.ACTIVE.value, 4),
                ("tiktok", IdentityState.DEGRADED.value, 1),
            ]
        }
    )

    totals, per_platform = await health.pool_counts(session)  # type: ignore[arg-type]

    assert totals[IdentityState.ACTIVE.value] == 16
    assert totals[IdentityState.COOLING.value] == 3
    assert totals[IdentityState.DEGRADED.value] == 1
    assert per_platform["douyin"][IdentityState.ACTIVE.value] == 12
    assert per_platform["tiktok"][IdentityState.ACTIVE.value] == 4


async def test_pool_counts_degrade_when_the_query_fails() -> None:
    class Broken(FakeSession):
        async def execute(self, statement: Any, _params: Any = None) -> FakeResult:
            raise RuntimeError("relation does not exist")

    totals, per_platform = await health.pool_counts(Broken({}))  # type: ignore[arg-type]

    assert totals[IdentityState.ACTIVE.value] == 0
    assert set(per_platform) == {"douyin", "tiktok"}


async def test_system_status_has_the_documented_shape() -> None:
    session = FakeSession(
        {
            "FROM identities": [("douyin", IdentityState.ACTIVE.value, 12)],
            "pg_database_size": [(1234567890,)],
            "approximate_row_count": [(892341,)],
            "pg_total_relation_size": [("users", 8192)],
            "hypertable_size": [(4096,)],
        }
    )

    report = await health.system_status(
        session,  # type: ignore[arg-type]
        engine=FakeEngine(),
        redis=FakeRedis(),
        rpc=FakeRpc(RpcHealth(available=True, warm_contexts=2, chromium_major=149)),
    )
    body = report.as_dict()

    assert body["version"] == __version__
    assert body["uptime_seconds"] >= 0
    assert set(body["components"]) == {"postgres", "redis", "browser_rpc"}
    assert body["pool"][IdentityState.ACTIVE.value] == 12
    assert body["storage"]["db_size_bytes"] == 1234567890
    assert body["storage"]["request_log_rows"] == 892341
    assert body["storage"]["table_bytes"]["users"] == 8192


async def test_request_log_rows_falls_back_to_an_exact_count() -> None:
    """An instance without TimescaleDB still gets a number."""

    class NoApproximate(FakeSession):
        async def execute(self, statement: Any, _params: Any = None) -> FakeResult:
            rendered = str(statement)
            if "approximate_row_count" in rendered:
                raise RuntimeError("function approximate_row_count does not exist")
            if "count(*)" in rendered:
                return FakeResult([(17,)])
            raise RuntimeError("unexpected statement")

    assert await health._request_log_rows(NoApproximate({})) == 17  # type: ignore[arg-type]
