"""Shared enumerations.

These values are machine-readable identifiers. They are never translated and
never renamed once released; see docs/design/14-i18n.md.
"""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    DOUYIN = "douyin"
    TIKTOK = "tiktok"


class ContentKind(StrEnum):
    VIDEO = "video"
    IMAGE_ALBUM = "image_album"
    LIVE = "live"


class Outcome(StrEnum):
    """Classification of a single upstream request.

    The split between BUSINESS_ERROR and RISK_CONTROL is load bearing: a deleted
    video must never be counted against an identity's health.
    """

    OK = "ok"
    BUSINESS_ERROR = "business_error"
    RISK_CONTROL = "risk_control"
    NETWORK_ERROR = "network_error"


class IdentityState(StrEnum):
    MINTING = "minting"
    ACTIVE = "active"
    COOLING = "cooling"
    DEGRADED = "degraded"
    RETIRED = "retired"


class IdentitySource(StrEnum):
    MINTED = "minted"
    IMPORTED = "imported"


class BrowserFamily(StrEnum):
    CHROME = "chrome"
    FIREFOX = "firefox"
    SAFARI = "safari"


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class RejectReason(StrEnum):
    """Why the scheduler refused to issue a lease. Recorded in request_log."""

    CIRCUIT_OPEN = "circuit_open"
    NO_IDENTITY = "no_identity"
    NO_TOKEN = "no_token"
    ALL_INFLIGHT = "all_inflight"
    QUEUE_FULL = "queue_full"
    WAIT_TIMEOUT = "wait_timeout"


class Scope(StrEnum):
    """API key scopes."""

    DOUYIN_READ = "douyin:read"
    TIKTOK_READ = "tiktok:read"
    IDENTITY_MANAGE = "identity:manage"
    ADMIN = "admin"


class Language(StrEnum):
    EN = "en"
    ZH = "zh"


DEFAULT_LANGUAGE = Language.EN
