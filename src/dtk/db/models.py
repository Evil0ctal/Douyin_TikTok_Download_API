"""SQLAlchemy ORM for every table in docs/design/05-data-model.md.

Two families of tables live here and they behave differently:

* Relational tables (``users`` ... ``audit_log``) are read and written through
  the ORM in the ordinary way.
* Time-series tables (``request_log``, ``identity_events``,
  ``content_snapshots``) are TimescaleDB hypertables. They are append-only and
  deliberately carry no primary key: a unique index on a hypertable would have
  to include the partitioning column and would cost a write on the hottest
  path in the system. They are mapped with ``__mapper_args__["primary_key"]``
  so the ORM has an identity key to work with, while the emitted DDL stays free
  of any PRIMARY KEY constraint.

Credentials (``proxies.url_encrypted``, ``identities.cookies_encrypted``) are
AES-GCM ciphertext produced by :class:`dtk.core.crypto.Cipher`; the record id is
the additional authenticated data, so a blob cannot be moved between rows. The
``__repr__`` of every model carrying such a column is written by hand and must
never widen to include it: a repr ends up in tracebacks and log lines, which is
exactly how a live session cookie leaks (docs/design/08-security.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dtk.core.db import Base
from dtk.core.types import IdentitySource, IdentityState, Platform, TaskState, UserRole

#: Hypertable name -> chunk interval, as declared in doc 05. The first migration
#: reads the same values; the unit tests compare the two so a table added here
#: without a matching ``create_hypertable`` call is caught before deployment.
HYPERTABLES: Final[dict[str, str]] = {
    "request_log": "1 day",
    "identity_events": "7 days",
    "content_snapshots": "7 days",
}

#: Continuous aggregates created by the first migration, exactly as they appear
#: in ``timescaledb_information.continuous_aggregates``. ``endpoint_health_5m``
#: is deliberately not in this tuple: ``count(DISTINCT identity_id)`` is not
#: partializable, so doc 05's endpoint view is materialized one level lower as
#: ``endpoint_identity_health_5m`` and rolled up by a plain view.
CONTINUOUS_AGGREGATES: Final[tuple[str, ...]] = (
    "identity_health_5m",
    "endpoint_identity_health_5m",
)

#: Plain views layered on the aggregates above. Kept apart from
#: CONTINUOUS_AGGREGATES so a health check does not look for them in the
#: TimescaleDB catalog, where they will never appear.
DERIVED_VIEWS: Final[tuple[str, ...]] = ("endpoint_health_5m",)

#: Retention, in days, applied as a TimescaleDB policy. ``content_snapshots`` is
#: absent on purpose: it is user data and only the user may delete it.
RETENTION_DAYS: Final[dict[str, int]] = {"request_log": 14, "identity_events": 90}

#: Compression (columnstore) kicks in this many days after ingest.
SNAPSHOT_COMPRESSION_AFTER_DAYS: Final[int] = 7

_UTC_NOW = func.now()
_NEW_UUID = text("gen_random_uuid()")


def _repr(name: str, **fields: object) -> str:
    """Render a repr from an explicit allowlist of fields.

    Allowlisting rather than filtering is the point: a new encrypted column
    cannot leak by being forgotten.
    """
    body = " ".join(f"{k}={v!r}" for k, v in fields.items())
    return f"<{name} {body}>"


class User(Base):
    """A local console account. A self-hosted instance has a handful of them."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','operator','viewer')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    username: Mapped[str] = mapped_column(Text, unique=True)
    #: argon2id digest. Never logged, never returned by any endpoint.
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default=text(f"'{UserRole.ADMIN.value}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return _repr("User", id=self.id, username=self.username, role=self.role)


class ApiKey(Base):
    """An API credential. Only the prefix and the digest survive creation."""

    __tablename__ = "api_keys"
    __table_args__ = (
        # Every authenticated request looks a key up by digest, so this index is
        # on the hot path even though doc 05 does not spell it out.
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    #: Plaintext prefix shown in the console, for example ``dtk_a1b2c3d4``.
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    #: sha256 of the full key. The key itself is displayed once, at creation.
    key_hash: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    #: Requests per minute. NULL means "use the global default"; abuse
    #: protection only, this project has no billing (docs/design/README.md).
    rate_limit: Mapped[int | None] = mapped_column(Integer, default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)

    def __repr__(self) -> str:
        return _repr("ApiKey", id=self.id, prefix=self.prefix, revoked=self.revoked_at is not None)


class Proxy(Base):
    """An egress. The URL carries credentials, so it is stored encrypted."""

    __tablename__ = "proxies"
    __table_args__ = (Index("ix_proxies_healthy", "healthy"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    url_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    label: Mapped[str | None] = mapped_column(Text, default=None)
    #: GeoIP of the exit address. Minting aligns the browser locale to it.
    country: Mapped[str | None] = mapped_column(Text, default=None)
    timezone: Mapped[str | None] = mapped_column(Text, default=None)
    healthy: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)

    def __repr__(self) -> str:
        return _repr(
            "Proxy", id=self.id, label=self.label, country=self.country, healthy=self.healthy
        )


class Identity(Base):
    """Cookies + proxy + fingerprint, bound together for life.

    Doc 02: the four parts may never be recombined. Changing the egress of an
    existing cookie jar is the single most correlatable thing this system could
    do, so a swap means retiring the identity and minting a new one.
    """

    __tablename__ = "identities"
    __table_args__ = (
        CheckConstraint("platform IN ('douyin','tiktok')", name="ck_identities_platform"),
        CheckConstraint("source IN ('minted','imported')", name="ck_identities_source"),
        # The scheduler's candidate query: healthy identities of one platform,
        # least recently used first.
        Index("ix_identities_platform_state_last_used", "platform", "state", "last_used_at"),
        Index("ix_identities_proxy_id", "proxy_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    platform: Mapped[str] = mapped_column(Text)
    #: AES-GCM ciphertext of the cookie jar, aad = str(id). Wiped on retirement.
    cookies_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    #: UA, browser family and major, screen, language, timezone.
    fingerprint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proxy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("proxies.id", ondelete="SET NULL"), default=None
    )
    authenticated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(
        Text, server_default=text(f"'{IdentityState.MINTING.value}'")
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    consecutive_fails: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    minted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    retire_reason: Mapped[str | None] = mapped_column(Text, default=None)

    def __repr__(self) -> str:
        # Never widen this with cookies_encrypted or fingerprint: the first is a
        # credential and the second identifies the browser profile.
        return _repr(
            "Identity",
            id=self.id,
            platform=self.platform,
            state=self.state,
            authenticated=self.authenticated,
        )


class Task(Base):
    """An asynchronous job. ``result`` is the full response and gets large."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_state_created_at", "state", "created_at"),
        # Drives the result-eviction sweep: finished long enough ago and still
        # holding a payload.
        Index("ix_tasks_finished_at", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), default=None
    )
    endpoint: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(Text, server_default=text(f"'{TaskState.QUEUED.value}'"))
    #: Evicted after ``retention.task_result_hours``; the row itself lives on so
    #: statistics survive. A later lookup then answers TASK_NOT_FOUND.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return _repr("Task", id=self.id, endpoint=self.endpoint, state=self.state)


class Setting(Base):
    """One runtime setting. The database wins over the environment (doc 10)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    #: jsonb, not text: scheduler policies are structured, and flattening them
    #: into dotted string keys makes atomic updates impossible.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    def __repr__(self) -> str:
        return _repr("Setting", key=self.key)


class SettingsVersion(Base):
    """Single-row counter every process polls to detect a config change."""

    __tablename__ = "settings_version"
    __table_args__ = (CheckConstraint("id = 1", name="ck_settings_version_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))

    def __repr__(self) -> str:
        return _repr("SettingsVersion", version=self.version)


class AuditLog(Base):
    """Sensitive operations, kept separately from the request log.

    Doc 08 requires an audit trail for importing and retiring identities,
    creating and revoking API keys, editing proxies and changing a SENSITIVE
    setting. Rows survive the actor: the foreign keys are ON DELETE SET NULL so
    deleting a user cannot erase what that user did.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_ts", text("ts DESC")),
        Index("ix_audit_log_action_ts", "action", text("ts DESC")),
        Index("ix_audit_log_user_ts", "user_id", text("ts DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), default=None
    )
    #: Dotted action name, matching the log event vocabulary, for example
    #: ``identity.imported`` or ``settings.updated``.
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text, default=None)
    #: Stringified id of the affected row. Text because targets differ in type.
    target_id: Mapped[str | None] = mapped_column(Text, default=None)
    #: Redacted payload. Never store a credential here, only what changed.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    ip: Mapped[str | None] = mapped_column(Text, default=None)
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)

    def __repr__(self) -> str:
        return _repr("AuditLog", id=self.id, action=self.action, target_id=self.target_id)


class RequestLog(Base):
    """Hypertable. The source of truth for health, circuits and the console.

    Nothing else counts requests: every derived number comes from here or from
    the continuous aggregates over it, so there are no counters to drift.
    Writes are batched by a background flusher; losing a few rows is acceptable,
    slowing a request down to log it is not.
    """

    __tablename__ = "request_log"
    __table_args__ = (
        Index("ix_request_log_endpoint_ts", "endpoint", text("ts DESC")),
        Index("ix_request_log_identity_ts", "identity_id", text("ts DESC")),
        # The Logs page's outcome filter, which is two clicks away and is how
        # anyone looks for what went wrong. Without this the filter falls off
        # the ts ordering and seq-scans every retained chunk with no early
        # stop - the one query shape on this hypertable that can take the
        # instance down, reached from a checkbox.
        Index("ix_request_log_outcome_ts", "outcome", text("ts DESC")),
        # Support lookups by the request_id printed in the API envelope: it is
        # the only handle a user has when reporting a broken request.
        Index("ix_request_log_request_id", "request_id"),
    )

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Matches ``meta.request_id`` in the API response.
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), default=None)
    platform: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text)
    # No foreign keys on hypertables: a per-row check against a relational table
    # on the highest-volume insert path, and it would block identity deletion.
    identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), default=None)
    proxy_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), default=None)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), default=None)
    #: ok | business_error | risk_control | network_error. The split matters:
    #: a deleted video must never count against an identity.
    outcome: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: native | browser
    signer: Mapped[str | None] = mapped_column(Text, default=None)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    #: Set when the scheduler refused to issue a lease; see RejectReason.
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)

    __mapper_args__ = {"primary_key": [ts, request_id]}  # noqa: RUF012

    def __repr__(self) -> str:
        return _repr(
            "RequestLog",
            request_id=self.request_id,
            endpoint=self.endpoint,
            outcome=self.outcome,
        )


class IdentityEvent(Base):
    """Hypertable. Lifecycle audit for one identity."""

    __tablename__ = "identity_events"
    __table_args__ = (Index("ix_identity_events_identity_ts", "identity_id", text("ts DESC")),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    identity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    #: minted | activated | cooled | degraded | retired | imported
    event: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)

    __mapper_args__ = {"primary_key": [ts, identity_id, event]}  # noqa: RUF012

    def __repr__(self) -> str:
        return _repr("IdentityEvent", identity_id=self.identity_id, event=self.event)


class ArchivedAuthor(Base):
    """One author's current state, upserted every time we parse them.

    Plain relational table, not a hypertable. The append-only history of the same
    object is `content_snapshots`, which already exists and stays exactly as it
    is; this is the current-state row it joins to. A hypertable cannot serve here
    because dedup is an upsert and a unique key on a hypertable would have to
    include the partitioning column - which the module docstring above refuses on
    the hot path.
    """

    __tablename__ = "archived_authors"

    platform: Mapped[str] = mapped_column(Text, primary_key=True)
    #: The platform's stable key: Douyin sec_user_id, TikTok's numeric id. Never
    #: unique_id, which users edit - the same reason `Author.uid` documents.
    uid: Mapped[str] = mapped_column(Text, primary_key=True)
    unique_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    nickname: Mapped[str] = mapped_column(Text)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    web_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    follower_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    following_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    content_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    total_digg: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    #: Kept only when `archive.store_raw` is on. Off by default: the parsers fill
    #: `raw` on every object, and doc 18 measured what storing all of it costs.
    #:
    #: ``none_as_null`` because SQLAlchemy's JSON types map Python None to the
    #: JSON literal ``null`` rather than to SQL NULL. Without it a row with no
    #: payload still answered ``raw IS NOT NULL``, which is the wrong answer to
    #: the only question anyone asks this column.
    raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True, default=None
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return _repr("ArchivedAuthor", platform=self.platform, uid=self.uid)


class ArchivedContent(Base):
    """One post's current state, plus the classification derived on write.

    Every classification column is a deterministic function of what the parser
    already returned - no model, no extra request, and recomputable from `raw`
    when the rule changes. docs/design/README.md records AI content analysis as a
    non-goal, and calling an embedding "auto-classification" would reverse that
    by stealth (doc 18).

    Engagement ratios are deliberately NOT stored. Doc 11 makes None and 0 mean
    different things, so a ratio over a NULL numerator is undefined rather than
    zero, and a column would bake that lie in. They are computed at read time.
    """

    __tablename__ = "archived_contents"
    __table_args__ = (
        Index("ix_archived_contents_author", "platform", "author_uid"),
        Index("ix_archived_contents_last_seen", text("last_seen_at DESC")),
        Index("ix_archived_contents_created", text("platform_created_at DESC")),
        Index("ix_archived_contents_tags", "tags", postgresql_using="gin"),
        Index("ix_archived_contents_music", "platform", "music_id"),
    )

    platform: Mapped[str] = mapped_column(Text, primary_key=True)
    content_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    web_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    platform_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)

    author_uid: Mapped[str] = mapped_column(Text)
    author_nickname: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    music_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    music_title: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    location: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    #: The whole parsed Media, mirror lists included, so a download can be
    #: retried later without re-parsing. Signed CDN links inside it expire; that
    #: is what `web_url` is for.
    media: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True, default=None
    )

    # --- derived on write, recomputable ---------------------------------
    #: portrait | landscape | square | unknown
    orientation: Mapped[str] = mapped_column(Text, default="unknown")
    #: short | medium | long | unknown, bucketed from duration_ms
    duration_bucket: Mapped[str] = mapped_column(Text, default="unknown")
    #: sd | hd | fhd | uhd | unknown, from the video's shorter edge
    resolution_class: Mapped[str] = mapped_column(Text, default="unknown")
    #: cjk | latin | mixed | unknown, from Unicode ranges in title+description
    script: Mapped[str] = mapped_column(Text, default="unknown")

    #: live | deleted | private | unknown. Only ever set from a real observation.
    availability: Mapped[str] = mapped_column(Text, default="live")
    raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True, default=None
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return _repr("ArchivedContent", platform=self.platform, content_id=self.content_id)


class MediaDownload(Base):
    """One request to store a post's media on this instance's own disk.

    The row outlives the files. Eviction under the size ceiling removes bytes
    and sets ``files_removed_at``; it never deletes this record, so an operator
    can still see what was collected, when, and that it was evicted rather than
    never fetched - and can download it again. Deleting the metadata to save
    disk would throw away the part that costs nothing to keep.

    ``pinned`` is the only exemption from eviction, and it exists because a
    size-based policy without one eventually deletes the file the operator
    cared about most. Nothing sets it automatically.
    """

    __tablename__ = "media_downloads"
    __table_args__ = (
        Index("ix_media_downloads_content", "platform", "content_id"),
        Index("ix_media_downloads_created", text("created_at DESC")),
        Index("ix_media_downloads_state", "state"),
        # Drives the eviction sweep: what is on disk, oldest first, skipping
        # the pinned. Partial, because everything already evicted is exactly
        # what the sweep never needs to look at again.
        Index(
            "ix_media_downloads_evictable",
            "pinned",
            text("finished_at ASC"),
            postgresql_where=text("files_removed_at IS NULL AND bytes_total > 0"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    platform: Mapped[str] = mapped_column(Text)
    content_id: Mapped[str] = mapped_column(Text)
    author_uid: Mapped[str] = mapped_column(Text)
    #: queued | running | done | partial | failed | cancelled
    state: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    #: Relative to the media root, as <platform>/<author_uid>/<content_id>.
    #: Never absolute: the path inside the container is not the path on the
    #: host, and storing one would make the row wrong the moment the volume
    #: is mounted somewhere else.
    directory: Mapped[str] = mapped_column(Text)
    bytes_total: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    file_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    #: Per file: name, kind, bytes, sha256, content_type, error.
    files: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True, default=None
    )
    #: Never evicted while true.
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: Set when the bytes were removed to stay under the size ceiling. The row
    #: and its metadata stay.
    files_removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), default=None
    )
    #: The task that runs it, so the console can follow one from the other.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return _repr(
            "MediaDownload", id=self.id, platform=self.platform, content_id=self.content_id
        )


class WatchlistEntry(Base):
    """One author or post this instance re-collects on a schedule.

    The point is `content_snapshots`. Until something collects on a timer, that
    hypertable holds whatever a human happened to parse, at whatever moments
    they happened to do it - which is not a time series, it is a scatter of
    unrelated observations. A watchlist turns it into one.

    Nothing here fetches. An entry that comes due is submitted as an ordinary
    task, so scheduled collection queues behind interactive requests, spends the
    same identity pool under the same scheduler, and shows up in the console
    beside every other task. A second collection path would be a second set of
    rate limits to get wrong.
    """

    __tablename__ = "watchlist"
    __table_args__ = (
        # One entry per target: adding the same author twice is a mistake, not
        # a way to collect twice as often.
        Index("ix_watchlist_target", "platform", "kind", "target_id", unique=True),
        # The due query, which runs every minute.
        Index("ix_watchlist_due", "enabled", text("next_run_at ASC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    platform: Mapped[str] = mapped_column(Text)
    #: author | content. An author is watched for new posts and follower counts;
    #: a post for how its metrics move.
    kind: Mapped[str] = mapped_column(Text)
    #: sec_user_id or aweme_id, as the platform spells it.
    target_id: Mapped[str] = mapped_column(Text)
    #: What to call it in the console. Filled from the first successful run, so
    #: an operator who pasted an id still sees a nickname afterwards.
    label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    #: How many pages of an author's posts one run walks. One is the useful
    #: default: a watchlist is for what is new, and a backfill is a different
    #: job with a different cost.
    pages: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, default=None
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    #: When the outcome of ``last_task_id`` was read back. A run whose result
    #: has not been reconciled yet has ``last_run_at`` newer than this, which is
    #: exactly the query the watcher uses - no extra flag to keep in step.
    last_result_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    #: Drives the backoff. An author whose id is wrong should be tried less and
    #: less often rather than every interval forever.
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    #: Cumulative, for the console: "this entry has produced 412 observations".
    runs: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_UTC_NOW)

    def __repr__(self) -> str:
        return _repr("WatchlistEntry", id=self.id, platform=self.platform, kind=self.kind)


class ContentSnapshot(Base):
    """Hypertable. One row per successful parse, deduplicated in Redis.

    Absent metrics stay NULL. Writing 0 for "the platform did not return it"
    puts a phantom cliff into the growth curve, which is the whole reason this
    table exists (doc 05, doc 11).
    """

    __tablename__ = "content_snapshots"
    __table_args__ = (
        Index(
            "ix_content_snapshots_platform_content_ts", "platform", "content_id", text("ts DESC")
        ),
    )

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    platform: Mapped[str] = mapped_column(Text)
    #: video | user
    content_type: Mapped[str] = mapped_column(Text)
    #: aweme_id or sec_user_id. Text, never an integer: a 19-digit aweme_id
    #: exceeds the JavaScript safe range and would lose precision in the UI.
    content_id: Mapped[str] = mapped_column(Text)
    play_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    digg_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    comment_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    share_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    collect_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    #: Used when content_type = 'user'.
    follower_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    #: Untouched platform payload, so a new metric can be back-computed later.
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)

    __mapper_args__ = {  # noqa: RUF012
        "primary_key": [ts, platform, content_type, content_id]
    }

    def __repr__(self) -> str:
        return _repr(
            "ContentSnapshot",
            platform=self.platform,
            content_type=self.content_type,
            content_id=self.content_id,
        )


#: Every table this module defines, in dependency order. Used by the backup
#: command and by the tests that compare the ORM against the first migration.
TABLE_NAMES: Final[tuple[str, ...]] = (
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
    "archived_authors",
    "archived_contents",
    "media_downloads",
    "watchlist",
)

#: Values the corresponding text columns accept, kept beside the models so a
#: repository can assert against them without importing the enum module.
PLATFORM_VALUES: Final[tuple[str, ...]] = tuple(p.value for p in Platform)
IDENTITY_STATE_VALUES: Final[tuple[str, ...]] = tuple(s.value for s in IdentityState)
IDENTITY_SOURCE_VALUES: Final[tuple[str, ...]] = tuple(s.value for s in IdentitySource)
ROLE_VALUES: Final[tuple[str, ...]] = tuple(r.value for r in UserRole)
TASK_STATE_VALUES: Final[tuple[str, ...]] = tuple(s.value for s in TaskState)

__all__ = [
    "CONTINUOUS_AGGREGATES",
    "DERIVED_VIEWS",
    "HYPERTABLES",
    "IDENTITY_SOURCE_VALUES",
    "IDENTITY_STATE_VALUES",
    "PLATFORM_VALUES",
    "RETENTION_DAYS",
    "ROLE_VALUES",
    "SNAPSHOT_COMPRESSION_AFTER_DAYS",
    "TABLE_NAMES",
    "TASK_STATE_VALUES",
    "ApiKey",
    "AuditLog",
    "ContentSnapshot",
    "Identity",
    "IdentityEvent",
    "MediaDownload",
    "Proxy",
    "RequestLog",
    "Setting",
    "SettingsVersion",
    "Task",
    "User",
    "WatchlistEntry",
]
