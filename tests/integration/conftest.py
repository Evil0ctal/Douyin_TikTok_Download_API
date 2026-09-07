"""Fixtures for tests that need real PostgreSQL and Redis.

Services are started with:
    docker compose -p dtk-test -f docker/compose.test.yml up -d --wait
and torn down with `down -v`. Nothing here starts a container on its own, so a
failed run never leaves one behind.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from dtk.core.redis import close_redis, init_redis

TEST_REDIS_URL = os.environ.get("DTK_TEST_REDIS_URL", "redis://127.0.0.1:56379/0")
TEST_DATABASE_URL = os.environ.get(
    "DTK_TEST_DATABASE_URL",
    "postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test",
)


@pytest_asyncio.fixture
async def redis_client():
    client = init_redis(TEST_REDIS_URL)
    try:
        await client.ping()
    except Exception as exc:
        await close_redis()
        pytest.skip(f"test Redis unavailable ({exc}); run `make fixtures-up`")
    await client.flushdb()
    yield client
    await client.flushdb()
    await close_redis()


@pytest_asyncio.fixture
async def db_engine():
    from sqlalchemy import text

    from dtk.core.db import dispose_engine, init_engine

    engine = init_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await dispose_engine()
        pytest.skip(f"test PostgreSQL unavailable ({exc}); run `make fixtures-up`")
    yield engine
    await dispose_engine()


@pytest.fixture
def fake_clock():
    """A clock the tests advance by hand, so backoff needs no real sleeping."""

    class Clock:
        def __init__(self) -> None:
            self.t = 1_700_000_000.0

        def __call__(self) -> float:
            return self.t

        def advance(self, seconds: float) -> None:
            self.t += seconds

    return Clock()
