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
    token: str = Field(
        min_length=8,
        max_length=256,
        description=(
            "The setup token this instance printed to its log on first start. "
            "It is spent once and never issued again."
        ),
    )
    username: str = Field(
        pattern=USERNAME_PATTERN,
        description="Username for the first administrator. Letters, digits, dot, dash, underscore.",
    )
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Password for that account. Stored hashed and never returned.",
    )


class LoginRequest(Body):
    username: str = Field(min_length=1, max_length=128, description="The console account.")
    password: str = Field(
        min_length=1,
        max_length=MAX_PASSWORD_LENGTH,
        description="Its password. A wrong one is rate limited per account and per address.",
    )


class PasswordChange(Body):
    current_password: str = Field(
        min_length=1,
        max_length=MAX_PASSWORD_LENGTH,
        description="The password in use now. Checked even for an administrator changing their own.",
    )
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="The replacement. Every other session for this account is signed out.",
    )


# --------------------------------------------------------------------------
# Data endpoints
# --------------------------------------------------------------------------


class ParseRequest(Body):
    """A link, or the share text a platform app puts on the clipboard."""

    url: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "A share link, a bare post URL, or the whole clipboard text a platform app "
            "produces with the link buried in it. Short links are followed."
        ),
    )
    include_raw: bool = Field(
        default=False,
        description=(
            "Also return the platform's own response, unparsed. Large - a single post "
            "runs to hundreds of kilobytes - so it is off unless asked for."
        ),
    )
    callback_url: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "Where to POST the result when the task finishes, instead of polling for it. "
            "The host must be on the operator's `security.url_allowlist`."
        ),
    )


class BatchItem(Body):
    url: str = Field(
        min_length=1,
        max_length=4096,
        description="One link, in any of the forms `/parse` accepts.",
    )
    include_raw: bool = Field(
        default=False, description="Also return this item's unparsed platform response."
    )


class BatchRequest(Body):
    items: list[BatchItem] = Field(
        min_length=1,
        max_length=MAX_BATCH_ITEMS,
        description=(
            "The links to submit. Each becomes its own task with its own id and its own "
            "fate, so one bad link cannot smear the whole request into a single error."
        ),
    )
    callback_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Where to POST each result as it finishes. Allowlisted hosts only.",
    )


# --------------------------------------------------------------------------
# Identity pool
# --------------------------------------------------------------------------


class MintRequest(Body):
    platform: Platform = Field(description="Which platform the new identities are for.")
    count: int = Field(
        default=1,
        ge=1,
        le=10,
        description=(
            "How many to mint. Each one drives a real headless browser against the "
            "platform, so this takes tens of seconds per identity."
        ),
    )
    proxy_id: str | None = Field(
        default=None,
        description=(
            "Mint behind this exit and bind the identities to it. An identity is minted "
            "from one address and must keep using it; omitting this mints direct."
        ),
    )


class IdentityImport(Body):
    """Paste from DevTools, a cookie extension, cookies.txt or by hand.

    The format is detected rather than declared: doc 07 is explicit that asking
    the user which of four shapes they hold is how an import flow fails.
    """

    platform: Platform = Field(description="Which platform the jar belongs to.")
    cookies: str = Field(
        min_length=1,
        max_length=MAX_PASTE_CHARS,
        description=(
            "The jar, in any of the shapes people actually hold: a `Cookie:` header, "
            "DevTools JSON, a cookies.txt file, or `name=value` pairs. The format is "
            "detected rather than declared."
        ),
    )
    user_agent: str | None = Field(
        default=None,
        max_length=1024,
        description=(
            "The User-Agent of the browser the jar came from. Both platforms hash it "
            "into their signatures, so a jar sent under a different one is a "
            "contradiction they can see."
        ),
    )
    language: str | None = Field(
        default=None,
        max_length=64,
        description="That browser's `navigator.language`, e.g. `zh-CN`.",
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="That browser's IANA zone, e.g. `Asia/Shanghai`. Should agree with the exit.",
    )
    proxy_id: str | None = Field(
        default=None,
        description="Send as this identity only through this exit.",
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Report what was understood without storing anything. A mistyped cookie jar "
            "is far easier to spot than to debug."
        ),
    )


class IdentityExport(Body):
    """Which identities to write into an export document.

    Bounded at 200 because the response carries every jar in full and is
    assembled in memory; a pool larger than that is exported in pages, which is
    a request the caller can see the size of.
    """

    identity_ids: list[str] = Field(
        min_length=1,
        max_length=200,
        description=(
            "The identities to write out. The document carries every jar in full, so "
            "treat it as a credential file."
        ),
    )


class IdentityBundle(Body):
    """An export document, on its way back in.

    `version` is checked rather than trusted: a file from a future build may
    describe fields this one would drop silently, and dropping a fingerprint
    turns a working identity into one the platform can tell apart from the
    browser it was taken from.
    """

    version: int = Field(
        description=(
            "The export document's format version. Checked rather than trusted: a file "
            "from a newer build may carry fields this one would drop silently."
        )
    )
    identities: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=200,
        description="The `identities` array from the export document, unchanged.",
    )
    proxy_id: str | None = Field(
        default=None, description="Bind every imported identity to this exit."
    )
    dry_run: bool = Field(
        default=False,
        description="Report what the file holds without storing anything.",
    )


class RetireRequest(Body):
    reason: str = Field(
        default="retired from the console",
        max_length=256,
        description="Recorded on the identity's timeline, so a later reader knows why it went.",
    )


# --------------------------------------------------------------------------
# Proxies
# --------------------------------------------------------------------------


class ProxyCreate(Body):
    url: str = Field(
        min_length=3,
        max_length=2048,
        description=(
            "The exit, as `scheme://user:pass@host:port`. Stored encrypted and never "
            "returned in full."
        ),
    )
    label: str | None = Field(
        default=None, max_length=128, description="What to call it in the console."
    )
    country: str | None = Field(
        default=None,
        max_length=8,
        description=(
            "ISO country code of the exit. Used to give an identity minted behind it a "
            "coherent locale; measured on the first probe when omitted."
        ),
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA zone of the exit, for the same reason as `country`.",
    )


class ProxyUpdate(Body):
    url: str | None = Field(
        default=None, min_length=3, max_length=2048, description="Replace the exit address."
    )
    label: str | None = Field(default=None, max_length=128, description="Rename it.")
    country: str | None = Field(default=None, max_length=8, description="Correct the country.")
    timezone: str | None = Field(default=None, max_length=64, description="Correct the zone.")
    healthy: bool | None = Field(
        default=None,
        description=(
            "Override what the last probe concluded. For an exit you know is fine but "
            "that fails the probe, or the reverse."
        ),
    )


class ProxyImport(Body):
    """Several dozen lines pasted at once, in any of the common shapes."""

    text: str = Field(
        min_length=1,
        max_length=MAX_PASTE_CHARS,
        description=(
            "The proxies, one per line, in any of the common shapes - "
            "`host:port:user:pass`, `user:pass@host:port`, or a full URL."
        ),
    )
    label: str | None = Field(
        default=None,
        max_length=128,
        description="Applied to every proxy in the paste, numbered when there is more than one.",
    )


# --------------------------------------------------------------------------
# API keys, settings, users
# --------------------------------------------------------------------------


class ApiKeyCreate(Body):
    name: str = Field(
        min_length=1,
        max_length=128,
        description="What the key is for. Shown in the list and in the audit log.",
    )
    scopes: list[Scope] = Field(
        default_factory=list,
        description=(
            "What the key may do. A key can never be given a scope its creator does not "
            "hold, so this is bounded by your own."
        ),
    )
    rate_limit: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
        description=(
            "Requests per minute. Null means the instance default. Abuse protection "
            "only - this project has no billing."
        ),
    )
    expires_at: datetime | None = Field(
        default=None,
        description="When the key stops working. Must be in the future; null never expires.",
    )


class SettingUpdate(Body):
    value: object = Field(
        description=(
            "The new value, typed as the setting declares it. A string where a number "
            "is expected is refused rather than coerced."
        )
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Required for a setting flagged SENSITIVE. Widening the URL allowlist or "
            "turning on the download proxy must not be a stray click."
        ),
    )


class UserCreate(Body):
    username: str = Field(
        pattern=USERNAME_PATTERN,
        description="Letters, digits, dot, dash and underscore. Unique, and not renameable.",
    )
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Their initial password. Stored hashed and never returned.",
    )
    role: UserRole = Field(
        default=UserRole.VIEWER,
        description=(
            "What the account may do. You cannot create an account with a role above your own."
        ),
    )


class UserUpdate(Body):
    role: UserRole | None = Field(
        default=None,
        description="Change what the account may do. The last administrator cannot be demoted.",
    )
    password: str | None = Field(
        default=None,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Set a new password, signing every session for that account out.",
    )


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------


class BackupRequest(Body):
    include_identities: bool = Field(
        default=False,
        description=(
            "Include the identity pool and its cookie jars. Off by default: an identity "
            "is bound to one exit and one fingerprint, so restoring it onto another "
            "machine revives a credential that must not be used from a new address."
        ),
    )


class RestoreRequest(Body):
    path: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "The file name the backup listing returned - a name, never a path. The "
            "server joins it onto the backup directory, so anything carrying a "
            "directory is refused rather than resolved."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Required. A restore inserts archived users, API keys and settings into a "
            "live instance and cannot be undone from the console."
        ),
    )


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

    platform: Platform | None = Field(
        default=None, description="Which platform the post is on. Implied by `url` when given."
    )
    content_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "The post id, as the archive holds it. Text, never an integer: a 19-digit "
            "id exceeds the JavaScript safe range."
        ),
    )
    url: str | None = Field(
        default=None,
        max_length=4096,
        description=(
            "A share link, or the share text with one inside it. Only the post id it "
            "yields is used; nothing here decides which host gets contacted."
        ),
    )
    skip_existing: bool = Field(
        default=False,
        description=(
            "Return the existing download instead of fetching the post again, when this "
            "instance already holds its media. What re-running a feed wants: three new "
            "posts and forty already on disk."
        ),
    )


class RetryRequest(Body):
    """Start a settled download over.

    There is no resume to ask for. The sidecar truncates its `.part` on every
    attempt, and it is built that way because media URLs are signed and expire -
    bytes fetched an hour ago cannot be continued against a link that now
    answers 403.
    """

    download_id: uuid.UUID = Field(
        description="The download to start over, from the download list."
    )


class DedupeRequest(Body):
    """Remove the older duplicate downloads of the same post.

    Re-downloading a post is a normal thing to do - the first attempt failed,
    the mirrors went stale, the file was evicted - and each one leaves a row.
    What accumulates is several complete copies of the same video under the
    same directory, which is the disk filling up with the same bytes.
    """

    dry_run: bool = Field(
        default=False, description="Report what would go without deleting anything."
    )


class PinRequest(Body):
    pinned: bool = Field(
        default=True,
        description=(
            "True exempts this download from the size-based cleanup for good; false "
            "puts it back in scope."
        ),
    )


class RecheckRequest(Body):
    """Verify that archived posts still exist.

    Every post checked is a real request through the identity pool, so the
    batch is bounded here rather than left to whatever the caller asks for.
    """

    limit: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description=(
            "How many posts to check. Every one is a real request through the identity "
            "pool, so the batch is bounded here rather than left to the caller."
        ),
    )
    older_than_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description="Only re-check posts last seen at least this many days ago.",
    )


class BackfillRequest(Body):
    platform: Platform = Field(description="Which platform the author is on.")
    author_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "The author's stable id - `sec_user_id` on Douyin, `secUid` on TikTok - and "
            "not the handle, which users edit."
        ),
    )
    pages: int = Field(
        default=5,
        ge=1,
        le=20,
        description="How many pages of their posts to walk. Each page is one upstream request.",
    )


class ContentRef(Body):
    """One archived post, by the pair the archive is keyed on."""

    platform: Platform = Field(description="Which platform the post is on.")
    content_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "`aweme_id` or its TikTok equivalent. Text, never an integer: a 19-digit id "
            "exceeds the JavaScript safe range and a JSON parser would round it."
        ),
    )


class CollectionCreate(Body):
    """A named set of posts, made by hand.

    Nothing infers membership; that is the point of it. The name is unique
    case-insensitively, which the database enforces rather than this schema -
    a check here would still lose a race between two console tabs.
    """

    name: str = Field(
        min_length=1,
        max_length=collections.MAX_NAME,
        description="What to call it. Unique case-insensitively, enforced by the database.",
    )
    note: str | None = Field(
        default=None, max_length=collections.MAX_NOTE, description="Free text, for your own use."
    )


class CollectionUpdate(Body):
    """Rename a collection, change its note, or both.

    Both fields are optional and both default to None, which is why the handler
    reads `model_fields_set` rather than the values: "leave the note alone" and
    "clear the note" arrive identically otherwise, and guessing at that would
    quietly erase text nobody mentioned.
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=collections.MAX_NAME,
        description="Rename it. Omit the field entirely to leave the name alone.",
    )
    note: str | None = Field(
        default=None,
        max_length=collections.MAX_NOTE,
        description=(
            "Replace the note. Omitting the field leaves it alone; sending null clears "
            "it - the two are told apart, so nothing is erased that nobody mentioned."
        ),
    )


class ContentSelection(Body):
    """The posts a bulk call acts on.

    Bounded here rather than in the handler: past this a caller should make two
    calls, not hold one transaction open across the archive.
    """

    items: list[ContentRef] = Field(
        min_length=1,
        max_length=collections.MAX_ITEMS,
        description=(
            "The posts to act on. Past this limit make two calls rather than hold one "
            "transaction open across the archive."
        ),
    )


class ArchiveDelete(ContentSelection):
    """Remove posts from the archive, and optionally their stored media.

    `media` defaults to true because that is what "delete this" means when the
    thing on screen is a video the instance has on disk. Setting it false keeps
    the files and removes only the record, which is the narrower thing somebody
    might want and is not the obvious reading of the button.
    """

    media: bool = Field(
        default=True,
        description=(
            "Also delete the stored media files. True because that is what "
            '"delete this" means when the thing on screen is a video on disk; false '
            "keeps the files and removes only the record."
        ),
    )


class WatchCreate(Body):
    """Add one target to the schedule.

    A content key, the same as a download: there is no URL here either. What
    this creates is a standing instruction to spend the identity pool every few
    hours, which is why it is administrative rather than a data endpoint.
    """

    platform: Platform = Field(description="Which platform the target is on.")
    kind: str = Field(
        pattern="^(author|content)$",
        description=(
            "`author` to watch someone's posts for new ones, `content` to watch one "
            "post's counters."
        ),
    )
    target_id: str = Field(
        min_length=1,
        max_length=128,
        description="`sec_user_id` or `aweme_id`, as the platform spells it.",
    )
    label: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "What to call it in the console. Filled in from the first successful run "
            "when omitted, so pasting a bare id is fine."
        ),
    )
    interval_seconds: int | None = Field(
        default=None,
        ge=1,
        description=(
            "How often to run it. Omitted means `watchlist.default_interval_seconds`. "
            "Anything under `watchlist.min_interval_seconds` is refused rather than "
            "silently raised to a number you did not ask for."
        ),
    )
    pages: int = Field(
        default=1,
        ge=1,
        le=10,
        description=(
            "Pages of an author's posts one run walks. One is the useful default: a "
            "watchlist is for what is new, and a backfill is a different job."
        ),
    )


class WatchUpdate(Body):
    interval_seconds: int | None = Field(
        default=None, ge=1, description="Change how often it runs. Same floor as on create."
    )
    enabled: bool | None = Field(
        default=None,
        description="Pause it or start it again. A paused entry keeps its history.",
    )
    pages: int | None = Field(
        default=None, ge=1, le=10, description="Change how many pages one run walks."
    )


class NotificationTest(Body):
    channel: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Which configured channel to send a test through. Omitted sends through "
            "every channel that is configured."
        ),
    )


class DiagnoseRequest(Body):
    include_smoke_test: bool = Field(
        default=True,
        description=(
            "Run the end-to-end step, which fetches one fixed public link and therefore "
            "spends an identity. Skipping it keeps the whole diagnosis local."
        ),
    )


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
