"""Redis connection and Lua script registry.

Redis carries three kinds of state: the task queue, scheduler token buckets and
inflight locks, and the response cache. Scheduler state is manipulated through
registered Lua scripts so that check-and-take is atomic; see
docs/design/03-scheduler.md.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import BlockingConnectionPool, Redis
from redis.commands.core import AsyncScript

_client: Redis | None = None
_scripts: dict[str, AsyncScript] = {}


#: Longest a blocking Redis command in this codebase waits, plus room.
#:
#: redis-py 8 changed the default socket_timeout from None to 5 seconds, which
#: is exactly what ``tasks.claim`` passes to BLPOP. The read then times out at
#: the same instant the command is due to return empty, so every idle poll
#: raised instead of returning None: the worker logged "Timeout reading from
#: redis" every few seconds and picked tasks up late. The socket has to be
#: allowed to outlast the command it is carrying, and the value is set here
#: rather than inherited so the next library default cannot move it back.
SOCKET_TIMEOUT_SECONDS: float = 30.0


def init_redis(
    url: str,
    *,
    max_connections: int = 64,
    pool_timeout: float = 10.0,
    socket_timeout: float = SOCKET_TIMEOUT_SECONDS,
) -> Redis:
    """Create the shared client.

    A blocking pool is used deliberately. The default pool raises
    ``MaxConnectionsError`` the moment it is exhausted, which turns a burst of
    concurrent scheduling into hard failures rather than brief waits - the
    opposite of what backpressure should do.
    """
    global _client
    pool: BlockingConnectionPool = BlockingConnectionPool.from_url(
        url,
        max_connections=max_connections,
        timeout=pool_timeout,
        socket_timeout=socket_timeout,
        decode_responses=True,
        health_check_interval=30,
    )
    _client = Redis(connection_pool=pool)
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
        # redis-py renamed close() to aclose() in 5.x; the bundled type stubs
        # still describe the old name, so this is resolved at runtime.
        await _client.aclose()  # type: ignore[attr-defined]
    _client = None
    _scripts.clear()


__all__ = [
    "SOCKET_TIMEOUT_SECONDS",
    "close_redis",
    "get_redis",
    "init_redis",
    "register_script",
    "run_script",
]
