"""Shared plumbing for the repositories.

Kept in its own module so the relational repositories and the append-only
time-series writers can both use it without importing each other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def utcnow() -> datetime:
    """Timezone-aware now. Naive datetimes have no business near timestamptz."""
    return datetime.now(UTC)


def affected(result: Any) -> int:
    """Rows touched by an UPDATE or DELETE.

    ``AsyncSession.execute`` is declared as returning ``Result``; only the
    cursor result underneath carries ``rowcount``, which every conditional
    update in these repositories depends on.
    """
    return int(result.rowcount)


class Repository:
    """Holds a session and never owns the transaction.

    The caller opens :func:`dtk.core.db.session_scope`, composes as many
    repositories as the operation needs, and commits once.
    """

    __slots__ = ("session",)

    def __init__(self, session: AsyncSession) -> None:
        self.session = session


__all__ = ["Repository", "affected", "utcnow"]
