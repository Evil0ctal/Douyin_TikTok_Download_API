"""Configurable data retention.

doc 05 pinned retention inside the first migration. Disks differ by two orders
of magnitude between a Raspberry Pi and a 2 TB server, so doc 15 moves the
windows into RUNTIME settings; changing one has to reach the database, which
means dropping the TimescaleDB policy and adding it again with the new
interval. That is what this module does, plus the two things a policy cannot
express: blanking finished task payloads, and deleting task rows that have
aged out.

``content_snapshots`` never gets a retention policy. It is the user's own
accumulated data and deleting it must stay an explicit, confirmed action; only
compression is applied automatically (doc 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.db.models import SNAPSHOT_COMPRESSION_AFTER_DAYS
from dtk.db.repositories import TaskRepository
from dtk.ops._sql import safe_execute

log = get_logger(__name__)

#: Hypertable -> the runtime setting that drives its retention window.
RETENTION_SETTINGS: Final[dict[str, str]] = {
    "request_log": "retention.request_log_days",
    "identity_events": "retention.identity_events_days",
}

#: Tables that must never receive a retention policy, whatever a caller asks.
PROTECTED_TABLES: Final[frozenset[str]] = frozenset({"content_snapshots"})

#: A window below this is almost certainly a typo, and one applied to
#: ``request_log`` would delete the evidence needed to debug the typo.
MIN_RETENTION_DAYS: Final[int] = 1
MAX_RETENTION_DAYS: Final[int] = 3650

#: Setting keys for the two task windows, which are plain SQL rather than
#: policies: the payload is blanked long before the row is deleted.
TASK_RESULT_SETTING: Final[str] = "retention.task_result_hours"
TASK_ROW_SETTING: Final[str] = "retention.task_days"


@dataclass(frozen=True, slots=True)
class PolicyChange:
    """One retention policy after it was (re)applied."""

    table: str
    days: int
    previous_days: int | None = None
    changed: bool = True
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "days": self.days,
            "previous_days": self.previous_days,
            "changed": self.changed,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """What one maintenance pass did."""

    policies: tuple[PolicyChange, ...] = ()
    task_payloads_cleared: int = 0
    task_rows_deleted: int = 0
    compression_after_days: int | None = None
    errors: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "policies": [p.as_dict() for p in self.policies],
            "task_payloads_cleared": self.task_payloads_cleared,
            "task_rows_deleted": self.task_rows_deleted,
            "compression_after_days": self.compression_after_days,
            "errors": dict(self.errors),
        }


def validate_days(table: str, days: Any) -> int:
    """Coerce and bound a retention window, or raise :class:`InvalidParam`."""
    if table in PROTECTED_TABLES:
        raise InvalidParam(
            f"{table} holds user data and must never be dropped by a policy; "
            "deleting it is a manual, confirmed action",
            details={"table": table},
        )
    if table not in RETENTION_SETTINGS:
        raise InvalidParam(
            f"unknown retention target: {table}",
            details={"supported": sorted(RETENTION_SETTINGS)},
        )
    try:
        value = int(days)
    except (TypeError, ValueError) as exc:
        raise InvalidParam(f"retention for {table} must be a whole number of days") from exc
    if not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
        raise InvalidParam(
            f"retention for {table} must be between {MIN_RETENTION_DAYS} and "
            f"{MAX_RETENTION_DAYS} days",
            details={"table": table, "days": value},
        )
    return value


async def current_policies(session: AsyncSession) -> dict[str, int | None]:
    """Retention windows TimescaleDB is currently enforcing, in days."""
    rows = await safe_execute(
        session,
        "SELECT hypertable_name, config ->> 'drop_after' AS drop_after "
        "FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'",
        event="ops.retention.policies_unreadable",
    )
    if rows is None:
        return {}
    return {str(table): _days_from_interval(value) for table, value in rows}


async def apply_policy(session: AsyncSession, table: str, days: Any) -> PolicyChange:
    """Reset one table's retention policy to ``days``.

    Removed and re-added rather than updated: TimescaleDB exposes no "change
    the interval" call, and ``add_retention_policy`` on a table that already
    has one is a no-op with ``if_not_exists``, which would silently keep the
    old window - the exact failure the setting was introduced to remove.
    """
    window = validate_days(table, days)
    previous = (await current_policies(session)).get(table)
    if previous == window:
        return PolicyChange(table=table, days=window, previous_days=previous, changed=False)

    removed = await safe_execute(
        session,
        f"SELECT remove_retention_policy('{table}', if_exists => TRUE)",
        event="ops.retention.policy_remove_failed",
    )
    added = await safe_execute(
        session,
        f"SELECT add_retention_policy('{table}', "
        "drop_after => make_interval(days => CAST(:days AS integer)), "
        "if_not_exists => TRUE)",
        {"days": window},
        event="ops.retention.policy_add_failed",
    )
    if removed is None or added is None:
        # Most often TimescaleDB is absent. Reported rather than raised: the
        # rest of a maintenance pass is still worth running.
        log.error("ops.retention.policy_failed", table=table, days=window)
        return PolicyChange(
            table=table,
            days=window,
            previous_days=previous,
            changed=False,
            error="the retention policy could not be applied; is TimescaleDB installed?",
        )

    log.info("ops.retention.policy_applied", table=table, days=window, previous=previous)
    return PolicyChange(table=table, days=window, previous_days=previous, changed=True)


async def apply_compression_policy(
    session: AsyncSession, days: int = SNAPSHOT_COMPRESSION_AFTER_DAYS
) -> int | None:
    """Set how old a snapshot chunk must be before it is compressed.

    Compression, never deletion: the rows stay readable, they just stop costing
    what uncompressed rows cost.
    """
    window = max(1, int(days))
    # TimescaleDB renamed compression to the columnstore in 2.18. Both spellings
    # are attempted and whichever this server does not have simply fails inside
    # its own savepoint. The columnstore forms are PROCEDURES, so they are
    # invoked with CALL; SELECT raises "is a procedure" on every server that has
    # them, which would leave the deprecated compression spelling as the only
    # one that ever ran.
    for statement in (
        "CALL remove_columnstore_policy('content_snapshots', if_exists => TRUE)",
        "SELECT remove_compression_policy('content_snapshots', if_exists => TRUE)",
    ):
        await safe_execute(session, statement, event="ops.retention.compression_remove_failed")

    for statement in (
        "CALL add_columnstore_policy('content_snapshots', "
        "after => make_interval(days => CAST(:days AS integer)), if_not_exists => TRUE)",
        "SELECT add_compression_policy('content_snapshots', "
        "make_interval(days => CAST(:days AS integer)), if_not_exists => TRUE)",
    ):
        applied = await safe_execute(
            session,
            statement,
            {"days": window},
            event="ops.retention.compression_variant_failed",
        )
        if applied is not None:
            log.info("ops.retention.compression_applied", table="content_snapshots", days=window)
            return window

    log.warning("ops.retention.compression_unavailable", table="content_snapshots")
    return None


async def blank_expired_task_payloads(session: AsyncSession, *, hours: int) -> int:
    """Clear results and errors of tasks finished longer than ``hours`` ago.

    The row survives so the statistics do; a later lookup of such a task
    answers TASK_NOT_FOUND, which is the documented behaviour.
    """
    window = max(1, int(hours))
    return await TaskRepository(session).purge_results(older_than=timedelta(hours=window))


async def delete_expired_tasks(session: AsyncSession, *, days: int) -> int:
    """Delete task rows older than ``days``."""
    window = max(1, int(days))
    return await TaskRepository(session).delete_old(older_than=timedelta(days=window))


async def apply_retention(session: AsyncSession, config: Any) -> tuple[PolicyChange, ...]:
    """Bring every hypertable policy in line with the current settings.

    A setting that is missing or out of range is reported on its own row rather
    than raised. Raising here would abort the whole maintenance pass - task
    payloads would stop being blanked and compression would stop being applied -
    because one number in the settings table was wrong.
    """
    changes: list[PolicyChange] = []
    for table, key in RETENTION_SETTINGS.items():
        try:
            changes.append(await apply_policy(session, table, config.get(key)))
        except InvalidParam as exc:
            log.error("ops.retention.setting_invalid", table=table, setting=key, error=str(exc))
            changes.append(
                PolicyChange(
                    table=table,
                    days=0,
                    changed=False,
                    error=f"{key} is not a usable retention window: {exc}",
                )
            )
    return tuple(changes)


async def run_maintenance(
    session: AsyncSession,
    config: Any,
    *,
    compression_after_days: int = SNAPSHOT_COMPRESSION_AFTER_DAYS,
) -> RetentionReport:
    """One full pass: policies, task payloads, task rows, compression.

    Called by the background worker after a settings change and on a schedule.
    Every part is independent, so one failure - a server without TimescaleDB,
    say - never stops the others from running.
    """
    errors: dict[str, str] = {}
    policies = await apply_retention(session, config)
    for change in policies:
        if change.error:
            errors[change.table] = change.error

    cleared = 0
    deleted = 0
    try:
        cleared = await blank_expired_task_payloads(
            session, hours=int(config.get(TASK_RESULT_SETTING))
        )
    except Exception as exc:
        errors["task_payloads"] = str(exc)[:200]
    try:
        deleted = await delete_expired_tasks(session, days=int(config.get(TASK_ROW_SETTING)))
    except Exception as exc:
        errors["task_rows"] = str(exc)[:200]

    compressed = await apply_compression_policy(session, compression_after_days)

    report = RetentionReport(
        policies=policies,
        task_payloads_cleared=cleared,
        task_rows_deleted=deleted,
        compression_after_days=compressed,
        errors=errors,
    )
    log.info(
        "ops.retention.maintenance_done",
        policies=len(policies),
        payloads_cleared=cleared,
        rows_deleted=deleted,
        errors=len(errors),
    )
    return report


def _days_from_interval(value: Any) -> int | None:
    """Days in a Postgres interval literal such as ``14 days`` or ``1 mon``.

    Anything this cannot read returns None rather than a guess: a wrong number
    here would make the caller think a policy is already correct and skip the
    update.
    """
    if value is None:
        return None
    text_value = str(value).strip().lower()
    parts = text_value.split()
    if len(parts) == 2 and parts[1].startswith("day"):
        try:
            return int(parts[0])
        except ValueError:
            return None
    if len(parts) == 2 and parts[1].startswith("mon"):
        try:
            return int(parts[0]) * 30
        except ValueError:
            return None
    return None


__all__ = [
    "MAX_RETENTION_DAYS",
    "MIN_RETENTION_DAYS",
    "PROTECTED_TABLES",
    "RETENTION_SETTINGS",
    "TASK_RESULT_SETTING",
    "TASK_ROW_SETTING",
    "PolicyChange",
    "RetentionReport",
    "apply_compression_policy",
    "apply_policy",
    "apply_retention",
    "blank_expired_task_payloads",
    "current_policies",
    "delete_expired_tasks",
    "run_maintenance",
    "validate_days",
]
