"""Request bodies.

Validation happens at the boundary and nowhere else. Anything that reaches a
handler has already been shaped, so a handler never re-checks a length or a
range. Field constraints render into the OpenAPI document, which is where most
callers will actually read them.

Response bodies are assembled as plain dictionaries inside the uniform
envelope; declaring them twice would only let the two drift apart.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from dtk.api.routes.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from dtk.core.types import Platform, Scope, UserRole

#: Usernames are local console accounts, not display names: keep them to what
#: reads unambiguously in a log line and a URL.
USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$"

#: One batch submission is a convenience for the console's paste-a-list flow.
#: Past this a caller should page, not widen the request.
MAX_BATCH_ITEMS = 50

#: Ceiling on pasted cookie and proxy blobs. Generous for real input, bounded
#: enough that a paste cannot become a memory problem.
MAX_PASTE_CHARS = 200_000


class Body(BaseModel):
    """Base for every request body: unknown fields are an error, not noise."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Setup and authentication
# --------------------------------------------------------------------------


class SetupInit(Body):
    token: str = Field(min_length=8, max_length=256)
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(Body):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class PasswordChange(Body):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


# --------------------------------------------------------------------------
# Data endpoints
# --------------------------------------------------------------------------


class ParseRequest(Body):
    """A link, or the share text a platform app puts on the clipboard."""

    url: str = Field(min_length=1, max_length=4096)
    include_raw: bool = False
    callback_url: str | None = Field(default=None, max_length=2048)


class BatchItem(Body):
    url: str = Field(min_length=1, max_length=4096)
    include_raw: bool = False


class BatchRequest(Body):
    items: list[BatchItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    callback_url: str | None = Field(default=None, max_length=2048)


# --------------------------------------------------------------------------
# Identity pool
# --------------------------------------------------------------------------


class MintRequest(Body):
    platform: Platform
    count: int = Field(default=1, ge=1, le=10)
    proxy_id: str | None = None


class IdentityImport(Body):
    """Paste from DevTools, a cookie extension, cookies.txt or by hand.

    The format is detected rather than declared: doc 07 is explicit that asking
    the user which of four shapes they hold is how an import flow fails.
    """

    platform: Platform
    cookies: str = Field(min_length=1, max_length=MAX_PASTE_CHARS)
    user_agent: str | None = Field(default=None, max_length=1024)
    language: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    proxy_id: str | None = None
    #: Preview only. The console shows what was understood before anything is
    #: stored, because a mistyped cookie jar is easier to spot than to debug.
    dry_run: bool = False


class RetireRequest(Body):
    reason: str = Field(default="retired from the console", max_length=256)


# --------------------------------------------------------------------------
# Proxies
# --------------------------------------------------------------------------


class ProxyCreate(Body):
    url: str = Field(min_length=3, max_length=2048)
    label: str | None = Field(default=None, max_length=128)
    country: str | None = Field(default=None, max_length=8)
    timezone: str | None = Field(default=None, max_length=64)


class ProxyUpdate(Body):
    url: str | None = Field(default=None, min_length=3, max_length=2048)
    label: str | None = Field(default=None, max_length=128)
    country: str | None = Field(default=None, max_length=8)
    timezone: str | None = Field(default=None, max_length=64)
    healthy: bool | None = None


class ProxyImport(Body):
    """Several dozen lines pasted at once, in any of the common shapes."""

    text: str = Field(min_length=1, max_length=MAX_PASTE_CHARS)
    label: str | None = Field(default=None, max_length=128)


# --------------------------------------------------------------------------
# API keys, settings, users
# --------------------------------------------------------------------------


class ApiKeyCreate(Body):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[Scope] = Field(default_factory=list)
    #: Requests per minute. Null means the instance default; abuse protection
    #: only, this project has no billing.
    rate_limit: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: datetime | None = None


class SettingUpdate(Body):
    value: object
    #: Required for a SENSITIVE key. Widening the URL allowlist or turning on
    #: the download proxy must not be a stray click (doc 10).
    confirm: bool = False


class UserCreate(Body):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: UserRole = UserRole.VIEWER


class UserUpdate(Body):
    role: UserRole | None = None
    password: str | None = Field(
        default=None, min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------


class BackupRequest(Body):
    #: Off by default: identities are bound to an egress and a fingerprint, so
    #: restoring them onto another machine revives credentials that should not
    #: be used from a new exit address (doc 15).
    include_identities: bool = False


class RestoreRequest(Body):
    #: The file name the listing returned, never a path. The server joins it
    #: onto the backup directory, so anything with a directory in it is refused
    #: rather than resolved.
    path: str = Field(min_length=1, max_length=255)
    #: A restore inserts archived users, API keys and settings into a live
    #: instance and cannot be undone from the console, so it is confirmed the
    #: way a SENSITIVE setting write is (doc 08).
    confirm: bool = False


class DownloadRequest(Body):
    """Start storing one archived post's media.

    A content key, never a URL. That is the whole of doc 08's fourth
    constraint expressed as a type: there is no field here a caller could use
    to point the downloader at a host of their choosing.
    """

    platform: Platform
    #: As the archive holds it. Text, never an integer: a 19-digit aweme_id
    #: exceeds the JavaScript safe range.
    content_id: str = Field(min_length=1, max_length=64)


class PinRequest(Body):
    #: True exempts this download from the size-based cleanup for good.
    pinned: bool = True


class WatchCreate(Body):
    """Add one target to the schedule.

    A content key, the same as a download: there is no URL here either. What
    this creates is a standing instruction to spend the identity pool every few
    hours, which is why it is administrative rather than a data endpoint.
    """

    platform: Platform
    #: author or content.
    kind: str = Field(pattern="^(author|content)$")
    #: sec_user_id or aweme_id, as the platform spells it.
    target_id: str = Field(min_length=1, max_length=128)
    #: What to call it in the console. Filled in from the first successful run
    #: when it is omitted, so pasting a bare id is fine.
    label: str | None = Field(default=None, max_length=128)
    #: Omitted means watchlist.default_interval_seconds. The server refuses
    #: anything under watchlist.min_interval_seconds rather than silently
    #: substituting a number the caller did not ask for.
    interval_seconds: int | None = Field(default=None, ge=1)
    #: Pages of an author's posts one run walks. One is the useful default: a
    #: watchlist is for what is new, and a backfill is a different job.
    pages: int = Field(default=1, ge=1, le=10)


class WatchUpdate(Body):
    interval_seconds: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    pages: int | None = Field(default=None, ge=1, le=10)


class NotificationTest(Body):
    channel: str | None = Field(default=None, max_length=64)


class DiagnoseRequest(Body):
    #: The end-to-end step fetches one fixed public link; skipping it keeps the
    #: diagnosis entirely local.
    include_smoke_test: bool = True


__all__ = [
    "MAX_BATCH_ITEMS",
    "MAX_PASTE_CHARS",
    "USERNAME_PATTERN",
    "ApiKeyCreate",
    "BackupRequest",
    "BatchItem",
    "BatchRequest",
    "Body",
    "DiagnoseRequest",
    "DownloadRequest",
    "IdentityImport",
    "LoginRequest",
    "MintRequest",
    "NotificationTest",
    "ParseRequest",
    "PasswordChange",
    "PinRequest",
    "ProxyCreate",
    "ProxyImport",
    "ProxyUpdate",
    "RestoreRequest",
    "RetireRequest",
    "SettingUpdate",
    "SetupInit",
    "UserCreate",
    "UserUpdate",
    "WatchCreate",
    "WatchUpdate",
]
