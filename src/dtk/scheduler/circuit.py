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

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from dtk.core.logging import get_logger
from dtk.core.redis import get_redis, run_script
from dtk.core.types import DEFAULT_LANGUAGE, Language, Outcome
from dtk.i18n.catalog import t

log = get_logger(__name__)

WINDOW_KEY = "sched:window:{endpoint}"
SEQ_KEY = "sched:window:{endpoint}:seq"
CIRCUIT_KEY = "sched:circuit:{endpoint}"
PROBE_KEY = "sched:probe:{endpoint}"

#: Rolling observation window used for the trip decision.
WINDOW_SECONDS = 300

#: Catalogue namespace for trip reasons.
REASON_KEY_PREFIX = "circuit.reason."

#: The one reason a trip can have today. New codes are appended, never renamed:
#: a code that changes meaning silently invalidates every translation of it.
RISK_ACROSS_IDENTITIES = "risk_across_identities"

#: Marks a stored reason as the structured form. A value written before reasons
#: were structured carries no marker and is English prose.
_REASON_MARKER = "code:"
_CODE_RE = re.compile(r"^[a-z0-9_]+$")


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


@dataclass(frozen=True, slots=True)
class TripReason:
    """Why an endpoint tripped: a stable code and the numbers behind it.

    A sentence stored in Redis can only ever be read back in the language it
    was written in, and the console shows this one in its landing-page alert.
    Storing the code instead lets the reader's language decide the wording,
    and the numbers travel alongside so the wording can still name them.
    """

    code: str
    args: Mapping[str, float] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{REASON_KEY_PREFIX}{self.code}"

    def template_args(self) -> dict[str, object]:
        """Catalogue arguments for :attr:`key`.

        The rate is stored as a fraction, matching every other rate in the
        health payload, but every message in this product talks in percent.
        Whole numbers lose their ``.0`` so a count reads as a count.
        """
        args: dict[str, object] = {
            name: int(value) if float(value).is_integer() else value
            for name, value in self.args.items()
        }
        rate = self.args.get("risk_rate")
        if rate is not None:
            args["risk_rate"] = f"{rate * 100:.0f}"
        return args

    def encode(self) -> str:
        parts = [f"{_REASON_MARKER}{self.code}"]
        parts += [f"{name}={value:g}" for name, value in self.args.items()]
        return ";".join(parts)


def parse_reason(raw: str) -> TripReason | None:
    """Structured form of a stored reason, or ``None`` for anything else.

    ``None`` covers both an empty value and the free English text an older
    build wrote. Neither can raise here: the circuit key outlives a deploy by
    up to ``open_seconds + 60``, and a reader that crashed on the previous
    build's value would blank the console for exactly as long.
    """
    if not raw.startswith(_REASON_MARKER):
        return None
    code, _, tail = raw[len(_REASON_MARKER) :].partition(";")
    if not _CODE_RE.match(code):
        return None
    args: dict[str, float] = {}
    for item in tail.split(";"):
        name, sep, value = item.partition("=")
        if not sep:
            continue
        try:
            args[name] = float(value)
        except ValueError:
            continue
    return TripReason(code, args)


@dataclass(frozen=True, slots=True)
class CircuitState:
    is_open: bool
    retry_after: int
    #: ``None`` when nothing is stored, or when the stored value predates
    #: structured reasons.
    reason: TripReason | None = None
    #: Exactly what was stored, so a legacy value stays readable.
    raw_reason: str = ""

    def message(self, language: Language | str = DEFAULT_LANGUAGE) -> str:
        """The reason as a sentence, empty when there is no reason at all.

        A legacy value is handed back verbatim rather than dropped: it is
        already English prose, and during the few minutes it survives an
        upgrade its numbers are the ones an operator is looking for.
        """
        if self.reason is None:
            return self.raw_reason
        return t(self.reason.key, language, **self.reason.template_args())


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
    """Decide, and say why.

    Only the tripping branch returns text anyone reads, as an encoded
    :class:`TripReason`; the caller discards the string whenever the decision
    is ``False``, so the three declining branches stay plain English notes for
    whoever is reading this function.
    """
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
    return True, TripReason(
        RISK_ACROSS_IDENTITIES,
        {
            "risk_rate": round(s.risk_rate, 4),
            "samples": s.total,
            "identities": s.distinct_risk_identities,
        },
    ).encode()


async def trip(endpoint: str, cfg: CircuitConfig, reason: str, *, now: float) -> None:
    """Open the circuit. ``reason`` is an encoded :class:`TripReason`."""
    await get_redis().set(
        CIRCUIT_KEY.format(endpoint=endpoint),
        f"{now + cfg.open_seconds:.6f}|{reason}",
        ex=cfg.open_seconds + 60,
    )
    log.error(
        "scheduler.circuit.open", endpoint=endpoint, reason=reason, open_seconds=cfg.open_seconds
    )


async def status(endpoint: str, *, now: float) -> CircuitState:
    """Circuit state with the trip reason left structured."""
    raw = await get_redis().get(CIRCUIT_KEY.format(endpoint=endpoint))
    if not raw:
        return CircuitState(False, 0)
    text = raw.decode() if isinstance(raw, bytes) else raw
    open_until_s, _, reason = text.partition("|")
    try:
        open_until = float(open_until_s)
    except ValueError:
        return CircuitState(False, 0)
    parsed = parse_reason(reason)
    if now >= open_until:
        return CircuitState(False, 0, parsed, reason)
    return CircuitState(True, max(1, int(open_until - now)), parsed, reason)


async def state(endpoint: str, *, now: float) -> tuple[bool, int, str]:
    """Return ``(is_open, retry_after_seconds, reason)`` with the reason in English.

    For callers with no requester to render for - the scheduler's rejection
    detail and the MCP tool prose, both English-only. A caller that knows the
    language reads :func:`status` and renders the reason itself.
    """
    current = await status(endpoint, now=now)
    return current.is_open, current.retry_after, current.message()


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
    "REASON_KEY_PREFIX",
    "RISK_ACROSS_IDENTITIES",
    "SEQ_KEY",
    "WINDOW_KEY",
    "WINDOW_SECONDS",
    "CircuitConfig",
    "CircuitState",
    "EndpointStats",
    "TripReason",
    "allow_probe",
    "parse_reason",
    "record",
    "reset",
    "should_trip",
    "state",
    "stats",
    "status",
    "trip",
]
