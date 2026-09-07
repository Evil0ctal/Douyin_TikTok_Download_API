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
    "IdentityImport",
    "LoginRequest",
    "MintRequest",
    "NotificationTest",
    "ParseRequest",
    "PasswordChange",
    "ProxyCreate",
    "ProxyImport",
    "ProxyUpdate",
    "RetireRequest",
    "SettingUpdate",
    "SetupInit",
    "UserCreate",
    "UserUpdate",
]
