"""Statements that are allowed to fail.

Operational code asks the database questions it may not be able to answer: an
instance without TimescaleDB has no ``approximate_row_count``, a server on 2.17
has no ``add_columnstore_policy``, a table may not exist yet before the first
migration. Every one of those raises.

In PostgreSQL a raised statement poisons the whole transaction - every later
statement fails with "current transaction is aborted" until a rollback. Probing
without a savepoint therefore turns one optional query into a broken status
page. These helpers run each optional statement inside ``SAVEPOINT`` so a
failure costs exactly that statement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.logging import get_logger

log = get_logger(__name__)


async def safe_execute(
    session: AsyncSession,
    statement: str,
    params: Mapping[str, Any] | None = None,
    *,
    event: str = "ops.sql.failed",
) -> Sequence[Row[Any]] | None:
    """Run one statement in a savepoint. Returns None when it failed."""
    try:
        async with session.begin_nested():
            result = await session.execute(text(statement), dict(params or {}))
            # ``returns_rows`` lives on the cursor result rather than on the
            # declared return type; a DDL-style call has none.
            if not getattr(result, "returns_rows", False):
                return []
            return cast("Sequence[Row[Any]]", result.all())
    except Exception as exc:
        log.debug(event, statement=statement[:120], error=str(exc)[:200])
        return None


async def safe_scalar(
    session: AsyncSession,
    statement: str,
    params: Mapping[str, Any] | None = None,
    *,
    event: str = "ops.sql.failed",
) -> Any:
    """First column of the first row, or None when the statement failed."""
    rows = await safe_execute(session, statement, params, event=event)
    if not rows:
        return None
    first = rows[0]
    return first[0] if len(first) else None


__all__ = ["safe_execute", "safe_scalar"]
