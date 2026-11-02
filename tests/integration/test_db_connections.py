"""The diagnosis reads Postgres' own connection limit off a real server."""

from __future__ import annotations

import pytest

from dtk.ops import diagnose

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_the_limit_is_read_as_a_number(db_engine) -> None:
    limit = await diagnose._max_connections(db_engine)

    assert isinstance(limit, int)
    assert limit > 0
