"""How many database connections a deployment can open, and from where it is set.

The pools were a fixed 10 + 20 per process. On the demo, whose Postgres had
tuned itself to 25 connections inside a 1 GB container, that let the api and
the worker together ask for more than the server would give, and requests
failed with "too many clients already".
"""

from __future__ import annotations

import pytest

from dtk.core import db
from dtk.core.config import BootstrapSettings

SECRET = "x" * 48


async def test_the_engine_takes_both_limits() -> None:
    engine = db.init_engine("postgresql+asyncpg://u:p@localhost:1/x", pool_size=3, max_overflow=7)
    try:
        assert engine.pool.size() == 3  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == 7  # type: ignore[attr-defined]
    finally:
        await db.dispose_engine()


async def test_overflow_defaults_to_the_pool_size_not_twice_it() -> None:
    engine = db.init_engine("postgresql+asyncpg://u:p@localhost:1/x", pool_size=4)
    try:
        assert engine.pool._max_overflow == 4  # type: ignore[attr-defined]
    finally:
        await db.dispose_engine()


def test_the_budget_is_both_processes_and_room_for_the_rest() -> None:
    assert db.connection_budget(5, 5) == 2 * 10 + db.OTHER_CONNECTIONS
    # The old fixed pools: more than the 25 the demo's Postgres accepted.
    assert db.connection_budget(10, 20) > 25


def test_the_defaults_fit_a_self_tuned_postgres() -> None:
    settings = BootstrapSettings(secret_key=SECRET)
    assert db.connection_budget(settings.db_pool_size, settings.db_max_overflow) <= 25


def test_the_pool_is_set_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DTK_DB_POOL_SIZE", "8")
    monkeypatch.setenv("DTK_DB_MAX_OVERFLOW", "0")
    settings = BootstrapSettings(secret_key=SECRET)
    assert (settings.db_pool_size, settings.db_max_overflow) == (8, 0)


def test_a_pool_of_nothing_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DTK_DB_POOL_SIZE", "0")
    with pytest.raises(ValueError):
        BootstrapSettings(secret_key=SECRET)
