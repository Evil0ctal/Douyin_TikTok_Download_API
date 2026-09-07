"""Redis connection and Lua script registry.

Redis carries three kinds of state: the task queue, scheduler token buckets and
inflight locks, and the response cache. Scheduler state is manipulated through
registered Lua scripts so that check-and-take is atomic; see
docs/design/03-scheduler.md.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

_client: Redis | None = None
_scripts: dict[str, AsyncScript] = {}


def init_redis(url: str, *, max_connections: int = 32) -> Redis:
    global _client
    _client = Redis.from_url(
        url, max_connections=max_connections, decode_responses=True, health_check_interval=30
    )
    _scripts.clear()
    return _client


def get_redis() -> Redis:
    if _client is None:
        raise RuntimeError("redis not initialised; call init_redis() first")
    return _client


def register_script(name: str, source: str) -> AsyncScript:
    """Register a Lua script once and reuse its SHA on later calls."""
    if name not in _scripts:
        _scripts[name] = get_redis().register_script(source)
    return _scripts[name]


async def run_script(name: str, source: str, keys: list[str], args: list[Any]) -> Any:
    return await register_script(name, source)(keys=keys, args=args)


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
    _scripts.clear()


__all__ = ["close_redis", "get_redis", "init_redis", "register_script", "run_script"]
