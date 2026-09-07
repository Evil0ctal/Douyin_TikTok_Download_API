"""Liveness, readiness and the detailed system status.

Three checks with three different jobs (docs/design/15-operations.md):

``liveness()``
    Answers one question: is this process wedged? It touches nothing outside
    the interpreter. Wiring a dependency into it produces a well known outage
    shape - the database hiccups, every liveness probe fails at once, the
    orchestrator restarts every API container simultaneously, and the restart
    storm keeps the database from recovering. A process that cannot reach its
    database is not dead; it is degraded, and that is what readiness is for.

``readiness()``
    Answers: should a load balancer send traffic here? PostgreSQL and Redis are
    required. browser-rpc is not: with no minting the pool falls back to
    imported cookies and the native signer still runs, so an unreachable RPC
    service must never take the instance out of rotation.

``system_status()``
    The authenticated detail view. It reports the Chromium major reported by
    browser-rpc next to the wreq emulation profile major, side by side, so the
    version drift described in docs/design/04-transport-signing.md stays visible
    instead of being discovered from a rising risk-control rate weeks later.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from dtk import __version__
from dtk.core.db import get_engine
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import BrowserFamily, IdentityState, Platform
from dtk.db.models import HYPERTABLES, TABLE_NAMES
from dtk.identity.minting.client import BrowserRpcClient, RpcHealth
from dtk.ops._sql import safe_execute, safe_scalar
from dtk.transport.emulation import DriftBand, emulation_drift, known_majors

log = get_logger(__name__)

#: Build metadata is injected by the image build; absent outside a container.
COMMIT_ENV_VARS: tuple[str, ...] = ("DTK_COMMIT", "DTK_GIT_COMMIT", "GIT_COMMIT")

#: Short commit hashes are displayed, not full ones.
COMMIT_LENGTH = 12

#: A dependency that has not answered within this many seconds is unreachable
#: as far as a readiness probe is concerned. Kept well below the usual probe
#: interval so a slow check cannot pile up behind itself. Enforced by every
#: probe below: a wedged dependency that never answers is exactly the case a
#: readiness probe exists for, and waiting on it forever is the one answer an
#: orchestrator cannot use.
PROBE_TIMEOUT_SECONDS = 3.0

POSTGRES = "postgres"
REDIS = "redis"
BROWSER_RPC = "browser_rpc"

#: Component names a readiness verdict depends on. browser_rpc is deliberately
#: absent; see the module docstring.
REQUIRED_COMPONENTS: frozenset[str] = frozenset({POSTGRES, REDIS})

_STARTED_MONOTONIC = time.monotonic()
_STARTED_AT = datetime.now(UTC)


def uptime_seconds() -> int:
    """Seconds since this process started, from a monotonic clock."""
    return int(time.monotonic() - _STARTED_MONOTONIC)


def started_at() -> datetime:
    """Wall-clock start time, for display beside the uptime."""
    return _STARTED_AT


def commit() -> str | None:
    """Build commit, or None when the process was not built from an image."""
    for name in COMMIT_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value[:COMMIT_LENGTH]
    return None


def wreq_profile_major(family: BrowserFamily = BrowserFamily.CHROME) -> int | None:
    """Newest emulation profile major wreq offers for a browser family.

    None when wreq exposes no profile for the family at all, which is a broken
    install rather than a condition to paper over with a plausible number.
    """
    try:
        majors = known_majors(family)
    except Exception as exc:  # pragma: no cover - depends on the wreq build
        log.warning("ops.health.emulation_unavailable", family=family.value, error=str(exc))
        return None
    return max(majors) if majors else None


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """One dependency probe."""

    name: str
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": self.ok, "latency_ms": self.latency_ms}
        if self.detail is not None:
            body["detail"] = self.detail
        body.update(self.extra)
        return body


@dataclass(frozen=True, slots=True)
class LivenessReport:
    """The answer to "is this process wedged"."""

    status: str
    version: str
    uptime_seconds: int
    pid: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
            "pid": self.pid,
        }


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Whether this instance should receive traffic."""

    ready: bool
    components: tuple[ComponentHealth, ...]

    def component(self, name: str) -> ComponentHealth | None:
        return next((c for c in self.components if c.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "components": {c.name: c.as_dict() for c in self.components},
        }


@dataclass(frozen=True, slots=True)
class StorageStats:
    """Disk usage, so a user can size retention against their actual disk."""

    db_size_bytes: int | None = None
    request_log_rows: int | None = None
    table_bytes: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_size_bytes": self.db_size_bytes,
            "request_log_rows": self.request_log_rows,
            "table_bytes": dict(self.table_bytes),
        }


@dataclass(frozen=True, slots=True)
class StatusReport:
    """Everything ``GET /api/v1/system/status`` returns."""

    version: str
    commit: str | None
    uptime_seconds: int
    started_at: datetime
    components: tuple[ComponentHealth, ...]
    pool: dict[str, int]
    pool_by_platform: dict[str, dict[str, int]]
    storage: StorageStats

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "commit": self.commit,
            "uptime_seconds": self.uptime_seconds,
            "started_at": self.started_at.isoformat(),
            "components": {c.name: c.as_dict() for c in self.components},
            "pool": dict(self.pool),
            "pool_by_platform": {k: dict(v) for k, v in self.pool_by_platform.items()},
            "storage": self.storage.as_dict(),
        }


def liveness() -> LivenessReport:
    """Process-local liveness. Performs no IO of any kind.

    Deliberately not ``async``-dependent on anything: no database, no Redis, no
    filesystem. See the module docstring for why that constraint is the whole
    point of this function.
    """
    return LivenessReport(
        status="alive",
        version=__version__,
        uptime_seconds=uptime_seconds(),
        pid=os.getpid(),
    )


async def _select_one(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_postgres(
    engine: AsyncEngine | None = None, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> ComponentHealth:
    """Round-trip a trivial query on a pooled connection, under a deadline."""
    started = time.perf_counter()
    try:
        target = engine or get_engine()
        await asyncio.wait_for(_select_one(target), timeout)
    except TimeoutError:
        return ComponentHealth(POSTGRES, ok=False, detail=_timed_out(timeout))
    except Exception as exc:
        return ComponentHealth(POSTGRES, ok=False, detail=str(exc)[:200])
    return ComponentHealth(POSTGRES, ok=True, latency_ms=_elapsed_ms(started))


async def check_redis(
    client: Redis | None = None, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> ComponentHealth:
    """PING the shared client, under a deadline."""
    started = time.perf_counter()
    try:
        target = client or get_redis()
        await asyncio.wait_for(target.ping(), timeout)
    except TimeoutError:
        return ComponentHealth(REDIS, ok=False, detail=_timed_out(timeout))
    except Exception as exc:
        return ComponentHealth(REDIS, ok=False, detail=str(exc)[:200])
    return ComponentHealth(REDIS, ok=True, latency_ms=_elapsed_ms(started))


async def check_browser_rpc(
    client: BrowserRpcClient | None = None, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> ComponentHealth:
    """Probe browser-rpc and report the version pair that may have drifted.

    Never required for readiness. ``chromium_major`` and ``wreq_profile_major``
    are reported together even when the RPC service is down, because the
    emulation side of the pair is known locally and is still worth showing.
    """
    profile_major = wreq_profile_major()
    extra: dict[str, Any] = {
        "configured": bool(client is not None and client.configured),
        "warm_contexts": None,
        "chromium_major": None,
        "wreq_profile_major": profile_major,
        "version_drift": None,
    }
    if client is None or not client.configured:
        return ComponentHealth(BROWSER_RPC, ok=False, detail="not configured", extra=extra)

    started = time.perf_counter()
    try:
        health: RpcHealth = await asyncio.wait_for(client.health(), timeout)
    except TimeoutError:
        return ComponentHealth(BROWSER_RPC, ok=False, detail=_timed_out(timeout), extra=extra)
    except Exception as exc:
        return ComponentHealth(BROWSER_RPC, ok=False, detail=str(exc)[:200], extra=extra)

    extra["warm_contexts"] = health.warm_contexts if health.available else None
    extra["chromium_major"] = health.chromium_major
    extra["backend_version"] = health.backend_version
    if health.chromium_major is not None and profile_major is not None:
        extra["version_drift"] = _drift_band(health.chromium_major, profile_major).value
    return ComponentHealth(
        BROWSER_RPC,
        ok=health.available,
        latency_ms=_elapsed_ms(started),
        detail=health.detail or None,
        extra=extra,
    )


async def readiness(
    *,
    engine: AsyncEngine | None = None,
    redis: Redis | None = None,
    rpc: BrowserRpcClient | None = None,
) -> ReadinessReport:
    """Check the dependencies that serving traffic actually requires."""
    components = [await check_postgres(engine), await check_redis(redis)]
    if rpc is not None:
        components.append(await check_browser_rpc(rpc))
    ready = all(c.ok for c in components if c.name in REQUIRED_COMPONENTS)
    return ReadinessReport(ready=ready, components=tuple(components))


async def pool_counts(session: AsyncSession) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Identity counts by state: totals, and the same split per platform."""
    zeroed = {state.value: 0 for state in IdentityState}
    totals = dict(zeroed)
    per_platform = {p.value: dict(zeroed) for p in Platform}
    rows = await safe_execute(
        session,
        "SELECT platform, state, count(*) FROM identities GROUP BY platform, state",
        event="ops.health.pool_counts_failed",
    )
    if rows is None:
        return totals, per_platform
    for platform, state, count in rows:
        totals[state] = totals.get(state, 0) + int(count)
        bucket = per_platform.setdefault(str(platform), dict(zeroed))
        bucket[state] = bucket.get(state, 0) + int(count)
    return totals, per_platform


async def storage_stats(session: AsyncSession) -> StorageStats:
    """Database size, request-log row count and per-table disk usage."""
    return StorageStats(
        db_size_bytes=await _scalar(session, "SELECT pg_database_size(current_database())"),
        request_log_rows=await _request_log_rows(session),
        table_bytes=await table_sizes(session),
    )


async def table_sizes(session: AsyncSession) -> dict[str, int]:
    """Bytes on disk per table, hypertable chunks included.

    ``pg_total_relation_size`` on a hypertable parent reports almost nothing:
    the rows live in chunk tables in another schema. TimescaleDB's
    ``hypertable_size`` is asked for those three and overwrites the plain
    number, so the settings page shows what the table really costs.
    """
    sizes: dict[str, int] = {}
    values = ", ".join(f"('{name}')" for name in TABLE_NAMES)
    rows = await safe_execute(
        session,
        "SELECT t.name, pg_total_relation_size(t.name::regclass) "
        f"FROM (VALUES {values}) AS t(name)",
        event="ops.health.table_sizes_failed",
    )
    for name, size in rows or []:
        if size is not None:
            sizes[str(name)] = int(size)

    for name in HYPERTABLES:
        measured = await _scalar(session, f"SELECT hypertable_size('{name}')")
        if measured is not None:
            sizes[name] = measured
    return sizes


async def system_status(
    session: AsyncSession,
    *,
    engine: AsyncEngine | None = None,
    redis: Redis | None = None,
    rpc: BrowserRpcClient | None = None,
) -> StatusReport:
    """Assemble the authenticated status document."""
    components = (
        await check_postgres(engine),
        await check_redis(redis),
        await check_browser_rpc(rpc),
    )
    totals, per_platform = await pool_counts(session)
    return StatusReport(
        version=__version__,
        commit=commit(),
        uptime_seconds=uptime_seconds(),
        started_at=started_at(),
        components=components,
        pool=totals,
        pool_by_platform=per_platform,
        storage=await storage_stats(session),
    )


def _drift_band(chromium_major: int, profile_major: int) -> DriftBand:
    return emulation_drift(chromium_major, profile_major)


def _timed_out(timeout: float) -> str:
    return f"no answer within {timeout:g}s"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


async def _scalar(session: AsyncSession, statement: str) -> int | None:
    value = await safe_scalar(session, statement, event="ops.health.scalar_failed")
    return None if value is None else int(value)


async def _request_log_rows(session: AsyncSession) -> int | None:
    """Row count of the hypertable, approximate first.

    An exact ``count(*)`` over a fortnight of request logs reads every chunk.
    That is far too expensive for a status endpoint a monitor polls, so the
    TimescaleDB estimate is preferred and the exact count is only the fallback
    for an instance where the extension is missing.
    """
    approximate = await _scalar(session, "SELECT approximate_row_count('request_log')")
    if approximate is not None:
        return approximate
    return await _scalar(session, "SELECT count(*) FROM request_log")


__all__ = [
    "BROWSER_RPC",
    "COMMIT_ENV_VARS",
    "POSTGRES",
    "PROBE_TIMEOUT_SECONDS",
    "REDIS",
    "REQUIRED_COMPONENTS",
    "ComponentHealth",
    "LivenessReport",
    "ReadinessReport",
    "StatusReport",
    "StorageStats",
    "check_browser_rpc",
    "check_postgres",
    "check_redis",
    "commit",
    "liveness",
    "pool_counts",
    "readiness",
    "started_at",
    "storage_stats",
    "system_status",
    "table_sizes",
    "uptime_seconds",
    "wreq_profile_major",
]
