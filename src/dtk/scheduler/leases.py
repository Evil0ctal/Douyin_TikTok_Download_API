"""Atomic lease acquisition and return.

The check and the take must happen together. Checking the token bucket and then
separately taking the inflight lock leaves a TOCTOU window in which several
workers all observe budget and all proceed, breaching the quota precisely when
the system is busiest. Both operations therefore live in one Lua script.

See docs/design/03-scheduler.md.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from dtk.core.logging import get_logger
from dtk.core.redis import run_script
from dtk.core.types import Outcome
from dtk.scheduler.policies import EndpointPolicy

log = get_logger(__name__)

INFLIGHT_KEY = "sched:inflight:{identity_id}"
BUCKET_KEY = "sched:bucket:{identity_id}:{endpoint}"

#: Last-use timestamps, written atomically with the grant.
#:
#: The identity row also carries ``last_used_at``, but that is a database write
#: that lands after the request completes. Ranking on it alone means several
#: requests issued back to back all see the same stale ordering and pick the
#: same identity - rotation degrades to "always the healthiest one". Keeping the
#: authoritative value for scheduling in Redis makes taking turns actually work.
LAST_USED_KEY = "sched:lastused"

#: Idle buckets expire so Redis does not accumulate a key per retired identity.
#: Long enough that a bucket in active use is never dropped mid-refill.
BUCKET_TTL_SECONDS = 24 * 3600


# Returns {granted, reason, tokens_left}.
#
# The bucket refills lazily from the elapsed time rather than from a background
# timer: one less loop to keep alive, and no global stall if that loop wedges.
#
# The lock is taken with SET NX rather than EXISTS-then-SET. Both are safe inside
# a Lua script, but NX states the requirement in one indivisible operation
# instead of two statements that only add up to the requirement, and it is the
# primitive Redis documents for exactly this. Under a heavily loaded machine an
# earlier EXISTS/SET form produced occasional over-granting that could not be
# reproduced afterwards and was never fully explained; using the stronger
# primitive removes the class of question rather than the symptom.
ACQUIRE_LUA = """
local inflight_key  = KEYS[1]
local bucket_key    = KEYS[2]
local last_used_key = KEYS[3]

local now          = tonumber(ARGV[1])
local inflight_ttl = tonumber(ARGV[2])
local capacity     = tonumber(ARGV[3])
local refill_rate  = tonumber(ARGV[4])
local lease_id     = ARGV[5]
local bucket_ttl   = tonumber(ARGV[6])
local identity_id  = ARGV[7]

-- SET NX is the whole lock. Testing EXISTS and then SET is two statements
-- describing one intent, and the pair only reads as safe because Lua happens to
-- be atomic; NX states the requirement directly and cannot be separated.
if redis.call('SET', inflight_key, lease_id, 'NX', 'EX', inflight_ttl) == false then
  return {0, 'inflight', '0'}
end

local stored = redis.call('HMGET', bucket_key, 'tokens', 'last_refill')
local tokens = tonumber(stored[1])
local last   = tonumber(stored[2])
if tokens == nil then tokens = capacity end
if last == nil then last = now end

local elapsed = now - last
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill_rate)

if tokens < 1 then
  redis.call('HMSET', bucket_key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
  redis.call('EXPIRE', bucket_key, bucket_ttl)
  -- The lock was taken before the budget was known, so hand it straight back;
  -- leaving it held would starve this identity until the TTL expired.
  redis.call('DEL', inflight_key)
  return {0, 'no_token', tostring(tokens)}
end

tokens = tokens - 1
redis.call('HMSET', bucket_key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
redis.call('EXPIRE', bucket_key, bucket_ttl)
redis.call('ZADD', last_used_key, now, identity_id)
return {1, 'ok', tostring(tokens)}
"""

# Releasing compares the lease id before deleting. Without that check, a worker
# whose lease already expired by TTL - after which another worker legitimately
# borrowed the identity - would delete the new holder's lock on its late return.
RELEASE_LUA = """
local inflight_key = KEYS[1]
local bucket_key   = KEYS[2]

local lease_id   = ARGV[1]
local refund     = tonumber(ARGV[2])
local capacity   = tonumber(ARGV[3])
local now        = tonumber(ARGV[4])
local bucket_ttl = tonumber(ARGV[5])

local held = redis.call('GET', inflight_key)
if held ~= lease_id then
  return {0, 'stale'}
end
redis.call('DEL', inflight_key)

if refund == 1 then
  local stored = redis.call('HGET', bucket_key, 'tokens')
  local tokens = tonumber(stored)
  if tokens == nil then tokens = 0 end
  tokens = math.min(capacity, tokens + 1)
  redis.call('HMSET', bucket_key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
  redis.call('EXPIRE', bucket_key, bucket_ttl)
  return {1, 'released_refunded'}
end
return {1, 'released'}
"""


@dataclass(frozen=True, slots=True)
class Lease:
    """A grant to make exactly one request with one identity.

    ``lease_id`` is checked on return so a late release cannot free a lock that
    now belongs to somebody else.
    """

    identity_id: str
    endpoint: str
    lease_id: str
    tokens_left: float


#: Only a request that never reached the platform gets its token back. Proxy
#: flapping and connect timeouts are unrelated to the identity's allowance, and
#: charging them would shrink throughput exactly when it is most needed.
REFUNDABLE: frozenset[Outcome] = frozenset({Outcome.NETWORK_ERROR})


def _keys(identity_id: str, endpoint: str) -> list[str]:
    return [
        INFLIGHT_KEY.format(identity_id=identity_id),
        BUCKET_KEY.format(identity_id=identity_id, endpoint=endpoint),
        LAST_USED_KEY,
    ]


async def last_used_map() -> dict[str, float]:
    """Scheduling-authoritative last-use timestamps for every known identity."""
    from dtk.core.redis import get_redis

    raw = await get_redis().zrange(LAST_USED_KEY, 0, -1, withscores=True)
    return {(m.decode() if isinstance(m, bytes) else m): float(score) for m, score in raw}


async def forget_identity(identity_id: str) -> None:
    """Drop a retired identity so the set does not grow without bound."""
    from dtk.core.redis import get_redis

    await get_redis().zrem(LAST_USED_KEY, identity_id)


async def try_acquire(
    identity_id: str,
    endpoint: str,
    policy: EndpointPolicy,
    *,
    now: float,
    inflight_ttl: int,
) -> tuple[Lease | None, str]:
    """Attempt to take the identity for one request on this endpoint.

    Returns ``(lease, reason)``. ``reason`` is ``ok``, ``inflight`` or
    ``no_token`` and is recorded so a rejection can always be explained.
    """
    lease_id = secrets.token_hex(8)
    granted, reason, tokens = await run_script(
        "sched_acquire",
        ACQUIRE_LUA,
        _keys(identity_id, endpoint),
        [
            now,
            inflight_ttl,
            policy.capacity,
            policy.refill_per_sec,
            lease_id,
            BUCKET_TTL_SECONDS,
            identity_id,
        ],
    )
    reason_s = reason.decode() if isinstance(reason, bytes) else str(reason)
    if int(granted) != 1:
        return None, reason_s
    return (
        Lease(
            identity_id=identity_id,
            endpoint=endpoint,
            lease_id=lease_id,
            tokens_left=float(tokens),
        ),
        "ok",
    )


async def release(lease: Lease, outcome: Outcome, policy: EndpointPolicy, *, now: float) -> str:
    """Return a lease, refunding the token only for outcomes that never left."""
    refund = 1 if outcome in REFUNDABLE else 0
    _released, status = await run_script(
        "sched_release",
        RELEASE_LUA,
        _keys(lease.identity_id, lease.endpoint),
        [lease.lease_id, refund, policy.capacity, now, BUCKET_TTL_SECONDS],
    )
    status_s = status.decode() if isinstance(status, bytes) else str(status)
    if status_s == "stale":
        # The TTL already freed the identity and someone else may hold it now.
        log.warning(
            "scheduler.release.stale",
            identity_id=lease.identity_id,
            endpoint=lease.endpoint,
            outcome=outcome.value,
        )
    return status_s


__all__ = [
    "ACQUIRE_LUA",
    "BUCKET_KEY",
    "BUCKET_TTL_SECONDS",
    "INFLIGHT_KEY",
    "LAST_USED_KEY",
    "REFUNDABLE",
    "RELEASE_LUA",
    "Lease",
    "forget_identity",
    "last_used_map",
    "release",
    "try_acquire",
]
