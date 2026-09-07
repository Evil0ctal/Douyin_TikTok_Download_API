"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


def init_engine(url: str, *, echo: bool = False, pool_size: int = 10) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=pool_size * 2,
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
