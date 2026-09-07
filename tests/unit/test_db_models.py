"""Model-level tests for the schema. No PostgreSQL required.

These cover what can go wrong without a server being involved: a column that
drifted away from docs/design/05-data-model.md, a missing index the scheduler
depends on, a repr that would print a cookie into a traceback, and a migration
that stopped creating the TimescaleDB objects. Behaviour against a real
database (hypertables actually being created, aggregates actually refreshing)
belongs to the integration suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Index, LargeBinary, Text, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.schema import CheckConstraint, ForeignKeyConstraint

from dtk.core.db import Base
from dtk.core.types import IdentityState, Platform
from dtk.db import migrate, models
from dtk.db.models import (
    ApiKey,
    AuditLog,
    ContentSnapshot,
    Identity,
    IdentityEvent,
    Proxy,
    RequestLog,
    Setting,
    SettingsVersion,
    Task,
    User,
)
from dtk.db.repositories import (
    ApiKeyRepository,
    IdentityRepository,
    SettingsRepository,
    request_log_row,
)

PG = postgresql.dialect()

#: Exactly the columns doc 05 specifies, per table. Compared as a set, so both a
#: missing column and an unannounced extra one fail.
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "users": {"id", "username", "password_hash", "role", "created_at", "last_login_at"},
    "api_keys": {
        "id",
        "user_id",
        "name",
        "prefix",
        "key_hash",
        "scopes",
        "rate_limit",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "created_at",
    },
    "proxies": {
        "id",
        "url_encrypted",
        "label",
        "country",
        "timezone",
        "healthy",
        "last_check_at",
        "created_at",
    },
    "identities": {
        "id",
        "platform",
        "cookies_encrypted",
        "fingerprint",
        "proxy_id",
        "authenticated",
        "source",
        "state",
        "cooldown_until",
        "consecutive_fails",
        "minted_at",
        "last_used_at",
        "retired_at",
        "retire_reason",
    },
    "tasks": {
        "id",
        "api_key_id",
        "endpoint",
        "params",
        "state",
        "result",
        "error",
        "created_at",
        "started_at",
        "finished_at",
    },
    "settings": {"key", "value", "updated_at", "updated_by"},
    "settings_version": {"id", "version"},
    "audit_log": {
        "id",
        "ts",
        "user_id",
        "api_key_id",
        "action",
        "target_type",
        "target_id",
        "detail",
        "ip",
        "user_agent",
    },
    "request_log": {
        "ts",
        "request_id",
        "task_id",
        "platform",
        "endpoint",
        "identity_id",
        "proxy_id",
        "api_key_id",
        "outcome",
        "http_status",
        "duration_ms",
        "cache_hit",
        "signer",
        "error_code",
        "reject_reason",
    },
    "identity_events": {"ts", "identity_id", "event", "detail"},
    "content_snapshots": {
        "ts",
        "platform",
        "content_type",
        "content_id",
        "play_count",
        "digg_count",
        "comment_count",
        "share_count",
        "collect_count",
        "follower_count",
        "raw",
    },
}


def _migration_source() -> str:
    path = Path(migrate.MIGRATIONS_DIR) / "versions" / "0001_initial_schema.py"
    return path.read_text(encoding="utf-8")


def _index_names(table_name: str) -> set[str]:
    return {ix.name for ix in Base.metadata.tables[table_name].indexes if ix.name}


def _index(table_name: str, index_name: str) -> Index:
    for ix in Base.metadata.tables[table_name].indexes:
        if ix.name == index_name:
            return ix
    raise AssertionError(f"index {index_name} missing from {table_name}")


def _index_targets(table_name: str, index_name: str) -> list[str]:
    """Index targets as written, with the table qualifier stripped.

    Expressions such as ``ts DESC`` come back as text, which is exactly what
    should be asserted on: the descending order is what makes the index usable
    for the "most recent first" queries.
    """
    return [
        str(expr).removeprefix(f"{table_name}.")
        for expr in _index(table_name, index_name).expressions
    ]


def _checks(table_name: str) -> dict[str, str]:
    return {
        str(c.name): str(c.sqltext)
        for c in Base.metadata.tables[table_name].constraints
        if isinstance(c, CheckConstraint) and c.name
    }


def _fk(table_name: str, column: str) -> ForeignKeyConstraint:
    for constraint in Base.metadata.tables[table_name].constraints:
        if isinstance(constraint, ForeignKeyConstraint) and column in constraint.column_keys:
            return constraint
    raise AssertionError(f"no foreign key on {table_name}.{column}")


class TestTables:
    def test_every_documented_table_is_mapped(self) -> None:
        assert set(Base.metadata.tables) == set(models.TABLE_NAMES)

    @pytest.mark.parametrize("table_name", sorted(EXPECTED_COLUMNS))
    def test_columns_match_the_data_model(self, table_name: str) -> None:
        table = Base.metadata.tables[table_name]
        assert set(table.c.keys()) == EXPECTED_COLUMNS[table_name]

    def test_ids_are_uuid_not_integer(self) -> None:
        for model in (User, ApiKey, Proxy, Identity, Task, AuditLog):
            assert model.__table__.c.id.type.compile(PG) == "UUID"

    def test_content_id_is_text(self) -> None:
        # A 19-digit aweme_id does not survive a JavaScript number.
        assert isinstance(ContentSnapshot.__table__.c.content_id.type, Text)

    def test_metric_columns_are_nullable(self) -> None:
        # Absent means NULL, never 0: a zero would draw a cliff in the curve.
        for name in ("play_count", "digg_count", "comment_count", "follower_count"):
            assert ContentSnapshot.__table__.c[name].nullable is True


class TestEncryptedColumns:
    def test_credentials_are_binary(self) -> None:
        assert isinstance(Identity.__table__.c.cookies_encrypted.type, LargeBinary)
        assert isinstance(Proxy.__table__.c.url_encrypted.type, LargeBinary)

    def test_credentials_are_not_nullable(self) -> None:
        # Retirement overwrites the jar with empty bytes rather than NULL.
        assert Identity.__table__.c.cookies_encrypted.nullable is False
        assert Proxy.__table__.c.url_encrypted.nullable is False

    def test_no_plaintext_credential_columns(self) -> None:
        forbidden = {"cookies", "cookie", "url", "password", "proxy_url", "secret"}
        for table in Base.metadata.tables.values():
            assert forbidden.isdisjoint(table.c.keys()), table.name


class TestJsonAndArrayColumns:
    def test_structured_columns_are_jsonb(self) -> None:
        for column in (
            Identity.__table__.c.fingerprint,
            Task.__table__.c.params,
            Task.__table__.c.result,
            Task.__table__.c.error,
            Setting.__table__.c.value,
            AuditLog.__table__.c.detail,
            IdentityEvent.__table__.c.detail,
            ContentSnapshot.__table__.c.raw,
        ):
            assert isinstance(column.type, JSONB)

    def test_scopes_is_a_text_array(self) -> None:
        column = ApiKey.__table__.c.scopes
        assert isinstance(column.type, ARRAY)
        assert isinstance(column.type.item_type, Text)
        assert column.nullable is False


class TestConstraints:
    def test_role_values_are_constrained(self) -> None:
        assert "admin" in _checks("users")["ck_users_role"]
        assert "viewer" in _checks("users")["ck_users_role"]

    def test_identity_platform_and_source_are_constrained(self) -> None:
        checks = _checks("identities")
        assert set(Platform) == {Platform.DOUYIN, Platform.TIKTOK}
        for platform in Platform:
            assert platform.value in checks["ck_identities_platform"]
        assert "minted" in checks["ck_identities_source"]
        assert "imported" in checks["ck_identities_source"]

    def test_settings_version_is_a_singleton(self) -> None:
        assert "id = 1" in _checks("settings_version")["ck_settings_version_singleton"]

    def test_api_key_prefix_and_username_are_unique(self) -> None:
        assert ApiKey.__table__.c.prefix.unique is True
        assert User.__table__.c.username.unique is True

    def test_api_keys_die_with_their_user(self) -> None:
        assert _fk("api_keys", "user_id").ondelete == "CASCADE"

    def test_dropping_a_proxy_does_not_drop_identities(self) -> None:
        # Doc 02: an identity keeps its statistics even when the egress goes.
        assert _fk("identities", "proxy_id").ondelete == "SET NULL"

    def test_audit_rows_outlive_their_actor(self) -> None:
        assert _fk("audit_log", "user_id").ondelete == "SET NULL"
        assert _fk("audit_log", "api_key_id").ondelete == "SET NULL"

    def test_tasks_survive_key_deletion(self) -> None:
        assert _fk("tasks", "api_key_id").ondelete == "SET NULL"


class TestIndexes:
    def test_scheduler_candidate_index(self) -> None:
        assert _index_targets("identities", "ix_identities_platform_state_last_used") == [
            "platform",
            "state",
            "last_used_at",
        ]

    def test_task_queue_index(self) -> None:
        assert _index_targets("tasks", "ix_tasks_state_created_at") == ["state", "created_at"]

    def test_request_log_indexes(self) -> None:
        names = _index_names("request_log")
        assert {
            "ix_request_log_endpoint_ts",
            "ix_request_log_identity_ts",
            # Doc 05: the user reporting a broken request has only this handle.
            "ix_request_log_request_id",
        } <= names
        assert _index_targets("request_log", "ix_request_log_endpoint_ts") == [
            "endpoint",
            "ts DESC",
        ]
        assert _index_targets("request_log", "ix_request_log_identity_ts") == [
            "identity_id",
            "ts DESC",
        ]
        assert _index_targets("request_log", "ix_request_log_request_id") == ["request_id"]

    def test_snapshot_history_index(self) -> None:
        assert _index_targets("content_snapshots", "ix_content_snapshots_platform_content_ts") == [
            "platform",
            "content_id",
            "ts DESC",
        ]

    def test_api_key_lookup_index(self) -> None:
        # Every authenticated request resolves a key by digest.
        assert "ix_api_keys_key_hash" in _index_names("api_keys")


class TestHypertables:
    @pytest.mark.parametrize("model", [RequestLog, IdentityEvent, ContentSnapshot])
    def test_no_primary_key_constraint_in_ddl(self, model: type[Any]) -> None:
        # A PK on a hypertable would have to include the partitioning column and
        # would cost an index write on the hottest path.
        assert len(model.__table__.primary_key.columns) == 0
        ddl = str(CreateTable(model.__table__).compile(dialect=PG))
        assert "PRIMARY KEY" not in ddl

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (RequestLog, ["ts", "request_id"]),
            (IdentityEvent, ["ts", "identity_id", "event"]),
            (ContentSnapshot, ["ts", "platform", "content_type", "content_id"]),
        ],
    )
    def test_mapper_still_has_an_identity_key(self, model: type[Any], expected: list[str]) -> None:
        assert [c.name for c in inspect(model).primary_key] == expected

    def test_time_column_is_not_nullable(self) -> None:
        for model in (RequestLog, IdentityEvent, ContentSnapshot):
            assert model.__table__.c.ts.nullable is False

    def test_no_foreign_keys_on_hypertables(self) -> None:
        for name in models.HYPERTABLES:
            table = Base.metadata.tables[name]
            assert not table.foreign_keys, name


class TestReprSafety:
    """A repr lands in tracebacks and log lines. Credentials must not."""

    def test_identity_repr_hides_the_cookie_jar(self) -> None:
        identity = Identity(
            id=uuid.uuid4(),
            platform=Platform.DOUYIN.value,
            cookies_encrypted=b"ttwid=SECRET_COOKIE_VALUE",
            fingerprint={"ua": "Mozilla/5.0", "timezone": "Europe/Berlin"},
            source="minted",
            state=IdentityState.ACTIVE.value,
        )
        rendered = repr(identity)
        assert "SECRET_COOKIE_VALUE" not in rendered
        assert "cookies" not in rendered
        assert "Mozilla" not in rendered
        assert identity.state in rendered

    def test_proxy_repr_hides_the_url(self) -> None:
        proxy = Proxy(id=uuid.uuid4(), url_encrypted=b"http://user:hunter2@host:8080", label="de-1")
        rendered = repr(proxy)
        assert "hunter2" not in rendered
        assert "url" not in rendered
        assert "de-1" in rendered

    def test_api_key_repr_hides_the_digest(self) -> None:
        key = ApiKey(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="ci",
            prefix="dtk_a1b2c3d4",
            key_hash="0" * 64,
        )
        rendered = repr(key)
        assert "0" * 64 not in rendered
        assert "dtk_a1b2c3d4" in rendered

    def test_user_repr_hides_the_password_hash(self) -> None:
        user = User(id=uuid.uuid4(), username="admin", password_hash="$argon2id$v=19$secret")
        assert "argon2" not in repr(user)
        assert "admin" in repr(user)

    def test_settings_repr_hides_the_value(self) -> None:
        # A setting may hold a webhook URL with a token in it.
        setting = Setting(key="notify.channels", value=[{"url": "https://hook/SECRET"}])
        assert "SECRET" not in repr(setting)


class TestServerDefaults:
    def test_ids_default_to_gen_random_uuid(self) -> None:
        for model in (User, ApiKey, Proxy, Identity, Task, AuditLog):
            default = model.__table__.c.id.server_default
            assert default is not None
            assert "gen_random_uuid" in str(default.arg)

    def test_creation_timestamps_default_to_now(self) -> None:
        for column in (
            User.__table__.c.created_at,
            ApiKey.__table__.c.created_at,
            Proxy.__table__.c.created_at,
            Identity.__table__.c.minted_at,
            Task.__table__.c.created_at,
            AuditLog.__table__.c.ts,
        ):
            assert column.server_default is not None

    def test_state_defaults(self) -> None:
        assert "minting" in str(Identity.__table__.c.state.server_default.arg)
        assert "queued" in str(Task.__table__.c.state.server_default.arg)
        assert "admin" in str(User.__table__.c.role.server_default.arg)
        assert str(SettingsVersion.__table__.c.version.server_default.arg) == "0"


class TestRequestLogRow:
    def test_row_covers_every_column(self) -> None:
        row = request_log_row(
            ts=datetime.now(UTC),
            request_id=uuid.uuid4(),
            platform=Platform.TIKTOK.value,
            endpoint="tiktok/video/detail",
            outcome="ok",
            duration_ms=42,
        )
        # Batched inserts need identical keys in every mapping.
        assert set(row) == set(RequestLog.__table__.c.keys())

    def test_absent_values_are_none(self) -> None:
        row = request_log_row(
            ts=datetime.now(UTC),
            request_id=uuid.uuid4(),
            platform=Platform.DOUYIN.value,
            endpoint="douyin/video/detail",
            outcome="ok",
            duration_ms=1,
        )
        assert row["http_status"] is None
        assert row["identity_id"] is None
        assert row["cache_hit"] is False


class _Result:
    """Stands in for a SQLAlchemy Result: only what the repositories touch."""

    def __init__(self, rowcount: int = 1, value: Any = None) -> None:
        self.rowcount = rowcount
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar_one(self) -> Any:
        return self._value

    def one_or_none(self) -> Any:
        return self._value

    def all(self) -> list[Any]:
        return []


class _RecordingSession:
    """Captures statements so a repository can be inspected without a server."""

    def __init__(self, value: Any = None) -> None:
        self.statements: list[Any] = []
        self._value = value

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(statement)
        return _Result(value=self._value)

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(statement)
        return _Result(value=self._value)


def _params(statement: Any) -> dict[str, Any]:
    return dict(statement.compile(dialect=PG).params)


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=PG))


class TestRepositoryStatements:
    """Statement shape, compiled but never executed."""

    async def test_retire_wipes_the_cookie_jar(self) -> None:
        session = _RecordingSession()
        repo = IdentityRepository(session)  # type: ignore[arg-type]
        await repo.retire(uuid.uuid4(), reason="proxy_dead")

        statement = session.statements[0]
        params = _params(statement)
        # Doc 05: retiring an identity must blank its credentials in the same
        # statement, not in a follow-up nobody guarantees runs.
        assert params["cookies_encrypted"] == b""
        assert params["state"] == IdentityState.RETIRED.value
        assert params["retire_reason"] == "proxy_dead"
        assert "cookies_encrypted" in _sql(statement)

    async def test_transition_is_compare_and_set(self) -> None:
        session = _RecordingSession()
        repo = IdentityRepository(session)  # type: ignore[arg-type]
        await repo.transition(
            uuid.uuid4(),
            expected=(IdentityState.ACTIVE, IdentityState.DEGRADED),
            new=IdentityState.COOLING,
        )
        statement = session.statements[0]
        params = _params(statement)
        assert params["state"] == IdentityState.COOLING.value
        # The expected states arrive as one expanding bind parameter.
        bound = {
            item
            for value in params.values()
            for item in (value if isinstance(value, list) else [value])
        }
        assert IdentityState.ACTIVE.value in bound
        assert IdentityState.DEGRADED.value in bound
        assert "state IN" in _sql(statement)

    async def test_usable_key_lookup_excludes_revoked_and_expired(self) -> None:
        session = _RecordingSession()
        repo = ApiKeyRepository(session)  # type: ignore[arg-type]
        await repo.get_usable_by_hash("a" * 64)

        sql = _sql(session.statements[0])
        assert "key_hash = " in sql
        assert "revoked_at IS NULL" in sql
        assert "expires_at IS NULL" in sql

    async def test_settings_bump_increments_server_side(self) -> None:
        session = _RecordingSession(value=7)
        repo = SettingsRepository(session)  # type: ignore[arg-type]
        assert await repo.bump_version() == 7
        sql = _sql(session.statements[0])
        # The counter must be incremented by the database, not by a read
        # followed by a write that two processes can interleave.
        assert "version + " in sql.replace("  ", " ")
        assert "RETURNING" in sql.upper()


class TestMigration:
    def test_single_head_at_the_initial_revision(self) -> None:
        assert migrate.head_revision() == "0001"

    def test_initial_revision_has_no_parent(self) -> None:
        source = _migration_source()
        assert 'revision: str = "0001"' in source
        assert "down_revision: str | Sequence[str] | None = None" in source

    def test_creates_the_extension(self) -> None:
        assert "CREATE EXTENSION IF NOT EXISTS timescaledb" in _migration_source()

    def test_creates_every_table(self) -> None:
        source = _migration_source()
        for table in models.TABLE_NAMES:
            assert f'"{table}"' in source, table

    def test_hypertable_intervals_match_the_models(self) -> None:
        module = _migration_module()
        assert dict(models.HYPERTABLES) == module.HYPERTABLES
        assert module.HYPERTABLES["request_log"] == "1 day"
        assert module.HYPERTABLES["identity_events"] == "7 days"
        assert module.HYPERTABLES["content_snapshots"] == "7 days"

    def test_calls_create_hypertable(self) -> None:
        source = _migration_source()
        assert "create_hypertable" in source
        # Both the modern dimension builder and the positional fallback.
        assert "by_range(" in source
        assert "chunk_time_interval" in source

    def test_continuous_aggregates_are_in_the_migration(self) -> None:
        module = _migration_module()
        assert "timescaledb.continuous" in module.IDENTITY_HEALTH_5M
        assert "time_bucket('5 minutes', ts)" in module.IDENTITY_HEALTH_5M
        assert "count(*) FILTER (WHERE outcome = 'risk_control')" in module.IDENTITY_HEALTH_5M
        assert "avg(duration_ms)" in module.IDENTITY_HEALTH_5M

        assert "timescaledb.continuous" in module.ENDPOINT_IDENTITY_HEALTH_5M
        assert "count(DISTINCT identity_id)" in module.ENDPOINT_HEALTH_5M
        assert "identities_used" in module.ENDPOINT_HEALTH_5M

    def test_aggregate_names_match_the_models(self) -> None:
        """The constants the rest of the codebase reads must name real objects.

        ``endpoint_health_5m`` is a plain view and will never appear in
        timescaledb_information.continuous_aggregates; ``endpoint_identity_-
        health_5m`` is the aggregate that will. Listing them the wrong way
        round makes any catalog health check silently wrong.
        """
        module = _migration_module()
        assert tuple(module.CAGGS) == tuple(models.CONTINUOUS_AGGREGATES)
        assert tuple(module.DERIVED_VIEWS) == tuple(models.DERIVED_VIEWS)
        assert set(models.CONTINUOUS_AGGREGATES).isdisjoint(models.DERIVED_VIEWS)
        for name, ddl in module.CAGGS.items():
            assert f"CREATE MATERIALIZED VIEW {name}" in ddl
            assert "timescaledb.continuous" in ddl
        for name, ddl in module.DERIVED_VIEWS.items():
            assert f"CREATE OR REPLACE VIEW {name}" in ddl
            assert "timescaledb.continuous" not in ddl

    def test_aggregates_answer_in_real_time(self) -> None:
        """Since TimescaleDB 2.13 a new aggregate is materialized-only.

        Left at the default, identity_health_5m and endpoint_health_5m would
        lag by end_offset plus one schedule interval, and doc 05 puts
        minute-level scheduling decisions on them. It has to be explicit.
        """
        module = _migration_module()
        for ddl in module.CAGGS.values():
            assert "timescaledb.materialized_only = false" in ddl

    def test_retention_and_compression_match_the_models(self) -> None:
        module = _migration_module()
        expected_retention = {
            table: f"{days} days" for table, days in models.RETENTION_DAYS.items()
        }
        assert dict(module.RETENTION) == expected_retention
        expected_compress = f"{models.SNAPSHOT_COMPRESSION_AFTER_DAYS} days"
        assert str(module.SNAPSHOT_COMPRESS_AFTER) == expected_compress

    def test_aggregates_run_outside_a_transaction(self) -> None:
        # TimescaleDB refuses CREATE MATERIALIZED VIEW ... WITH
        # (timescaledb.continuous) inside a transaction block.
        assert "autocommit_block()" in _migration_source()

    def test_refresh_policies_exist(self) -> None:
        assert "add_continuous_aggregate_policy" in _migration_source()

    def test_retention_matches_the_documented_windows(self) -> None:
        module = _migration_module()
        assert module.RETENTION == {"request_log": "14 days", "identity_events": "90 days"}
        assert "add_retention_policy" in _migration_source()
        # Snapshots are user data: compressed, never auto-deleted.
        assert "content_snapshots" not in module.RETENTION

    def test_compression_policy_for_snapshots(self) -> None:
        source = _migration_source()
        assert "add_compression_policy" in source or "add_columnstore_policy" in source
        module = _migration_module()
        assert module.SNAPSHOT_COMPRESS_AFTER == "7 days"
        assert module.SNAPSHOT_SEGMENT_BY == "platform, content_id"

    def test_settings_version_row_is_seeded(self) -> None:
        assert "INSERT INTO settings_version" in _migration_source()

    def test_downgrade_removes_the_aggregates_first(self) -> None:
        source = _migration_source()
        downgrade = source[source.index("def downgrade()") :]
        assert "DROP MATERIALIZED VIEW IF EXISTS" in downgrade
        assert "autocommit_block()" in downgrade
        for table in models.TABLE_NAMES:
            assert f'"{table}"' in downgrade, table

    def test_downgrade_drops_each_relation_as_its_own_kind(self) -> None:
        """DROP MATERIALIZED VIEW on a plain view errors even with IF EXISTS.

        Postgres only lets IF EXISTS swallow "does not exist", not
        "is not a materialized view", so a downgrade that mixed the two up
        would abort halfway and leave the schema in pieces.
        """
        source = _migration_source()
        downgrade = source[source.index("def downgrade()") :]
        for name in models.DERIVED_VIEWS:
            assert f"DROP MATERIALIZED VIEW IF EXISTS {name}" not in downgrade
        assert "DROP VIEW IF EXISTS {name}" in downgrade
        assert "DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE" in downgrade


def _migration_module() -> Any:
    """Import the revision through Alembic, which also proves the config works."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision("0001")
    return revision.module


class TestSourceIsEnglishOnly:
    def test_no_cjk_characters_in_the_db_package(self) -> None:
        root = Path(models.__file__).resolve().parent
        offenders = []
        for path in sorted(root.rglob("*.py*")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            # CJK ideographs, plus the CJK punctuation, hiragana and katakana
            # blocks that come with copy-pasted Chinese comments.
            if any(0x4E00 <= ord(ch) <= 0x9FFF or 0x3000 <= ord(ch) <= 0x30FF for ch in text):
                offenders.append(path.name)
        assert offenders == []
