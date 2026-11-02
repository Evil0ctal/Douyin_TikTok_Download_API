"""A sweep that passes a platform over is not a failed attempt (#807).

With one proxy and a low-water mark of three, the filler found every exit taken
on every tick and wrote that into the attempt list as a failure. The console
drew "20 of the last 20 failed" in red for a pool that had simply run out of
exits, and the real attempts were pushed out of the window within twenty
minutes. A skip now has its own per-platform record, which lapses on its own
and is cleared by the next real attempt.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from dtk.core.types import Platform
from dtk.identity import mint_log
from tests.unit.test_mint_backoff_record import MemoryRedis
from tests.unit.test_worker import FakePool, FakeRpc, FakeSession, make_filler


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> MemoryRedis:
    fake = MemoryRedis()
    monkeypatch.setattr(mint_log, "get_redis", lambda: fake)
    return fake


async def test_a_skip_is_kept_out_of_the_attempts(redis: MemoryRedis) -> None:
    proxy = SimpleNamespace(id=uuid.uuid4(), url_encrypted=b"x", country="DE", timezone=None)
    filler = make_filler(
        FakeRpc(),
        FakePool(),
        session=FakeSession(scalars_results=[[], [proxy], [uuid.uuid4()]]),
        options={"platforms": (Platform.DOUYIN,), "interval_seconds": 60.0},
    )

    assert (await filler.tick()).reason == "no_free_proxy"

    snapshot = await mint_log.snapshot()
    assert snapshot["recent"] == []
    assert snapshot["skips"]["douyin"]["reason"] == "no_free_proxy"
    # A few sweeps, so it lapses on its own once the sweep stops skipping.
    assert redis.ttls[mint_log.skip_key(Platform.DOUYIN)] == 180


async def test_a_repeated_skip_keeps_when_it_started(redis: MemoryRedis) -> None:
    await mint_log.skipped(Platform.DOUYIN, reason="no_free_proxy", ttl=180)
    first = (await mint_log.snapshot())["skips"]["douyin"]
    stored = json.loads(redis.values[mint_log.skip_key(Platform.DOUYIN)])
    redis.values[mint_log.skip_key(Platform.DOUYIN)] = json.dumps(
        {**stored, "since": "2026-10-31T09:00:00+00:00"}
    )

    await mint_log.skipped(Platform.DOUYIN, reason="no_free_proxy", ttl=180)

    again = (await mint_log.snapshot())["skips"]["douyin"]
    assert again["since"] == "2026-10-31T09:00:00+00:00"
    assert again["at"] >= first["at"]


async def test_another_reason_starts_the_clock_again(redis: MemoryRedis) -> None:
    redis.values[mint_log.skip_key(Platform.DOUYIN)] = json.dumps(
        {"reason": "something_else", "since": "2026-10-31T09:00:00+00:00", "at": "x"}
    )

    await mint_log.skipped(Platform.DOUYIN, reason="no_free_proxy", ttl=180)

    assert (await mint_log.snapshot())["skips"]["douyin"]["since"] != "2026-10-31T09:00:00+00:00"


async def test_an_attempt_clears_its_platforms_skip_only(redis: MemoryRedis) -> None:
    await mint_log.skipped(Platform.DOUYIN, reason="no_free_proxy", ttl=180)
    await mint_log.skipped(Platform.TIKTOK, reason="no_free_proxy", ttl=180)

    await mint_log.finished(Platform.DOUYIN, ok=True, reason="minted")

    assert set((await mint_log.snapshot())["skips"]) == {"tiktok"}


async def test_a_missing_named_proxy_is_still_a_failed_attempt(
    redis: MemoryRedis,
) -> None:
    """Only "every exit is taken" is a skip. A mint the console asked for on a
    proxy that has since been deleted could not go ahead, and says so."""
    filler = make_filler(FakeRpc(), FakePool(), session=FakeSession())

    result = await filler.top_up_once(Platform.DOUYIN, proxy_id=uuid.uuid4())

    assert result.reason == "proxy_not_found"
    snapshot = await mint_log.snapshot()
    assert [entry["reason"] for entry in snapshot["recent"]] == ["proxy_not_found"]
    assert snapshot["skips"] == {}
