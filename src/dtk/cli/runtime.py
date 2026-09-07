"""Process bootstrap for one CLI invocation.

Every command that touches state goes through :func:`with_context`, which owns
the whole lifecycle: read the bootstrap environment, open the engine and - only
when the command needs it - Redis, run one unit of work inside a transaction,
then dispose of both. A CLI process is short-lived, so connections are opened
per invocation rather than kept in a pool.

The single seam matters twice over. It is the only place that translates an
infrastructure failure into the exit-code contract of :mod:`dtk.cli.output`,
and it is the only thing the CLI tests have to replace to run without a
database or a Redis.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.cli import output
from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher, SecretKeyMissing
from dtk.core.db import dispose_engine, init_engine, session_scope
from dtk.core.errors import DtkError
from dtk.core.redis import close_redis, init_redis


@dataclass(frozen=True, slots=True)
class Context:
    """What a unit of CLI work is handed.

    ``config`` is the database-backed runtime snapshot when the command asked
    for it, and the code defaults otherwise, so a command never has to test
    whether configuration was loaded.
    """

    settings: BootstrapSettings
    cipher: Cipher
    session: AsyncSession
    config: Config


type Operation[T] = Callable[[Context], Awaitable[T]]


def load_settings() -> BootstrapSettings:
    """Read the bootstrap environment, or explain what is missing and stop.

    A self-hosted operator reaching for the CLI is often mid-incident; an
    unhandled pydantic traceback at that moment is worse than useless.
    """
    try:
        return BootstrapSettings()
    except SecretKeyMissing as exc:
        output.fail(
            str(exc),
            hint="export DTK_SECRET_KEY, or run the CLI inside the container that has it",
        )
    except ValidationError as exc:
        output.fail(f"invalid bootstrap configuration: {exc}", hint="check the DTK_* environment")


def run[T](factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run one coroutine and map infrastructure failures onto exit code 1.

    A factory rather than a coroutine: an unawaited coroutine object left
    behind by an early failure produces a warning that hides the real error.
    """
    try:
        return asyncio.run(factory())
    except DtkError as exc:
        output.fail(f"{exc.code.value}: {exc}")
    except SQLAlchemyError as exc:
        output.fail(
            f"database error: {exc}",
            hint="check DTK_DATABASE_URL, and that migrations have been applied (dtk migrate)",
        )
    except RedisError as exc:
        output.fail(f"redis error: {exc}", hint="check DTK_REDIS_URL")
    except httpx.HTTPError as exc:
        # httpx errors are not OSError. Short-link expansion, browser-rpc and
        # the proxy probes all run on httpx, so without this a `dtk fetch` on a
        # box with no egress prints a traceback instead of exiting 1.
        output.fail(f"{type(exc).__name__}: {exc}", hint="check egress, and the proxy if one is set")
    except OSError as exc:
        output.fail(f"cannot reach a dependency: {exc}")


@asynccontextmanager
async def open_context(
    settings: BootstrapSettings, *, redis: bool = False, config: bool = False
) -> AsyncIterator[Context]:
    """Open the engine, optionally Redis, and yield one transactional context."""
    init_engine(settings.database_url, pool_size=2)
    try:
        if redis:
            init_redis(settings.redis_url, max_connections=8)
        async with session_scope() as session:
            snapshot = Config.defaults()
            if config:
                from dtk.services.settings_store import load_config

                snapshot = await load_config()
            yield Context(
                settings=settings,
                cipher=Cipher(settings.secret_key),
                session=session,
                config=snapshot,
            )
    finally:
        if redis:
            await close_redis()
        await dispose_engine()


def with_context[T](
    operation: Operation[T],
    *,
    redis: bool = False,
    config: bool = False,
) -> T:
    """Run ``operation`` against a live context and return its result.

    ``redis`` and ``config`` are opt-in because ``dtk user passwd`` must work on
    a box where Redis is down: an unreachable cache has nothing to do with
    resetting a password, and making it a hard dependency would take away the
    rescue path the command exists for.
    """
    settings = load_settings()

    async def _main() -> T:
        async with open_context(settings, redis=redis, config=config) as ctx:
            return await operation(ctx)

    return run(_main)


__all__ = [
    "Context",
    "Operation",
    "load_settings",
    "open_context",
    "run",
    "with_context",
]
