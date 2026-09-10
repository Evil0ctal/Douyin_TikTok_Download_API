"""Request bodies.

Validation happens at the boundary and nowhere else. Anything that reaches a
handler has already been shaped, so a handler never re-checks a length or a
range. Field constraints render into the OpenAPI document, which is where most
callers will actually read them.

Response bodies are assembled as plain dictionaries inside the uniform
envelope; declaring them twice would only let the two drift apart.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dtk.api.routes.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from dtk.core.types import Platform, Scope, UserRole
from dtk.services import collections

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


class IdentityExport(Body):
    """Which identities to write into an export document.

    Bounded at 200 because the response carries every jar in full and is
    assembled in memory; a pool larger than that is exported in pages, which is
    a request the caller can see the size of.
    """

    identity_ids: list[str] = Field(min_length=1, max_length=200)


class IdentityBundle(Body):
    """An export document, on its way back in.

    `version` is checked rather than trusted: a file from a future build may
    describe fields this one would drop silently, and dropping a fingerprint
    turns a working identity into one the platform can tell apart from the
    browser it was taken from.
    """

    version: int
    identities: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    proxy_id: str | None = None
    #: Preview only, exactly as on a paste: the console says what it found
    #: before anything is written.
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
    """Start storing one post's media.

    A content key or a link, and a link is still not a URL the downloader will
    fetch. Doc 08's fourth constraint is that nothing a caller sends decides
    what host gets contacted, and that holds: a link here is run through
    `dtk.urls.identify`, which is the SSRF chokepoint, and only the post id it
    yields is used. The request goes to the platform's own endpoint table, the
    same as if the id had been typed.

    Everything is optional at this layer because the combinations are the
    handler's to judge - a link alone, a platform and an id, or a link whose
    platform disagrees with the one named.
    """

    platform: Platform | None = None
    #: As the archive holds it. Text, never an integer: a 19-digit aweme_id
    #: exceeds the JavaScript safe range.
    content_id: str | None = Field(default=None, min_length=1, max_length=64)
    #: A share link, or the share text with one inside it.
    url: str | None = Field(default=None, max_length=4096)
    #: Return the existing download instead of fetching the post again, when
    #: this instance already has its media. What a feed being re-run wants: an
    #: author has added three posts since last time and the other forty are
    #: already on the disk.
    skip_existing: bool = False


class RetryRequest(Body):
    """Start a settled download over.

    There is no resume to ask for. The sidecar truncates its `.part` on every
    attempt, and it is built that way because media URLs are signed and expire -
    bytes fetched an hour ago cannot be continued against a link that now
    answers 403.
    """

    download_id: uuid.UUID


class DedupeRequest(Body):
    """Remove the older duplicate downloads of the same post.

    Re-downloading a post is a normal thing to do - the first attempt failed,
    the mirrors went stale, the file was evicted - and each one leaves a row.
    What accumulates is several complete copies of the same video under the
    same directory, which is the disk filling up with the same bytes.
    """

    #: Report what would go without touching anything.
    dry_run: bool = False


class PinRequest(Body):
    #: True exempts this download from the size-based cleanup for good.
    pinned: bool = True


class RecheckRequest(Body):
    """Verify that archived posts still exist.

    Every post checked is a real request through the identity pool, so the
    batch is bounded here rather than left to whatever the caller asks for.
    """

    limit: int | None = Field(default=None, ge=1, le=200)
    older_than_days: int | None = Field(default=None, ge=0, le=3650)


class BackfillRequest(Body):
    platform: Platform
    #: The author's stable id, not the handle: users edit handles.
    author_id: str = Field(min_length=1, max_length=128)
    pages: int = Field(default=5, ge=1, le=20)


class ContentRef(Body):
    """One archived post, by the pair the archive is keyed on."""

    platform: Platform
    #: aweme_id or its TikTok equivalent. Text, never an integer: a 19-digit id
    #: exceeds the JavaScript safe range and the console would round it.
    content_id: str = Field(min_length=1, max_length=128)


class CollectionCreate(Body):
    """A named set of posts, made by hand.

    Nothing infers membership; that is the point of it. The name is unique
    case-insensitively, which the database enforces rather than this schema -
    a check here would still lose a race between two console tabs.
    """

    name: str = Field(min_length=1, max_length=collections.MAX_NAME)
    note: str | None = Field(default=None, max_length=collections.MAX_NOTE)


class CollectionUpdate(Body):
    """Rename a collection, change its note, or both.

    Both fields are optional and both default to None, which is why the handler
    reads `model_fields_set` rather than the values: "leave the note alone" and
    "clear the note" arrive identically otherwise, and guessing at that would
    quietly erase text nobody mentioned.
    """

    name: str | None = Field(default=None, min_length=1, max_length=collections.MAX_NAME)
    note: str | None = Field(default=None, max_length=collections.MAX_NOTE)


class ContentSelection(Body):
    """The posts a bulk call acts on.

    Bounded here rather than in the handler: past this a caller should make two
    calls, not hold one transaction open across the archive.
    """

    items: list[ContentRef] = Field(min_length=1, max_length=collections.MAX_ITEMS)


class ArchiveDelete(ContentSelection):
    """Remove posts from the archive, and optionally their stored media.

    `media` defaults to true because that is what "delete this" means when the
    thing on screen is a video the instance has on disk. Setting it false keeps
    the files and removes only the record, which is the narrower thing somebody
    might want and is not the obvious reading of the button.
    """

    media: bool = True


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
    "BackfillRequest",
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
    "RecheckRequest",
    "RestoreRequest",
    "RetireRequest",
    "SettingUpdate",
    "SetupInit",
    "UserCreate",
    "UserUpdate",
    "WatchCreate",
    "WatchUpdate",
]
