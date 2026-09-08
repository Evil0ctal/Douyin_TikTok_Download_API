"""A backend with no browser, for wiring up and testing the service.

What it is for: bringing the stack up on a laptop, exercising the RPC contract
end to end in CI, and reproducing service-level behaviour (single-use profiles,
warm-context refresh, timeouts) without a 500MB Chromium.

What it is not for: minting anything a platform will accept. The cookies are
derived from a hash and carry no session at all. Selecting a backend is
therefore explicit in the compose file - nothing ever falls back to this one,
because a pool quietly filling up with synthetic identities looks healthy right
up to the moment every request comes back risk-controlled.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping

from browser_rpc.backends.base import (
    BackendInfo,
    MintedProfile,
    MintPlan,
    SigningContext,
    SignPlan,
)
from browser_rpc.errors import BackendFailure
from browser_rpc.geo import GeoProfile
from browser_rpc.validation import Platform, ProxyEndpoint

logger = logging.getLogger(__name__)

BACKEND_NAME = "fake"

#: Kept in step with the Chromium the real backend ships, so a deployment that
#: tests with the fake sees the same emulation profile selection it will see in
#: production (docs/design/04-transport-signing.md).
FAKE_CHROMIUM_MAJOR = 149

USER_AGENT_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)

#: The cookie names each platform actually sets on a guest session; the pool's
#: import validation looks for these, so the fake has to produce them.
COOKIE_NAMES: Mapping[Platform, tuple[str, ...]] = {
    Platform.DOUYIN: ("ttwid", "odin_tt", "msToken", "s_v_web_id"),
    Platform.TIKTOK: ("ttwid", "msToken", "tt_csrf_token", "tt_chain_token"),
}

#: Which signature parameter each platform expects back.
SIGNATURE_FIELDS: Mapping[Platform, str] = {
    Platform.DOUYIN: "a_bogus",
    Platform.TIKTOK: "x_bogus",
}


def _digest(*parts: str, length: int = 32) -> str:
    """A stable, URL-safe pseudo-token. Deterministic so tests can assert on it."""
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:length]


class FakeSigningContext:
    """A warm context that computes a hash instead of running platform code.

    It models one property of the real thing deliberately: the signature it
    returns names the ``s_v_web_id`` of the jar the context was OPENED with, not
    the jar of whoever asks it to sign. That is how the platforms behave -
    measured on 2026-09-08, Douyin's ``verifyFp`` is the cookie the document
    loaded with, cached - and reproducing it here is what lets a test catch a
    context being reused across identities without driving a browser. A fake
    that ignored cookies would pass just as happily with the bug in place.
    """

    def __init__(
        self, platform: Platform, geo: GeoProfile, cookies: Mapping[str, str] | None = None
    ) -> None:
        self._platform = platform
        self._geo = geo
        self._closed = False
        #: Frozen at open time on purpose. See the class docstring.
        self.cookies: Mapping[str, str] = dict(cookies or {})

    @property
    def platform(self) -> Platform:
        return self._platform

    @property
    def closed(self) -> bool:
        return self._closed

    async def sign(self, plan: SignPlan) -> dict[str, str]:
        if self._closed:
            raise BackendFailure("signing context is closed")
        field = SIGNATURE_FIELDS[self._platform]
        signature = _digest("sign", self._platform.value, plan.query, plan.user_agent or "")
        signed = {
            field: signature,
            "ms_token": _digest("mstoken", self._platform.value, plan.url, length=48),
        }
        # Only Douyin sends it, and only when the context holds one - an
        # anonymous context has no visitor to name.
        carried = self.cookies.get("s_v_web_id")
        if carried and self._platform is Platform.DOUYIN:
            signed["verifyFp"] = carried
        return signed

    async def close(self) -> None:
        self._closed = True


class FakeBackend:
    """In-process stand-in implementing `BrowserBackend`."""

    def __init__(self, chromium_major: int = FAKE_CHROMIUM_MAJOR) -> None:
        self._chromium_major = chromium_major
        self._started = False
        #: Every profile directory ever handed to `mint`. The service asserts
        #: single use; this is what makes that assertion observable.
        self.minted_profile_dirs: list[str] = []

    async def start(self) -> None:
        self._started = True
        logger.warning(
            "backend.fake.started: minting synthetic identities; "
            "no platform will accept these cookies"
        )

    async def close(self) -> None:
        self._started = False

    def info(self) -> BackendInfo:
        return BackendInfo(
            name=BACKEND_NAME,
            version=f"fake-{self._chromium_major}",
            chromium_major=self._chromium_major,
            pin=None,
        )

    async def mint(self, plan: MintPlan) -> MintedProfile:
        if not self._started:
            raise BackendFailure("backend is not started")
        if plan.profile_dir in self.minted_profile_dirs:
            raise BackendFailure(f"profile directory reused: {plan.profile_dir}")
        self.minted_profile_dirs.append(plan.profile_dir)
        # The real backend writes a profile here; creating it keeps the
        # service's cleanup path exercised.
        os.makedirs(plan.profile_dir, exist_ok=True)

        seed = plan.proxy.server if plan.proxy else "direct"
        cookies = {
            name: _digest("cookie", plan.platform.value, name, seed, plan.profile_dir)
            for name in COOKIE_NAMES[plan.platform]
        }
        return MintedProfile(
            cookies=cookies,
            user_agent=USER_AGENT_TEMPLATE.format(major=self._chromium_major),
            browser_family="chrome",
            browser_major=self._chromium_major,
            navigator_platform="Win32",
            screen="1920x1080",
            language=plan.geo.locale,
            timezone=plan.geo.timezone,
            exit_ip=None,
        )

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> SigningContext:
        if not self._started:
            raise BackendFailure("backend is not started")
        return FakeSigningContext(platform, geo, cookies)


def build(settings: object) -> FakeBackend:
    """Factory used by the backend registry. Takes no configuration."""
    return FakeBackend()


__all__ = ["BACKEND_NAME", "FAKE_CHROMIUM_MAJOR", "FakeBackend", "FakeSigningContext", "build"]
