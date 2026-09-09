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


class DownloadState(StrEnum):
    """Where one media download stands.

    Wider than :class:`TaskState`: a download of a post with several files can
    land some and lose others, which is neither done nor failed.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Availability(StrEnum):
    """Whether the archived post is still reachable upstream.

    ``UNKNOWN`` is what a failed check leaves behind. It is deliberately not
    ``DELETED``: an archive that quietly reclassifies posts on a network blip
    would be worse than one that admits it does not know.
    """

    LIVE = "live"
    DELETED = "deleted"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class DurationBucket(StrEnum):
    """Coarse length classes, from ``archive.duration_bucket``."""

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    UNKNOWN = "unknown"


class WatchKind(StrEnum):
    """What a watchlist entry follows."""

    AUTHOR = "author"
    CONTENT = "content"


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
    #: A caller named an identity that cannot serve the request at all - it is
    #: retired, still minting, or belongs to another platform. Distinct from
    #: NO_IDENTITY because the pool is fine and only this request is refused.
    PINNED_UNAVAILABLE = "pinned_unavailable"
    QUEUE_FULL = "queue_full"
    WAIT_TIMEOUT = "wait_timeout"


class Scope(StrEnum):
    """API key scopes."""

    DOUYIN_READ = "douyin:read"
    TIKTOK_READ = "tiktok:read"
    IDENTITY_MANAGE = "identity:manage"
    #: Read what this instance has already stored. Separate from the platform
    #: read scopes on purpose: an operator may open a platform endpoint to
    #: unauthenticated callers, and "read douyin" must not thereby become "read
    #: everything this instance has ever collected".
    ARCHIVE_READ = "archive:read"
    #: Walk the whole archive in one request. Its own scope because a bulk
    #: export is the single call that turns a read key into a copy of the
    #: database.
    ARCHIVE_EXPORT = "archive:export"
    #: See what media this instance has stored on its own disk.
    MEDIA_READ = "media:read"
    #: Start a download, and pin or cancel one. Separate from `media:read`
    #: because starting one spends an identity, fills a disk and is the only
    #: read-shaped call in this API with a lasting side effect on the host.
    MEDIA_WRITE = "media:write"
    ADMIN = "admin"


class Language(StrEnum):
    EN = "en"
    ZH = "zh"


DEFAULT_LANGUAGE = Language.EN
