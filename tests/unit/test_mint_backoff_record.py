"""The minting backoff the console reads, per platform.

The worker rests each minting route on its own - the browser for Douyin and
TikTok, plain HTTP for Bilibili - since 5.1.6. The record the console reads
did not follow: it was one Redis key for the whole job, so a browser that was
down showed every platform as resting, and the next Bilibili guest minted over
HTTP cleared it while Douyin was still holding off.

A small in-memory Redis stands in for the real one; tests/integration/
test_mint_log.py covers the key lifetimes against the real thing.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.core.types import Platform
from dtk.identity import mint_log
from dtk.identity.minting import BrowserRpcUnavailable
from tests.unit.test_worker import FakeNativeMinter, FakeRpc, PerPlatformPool, make_filler


class MemoryRedis:
    """Just the commands mint_log sends, pipelined or not."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.values.pop(key, None) is not None)
            self.ttls.pop(key, None)
        return removed

    async def lpush(self, key: str, value: Any) -> int:
        self.values.setdefault(key, []).insert(0, value)
        return len(self.values[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self.values[key] = self.values.get(key, [])[start : end + 1]
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[Any]:
        return list(self.values.get(key, [])[start : end + 1])

    def pipeline(self) -> MemoryPipeline:
        return MemoryPipeline(self)


class MemoryPipeline:
    def __init__(self, redis: MemoryRedis) -> None:
        self._redis = redis
        self._calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def queue(*args: Any, **kwargs: Any) -> MemoryPipeline:
            self._calls.append((name, args, kwargs))
            return self

        return queue

    async def execute(self) -> list[Any]:
        return [
            await getattr(self._redis, name)(*args, **kwargs) for name, args, kwargs in self._calls
        ]


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> MemoryRedis:
    fake = MemoryRedis()
    monkeypatch.setattr(mint_log, "get_redis", lambda: fake)
    return fake


async def test_a_backoff_is_recorded_for_each_platform_its_route_mints(
    redis: MemoryRedis,
) -> None:
    await mint_log.backing_off(
        (Platform.DOUYIN, Platform.TIKTOK), route="browser", failures=2, seconds=120
    )

    snapshot = await mint_log.snapshot()
    assert set(snapshot["backoffs"]) == {"douyin", "tiktok"}
    assert snapshot["backoffs"]["douyin"]["route"] == "browser"
    assert snapshot["backoffs"]["douyin"]["failures"] == 2
    assert redis.ttls[mint_log.backoff_key(Platform.TIKTOK)] == 120
    # The one field an older reader looked at is still filled in.
    assert snapshot["backoff"]["failures"] == 2


async def test_recovering_one_route_leaves_the_other_resting(redis: MemoryRedis) -> None:
    await mint_log.backing_off(
        (Platform.DOUYIN, Platform.TIKTOK), route="browser", failures=1, seconds=60
    )
    await mint_log.backing_off((Platform.BILIBILI,), route="native", failures=3, seconds=240)

    await mint_log.recovered((Platform.BILIBILI,))

    assert set((await mint_log.snapshot())["backoffs"]) == {"douyin", "tiktok"}


async def test_nothing_resting_is_an_empty_mapping(redis: MemoryRedis) -> None:
    snapshot = await mint_log.snapshot()
    assert snapshot["backoffs"] == {}
    assert snapshot["backoff"] is None


async def test_a_browser_failure_rests_douyin_and_tiktok_but_not_bilibili(
    redis: MemoryRedis,
) -> None:
    rpc = FakeRpc(error=BrowserRpcUnavailable("browser is down"))
    filler = make_filler(rpc, PerPlatformPool({}), native=FakeNativeMinter())

    result = await filler.top_up_once(Platform.DOUYIN)

    assert result.reason == "rpc_unavailable"
    assert set((await mint_log.snapshot())["backoffs"]) == {"douyin", "tiktok"}


async def test_a_bilibili_mint_no_longer_clears_the_browsers_backoff(
    redis: MemoryRedis,
) -> None:
    """The bug: any success deleted the one shared key."""
    rpc = FakeRpc(error=BrowserRpcUnavailable("browser is down"))
    filler = make_filler(rpc, PerPlatformPool({}), native=FakeNativeMinter())
    await filler.top_up_once(Platform.DOUYIN)

    minted = await filler.top_up_once(Platform.BILIBILI)

    assert minted.minted is True
    assert set((await mint_log.snapshot())["backoffs"]) == {"douyin", "tiktok"}


async def test_a_browser_success_clears_both_browser_platforms(redis: MemoryRedis) -> None:
    rpc = FakeRpc(error=BrowserRpcUnavailable("browser is down"))
    filler = make_filler(rpc, PerPlatformPool({}), native=FakeNativeMinter())
    await filler.top_up_once(Platform.TIKTOK)

    rpc.error = None
    minted = await filler.top_up_once(Platform.DOUYIN)

    assert minted.minted is True
    assert (await mint_log.snapshot())["backoffs"] == {}


async def test_a_pressed_mint_that_fails_records_no_backoff(redis: MemoryRedis) -> None:
    """A button press says nothing about the sweep, so it does not rest it."""
    rpc = FakeRpc(error=BrowserRpcUnavailable("browser is down"))
    filler = make_filler(rpc, PerPlatformPool({}))

    await filler.top_up_once(Platform.DOUYIN, shared_backoff=False)

    snapshot = await mint_log.snapshot()
    assert snapshot["backoffs"] == {}
    assert snapshot["recent"][0]["reason"] == "rpc_unavailable"
