"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-07

Creates every table in docs/design/05-data-model.md, converts the three
time-series tables into TimescaleDB hypertables, materializes the health
continuous aggregates and installs the retention and compression policies.

All of it lives in the migration on purpose. A fresh ``docker compose up`` has
to produce a complete database; a schema that needs a human to log in and run
``create_hypertable`` by hand is a schema that will be missing on most
installations.

Two TimescaleDB behaviours shape this script:

* ``create_hypertable`` and the ``add_*_policy`` functions run happily inside
  the migration transaction. ``CREATE MATERIALIZED VIEW ... WITH
  (timescaledb.continuous)`` does not: it is refused inside a transaction
  block. Those statements therefore run inside
  ``op.get_context().autocommit_block()``, which commits the transaction so far
  and switches the same connection to AUTOCOMMIT for the duration.
* A continuous aggregate stores *partial* aggregate states, so every aggregate
  in it must be partializable. ``count(DISTINCT x)`` is not, in TimescaleDB no
  more than in PostgreSQL's own parallel aggregation, so doc 05's

      count(DISTINCT identity_id) AS identities_used

  cannot be materialized directly. It is produced exactly, not approximated, by
  materializing one row per (bucket, platform, endpoint, identity_id) in
  ``endpoint_identity_health_5m`` and exposing ``endpoint_health_5m`` as a view
  that rolls those rows up. The relation named ``endpoint_health_5m`` has the
  columns doc 05 specifies, which is what the scheduler and the console query.

Because a failed run leaves committed objects behind (the autocommit block
commits), every step is written to be safely repeatable: IF NOT EXISTS on
tables and indexes, ``if_not_exists => TRUE`` on the policies, and an explicit
catalog check before each continuous aggregate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Hypertable name -> chunk interval. Mirrors dtk.db.models.HYPERTABLES; the
#: migration keeps its own copy so it stays valid even after the models move on.
HYPERTABLES: dict[str, str] = {
    "request_log": "1 day",
    "identity_events": "7 days",
    "content_snapshots": "7 days",
}

RETENTION: dict[str, str] = {"request_log": "14 days", "identity_events": "90 days"}

#: Compression (columnstore) for the one table that is never auto-deleted.
SNAPSHOT_COMPRESS_AFTER = "7 days"
SNAPSHOT_SEGMENT_BY = "platform, content_id"
SNAPSHOT_ORDER_BY = "ts DESC"

#: Refresh policy window shared by both aggregates. The end offset is two
#: buckets so a bucket is only materialized once it can no longer receive rows.
#: Everything newer than that is answered by real-time aggregation, which is
#: why both views set ``timescaledb.materialized_only = false`` explicitly:
#: since TimescaleDB 2.13 a new continuous aggregate is materialized-only by
#: default, and leaving it at the default would make the scheduler's
#: circuit-breaker inputs (doc 03, third condition) up to end_offset +
#: schedule_interval stale.
CAGG_START_OFFSET = "2 hours"
CAGG_END_OFFSET = "10 minutes"
CAGG_SCHEDULE = "5 minutes"

IDENTITY_HEALTH_5M = """
CREATE MATERIALIZED VIEW identity_health_5m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('5 minutes', ts) AS bucket,
       identity_id,
       count(*)                                         AS total,
       count(*) FILTER (WHERE outcome = 'ok')           AS ok,
       count(*) FILTER (WHERE outcome = 'risk_control') AS risk,
       avg(duration_ms)                                 AS avg_ms
FROM request_log
WHERE identity_id IS NOT NULL
GROUP BY bucket, identity_id
WITH NO DATA
"""

ENDPOINT_IDENTITY_HEALTH_5M = """
CREATE MATERIALIZED VIEW endpoint_identity_health_5m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('5 minutes', ts) AS bucket,
       platform, endpoint, identity_id,
       count(*)                                         AS total,
       count(*) FILTER (WHERE outcome = 'ok')           AS ok,
       count(*) FILTER (WHERE outcome = 'risk_control') AS risk
FROM request_log
GROUP BY bucket, platform, endpoint, identity_id
WITH NO DATA
"""

ENDPOINT_HEALTH_5M = """
CREATE OR REPLACE VIEW endpoint_health_5m AS
SELECT bucket,
       platform, endpoint,
       CAST(sum(total) AS bigint)      AS total,
       CAST(sum(ok) AS bigint)         AS ok,
       CAST(sum(risk) AS bigint)       AS risk,
       count(DISTINCT identity_id)     AS identities_used
FROM endpoint_identity_health_5m
GROUP BY bucket, platform, endpoint
"""


#: The continuous aggregates, in creation order, mirroring
#: dtk.db.models.CONTINUOUS_AGGREGATES. endpoint_health_5m is not one of them:
#: it is the plain view below, rolling up the per-identity aggregate.
CAGGS: dict[str, str] = {
    "identity_health_5m": IDENTITY_HEALTH_5M,
    "endpoint_identity_health_5m": ENDPOINT_IDENTITY_HEALTH_5M,
}

#: Plain views over the aggregates, mirroring dtk.db.models.DERIVED_VIEWS.
DERIVED_VIEWS: dict[str, str] = {"endpoint_health_5m": ENDPOINT_HEALTH_5M}


def _offline() -> bool:
    """True under ``alembic upgrade --sql``, where there is nothing to query.

    Offline mode emits SQL for an operator to apply by hand, so every runtime
    probe below assumes a current TimescaleDB and an empty database.
    """
    return bool(op.get_context().as_sql)


def _require_timescaledb() -> None:
    """Fail early and legibly when the server is plain PostgreSQL."""
    if _offline():
        return
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    if not available:
        raise RuntimeError(
            "the timescaledb extension is not available on this server. dtk stores "
            "request logs, identity events and content snapshots in hypertables; run "
            "PostgreSQL from the timescale/timescaledb-ha image (docs/design/09-deployment.md)"
        )


def _create_hypertable(table: str, column: str, interval: str) -> None:
    """Convert ``table`` into a hypertable partitioned on ``column``.

    TimescaleDB 2.13 introduced the dimension-builder form and deprecated the
    positional one. Try the new form, fall back when ``by_range`` is unknown,
    so the same migration applies on both.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            PERFORM create_hypertable(
                '{table}',
                by_range('{column}', INTERVAL '{interval}'),
                if_not_exists => TRUE
            );
        EXCEPTION WHEN undefined_function THEN
            PERFORM create_hypertable(
                '{table}', '{column}',
                chunk_time_interval => INTERVAL '{interval}',
                if_not_exists => TRUE
            );
        END
        $$
        """
    )


def _has_columnstore_api() -> bool:
    """True on TimescaleDB 2.18+, where compression became the columnstore."""
    if _offline():
        return True
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT count(*) FROM pg_proc WHERE proname = 'add_columnstore_policy'")
        ).scalar()
    )


def _cagg_exists(name: str) -> bool:
    if _offline():
        return False
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM timescaledb_information.continuous_aggregates "
                "WHERE view_name = :name"
            ),
            {"name": name},
        ).scalar()
    )


def _create_relational_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'admin'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.CheckConstraint("role IN ('admin','operator','viewer')", name="ck_users_role"),
        if_not_exists=True,
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("rate_limit", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prefix"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        if_not_exists=True,
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], if_not_exists=True)
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], if_not_exists=True)

    op.create_table(
        "proxies",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("url_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=True),
        sa.Column("healthy", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index("ix_proxies_healthy", "proxies", ["healthy"], if_not_exists=True)

    op.create_table(
        "identities",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("cookies_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proxy_id", sa.Uuid(), nullable=True),
        sa.Column("authenticated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'minting'"), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_fails", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "minted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["proxy_id"], ["proxies.id"], ondelete="SET NULL"),
        sa.CheckConstraint("platform IN ('douyin','tiktok')", name="ck_identities_platform"),
        sa.CheckConstraint("source IN ('minted','imported')", name="ck_identities_source"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_identities_platform_state_last_used",
        "identities",
        ["platform", "state", "last_used_at"],
        if_not_exists=True,
    )
    op.create_index("ix_identities_proxy_id", "identities", ["proxy_id"], if_not_exists=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_tasks_state_created_at", "tasks", ["state", "created_at"], if_not_exists=True
    )
    op.create_index("ix_tasks_finished_at", "tasks", ["finished_at"], if_not_exists=True)

    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        if_not_exists=True,
    )

    op.create_table(
        "settings_version",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_settings_version_singleton"),
        if_not_exists=True,
    )
    # The single row exists from the start so every reader can assume it is
    # there and only ever UPDATE it.
    op.execute(
        "INSERT INTO settings_version (id, version) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # SET NULL, not CASCADE: deleting a user must not erase the record of
        # what that user did.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        if_not_exists=True,
    )
    op.create_index("ix_audit_log_ts", "audit_log", [sa.text("ts DESC")], if_not_exists=True)
    op.create_index(
        "ix_audit_log_action_ts", "audit_log", ["action", sa.text("ts DESC")], if_not_exists=True
    )
    op.create_index(
        "ix_audit_log_user_ts", "audit_log", ["user_id", sa.text("ts DESC")], if_not_exists=True
    )


def _create_timeseries_tables() -> None:
    op.create_table(
        "request_log",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=True),
        sa.Column("proxy_id", sa.Uuid(), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("signer", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        # No primary key and no foreign keys: this is the highest-volume insert
        # path in the system and a unique index on a hypertable would have to
        # carry the partitioning column anyway.
        if_not_exists=True,
    )
    op.create_table(
        "identity_events",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        if_not_exists=True,
    )
    op.create_table(
        "content_snapshots",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column("play_count", sa.BigInteger(), nullable=True),
        sa.Column("digg_count", sa.BigInteger(), nullable=True),
        sa.Column("comment_count", sa.BigInteger(), nullable=True),
        sa.Column("share_count", sa.BigInteger(), nullable=True),
        sa.Column("collect_count", sa.BigInteger(), nullable=True),
        sa.Column("follower_count", sa.BigInteger(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        if_not_exists=True,
    )

    for table, interval in HYPERTABLES.items():
        _create_hypertable(table, "ts", interval)

    op.create_index(
        "ix_request_log_endpoint_ts",
        "request_log",
        ["endpoint", sa.text("ts DESC")],
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_log_identity_ts",
        "request_log",
        ["identity_id", sa.text("ts DESC")],
        if_not_exists=True,
    )
    # Doc 05: the handle a user has when reporting a broken request is the
    # request_id from the response envelope.
    op.create_index("ix_request_log_request_id", "request_log", ["request_id"], if_not_exists=True)
    op.create_index(
        "ix_identity_events_identity_ts",
        "identity_events",
        ["identity_id", sa.text("ts DESC")],
        if_not_exists=True,
    )
    op.create_index(
        "ix_content_snapshots_platform_content_ts",
        "content_snapshots",
        ["platform", "content_id", sa.text("ts DESC")],
        if_not_exists=True,
    )


def _create_continuous_aggregates() -> None:
    """Create both aggregates and their refresh policies.

    Runs outside the migration transaction: TimescaleDB refuses to create a
    continuous aggregate inside a transaction block. Alembic's autocommit_block
    commits everything created so far and switches this same connection to
    AUTOCOMMIT, so the tables the aggregates read already exist and no second
    connection is opened (which would deadlock against the locks this one
    holds).
    """
    with op.get_context().autocommit_block():
        for name, ddl in CAGGS.items():
            if not _cagg_exists(name):
                op.execute(ddl)
        # Plain views, so CREATE OR REPLACE is enough; see the module docstring
        # for why identities_used cannot be materialized directly.
        for ddl in DERIVED_VIEWS.values():
            op.execute(ddl)

        for cagg in CAGGS:
            op.execute(
                f"""
                SELECT add_continuous_aggregate_policy(
                    '{cagg}',
                    start_offset => INTERVAL '{CAGG_START_OFFSET}',
                    end_offset => INTERVAL '{CAGG_END_OFFSET}',
                    schedule_interval => INTERVAL '{CAGG_SCHEDULE}',
                    if_not_exists => TRUE
                )
                """
            )


def _create_policies() -> None:
    """Retention and compression.

    ``content_snapshots`` deliberately gets no retention policy: it is user
    data accumulated over months and only the user may decide to delete it.
    The aggregates get none either, so minute-level history outlives the raw
    request_log rows it was derived from.
    """
    for table, interval in RETENTION.items():
        op.execute(
            f"SELECT add_retention_policy('{table}', INTERVAL '{interval}', if_not_exists => TRUE)"
        )

    if _has_columnstore_api():
        op.execute(
            "ALTER TABLE content_snapshots SET ("
            "timescaledb.enable_columnstore = true, "
            f"timescaledb.segmentby = '{SNAPSHOT_SEGMENT_BY}', "
            f"timescaledb.orderby = '{SNAPSHOT_ORDER_BY}')"
        )
        # CALL, not SELECT: add_columnstore_policy is a PROCEDURE, unlike
        # add_retention_policy / add_compression_policy / add_continuous_-
        # aggregate_policy which are all functions. Verified against
        # TimescaleDB 2.29.2: SELECT raises WrongObjectTypeError and aborts the
        # migration, which would leave every fresh `docker compose up` with a
        # half-built database.
        op.execute(
            "CALL add_columnstore_policy('content_snapshots', "
            f"after => INTERVAL '{SNAPSHOT_COMPRESS_AFTER}', if_not_exists => TRUE)"
        )
    else:
        op.execute(
            "ALTER TABLE content_snapshots SET ("
            "timescaledb.compress, "
            f"timescaledb.compress_segmentby = '{SNAPSHOT_SEGMENT_BY}', "
            f"timescaledb.compress_orderby = '{SNAPSHOT_ORDER_BY}')"
        )
        op.execute(
            "SELECT add_compression_policy('content_snapshots', "
            f"INTERVAL '{SNAPSHOT_COMPRESS_AFTER}', if_not_exists => TRUE)"
        )


def upgrade() -> None:
    _require_timescaledb()
    # Its own autocommit block: on a server where the extension is not yet
    # present, creating it and using it in one transaction is asking for
    # trouble, and the timescaledb-ha image has already created it anyway.
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    _create_relational_tables()
    _create_timeseries_tables()
    _create_policies()
    _create_continuous_aggregates()


def downgrade() -> None:
    with op.get_context().autocommit_block():
        # Plain views first, and as plain views: DROP MATERIALIZED VIEW against
        # an ordinary view raises "is not a materialized view", and IF EXISTS
        # does not suppress a wrong-relkind error.
        for name in DERIVED_VIEWS:
            op.execute(f"DROP VIEW IF EXISTS {name}")
        for name in reversed(list(CAGGS)):
            op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")

    # Policies and chunks are dropped along with their hypertable.
    for table in ("content_snapshots", "identity_events", "request_log"):
        op.drop_table(table, if_exists=True)

    for table in ("audit_log", "settings_version", "settings", "tasks", "identities", "proxies"):
        op.drop_table(table, if_exists=True)
    op.drop_table("api_keys", if_exists=True)
    op.drop_table("users", if_exists=True)
