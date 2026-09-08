"""Service configuration, read once from the environment at start.

browser-rpc holds no database and no secrets: everything it needs is a handful
of numbers and one backend name. They are read into a frozen snapshot so no code
path can reconfigure the service while a mint is in flight.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from browser_rpc.errors import ConfigError

ENV_PREFIX = "DTK_BROWSER_"

#: An endpoint that echoes the caller's address. Queried *through the proxy*, so
#: the answer is the exit the identity will actually use.
DEFAULT_GEO_PROBE_URL = "https://ipinfo.io/json"


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable configuration snapshot."""

    #: Backend name, resolved by `browser_rpc.backends.build_backend`. There is
    #: no fallback between backends: silently minting synthetic identities
    #: because the real browser failed would poison the pool.
    backend: str = "cloak"
    #: Provenance of the backend build, stamped in by the image and reported on
    #: /rpc/health so version drift against wreq stays visible.
    backend_pin: str | None = None

    bind_host: str = "127.0.0.1"
    bind_port: int = 9000

    #: Parent directory for browser profiles. On the container it is a tmpfs, so
    #: single-use mint profiles never touch a disk.
    profile_root: str = "/tmp/dtk-browser-profiles"

    #: Warm signing contexts kept per platform. One is enough for the fallback
    #: path; two hides the refresh pause under load.
    warm_contexts: int = 1
    #: How old a warm context may get before it is rebuilt. The platform ships
    #: new JavaScript regularly and a stale page signs with stale code.
    warm_refresh_seconds: float = 1800.0
    #: Prewarm at startup so the first signature does not pay for a cold page.
    prewarm: bool = True

    #: Browsers are the memory-heavy part of the stack (~500MB each), so the
    #: number that can mint at once is capped rather than left to the caller.
    max_concurrent_mints: int = 2

    #: Service-side budgets, each below the matching client timeout in
    #: dtk.identity.minting.client (90s mint, 15s sign) and dtk.signing.rpc (10s).
    mint_timeout_seconds: float = 75.0
    sign_timeout_seconds: float = 8.0
    context_open_timeout_seconds: float = 45.0
    #: How long a freshly opened page may take to become able to sign. A page is
    #: navigable well before its security bundle has loaded, and signing in that
    #: window fails with "the SDK added nothing" - which reads like the platform
    #: changed its algorithm and is really impatience. Counted from the end of
    #: the navigation, so it is additional to the open above.
    sdk_ready_timeout_seconds: float = 25.0

    #: Optional exit for the warm signing pages. Without it they load the
    #: platform's site from the container's own address, which puts the
    #: deployment's IP in front of the platform even though every signed request
    #: later leaves through an identity's proxy.
    sign_proxy_url: str | None = None

    #: Empty disables the probe; the geo hint from the caller is then the only
    #: source of timezone and locale.
    geo_probe_url: str | None = DEFAULT_GEO_PROBE_URL
    geo_probe_timeout_seconds: float = 8.0
    #: Used when neither the caller nor the probe says where the exit is.
    default_country: str = "US"

    #: Off only for debugging on a machine with a display.
    headless: bool = True
    log_level: str = "info"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build a snapshot from ``DTK_BROWSER_*`` variables."""
        source = os.environ if env is None else env
        # `slots=True` turns the class attributes into descriptors, so the
        # defaults have to be read off an instance, not off the class.
        defaults = cls()

        def raw(name: str) -> str | None:
            value = source.get(ENV_PREFIX + name)
            return value.strip() if value is not None else None

        def as_int(name: str, default: int) -> int:
            value = raw(name)
            if not value:
                return default
            try:
                return int(value)
            except ValueError as exc:
                raise ConfigError(f"{ENV_PREFIX}{name} must be an integer, got {value!r}") from exc

        def as_float(name: str, default: float) -> float:
            value = raw(name)
            if not value:
                return default
            try:
                return float(value)
            except ValueError as exc:
                raise ConfigError(f"{ENV_PREFIX}{name} must be a number, got {value!r}") from exc

        def as_bool(name: str, default: bool) -> bool:
            value = raw(name)
            if not value:
                return default
            lowered = value.lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            raise ConfigError(f"{ENV_PREFIX}{name} must be a boolean, got {value!r}")

        probe = raw("GEO_PROBE_URL")
        if probe is None:
            probe = DEFAULT_GEO_PROBE_URL

        settings = cls(
            backend=(raw("BACKEND") or defaults.backend).lower(),
            backend_pin=raw("BACKEND_PIN") or None,
            bind_host=raw("BIND_HOST") or defaults.bind_host,
            bind_port=as_int("BIND_PORT", defaults.bind_port),
            profile_root=raw("PROFILE_ROOT") or defaults.profile_root,
            sign_proxy_url=raw("SIGN_PROXY_URL") or None,
            warm_contexts=as_int("WARM_CONTEXTS", defaults.warm_contexts),
            warm_refresh_seconds=as_float("WARM_REFRESH_SECONDS", defaults.warm_refresh_seconds),
            prewarm=as_bool("PREWARM", defaults.prewarm),
            max_concurrent_mints=as_int("MAX_CONCURRENT_MINTS", defaults.max_concurrent_mints),
            mint_timeout_seconds=as_float("MINT_TIMEOUT_SECONDS", defaults.mint_timeout_seconds),
            sign_timeout_seconds=as_float("SIGN_TIMEOUT_SECONDS", defaults.sign_timeout_seconds),
            sdk_ready_timeout_seconds=as_float(
                "SDK_READY_TIMEOUT_SECONDS", defaults.sdk_ready_timeout_seconds
            ),
            context_open_timeout_seconds=as_float(
                "CONTEXT_OPEN_TIMEOUT_SECONDS", defaults.context_open_timeout_seconds
            ),
            geo_probe_url=probe or None,
            geo_probe_timeout_seconds=as_float(
                "GEO_PROBE_TIMEOUT_SECONDS", defaults.geo_probe_timeout_seconds
            ),
            default_country=(raw("DEFAULT_COUNTRY") or defaults.default_country).upper(),
            headless=as_bool("HEADLESS", defaults.headless),
            log_level=(raw("LOG_LEVEL") or defaults.log_level).lower(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Reject values that would make the service misbehave silently."""
        if self.warm_contexts < 0:
            raise ConfigError("warm_contexts cannot be negative")
        if self.max_concurrent_mints < 1:
            raise ConfigError("max_concurrent_mints must be at least 1")
        for name in (
            "warm_refresh_seconds",
            "mint_timeout_seconds",
            "sign_timeout_seconds",
            "context_open_timeout_seconds",
            "sdk_ready_timeout_seconds",
            "geo_probe_timeout_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} must be positive")
        if not 1 <= self.bind_port <= 65535:
            raise ConfigError(f"bind_port out of range: {self.bind_port}")
        if len(self.default_country) != 2 or not self.default_country.isalpha():
            raise ConfigError(
                f"default_country must be a two-letter code, got {self.default_country!r}"
            )


__all__ = ["DEFAULT_GEO_PROBE_URL", "ENV_PREFIX", "Settings"]
