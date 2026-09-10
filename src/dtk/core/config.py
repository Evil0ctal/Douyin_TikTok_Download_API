"""Two-layer configuration.

Bootstrap settings come from the environment because they are needed before the
database is reachable. Everything else is seeded from the environment once at
init, then lives in the ``settings`` table where it can be changed at runtime.

The master key is the reason two layers are unavoidable: it decrypts the
database, so it can never live inside it.

See docs/design/10-configuration.md.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dtk.core.crypto import MIN_SECRET_LEN, SecretKeyMissing


class Scope(StrEnum):
    """Where a setting may live and whether it can change without a restart."""

    BOOTSTRAP = "bootstrap"  # env only; needed before the DB exists
    ENV_ONLY = "env_only"  # env only; never persisted, never shown in the UI
    RUNTIME = "runtime"  # seeded from env, then DB-authoritative, hot reloadable
    SENSITIVE = "sensitive"  # runtime, but widening it enlarges the attack surface


class BootstrapSettings(BaseSettings):
    """Read once at process start. Changing any of these requires a restart."""

    model_config = SettingsConfigDict(
        env_prefix="DTK_", env_file=".env", extra="ignore", case_sensitive=False
    )

    secret_key: str = Field(default="", description="Master key for credential encryption")
    database_url: str = Field(default="postgresql+asyncpg://dtk:dtk@postgres:5432/dtk")
    redis_url: str = Field(default="redis://redis:6379/0")
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    log_level: str = "info"
    log_json: bool = True
    #: Empty disables automatic minting; the pool then relies on manual imports.
    browser_rpc_url: str = ""
    #: Where backup archives are written and listed from. Both processes must
    #: agree: the worker writes them and the API serves them. The default is
    #: relative, which suits a checkout and the CLI; the container image is
    #: read-only, so it sets this to a mounted volume instead.
    backup_dir: str = "backups"
    #: Where the Go media downloader listens. Empty disables media downloads
    #: entirely, which is the correct state for a deployment that never starts
    #: the `downloader` compose profile - the archive is unaffected.
    downloader_url: str = ""
    #: Optional shared secret for that sidecar. It listens on an internal
    #: network no other container is on, so this is defence in depth rather
    #: than the boundary; empty means the sidecar checks nothing.
    downloader_token: str = ""

    @field_validator("secret_key")
    @classmethod
    def _require_secret(cls, v: str) -> str:
        if not v or len(v) < MIN_SECRET_LEN:
            raise SecretKeyMissing(
                f"DTK_SECRET_KEY must be set and at least {MIN_SECRET_LEN} characters. "
                "Generate one with: openssl rand -base64 48"
            )
        return v


class SettingSpec:
    """Declaration of one runtime setting."""

    __slots__ = ("choices", "default", "description", "key", "scope", "type_", "validate")

    def __init__(
        self,
        key: str,
        default: Any,
        scope: Scope,
        type_: type,
        description: str,
        choices: tuple[str, ...] | None = None,
        validate: Callable[[Any], Any] | None = None,
    ) -> None:
        self.key = key
        self.default = default
        self.scope = scope
        self.type_ = type_
        #: A note for whoever reads this file. The text a user reads comes from
        #: the catalogue key settings.description.<key> in both languages, so
        #: that adding a setting and translating it are one habit rather than
        #: two; this stays as the fallback for a setting nobody has written a
        #: catalogue entry for yet. tests/unit/test_i18n.py asserts the coverage.
        self.description = description
        #: Permitted values for a string setting, or None if it is free-form.
        #: Enforced on write, because a rejected value naming the valid ones is
        #: far more useful than a stored typo that silently falls back to the
        #: default every time it is read.
        self.choices = choices
        #: Checking the declared type cannot express, run after coercion. It
        #: returns the value to store, so it may canonicalize as well as refuse:
        #: a setting whose entries feed a security decision has to be pinned
        #: down at the boundary rather than re-guessed by every reader.
        self.validate = validate


def _url_allowlist(value: Any) -> list[str]:
    """Canonicalize security.url_allowlist, refusing anything it cannot mean.

    This list is the one setting that can widen an SSRF boundary, so it is
    pinned down here rather than interpreted by each reader: entries come back
    lowercased, deduplicated and sorted, and a hostname that names this machine
    or a private network is refused by name instead of stored and ignored.

    The import is local because :mod:`dtk.urls` is layered above
    :mod:`dtk.core`, and a settings write is far too rare to pay for the
    package at import time.
    """
    from dtk.urls.parse import sanitize_extra_hosts

    return sorted(sanitize_extra_hosts(value))


#: The runtime setting registry. Anything not listed here cannot be stored in
#: the settings table, which keeps a typo from silently becoming a config key.
def _percent(value: Any) -> int:
    """A percentage that is actually a percentage.

    Out of range is not a typo to normalise away: 0 would pause an instance the
    moment it starts, and 150 would mean the guard never fires at all - silently,
    which is the failure the capacity guard exists to prevent.
    """
    number = int(value)
    if not 1 <= number <= 99:
        raise ValueError("must be between 1 and 99")
    return number


def _byte_ceiling(value: Any) -> int:
    """A byte limit that is either off or big enough to be a limit.

    0 means "no ceiling" and is a deliberate choice an operator may make. A
    small positive number is not: 1000 bytes would refuse every real transfer,
    and it would do it as a per-file error rather than as anything resembling
    "your setting is wrong". One mebibyte is the floor.
    """
    number = int(value)
    if number < 0:
        raise ValueError("must not be negative")
    if 0 < number < 1024 * 1024:
        raise ValueError("must be 0 (unlimited) or at least 1 MiB")
    return number


#: What an unattended instance may fill by itself, chosen by the maintainer:
#: a self-hoster who leaves this running overnight would rather lose a few old
#: videos than a partition.
DEFAULT_MEDIA_CEILING: Final = 2 * 1024**3
#: One file. A long Douyin video measured 246 MB on 2026-09-08, so this is
#: roughly twice the largest thing normally encountered.
DEFAULT_MEDIA_FILE_CEILING: Final = 512 * 1024**2


def _watch_floor(value: Any) -> int:
    """A collection interval floor that is actually a floor.

    Below a minute this stops being a schedule and becomes a loop: the entry
    would be re-queued before its previous run had finished, and the pool would
    spend itself on one target. Sixty seconds is already far more often than
    any real watch needs.
    """
    number = int(value)
    if number < 60:
        raise ValueError("must be at least 60 seconds")
    return number


#: Six hours. Often enough that a day's posts arrive in a few observations,
#: rare enough that a watchlist of a hundred authors is 400 requests a day.
DEFAULT_WATCH_INTERVAL: Final = 6 * 3600
#: Fifteen minutes. Nothing on either platform moves fast enough to need more,
#: and the floor is what stops a watchlist becoming a scraper.
DEFAULT_WATCH_FLOOR: Final = 900


RUNTIME_SETTINGS: dict[str, SettingSpec] = {
    s.key: s
    for s in [
        # --- scheduler -----------------------------------------------------
        SettingSpec(
            "sched.max_wait_seconds",
            10,
            Scope.RUNTIME,
            int,
            "How long a request waits for an identity before degrading to a task",
        ),
        SettingSpec("sched.queue_max", 500, Scope.RUNTIME, int, "Maximum queued requests"),
        SettingSpec(
            "sched.cooldown_base_seconds",
            60,
            Scope.RUNTIME,
            int,
            "First cooldown after a risk-control hit",
        ),
        SettingSpec(
            "sched.cooldown_max_seconds",
            21600,
            Scope.RUNTIME,
            int,
            "Cooldown ceiling before an identity is degraded",
        ),
        SettingSpec(
            "sched.circuit_risk_threshold",
            0.6,
            Scope.RUNTIME,
            float,
            "Risk rate above which an endpoint trips",
        ),
        SettingSpec(
            "sched.circuit_min_samples",
            20,
            Scope.RUNTIME,
            int,
            "Minimum samples before an endpoint may trip",
        ),
        SettingSpec(
            "sched.circuit_min_identities",
            3,
            Scope.RUNTIME,
            int,
            "Distinct failing identities required to trip; separates a broken "
            "endpoint from one broken identity",
        ),
        # --- identity pool -------------------------------------------------
        SettingSpec("pool.min_size", 3, Scope.RUNTIME, int, "Low-water mark per platform"),
        SettingSpec("pool.target_size", 8, Scope.RUNTIME, int, "Desired pool size"),
        SettingSpec(
            "pool.max_fail_streak",
            3,
            Scope.RUNTIME,
            int,
            "Consecutive failures after which an identity stops counting towards "
            "the pool level, so the filler replaces it instead of counting it",
        ),
        SettingSpec(
            "pool.health_prior",
            0.8,
            Scope.RUNTIME,
            float,
            "Assumed success rate for a cold-started identity",
        ),
        # --- cache ---------------------------------------------------------
        SettingSpec("cache.content_ttl", 1800, Scope.RUNTIME, int, "Video detail TTL"),
        SettingSpec("cache.author_ttl", 900, Scope.RUNTIME, int, "User profile TTL"),
        SettingSpec("cache.list_ttl", 300, Scope.RUNTIME, int, "List endpoint TTL"),
        SettingSpec(
            "snapshot.min_interval_seconds",
            300,
            Scope.RUNTIME,
            int,
            "Deduplication window for content_snapshots writes",
        ),
        # --- retention -----------------------------------------------------
        SettingSpec("retention.request_log_days", 14, Scope.RUNTIME, int, ""),
        SettingSpec("retention.identity_events_days", 90, Scope.RUNTIME, int, ""),
        SettingSpec("retention.task_days", 90, Scope.RUNTIME, int, ""),
        SettingSpec("retention.task_result_hours", 24, Scope.RUNTIME, int, ""),
        SettingSpec(
            "retention.retired_identity_days",
            90,
            Scope.RUNTIME,
            int,
            "How long a retired identity's row survives its retirement",
        ),
        # --- demo ----------------------------------------------------------
        SettingSpec(
            "demo.enabled",
            False,
            Scope.SENSITIVE,
            bool,
            "Publish a shared read-only account and API key so strangers can "
            "try this instance. Turning it on mints both and prints them once; "
            "turning it off ends every demo session and stops the key working, "
            "without deleting anything. SENSITIVE because it is the one switch "
            "that lets an unauthenticated person become an authenticated one.",
        ),
        SettingSpec(
            "demo.task_retention_minutes",
            30,
            Scope.RUNTIME,
            int,
            "How long a task started by the demo account is kept. The row "
            "cannot be skipped - it is what a task id refers to - so it is "
            "swept instead, on a window measured in minutes rather than the "
            "90 days a real caller's task gets.",
        ),
        # --- api -----------------------------------------------------------
        SettingSpec(
            "api.default_rate_limit_per_min",
            120,
            Scope.RUNTIME,
            int,
            "Abuse protection, not billing",
        ),
        SettingSpec("api.max_wait_seconds", 30, Scope.RUNTIME, int, "Ceiling for ?wait="),
        SettingSpec("api.mcp_tool_timeout", 60, Scope.RUNTIME, int, ""),
        SettingSpec("api.default_language", "en", Scope.RUNTIME, str, ""),
        # --- sensitive -----------------------------------------------------
        SettingSpec(
            "security.url_allowlist",
            [],
            Scope.SENSITIVE,
            list,
            "Extra hostnames a short link may redirect through. The built-in "
            "platform list always applies; entries here are matched exactly and "
            "carry no platform, so they widen expansion, never routing.",
            validate=_url_allowlist,
        ),
        SettingSpec(
            "security.cors_allow_origins",
            [],
            Scope.SENSITIVE,
            list,
            "Empty means same-origin only. '*' would leak API keys to any site.",
        ),
        SettingSpec("security.cors_allow_credentials", False, Scope.SENSITIVE, bool, ""),
        SettingSpec(
            "security.enable_task_webhook",
            False,
            Scope.SENSITIVE,
            bool,
            "Caller-supplied callback_url is an SSRF vector.",
        ),
        SettingSpec(
            "security.webhook_secret",
            "",
            Scope.SENSITIVE,
            str,
            "Shared secret for the X-Dtk-Signature header on task callbacks. "
            "Without it a receiver cannot tell a real notification from anyone "
            "who guessed the URL.",
        ),
        # --- notifications --------------------------------------------------
        SettingSpec("notify.enabled", False, Scope.RUNTIME, bool, ""),
        SettingSpec(
            "notify.channels",
            [],
            Scope.RUNTIME,
            list,
            "List of {type, url, language} channel descriptors",
        ),
        SettingSpec("notify.language", "en", Scope.RUNTIME, str, ""),
        # --- signing ---------------------------------------------------------
        # Which signer to try first. The V4 ports really were stale on
        # 2026-09-07; the current algorithms were reverse-engineered on
        # 2026-09-08 and verified live on all four endpoints of both platforms
        # with no browser in the request path, so "native" is the default and
        # the browser is what mints identities rather than what signs.
        # "rpc" stays selectable, and is where to go the day a platform ships a
        # new SDK. See docs/design/17-signature-reversing.md.
        SettingSpec(
            "signing.mode",
            "native",
            Scope.RUNTIME,
            str,
            "Which signer to prefer: rpc drives a real browser, native runs the "
            "in-process algorithms, auto tries rpc then falls back to native.",
            choices=("rpc", "native", "auto"),
        ),
        # --- capacity ----------------------------------------------------------
        # What keeps an unattended instance safe, and it is not retention:
        # nothing here deletes. Past the hard stop, background collection and new
        # download jobs stand down while interactive reads carry on - turning a
        # full disk into "my API is down" is a worse outage than the one being
        # prevented. See docs/design/18.
        SettingSpec(
            "capacity.warn_percent",
            80,
            Scope.RUNTIME,
            int,
            "Disk usage percent at which to raise a warning.",
            validate=_percent,
        ),
        SettingSpec(
            "capacity.hard_stop_percent",
            92,
            Scope.RUNTIME,
            int,
            "Disk usage percent at which background collection and new download "
            "jobs pause. Interactive reads are never paused, and nothing is deleted.",
            validate=_percent,
        ),
        # --- content archive ---------------------------------------------------
        # A parsed post used to live 24 hours in tasks.result and then vanish.
        # The archive keeps its current state; content_snapshots keeps the
        # history of the same object, and the two join on (platform, content_id).
        SettingSpec(
            "archive.enabled",
            True,
            Scope.RUNTIME,
            bool,
            "Keep parsed posts and authors after the task result expires. Off "
            "makes the instance stateless beyond its logs.",
        ),
        SettingSpec(
            "archive.store_raw",
            False,
            Scope.RUNTIME,
            bool,
            "Also keep each platform's untouched payload in the archive. Off by "
            "default: the parsers fill it on every object, so it is the largest "
            "single thing this instance can choose to store.",
        ),
        SettingSpec(
            "archive.recheck_after_days",
            7,
            Scope.RUNTIME,
            int,
            "How old a post's last existence check may be before it is checked "
            "again. This is what makes 'which of the things I saved are gone' "
            "answerable at all. 0 turns rechecking off.",
        ),
        SettingSpec(
            "archive.recheck_batch",
            25,
            Scope.RUNTIME,
            int,
            "How many posts one recheck pass verifies. Each is a real request "
            "through the identity pool, so this is the knob that decides what "
            "the answer costs.",
        ),
        SettingSpec(
            "archive.recheck_pause_seconds",
            3,
            Scope.RUNTIME,
            int,
            "Seconds between the individual requests a recheck or backfill "
            "makes. The scheduler paces per identity, which does nothing about "
            "a platform limit that counts requests per address; nobody is "
            "waiting on these passes, so slow costs nothing.",
        ),
        SettingSpec(
            "retention.content_days",
            0,
            Scope.RUNTIME,
            int,
            "Delete archived posts older than this many days. 0 means never, "
            "which is the default: the archive exists precisely to outlive the "
            "platform.",
        ),
        # --- watchlist ---------------------------------------------------------
        # What turns content_snapshots into a time series instead of a scatter
        # of whatever somebody happened to parse. Every due entry is submitted
        # as an ordinary task, so these knobs bound how much of the queue
        # scheduled collection may take - never how fast it may go, which the
        # scheduler and the identity pool already decide.
        SettingSpec(
            "watchlist.enabled",
            True,
            Scope.RUNTIME,
            bool,
            "Run scheduled collection. Off leaves the entries and their history "
            "alone and simply stops submitting runs.",
        ),
        SettingSpec(
            "watchlist.min_interval_seconds",
            DEFAULT_WATCH_FLOOR,
            Scope.RUNTIME,
            int,
            "Shortest interval an entry may be given. A target collected every "
            "few seconds does not produce a better series, it spends the whole "
            "identity pool on one author.",
            validate=_watch_floor,
        ),
        SettingSpec(
            "watchlist.default_interval_seconds",
            DEFAULT_WATCH_INTERVAL,
            Scope.RUNTIME,
            int,
            "Interval a new entry gets when none is given.",
        ),
        SettingSpec(
            "watchlist.batch_size",
            10,
            Scope.RUNTIME,
            int,
            "How many due entries one tick may queue. The rest stay due and go "
            "on the next tick, so a large watchlist is spread over minutes "
            "rather than landing in front of whoever is using the API.",
        ),
        # --- media downloads ---------------------------------------------------
        # The one part of this system that writes large files to a disk on its
        # own initiative, so both of its limits are settings rather than
        # constants: the total the volume may hold, and the most any one file
        # may be. Both are counted in bytes actually written - Content-Length
        # and the platform's own size_bytes are claims, and doc 18 records that
        # neither is trusted.
        SettingSpec(
            "media.enabled",
            True,
            Scope.RUNTIME,
            bool,
            "Allow media downloads. Off refuses new jobs and leaves stored "
            "files alone; nothing is deleted by turning this off.",
        ),
        SettingSpec(
            "media.max_bytes",
            DEFAULT_MEDIA_CEILING,
            Scope.RUNTIME,
            int,
            "How much disk stored media may occupy. Past it the oldest "
            "unpinned downloads are removed until the volume is back under, "
            "and an alert says what went. 0 disables eviction entirely.",
            validate=_byte_ceiling,
        ),
        SettingSpec(
            "media.max_file_bytes",
            DEFAULT_MEDIA_FILE_CEILING,
            Scope.RUNTIME,
            int,
            "Ceiling for one file, counted on bytes written rather than on "
            "what the server claims. A transfer that reaches it is refused and "
            "leaves nothing behind.",
            validate=_byte_ceiling,
        ),
        SettingSpec(
            "media.mirror_max_age_seconds",
            600,
            Scope.RUNTIME,
            int,
            "How old an archived media link may be before a download re-parses "
            "the post to get fresh ones. Signed CDN links expire within hours; "
            "0 always re-parses.",
        ),
        # --- selectively opened endpoints -------------------------------------
        # Empty by default: every endpoint needs a credential. An operator can
        # open specific ones by listing them exactly as the API document spells
        # them. dtk.api.public_endpoints refuses admin, auth and setup paths
        # whatever this says, with no override - the failure of a mistyped entry
        # matching an admin route is unrecoverable and silent.
        SettingSpec(
            "api.public_endpoints",
            [],
            # SENSITIVE, not RUNTIME: every entry removes a credential check.
            # That is the definition this scope carries, and it is what makes
            # the console demand an administrator and a typed confirmation
            # rather than accepting a stray click.
            Scope.SENSITIVE,
            list,
            "Endpoints served without an API key, each written as "
            "'<METHOD> <path>' exactly as the API document shows it, for "
            "example 'GET /api/v1/{platform}/video'. Admin, auth and setup "
            "endpoints can never be opened.",
        ),
        # --- caller-supplied egress ------------------------------------------
        # An operator-configured proxy and a caller-supplied one carry opposite
        # trust; see dtk.api.request_proxy. "deny" is the default because the
        # safe failure of this feature is not having it: an instance on a public
        # server sits inside a network its callers cannot otherwise reach, and
        # "dial this for me" is request forgery in its plainest form.
        SettingSpec(
            "security.request_proxy",
            "deny",
            # SENSITIVE for the same reason as security.url_allowlist beside it:
            # "any" lets an API key holder reach the network this instance runs
            # in, which is the largest single widening available in this file.
            Scope.SENSITIVE,
            str,
            "Whether a caller may pass ?proxy=. deny refuses it, public allows "
            "only publicly routable addresses, any allows loopback and private "
            "ranges too and is only coherent when every API key is trusted with "
            "the instance's own network.",
            choices=("deny", "public", "any"),
        ),
        SettingSpec(
            "signing.fallback_enabled",
            True,
            Scope.RUNTIME,
            bool,
            "Whether the other signer may be tried when the preferred one fails. "
            "Turn it off to find out which signer is actually carrying traffic.",
        ),
        SettingSpec(
            "signing.rpc_timeout_seconds",
            60,
            Scope.RUNTIME,
            int,
            "Ceiling for one browser-rpc signing call. It has to cover a cold "
            "one: a signature is taken in a page loaded with the identity's own "
            "cookies, so the first call for an identity launches a browser and "
            "loads the platform through that identity's proxy. Measured at "
            "about 4s without a proxy; later calls for the same identity are "
            "answered by the resident page in milliseconds. Raise "
            "DTK_BROWSER_WARM_CONTEXTS to keep more identities resident.",
        ),
        # --- misc -----------------------------------------------------------
        SettingSpec(
            "system.check_updates",
            True,
            Scope.RUNTIME,
            bool,
            "Let the console ask GitHub, once a day, whether a newer release "
            "exists. The request is made by the operator's browser and never by "
            "this server, so it carries no information about the deployment - "
            "which is what makes it defensible to have on by default. An "
            "instance that never learns it is out of date is the more common "
            "harm on a self-hosted tool. Turn it off for an air-gapped box.",
        ),
    ]
}

SENSITIVE_KEYS = frozenset(k for k, s in RUNTIME_SETTINGS.items() if s.scope is Scope.SENSITIVE)


class Config:
    """An immutable snapshot of the runtime settings.

    Replaced wholesale on reload rather than mutated field by field, so an
    in-flight request keeps the values it started with. Field-level mutation
    produces bugs that are almost impossible to reproduce.
    """

    __slots__ = ("_values", "version")

    def __init__(self, values: dict[str, Any], version: int = 0) -> None:
        self._values = values
        self.version = version

    @classmethod
    def defaults(cls) -> Config:
        return cls({k: s.default for k, s in RUNTIME_SETTINGS.items()})

    def get(self, key: str) -> Any:
        if key not in RUNTIME_SETTINGS:
            raise KeyError(f"unknown setting: {key}")
        return self._values.get(key, RUNTIME_SETTINGS[key].default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def __repr__(self) -> str:
        return f"<Config version={self.version} keys={len(self._values)}>"


def coerce(key: str, value: Any) -> Any:
    """Validate and coerce a value against its declared type.

    Applied on write and again on read: a value hand-edited into the database
    must not be able to take the process down. Invalid reads fall back to the
    default with a warning rather than propagating.
    """
    spec = RUNTIME_SETTINGS.get(key)
    if spec is None:
        raise KeyError(f"unknown setting: {key}")
    coerced = _as_declared_type(spec, key, value)
    return spec.validate(coerced) if spec.validate is not None else coerced


def _as_declared_type(spec: SettingSpec, key: str, value: Any) -> Any:
    if spec.choices is not None:
        text = str(value).strip().lower()
        if text not in spec.choices:
            raise ValueError(f"{key} must be one of {', '.join(spec.choices)}; got {value!r}")
        return text
    if spec.type_ is bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if spec.type_ is list:
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        return list(value)
    return spec.type_(value)


def extra_url_hosts(config: Config) -> frozenset[str]:
    """The hostnames ``security.url_allowlist`` adds, shaped for :mod:`dtk.urls`.

    Everything that reaches a :class:`Config` has been through :func:`coerce`,
    so these are already normalized; a stored value that was not would have
    fallen back to the empty default with a warning, which is the fail-closed
    direction for an allowlist.
    """
    return frozenset(config.get("security.url_allowlist"))


LangCode = Literal["en", "zh"]

__all__ = [
    "RUNTIME_SETTINGS",
    "SENSITIVE_KEYS",
    "BootstrapSettings",
    "Config",
    "Scope",
    "SettingSpec",
    "coerce",
    "extra_url_hosts",
]
