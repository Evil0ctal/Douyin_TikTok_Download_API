"""What the refill job leaves behind for the console to read.

The whole point of this record is failures. A successful mint writes an
identity row and shows up in the pool count on its own; a failed one used to
write nothing but a log line, so a pool that stubbornly would not refill looked
exactly like one nobody had asked to refill.

Against real Redis, because the guarantees being tested are Redis' own: the
cap, the expiry, and the in-flight marker outliving nothing.
"""

from __future__ import annotations

import uuid

import pytest

from dtk.core.redis import get_redis
from dtk.core.types import Platform
from dtk.identity import mint_log

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_an_attempt_in_flight_is_visible(redis_client) -> None:
    await mint_log.started(Platform.TIKTOK, ttl=300)

    snapshot = await mint_log.snapshot()

    assert snapshot["current"]["platform"] == Platform.TIKTOK.value
    assert snapshot["current"]["started_at"]


async def test_finishing_clears_the_in_flight_marker(redis_client) -> None:
    """Otherwise the console shows "minting..." forever after one mint."""
    await mint_log.started(Platform.TIKTOK, ttl=300)
    await mint_log.finished(Platform.TIKTOK, ok=True, reason="minted", identity_id=uuid.uuid4())

    assert (await mint_log.snapshot())["current"] is None


async def test_the_in_flight_marker_expires_with_the_lock(redis_client) -> None:
    """A worker killed mid-mint leaves a stale marker for seconds, not forever."""
    await mint_log.started(Platform.DOUYIN, ttl=42)

    assert 0 < await get_redis().ttl(mint_log.CURRENT_KEY) <= 42


async def test_a_failure_records_its_reason(redis_client) -> None:
    await mint_log.finished(
        Platform.TIKTOK, ok=False, reason="rpc_unavailable", error="connection refused"
    )

    entry = (await mint_log.snapshot())["recent"][0]
    assert entry["ok"] is False
    assert entry["reason"] == "rpc_unavailable"
    assert entry["error"] == "connection refused"
    assert entry["platform"] == Platform.TIKTOK.value


async def test_a_long_error_is_trimmed(redis_client) -> None:
    """A status line, not a stack trace. The log has the whole thing."""
    await mint_log.finished(Platform.TIKTOK, ok=False, reason="rpc_unavailable", error="x" * 5_000)

    assert len((await mint_log.snapshot())["recent"][0]["error"]) == 200


async def test_attempts_are_newest_first(redis_client) -> None:
    for index in range(3):
        await mint_log.finished(Platform.DOUYIN, ok=True, reason=f"minted-{index}")

    recent = (await mint_log.snapshot())["recent"]
    assert [entry["reason"] for entry in recent] == ["minted-2", "minted-1", "minted-0"]


async def test_the_list_is_capped(redis_client) -> None:
    """It cannot become a retention problem, so it is not one."""
    for index in range(mint_log.KEEP + 15):
        await mint_log.finished(Platform.DOUYIN, ok=True, reason=str(index))

    assert len((await mint_log.snapshot())["recent"]) == mint_log.KEEP
    assert await get_redis().llen(mint_log.RECENT_KEY) == mint_log.KEEP


async def test_the_list_expires_on_its_own(redis_client) -> None:
    await mint_log.finished(Platform.DOUYIN, ok=True, reason="minted")

    assert 0 < await get_redis().ttl(mint_log.RECENT_KEY) <= mint_log.RECENT_TTL


async def test_a_backoff_is_visible_and_carries_its_own_lifetime(redis_client) -> None:
    """The key's presence IS the state: nothing compares a deadline to a clock."""
    await mint_log.backing_off(failures=3, seconds=120)

    snapshot = await mint_log.snapshot()
    assert snapshot["backoff"]["failures"] == 3
    assert snapshot["backoff"]["until"]
    assert 0 < await get_redis().ttl(mint_log.BACKOFF_KEY) <= 120


async def test_a_success_lifts_the_backoff(redis_client) -> None:
    await mint_log.backing_off(failures=3, seconds=120)
    await mint_log.recovered()

    assert (await mint_log.snapshot())["backoff"] is None


async def test_an_unreadable_entry_is_skipped_not_fatal(redis_client) -> None:
    """One entry written by an older build must not take the panel down."""
    await mint_log.finished(Platform.DOUYIN, ok=True, reason="minted")
    await get_redis().lpush(mint_log.RECENT_KEY, "not json")

    recent = (await mint_log.snapshot())["recent"]
    assert [entry["reason"] for entry in recent] == ["minted"]


async def test_an_empty_history_is_empty_not_missing(redis_client) -> None:
    assert await mint_log.snapshot() == {"current": None, "recent": [], "backoff": None}
