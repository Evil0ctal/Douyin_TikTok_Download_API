"""Liveness, readiness and the detailed status page.

``/healthz`` and ``/readyz`` are separate for one specific reason (doc 15): a
liveness probe that touches the database turns a brief database hiccup into an
orchestrator restarting every api container at once, and the restart storm is
what keeps the database down. Liveness answers "is this process wedged"; it
reads no dependency, holds no lock and allocates nothing.

Both live outside ``/api/v1``: they are infrastructure endpoints polled by an
orchestrator or a load balancer, and they answer with a flat object rather than
the API envelope so a probe can match on one field. ``/api/v1/system/status``
is the API-shaped view and does go through the envelope.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Coroutine
from typing import Any, Final

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from dtk import __version__
from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import authenticated, ok
from dtk.core.db import session_scope
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import IdentityState, Platform
from dtk.db.models import Identity
from dtk.db.repositories import IdentityRepository

log = get_logger(__name__)

router = APIRouter(tags=["system"])

#: Process start, captured at import. Uptime is a diagnostic, so a monotonic
#: reading is the right source: a clock adjustment must not make it jump.
_STARTED_MONOTONIC = time.monotonic()

#: A dependency check that takes longer than this is a failed check. The probe
#: has its own timeout and a hung check would burn it.
PROBE_TIMEOUT_SECONDS = 3.0

#: Set by the build so a bug report can name the exact tree.
COMMIT_ENV_VAR = "DTK_COMMIT"


def uptime_seconds() -> int:
    return int(time.monotonic() - _STARTED_MONOTONIC)


async def _probe(name: str, check: Coroutine[Any, Any, Any]) -> dict[str, Any]:
    """Run one dependency check under a deadline.

    A hung dependency must not hold the probe open: an orchestrator that never
    gets an answer treats the container as failed anyway, and it gets there
    faster with a bounded check.
    """
    started = time.monotonic()
    try:
        await asyncio.wait_for(check, timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        log.warning("system.probe_timeout", component=name)
        return {"ok": False, "latency_ms": None, "error": "timeout"}
    except Exception as exc:
        log.warning("system.probe_failed", component=name, error=str(exc)[:200])
        return {"ok": False, "latency_ms": None, "error": type(exc).__name__}
    return {"ok": True, "latency_ms": int((time.monotonic() - started) * 1000)}


async def _select_one() -> None:
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))


async def _probe_postgres() -> dict[str, Any]:
    return await _probe("postgres", _select_one())


async def _probe_redis() -> dict[str, Any]:
    return await _probe("redis", get_redis().ping())


@router.get("/healthz", summary="Liveness", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Process liveness. Touches no dependency, on purpose (doc 15).

    The request still passes through the session middleware, but a session that
    issues no statement never checks a connection out of the pool, so a
    database outage cannot fail this probe.
    """
    return JSONResponse({"status": "ok", "uptime_seconds": uptime_seconds()})


@router.get("/readyz", summary="Readiness", include_in_schema=False)
async def readyz() -> JSONResponse:
    """Readiness: can this process serve a request right now?

    The browser RPC is excluded deliberately - minting is not on the request
    path, so an instance without it is degraded, not unready.
    """
    postgres = await _probe_postgres()
    redis = await _probe_redis()
    ready = bool(postgres["ok"] and redis["ok"])
    return JSONResponse(
        {
            "status": "ok" if ready else "unavailable",
            "components": {"postgres": postgres, "redis": redis},
        },
        status_code=200 if ready else 503,
    )


@router.get(
    "/api/v1/system/status",
    tags=["system"],
    summary="Instance status",
    openapi_extra={I18N_KEY: "health"},
)
async def system_status(request: Request, principal: Principal = Depends(authenticated)) -> Any:
    """Version, component health, pool census and storage use.

    Authenticated: it names component versions and row counts, which is more
    than an unauthenticated probe has any reason to learn.
    """
    postgres = await _probe_postgres()
    redis = await _probe_redis()

    session = request.state.db
    per_platform: dict[str, dict[str, int]] = {}
    for platform in Platform:
        counts = await IdentityRepository(session).count_by_state(platform=platform)
        per_platform[platform.value] = {
            state.value: counts.get(state.value, 0) for state in IdentityState
        }
    pool: dict[str, Any] = dict(per_platform)
    pool["total_active"] = sum(
        counts[IdentityState.ACTIVE.value] for counts in per_platform.values()
    )

    return ok(
        request,
        {
            "version": __version__,
            "commit": os.environ.get(COMMIT_ENV_VAR),
            "uptime_seconds": uptime_seconds(),
            "settings_version": request.app.state.config.version,
            "components": {
                "postgres": postgres,
                "redis": redis,
                "browser_rpc": await _browser_rpc_status(request),
            },
            "pool": pool,
            "storage": await _storage(session),
        },
    )


#: Ceiling for the browser probe below. Short on purpose: the page polls, and a
#: browser that cannot answer a health check in a second is not one the pool
#: should be told is fine.
BROWSER_PROBE_TIMEOUT_SECONDS: Final = 1.0


async def _browser_rpc_status(request: Request) -> dict[str, Any]:
    """Configured, and answering.

    An earlier version reported only whether a URL was set, on the reasoning
    that probing would put a multi-second dependency behind a polled page. The
    reasoning was right and the conclusion was not: the System page then said
    "unknown" for a browser that was running perfectly, which is the one
    question an operator opens that row to answer. /rpc/health is a local call
    that returns in milliseconds, so it is bounded rather than skipped, and a
    timeout is reported as unhealthy rather than as unknown.
    """
    url = str(getattr(request.app.state.settings, "browser_rpc_url", "") or "")
    if not url:
        return {"configured": False, "ok": None}

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=BROWSER_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{url.rstrip('/')}/rpc/health")
            body = response.json() if response.is_success else {}
    except Exception as exc:
        # Named by type only: the message quotes the URL, which is internal but
        # is still not something a status row needs to carry.
        log.warning("system.browser_rpc.unreachable", error=type(exc).__name__)
        return {"configured": True, "ok": False, "detail_code": "unreachable"}

    status = str(body.get("status") or "")
    return {
        "configured": True,
        "ok": status == "ok",
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "chromium_major": body.get("chromium_major"),
        "warm_contexts": body.get("warm_contexts"),
        "detail_code": None if status == "ok" else "degraded",
    }


async def _storage(session: Any) -> dict[str, Any]:
    """Disk use and row counts, estimated rather than counted.

    ``count(*)`` on the request log is a full scan of the largest table in the
    system; the planner's estimate is accurate enough to size a retention
    policy and costs nothing.
    """
    try:
        size = (
            await session.execute(text("SELECT pg_database_size(current_database())"))
        ).scalar_one()
        estimates = (
            await session.execute(
                text(
                    "SELECT relname, GREATEST(reltuples, 0)::bigint AS rows "
                    "FROM pg_class WHERE relname = ANY(:names)"
                ),
                {"names": ["request_log", "identity_events", "content_snapshots", "tasks"]},
            )
        ).all()
        identities = (
            await session.execute(select(func.count()).select_from(Identity))
        ).scalar_one()
    except Exception as exc:
        log.warning("system.storage_unavailable", error=str(exc)[:200])
        return {"db_size_bytes": None, "rows": {}}
    return {
        "db_size_bytes": int(size),
        "rows": {name: int(rows) for name, rows in estimates},
        "identities": int(identities),
    }


__all__ = ["COMMIT_ENV_VAR", "PROBE_TIMEOUT_SECONDS", "router", "uptime_seconds"]
