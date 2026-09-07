"""Task queue behaviour against real Redis."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from dtk.services import cache
from dtk.services.tasks import QUEUE_KEY, SIGNAL_KEY, claim

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_claim_returns_none_when_queue_is_empty(redis_client):
    assert await claim(timeout=1) is None


async def test_claim_pops_in_fifo_order(redis_client):
    ids = [uuid.uuid4() for _ in range(3)]
    for task_id in ids:
        await redis_client.rpush(QUEUE_KEY, str(task_id))
    assert [await claim(timeout=1) for _ in ids] == ids


async def test_claim_ignores_a_malformed_entry(redis_client):
    await redis_client.rpush(QUEUE_KEY, "not-a-uuid")
    assert await claim(timeout=1) is None


async def test_signal_wakes_a_waiter_immediately(redis_client):
    """Long polling must not have to wait out its poll interval."""
    task_id = uuid.uuid4()
    key = SIGNAL_KEY.format(task_id=task_id)

    async def waiter():
        return await redis_client.blpop([key], timeout=5)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    await redis_client.rpush(key, "1")
    assert await asyncio.wait_for(task, timeout=3) is not None


class TestCache:
    async def test_round_trip(self, redis_client):
        digest = cache.cache_key("douyin.content.detail", {"aweme_id": "123"})
        assert await cache.get(digest) is None
        await cache.put(digest, {"hello": "world"}, ttl=60)
        assert await cache.get(digest) == {"hello": "world"}

    async def test_key_ignores_parameter_order_and_none(self, redis_client):
        a = cache.cache_key("e", {"b": 2, "a": 1})
        b = cache.cache_key("e", {"a": 1, "b": 2, "z": None})
        assert a == b

    async def test_different_endpoints_do_not_collide(self, redis_client):
        assert cache.cache_key("a", {"x": 1}) != cache.cache_key("b", {"x": 1})

    async def test_corrupt_entry_is_dropped_rather_than_raising(self, redis_client):
        digest = cache.cache_key("e", {"x": 1})
        await redis_client.set(cache.CACHE_KEY.format(digest=digest), "{not json")
        assert await cache.get(digest) is None
        assert await redis_client.get(cache.CACHE_KEY.format(digest=digest)) is None

    async def test_zero_ttl_is_not_stored(self, redis_client):
        digest = cache.cache_key("e", {"x": 2})
        await cache.put(digest, {"a": 1}, ttl=0)
        assert await cache.get(digest) is None


class TestCoalescing:
    async def test_second_caller_is_told_who_is_already_fetching(self, redis_client):
        """A hundred callers for one video must produce one upstream request."""
        digest = cache.cache_key("e", {"id": "1"})
        assert await cache.claim_inflight(digest, "task-a") is None
        assert await cache.claim_inflight(digest, "task-b") == "task-a"

    async def test_release_frees_the_claim(self, redis_client):
        digest = cache.cache_key("e", {"id": "2"})
        await cache.claim_inflight(digest, "task-a")
        await cache.release_inflight(digest, "task-a")
        assert await cache.claim_inflight(digest, "task-b") is None

    async def test_release_by_a_non_owner_is_ignored(self, redis_client):
        digest = cache.cache_key("e", {"id": "3"})
        await cache.claim_inflight(digest, "task-a")
        await cache.release_inflight(digest, "task-b")
        assert await cache.claim_inflight(digest, "task-c") == "task-a"

    async def test_concurrent_claims_elect_exactly_one_owner(self, redis_client):
        digest = cache.cache_key("e", {"id": "4"})
        results = await asyncio.gather(
            *[cache.claim_inflight(digest, f"task-{i}") for i in range(30)]
        )
        assert sum(1 for r in results if r is None) == 1
