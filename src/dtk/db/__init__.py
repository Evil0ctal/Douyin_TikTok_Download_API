"""Database schema, migrations and data access.

``dtk.core.db`` owns the engine and the session; this package owns what lives
inside the database: the ORM models (docs/design/05-data-model.md), the Alembic
revisions that build the TimescaleDB objects, and the repositories the service
layer calls.

Importing this package imports the models, which is what registers every table
on ``dtk.core.db.Base.metadata``.
"""

from __future__ import annotations

from dtk.db.models import (
    CONTINUOUS_AGGREGATES,
    HYPERTABLES,
    RETENTION_DAYS,
    TABLE_NAMES,
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
    AuditRepository,
    ContentSnapshotRepository,
    IdentityEventRepository,
    IdentityRepository,
    ProxyRepository,
    RequestLogRepository,
    SettingsRepository,
    TaskRepository,
    UserRepository,
    request_log_row,
)

__all__ = [
    "CONTINUOUS_AGGREGATES",
    "HYPERTABLES",
    "RETENTION_DAYS",
    "TABLE_NAMES",
    "ApiKey",
    "ApiKeyRepository",
    "AuditLog",
    "AuditRepository",
    "ContentSnapshot",
    "ContentSnapshotRepository",
    "Identity",
    "IdentityEvent",
    "IdentityEventRepository",
    "IdentityRepository",
    "Proxy",
    "ProxyRepository",
    "RequestLog",
    "RequestLogRepository",
    "Setting",
    "SettingsRepository",
    "SettingsVersion",
    "Task",
    "TaskRepository",
    "User",
    "UserRepository",
    "request_log_row",
]
