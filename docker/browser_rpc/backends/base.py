"""The browser backend contract.

Everything in browser-rpc except `backends/cloak.py` is written against this
file. That is the point: CloakBrowser is one decision among several the project
has already had to revisit, and the day it is replaced the change should be one
new module implementing `BrowserBackend`, not a rewrite of the service.

The contract is deliberately narrow. A backend opens contexts and reads pages;
it does not decide policy. Which profile is single-use, how long a warm context
lives, what the geo alignment should be and how long anything may take are all
decided in `service.py`, where they can be tested without a browser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from browser_rpc.geo import GeoProfile
from browser_rpc.validation import Platform, ProxyEndpoint


@dataclass(frozen=True, slots=True)
class BackendInfo:
    """What the backend is, for /rpc/health and the console's system page.

    ``chromium_major`` is reported next to the wreq emulation profile version in
    the console: the two drifting apart means the TLS fingerprint no longer
    matches the User-Agent, which is self-disclosure
    (docs/design/04-transport-signing.md).
    """

    name: str
    version: str | None = None
    chromium_major: int | None = None
    pin: str | None = None


@dataclass(frozen=True, slots=True)
class MintPlan:
    """One minting session, fully specified. Every field is already validated."""

    platform: Platform
    landing_url: str
    profile_dir: str
    geo: GeoProfile
    proxy: ProxyEndpoint | None = None
    timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class MintedProfile:
    """What a session produced: cookies plus the fingerprint that earned them.

    The two travel together for a reason. A cookie set collected under one
    fingerprint and replayed under another is a mismatch the platform can see
    (docs/design/02-identity-pool.md).
    """

    cookies: Mapping[str, str]
    user_agent: str | None = None
    browser_family: str = "chrome"
    browser_major: int | None = None
    #: `navigator.platform`, e.g. "Win32".
    navigator_platform: str | None = None
    #: "1920x1080".
    screen: str | None = None
    language: str | None = None
    timezone: str | None = None
    #: `navigator.hardwareConcurrency` and `navigator.deviceMemory` (GiB). Both
    #: are echoed back in the platforms' query strings, so a guess here becomes
    #: a claim the User-Agent cannot support.
    hardware_concurrency: int | None = None
    device_memory: int | None = None
    #: Filled in by the backend only when it measured the exit itself; the
    #: service falls back to its own probe.
    exit_ip: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SignPlan:
    """One signature request.

    ``query`` is the exact byte sequence to sign, as built by the caller. The
    parameter map is passed alongside for backends that need it, but the query
    string is authoritative: a signature over almost the right bytes is a wrong
    signature (see `dtk.signing.rpc`).
    """

    platform: Platform
    url: str
    query: str
    params: Mapping[str, str]
    user_agent: str | None = None
    timeout_seconds: float = 8.0


@runtime_checkable
class SigningContext(Protocol):
    """A warm page with the platform's own JavaScript loaded."""

    @property
    def platform(self) -> Platform: ...

    async def sign(self, plan: SignPlan) -> dict[str, str]:
        """Return signature fields, e.g. {"a_bogus": ..., "ms_token": ...}.

        Keys use the wire names of `dtk.signing.rpc.RESPONSE_FIELDS` and
        `PASSTHROUGH_FIELDS`. An empty mapping means the page produced nothing,
        which the service reports as a backend failure.
        """

    async def close(self) -> None:
        """Release the page and its context. Must be safe to call twice."""


@runtime_checkable
class BrowserBackend(Protocol):
    """The whole browser dependency, expressed in five methods."""

    async def start(self) -> None:
        """Bring the backend up. Raises `BackendUnavailable` when it cannot."""

    async def close(self) -> None:
        """Shut everything down. Must be safe to call without a prior start."""

    def info(self) -> BackendInfo:
        """Describe the backend. Cheap and synchronous: /rpc/health calls it."""

    async def mint(self, plan: MintPlan) -> MintedProfile:
        """Run one minting session in a fresh profile directory.

        The service creates and deletes ``plan.profile_dir``; the backend must
        not reuse a directory across calls, because a reused profile carries the
        previous identity's traces.
        """

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> SigningContext:
        """Open a warm context for a platform's signing page.

        ``cookies`` are installed before the page is navigated, not after: the
        signing SDK reads the cookies that decide a signature once, while the
        document loads. Handing them over afterwards changes the jar without
        changing the signature, which is the incoherence this argument exists
        to prevent.
        """


__all__ = [
    "BackendInfo",
    "BrowserBackend",
    "MintPlan",
    "MintedProfile",
    "SignPlan",
    "SigningContext",
]
