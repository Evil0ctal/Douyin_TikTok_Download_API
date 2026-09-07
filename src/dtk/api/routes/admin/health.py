"""Endpoint health board and the console's chart data.

Two different questions, two different sources:

* "is this endpoint usable right now" is answered from the scheduler's rolling
  window in Redis, because that is what the circuit breaker itself decides on;
* "what did the last day look like" is answered from ``request_log``, the one
  table every derived number in this system comes from (doc 05).

The time series is bucketed with plain SQL rather than ``time_bucket``. The
query then runs on a stock PostgreSQL as well as on TimescaleDB, which matters
because the console must not go blank on an instance where the extension is
missing. The optional filters are cast explicitly: a parameter that appears
only as ``$1 IS NULL OR col = $1`` gives the driver nothing to infer a type
from, and asyncpg refuses to guess.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from dtk.api.deps import Principal
from dtk.api.routes.support import language, ok, read_admin
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.platforms import get_adapter
from dtk.platforms.registry import available_platforms
from dtk.scheduler import circuit
from dtk.scheduler.policies import policy_for

log = get_logger(__name__)

router = APIRouter(tags=["admin"])

#: Chart windows the console offers. A day at five-minute resolution is 288
#: points, which draws instantly; a week at that resolution would not.
MAX_WINDOW_HOURS = 24 * 30
DEFAULT_WINDOW_HOURS = 24
MIN_STEP_SECONDS = 60
DEFAULT_STEP_SECONDS = 300

_TIMESERIES_SQL = text(
    """
    SELECT to_timestamp(floor(extract(epoch FROM ts) / :step) * :step) AS bucket,
           endpoint,
           outcome,
           count(*) AS requests,
           avg(duration_ms)::int AS avg_duration_ms
    FROM request_log
    WHERE ts >= now() - make_interval(hours => :hours)
      AND (CAST(:endpoint AS text) IS NULL OR endpoint = CAST(:endpoint AS text))
      AND (CAST(:platform AS text) IS NULL OR platform = CAST(:platform AS text))
    GROUP BY 1, 2, 3
    ORDER BY 1
    """
)


@router.get("/endpoints/health", summary="Per-endpoint health and circuit state")
async def endpoint_health(request: Request, principal: Principal = Depends(read_admin)) -> Any:
    """Every declared endpoint, whether or not it has traffic.

    Listing the quiet ones too is deliberate: V4's failure mode was an endpoint
    that quietly died and nobody noticed, and an endpoint missing from a board
    is exactly as invisible as one that was never called.
    """
    now = time.time()
    lang = language(request)
    rows: list[dict[str, Any]] = []
    for platform_name in available_platforms():
        adapter = get_adapter(platform_name)
        for name in adapter.endpoints.names():
            stats = await circuit.stats(name, now=now)
            state = await circuit.status(name, now=now)
            policy = policy_for(name)
            rows.append(
                {
                    "endpoint": name,
                    "platform": platform_name,
                    "circuit_open": state.is_open,
                    "retry_after": state.retry_after or None,
                    # The sentence is for reading and changes with the
                    # language; the code is the half worth keying a rule or a
                    # runbook off, so both go out.
                    "reason": state.message(lang) or None,
                    "reason_code": state.reason.code if state.reason else None,
                    "reason_args": dict(state.reason.args) if state.reason else None,
                    "window_seconds": circuit.WINDOW_SECONDS,
                    "samples": stats.total,
                    "success_rate": round(stats.success_rate, 4) if stats.total else None,
                    "risk_rate": round(stats.risk_rate, 4) if stats.total else None,
                    "distinct_risk_identities": stats.distinct_risk_identities,
                    "policy": {
                        "capacity": policy.capacity,
                        "refill_per_sec": policy.refill_per_sec,
                        "risk_weight": policy.risk_weight,
                    },
                }
            )
    return ok(request, rows)


@router.get("/metrics/timeseries", summary="Request volume and outcomes over time")
async def metrics_timeseries(
    request: Request,
    hours: int = Query(default=DEFAULT_WINDOW_HOURS, ge=1, le=MAX_WINDOW_HOURS),
    step: int = Query(default=DEFAULT_STEP_SECONDS, ge=MIN_STEP_SECONDS, le=86400),
    endpoint: str | None = Query(default=None, max_length=128),
    platform: Platform | None = Query(default=None),
    principal: Principal = Depends(read_admin),
) -> Any:
    """Raw buckets. Series selection and zooming happen in the browser (doc 07)."""
    result = await request.state.db.execute(
        _TIMESERIES_SQL,
        {
            "step": step,
            "hours": hours,
            "endpoint": endpoint,
            "platform": platform.value if platform else None,
        },
    )
    points = [
        {
            "bucket": bucket.isoformat(),
            "endpoint": row_endpoint,
            "outcome": outcome,
            "requests": int(requests),
            "avg_duration_ms": int(avg_duration) if avg_duration is not None else None,
        }
        for bucket, row_endpoint, outcome, requests, avg_duration in result.all()
    ]
    return ok(
        request,
        {
            "window_hours": hours,
            "step_seconds": step,
            "points": points,
        },
    )


__all__ = ["DEFAULT_STEP_SECONDS", "DEFAULT_WINDOW_HOURS", "MAX_WINDOW_HOURS", "router"]
