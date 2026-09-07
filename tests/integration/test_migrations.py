"""The migration must produce a complete database on its own.

A fresh `docker compose up` runs this once and nothing else. If a hypertable, a
continuous aggregate or a policy is missing afterwards, the deployment looks
healthy and then behaves strangely under load - the failure mode that is hardest
to diagnose. So this asserts the physical shape of the database, not just that
alembic exited zero.

Regression guarded here: add_columnstore_policy is a PROCEDURE, so calling it
with SELECT aborts the migration partway through.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EXPECTED_TABLES = {
    "users",
    "api_keys",
    "proxies",
    "identities",
    "tasks",
    "settings",
    "settings_version",
    "audit_log",
    "request_log",
    "identity_events",
    "content_snapshots",
}

EXPECTED_HYPERTABLES = {
    "request_log": 1,
    "identity_events": 7,
    "content_snapshots": 7,
}


async def _scalars(engine, sql: str, **params):
    async with engine.connect() as conn:
        return list((await conn.execute(text(sql), params)).scalars())


async def test_every_table_exists(db_engine):
    found = set(
        await _scalars(db_engine, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    assert found >= EXPECTED_TABLES, f"missing: {EXPECTED_TABLES - found}"


async def test_hypertables_with_expected_chunk_intervals(db_engine):
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT h.table_name, d.interval_length / 1000000 / 86400 AS days
                    FROM _timescaledb_catalog.hypertable h
                    JOIN _timescaledb_catalog.dimension d ON d.hypertable_id = h.id
                    WHERE h.schema_name = 'public'
                    """
                )
            )
        ).all()
    actual = {name: int(days) for name, days in rows}
    for table, days in EXPECTED_HYPERTABLES.items():
        assert table in actual, f"{table} is not a hypertable"
        assert actual[table] == days, f"{table} chunk interval is {actual[table]}d, want {days}d"


async def test_continuous_aggregates_exist(db_engine):
    views = set(
        await _scalars(
            db_engine,
            "SELECT view_name FROM timescaledb_information.continuous_aggregates",
        )
    )
    assert "identity_health_5m" in views
    assert any("endpoint" in v for v in views)


async def test_endpoint_health_view_can_count_distinct_identities(db_engine):
    """The circuit breaker's third condition depends on this column.

    Continuous aggregates cannot do COUNT(DISTINCT), so the per-(endpoint,
    identity) rows are materialized and a plain view aggregates over them.
    Without identities_used, one flapping identity would trip a whole endpoint.
    """
    columns = set(
        await _scalars(
            db_engine,
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'endpoint_health_5m'
            """,
        )
    )
    assert "identities_used" in columns
    assert {"total", "ok", "risk", "endpoint", "bucket"} <= columns


async def test_background_policies_are_registered(db_engine):
    procs = await _scalars(
        db_engine,
        "SELECT proc_name FROM timescaledb_information.jobs WHERE job_id >= 1000",
    )
    assert any(p == "policy_retention" for p in procs), "no retention policy"
    assert any("refresh_continuous_aggregate" in p for p in procs), "aggregates never refresh"
    # The regression: this one is a procedure and needs CALL. If the migration
    # used SELECT it would have aborted before reaching here, but assert the
    # end state anyway so a future rewrite cannot quietly drop it.
    assert any(p in ("policy_compression", "policy_columnstore") for p in procs), (
        "content_snapshots is never compressed"
    )


async def test_request_log_carries_the_correlation_id(db_engine):
    """docs/design/06 promises meta.request_id is written here.

    The two documents contradicted each other at one point; this keeps them
    honest, because request_id is the only handle a user has when reporting a
    problem.
    """
    columns = set(
        await _scalars(
            db_engine,
            "SELECT column_name FROM information_schema.columns WHERE table_name='request_log'",
        )
    )
    assert {"request_id", "task_id", "outcome", "reject_reason", "identity_id"} <= columns


async def test_identities_table_enforces_its_enums(db_engine):
    constraints = await _scalars(
        db_engine,
        """
        SELECT conname FROM pg_constraint
        WHERE contype = 'c' AND conrelid = 'identities'::regclass
        """,
    )
    assert any("platform" in c for c in constraints)
    assert any("source" in c for c in constraints)
