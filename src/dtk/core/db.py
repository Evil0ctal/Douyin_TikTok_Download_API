"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

#: Processes that each open a pool of their own: the api and the worker.
POOLED_PROCESSES: Final = 2
#: Connections from everything else - TimescaleDB's background workers, a CLI
#: command, a migration - that the budget leaves room for.
OTHER_CONNECTIONS: Final = 5


def connection_budget(pool_size: int, max_overflow: int) -> int:
    """The most connections this deployment can open at once.

    Compared with Postgres' ``max_connections`` by the diagnosis. The pools
    were a fixed 10 + 20 per process until that sum met the 25 connections
    TimescaleDB tunes itself to inside a 1 GB container: under load the demo
    answered "too many clients already" and served 500s while every pool still
    believed it had room.
    """
    return POOLED_PROCESSES * (pool_size + max_overflow) + OTHER_CONNECTIONS


def init_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int | None = None,
) -> AsyncEngine:
    """Open the process-wide engine.

    ``max_overflow`` defaults to ``pool_size``. Past both, a session waits for a
    connection to come back - SQLAlchemy's ``pool_timeout`` - rather than
    asking Postgres for one it may not have.
    """
    global _engine, _sessionmaker
    _engine = create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=pool_size if max_overflow is None else max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("engine not initialised; call init_engine() first")
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session. Commits on success, rolls back on any exception."""
    if _sessionmaker is None:
        raise RuntimeError("engine not initialised; call init_engine() first")
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


__all__ = ["Base", "dispose_engine", "get_engine", "init_engine", "session_scope"]
