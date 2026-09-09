"""What the refill job has been doing, readable from the API.

Minting happens in the worker and the console runs in the API, so "is it
minting right now, and how did the last few attempts go" is a question one
process has the answer to and the other is being asked. The worker leaves the
answer in Redis and the API reads it.

Redis rather than a table, on purpose. This is a live status breadcrumb with a
horizon of minutes: an operator who has just retired a platform's identities
wants to see the pool refill, and nobody wants an audit trail of every mint
this instance has ever attempted. It is capped at :data:`KEEP` entries and
expires on its own, so it cannot grow into a retention problem.

Failures are the reason it exists at all. A successful mint leaves an identity
row behind and is visible in the pool count; a failed one used to leave nothing
but a log line, which meant a pool that stubbornly refused to refill looked
identical to one nobody had asked to refill.

Every write here is best effort. Recording that a mint happened must never be
the reason a mint fails, so a Redis that is down or not yet initialised costs a
debug line and nothing else.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Platform

log = get_logger(__name__)

#: The mint in flight, if there is one. Carries a TTL so a worker killed
#: mid-mint leaves a stale "minting..." for seconds rather than forever.
CURRENT_KEY: Final = "worker:mint:current"
#: The last few attempts, newest first.
RECENT_KEY: Final = "worker:mint:recent"
#: Set while the shared backoff is holding the sweep off.
BACKOFF_KEY: Final = "worker:mint:backoff"

#: How many attempts to keep. Enough to show a pattern - a run of failures with
#: one reason is a different story from an alternating one - and small enough
#: that the whole thing renders on one row.
KEEP: Final = 20

#: Attempts stop being interesting long before this; it is here so an instance
#: that stops minting entirely does not keep the list forever.
RECENT_TTL: Final = 24 * 60 * 60


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def started(platform: Platform, *, ttl: int) -> None:
    """Record that a mint is in flight, expiring after the lock does."""
    try:
        await get_redis().set(
            CURRENT_KEY,
            json.dumps({"platform": platform.value, "started_at": _now()}),
            ex=max(1, ttl),
        )
    except Exception as exc:  # pragma: no cover - a breadcrumb, never a failure
        log.debug("mint_log.started_unavailable", error=type(exc).__name__)


async def finished(
    platform: Platform,
    *,
    ok: bool,
    reason: str,
    identity_id: uuid.UUID | None = None,
    error: str | None = None,
) -> None:
    """Record how an attempt ended and clear the in-flight marker.

    ``reason`` is the same short code the worker logs, so what the console
    shows and what an operator finds in the logs are the same word.
    """
    entry = {
        "ts": _now(),
        "platform": platform.value,
        "ok": ok,
        "reason": reason,
        "identity_id": str(identity_id) if identity_id else None,
        # Trimmed: this is a status line, not a stack trace, and the log has
        # the whole thing.
        "error": error[:200] if error else None,
    }
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.delete(CURRENT_KEY)
        pipe.lpush(RECENT_KEY, json.dumps(entry))
        pipe.ltrim(RECENT_KEY, 0, KEEP - 1)
        pipe.expire(RECENT_KEY, RECENT_TTL)
        await pipe.execute()
    except Exception as exc:  # pragma: no cover - a breadcrumb, never a failure
        log.debug("mint_log.finished_unavailable", error=type(exc).__name__)


async def backing_off(*, failures: int, seconds: float) -> None:
    """Record that the sweep is holding off, and for how long.

    Given the same lifetime as the wait itself, so the key's presence IS the
    state: nothing has to compare a stored deadline against the clock, and a
    worker that restarts does not leave a backoff nobody is honouring.
    """
    try:
        await get_redis().set(
            BACKOFF_KEY,
            json.dumps(
                {
                    "failures": failures,
                    "until": datetime.fromtimestamp(
                        datetime.now(UTC).timestamp() + seconds, tz=UTC
                    ).isoformat(),
                }
            ),
            ex=max(1, int(seconds)),
        )
    except Exception as exc:  # pragma: no cover - a breadcrumb, never a failure
        log.debug("mint_log.backoff_unavailable", error=type(exc).__name__)


async def recovered() -> None:
    """A mint succeeded, so the backoff no longer applies."""
    try:
        await get_redis().delete(BACKOFF_KEY)
    except Exception as exc:  # pragma: no cover - a breadcrumb, never a failure
        log.debug("mint_log.recover_unavailable", error=type(exc).__name__)


async def snapshot() -> dict[str, Any]:
    """Everything the console needs about minting, in one round trip.

    Returns empty structures rather than raising when Redis cannot answer: a
    status panel that disappears is better than a page that fails to load
    because the thing it reports on is down.
    """
    empty: dict[str, Any] = {"current": None, "recent": [], "backoff": None}
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.get(CURRENT_KEY)
        pipe.lrange(RECENT_KEY, 0, KEEP - 1)
        pipe.get(BACKOFF_KEY)
        current, recent, backoff = await pipe.execute()
    except Exception as exc:
        log.debug("mint_log.snapshot_unavailable", error=type(exc).__name__)
        return empty

    return {
        "current": _decode(current),
        "recent": [entry for entry in (_decode(raw) for raw in recent or []) if entry is not None],
        "backoff": _decode(backoff),
    }


def _decode(raw: Any) -> dict[str, Any] | None:
    """Read one stored entry, dropping anything unreadable.

    An entry written by an older build is not worth failing a status panel
    over; it just does not appear.
    """
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


__all__ = [
    "BACKOFF_KEY",
    "CURRENT_KEY",
    "KEEP",
    "RECENT_KEY",
    "RECENT_TTL",
    "backing_off",
    "finished",
    "recovered",
    "snapshot",
    "started",
]
