"""Retention policies and task payload expiry.

The assertion that matters most is the negative one: ``content_snapshots`` must
never acquire a retention policy. It is the user's own accumulated data, and a
policy on it would delete months of history on a schedule nobody watched.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.core.errors import InvalidParam
from dtk.ops import retention


class _AsyncCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FakeResult:
    def __init__(self, rows: list[Any], rowcount: int = 0) -> None:
        self._rows = rows
        self.returns_rows = True
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Records every statement and answers by matching a fragment."""

    def __init__(
        self,
        answers: dict[str, list[Any]] | None = None,
        *,
        failing: tuple[str, ...] = (),
        rowcount: int = 0,
    ) -> None:
        self._answers = answers or {}
        self._failing = failing
        self._rowcount = rowcount
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def begin_nested(self) -> _AsyncCtx:
        return _AsyncCtx()

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        rendered = str(statement)
        self.executed.append((rendered, dict(params or {})))
        for fragment in self._failing:
            if fragment in rendered:
                raise RuntimeError(f"function {fragment} does not exist")
        for fragment, rows in self._answers.items():
            if fragment in rendered:
                return FakeResult(rows, self._rowcount)
        return FakeResult([], self._rowcount)


class StubConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str) -> Any:
        return self._values.get(key)


DEFAULTS = StubConfig(
    {
        "retention.request_log_days": 14,
        "retention.identity_events_days": 90,
        "retention.task_days": 90,
        "retention.task_result_hours": 24,
    }
)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_user_data_never_gets_a_retention_policy() -> None:
    assert "content_snapshots" not in retention.RETENTION_SETTINGS
    assert "content_snapshots" in retention.PROTECTED_TABLES

    with pytest.raises(InvalidParam) as raised:
        retention.validate_days("content_snapshots", 30)
    assert "must never be dropped" in str(raised.value)


@pytest.mark.parametrize("days", [0, -1, retention.MAX_RETENTION_DAYS + 1, "soon", None])
def test_out_of_range_windows_are_refused(days: Any) -> None:
    with pytest.raises(InvalidParam):
        retention.validate_days("request_log", days)


def test_unknown_tables_are_refused() -> None:
    with pytest.raises(InvalidParam):
        retention.validate_days("pg_class", 14)


def test_valid_windows_are_coerced_to_int() -> None:
    assert retention.validate_days("request_log", "14") == 14


# --------------------------------------------------------------------------
# policies
# --------------------------------------------------------------------------


async def test_policy_is_removed_before_it_is_added() -> None:
    """add_retention_policy(if_not_exists) alone would keep the old window."""
    session = FakeSession()

    change = await retention.apply_policy(session, "request_log", 30)  # type: ignore[arg-type]

    statements = [sql for sql, _ in session.executed]
    remove = next(i for i, sql in enumerate(statements) if "remove_retention_policy" in sql)
    add = next(i for i, sql in enumerate(statements) if "add_retention_policy" in sql)
    assert remove < add
    assert session.executed[add][1] == {"days": 30}
    assert change.changed is True
    assert change.days == 30
    assert change.error is None


async def test_an_unchanged_window_touches_nothing() -> None:
    session = FakeSession({"timescaledb_information.jobs": [("request_log", "14 days")]})

    change = await retention.apply_policy(session, "request_log", 14)  # type: ignore[arg-type]

    assert change.changed is False
    assert change.previous_days == 14
    assert not any("add_retention_policy" in sql for sql, _ in session.executed)


async def test_a_server_without_timescaledb_reports_instead_of_raising() -> None:
    session = FakeSession(failing=("add_retention_policy",))

    change = await retention.apply_policy(session, "request_log", 30)  # type: ignore[arg-type]

    assert change.changed is False
    assert change.error is not None
    assert "TimescaleDB" in change.error


async def test_apply_retention_covers_every_configured_table() -> None:
    session = FakeSession()

    changes = await retention.apply_retention(session, DEFAULTS)  # type: ignore[arg-type]

    assert {c.table for c in changes} == set(retention.RETENTION_SETTINGS)
    assert {c.days for c in changes} == {14, 90}


async def test_compression_falls_back_to_the_older_spelling() -> None:
    """TimescaleDB renamed compression to the columnstore in 2.18."""
    session = FakeSession(failing=("add_columnstore_policy", "remove_columnstore_policy"))

    applied = await retention.apply_compression_policy(session, 7)  # type: ignore[arg-type]

    assert applied == 7
    assert any("add_compression_policy" in sql for sql, _ in session.executed)


async def test_compression_reports_none_when_neither_call_exists() -> None:
    session = FakeSession(failing=("columnstore", "compression"))
    assert await retention.apply_compression_policy(session, 7) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("14 days", 14),
        ("1 day", 1),
        ("90 days", 90),
        ("2 mons", 60),
        ("garbage", None),
        (None, None),
    ],
)
def test_interval_parsing(value: Any, expected: int | None) -> None:
    assert retention._days_from_interval(value) == expected


# --------------------------------------------------------------------------
# task payloads
# --------------------------------------------------------------------------


async def test_expired_task_payloads_are_blanked_not_deleted() -> None:
    session = FakeSession(rowcount=7)

    cleared = await retention.blank_expired_task_payloads(session, hours=24)  # type: ignore[arg-type]

    statement = session.executed[-1][0]
    assert cleared == 7
    assert statement.startswith("UPDATE tasks")
    assert "result=" in statement.replace(" ", "")
    assert "DELETE" not in statement


async def test_old_task_rows_are_deleted() -> None:
    session = FakeSession(rowcount=3)

    deleted = await retention.delete_expired_tasks(session, days=90)  # type: ignore[arg-type]

    assert deleted == 3
    assert session.executed[-1][0].startswith("DELETE FROM tasks")


async def test_maintenance_runs_every_part_even_when_one_fails() -> None:
    session = FakeSession(failing=("add_retention_policy",), rowcount=2)

    report = await retention.run_maintenance(session, DEFAULTS)  # type: ignore[arg-type]

    assert len(report.policies) == len(retention.RETENTION_SETTINGS)
    assert report.errors  # the policy failure was recorded
    assert report.task_payloads_cleared == 2
    assert report.task_rows_deleted == 2
    assert report.compression_after_days == retention.SNAPSHOT_COMPRESSION_AFTER_DAYS
    assert set(report.as_dict()) == {
        "policies",
        "task_payloads_cleared",
        "task_rows_deleted",
        "compression_after_days",
        "errors",
    }


async def test_a_broken_retention_setting_does_not_abort_the_whole_pass() -> None:
    """One unusable number in the settings table must not stop everything else.

    Retention windows are runtime settings, so a bad value is a user typo, not
    a programming error. Raising out of the pass would silently stop task
    payloads being blanked and compression being applied as well.
    """
    session = FakeSession(rowcount=2)
    config = StubConfig(
        {
            "retention.request_log_days": "whenever",
            "retention.identity_events_days": 90,
            "retention.task_days": 90,
            "retention.task_result_hours": 24,
        }
    )

    report = await retention.run_maintenance(session, config)  # type: ignore[arg-type]

    assert "request_log" in report.errors
    healthy = next(c for c in report.policies if c.table == "identity_events")
    assert healthy.changed is True and healthy.error is None
    assert report.task_payloads_cleared == 2
    assert report.task_rows_deleted == 2
    assert report.compression_after_days == retention.SNAPSHOT_COMPRESSION_AFTER_DAYS


async def test_the_columnstore_spelling_is_invoked_as_a_procedure() -> None:
    """``add_columnstore_policy`` is a PROCEDURE; SELECT on it always raises.

    Verified against TimescaleDB 2.29: ``SELECT add_columnstore_policy(...)``
    fails with "is a procedure", so a SELECT here would make the whole 2.18+
    branch dead and leave the deprecated compression spelling as the only one
    that ever runs.
    """
    session = FakeSession()

    await retention.apply_compression_policy(session, 7)  # type: ignore[arg-type]

    columnstore = [sql for sql, _ in session.executed if "columnstore" in sql]
    assert columnstore
    assert all(sql.startswith("CALL ") for sql in columnstore)
