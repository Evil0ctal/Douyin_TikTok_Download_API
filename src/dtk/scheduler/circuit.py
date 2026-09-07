"""Endpoint-level circuit breaker.

One identity getting rate limited is routine. Every identity failing on the same
endpoint is a different event, usually a changed upstream API or a dead signer.
Continuing to retry then only burns the pool, so the endpoint is tripped.

Three conditions must hold together. The third - that failures span several
distinct identities - is what separates "the endpoint broke" from "one identity
broke"; without it a single flapping identity trips the whole endpoint.

See docs/design/03-scheduler.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from dtk.core.logging import get_logger
from dtk.core.redis import get_redis, run_script
from dtk.core.types import Outcome

log = get_logger(__name__)

WINDOW_KEY = "sched:window:{endpoint}"
SEQ_KEY = "sched:window:{endpoint}:seq"
CIRCUIT_KEY = "sched:circuit:{endpoint}"
PROBE_KEY = "sched:probe:{endpoint}"

#: Rolling observation window used for the trip decision.
WINDOW_SECONDS = 300


@dataclass(frozen=True, slots=True)
class CircuitConfig:
    risk_threshold: float = 0.6
    min_samples: int = 20
    min_identities: int = 3
    open_seconds: int = 300
    #: A tripped endpoint lets exactly one probe through per interval.
    probe_interval_seconds: int = 60


@dataclass(frozen=True, slots=True)
class EndpointStats:
    total: int
    ok: int
    risk: int
    distinct_risk_identities: int

    @property
    def risk_rate(self) -> float:
        return self.risk / self.total if self.total else 0.0

    @property
    def success_rate(self) -> float:
        return self.ok / self.total if self.total else 0.0


# Record one observation and trim the window in a single round trip. Members are
# unique per call so identical outcomes never collapse into one entry.
RECORD_LUA = """
local window_key = KEYS[1]
local seq_key    = KEYS[2]
local now      = tonumber(ARGV[1])
local horizon  = tonumber(ARGV[2])
local member   = ARGV[3]
local ttl      = tonumber(ARGV[4])

-- A sorted set stores unique members, so two observations that share a
-- timestamp, identity and outcome would collapse into one and silently
-- undercount the window. The sequence number keeps every observation distinct.
local seq = redis.call('INCR', seq_key)
redis.call('EXPIRE', seq_key, ttl)
redis.call('ZADD', window_key, now, member .. '|' .. seq)
redis.call('ZREMRANGEBYSCORE', window_key, '-inf', now - horizon)
redis.call('EXPIRE', window_key, ttl)
return redis.call('ZCARD', window_key)
"""


async def record(endpoint: str, identity_id: str, outcome: Outcome, *, now: float) -> None:
    """Note one request result in the endpoint's rolling window."""
    member = f"{now:.6f}|{identity_id}|{outcome.value}"
    await run_script(
        "circuit_record",
        RECORD_LUA,
        [WINDOW_KEY.format(endpoint=endpoint), SEQ_KEY.format(endpoint=endpoint)],
        [now, WINDOW_SECONDS, member, WINDOW_SECONDS * 2],
    )


async def stats(endpoint: str, *, now: float) -> EndpointStats:
    raw = await get_redis().zrangebyscore(
        WINDOW_KEY.format(endpoint=endpoint), now - WINDOW_SECONDS, "+inf"
    )
    total = ok = risk = 0
    risk_identities: set[str] = set()
    for entry in raw:
        text = entry.decode() if isinstance(entry, bytes) else entry
        parts = text.split("|")
        if len(parts) < 3:
            continue
        _ts, identity_id, outcome = parts[0], parts[1], parts[2]
        total += 1
        if outcome == Outcome.OK.value:
            ok += 1
        elif outcome == Outcome.RISK_CONTROL.value:
            risk += 1
            risk_identities.add(identity_id)
    return EndpointStats(total, ok, risk, len(risk_identities))


async def should_trip(endpoint: str, cfg: CircuitConfig, *, now: float) -> tuple[bool, str]:
    s = await stats(endpoint, now=now)
    if s.total < cfg.min_samples:
        return False, f"insufficient samples ({s.total}<{cfg.min_samples})"
    if s.risk_rate <= cfg.risk_threshold:
        return False, f"risk rate {s.risk_rate:.2f} <= {cfg.risk_threshold}"
    if s.distinct_risk_identities < cfg.min_identities:
        # Looks like one bad identity, not a broken endpoint.
        return False, (
            f"only {s.distinct_risk_identities} identity(ies) affected "
            f"(<{cfg.min_identities}); treating as identity-local"
        )
    return True, (
        f"risk rate {s.risk_rate:.2f} over {s.total} samples "
        f"across {s.distinct_risk_identities} identities"
    )


async def trip(endpoint: str, cfg: CircuitConfig, reason: str, *, now: float) -> None:
    await get_redis().set(
        CIRCUIT_KEY.format(endpoint=endpoint),
        f"{now + cfg.open_seconds:.6f}|{reason}",
        ex=cfg.open_seconds + 60,
    )
    log.error(
        "scheduler.circuit.open", endpoint=endpoint, reason=reason, open_seconds=cfg.open_seconds
    )


async def state(endpoint: str, *, now: float) -> tuple[bool, int, str]:
    """Return ``(is_open, retry_after_seconds, reason)``."""
    raw = await get_redis().get(CIRCUIT_KEY.format(endpoint=endpoint))
    if not raw:
        return False, 0, ""
    text = raw.decode() if isinstance(raw, bytes) else raw
    open_until_s, _, reason = text.partition("|")
    try:
        open_until = float(open_until_s)
    except ValueError:
        return False, 0, ""
    if now >= open_until:
        return False, 0, reason
    return True, max(1, int(open_until - now)), reason


async def allow_probe(endpoint: str, cfg: CircuitConfig) -> bool:
    """Half-open: let a single request through per probe interval.

    Recovering at full allowance immediately re-trips almost every time, so the
    endpoint is re-opened only after a probe actually succeeds.
    """
    acquired = await get_redis().set(
        PROBE_KEY.format(endpoint=endpoint), "1", ex=cfg.probe_interval_seconds, nx=True
    )
    return bool(acquired)


async def reset(endpoint: str) -> None:
    r = get_redis()
    await r.delete(CIRCUIT_KEY.format(endpoint=endpoint))
    await r.delete(PROBE_KEY.format(endpoint=endpoint))
    await r.delete(WINDOW_KEY.format(endpoint=endpoint))
    log.info("scheduler.circuit.closed", endpoint=endpoint)


__all__ = [
    "SEQ_KEY",
    "WINDOW_KEY",
    "WINDOW_SECONDS",
    "CircuitConfig",
    "EndpointStats",
    "allow_probe",
    "record",
    "reset",
    "should_trip",
    "state",
    "stats",
    "trip",
]
