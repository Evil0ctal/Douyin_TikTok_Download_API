"""Service behaviour: single-use mint profiles, warm signing contexts, budgets.

These are the rules the backends do not own, which is why they can be tested
without a browser at all.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from browser_rpc.backends.base import MintedProfile, MintPlan, SignPlan
from browser_rpc.backends.fake import FakeBackend, FakeSigningContext
from browser_rpc.errors import (
    BackendFailure,
    BackendUnavailable,
    InvalidRequest,
    OperationTimeout,
)
from browser_rpc.geo import ExitInfo, GeoProfile
from browser_rpc.service import BrowserRpcService
from browser_rpc.settings import Settings
from browser_rpc.validation import Platform, ProxyEndpoint

DOUYIN_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"


def probe_returning(info: ExitInfo) -> Any:
    async def probe(*_: object, **__: object) -> ExitInfo:
        return info

    return probe


class CountingBackend(FakeBackend):
    """Counts how many signing contexts were opened, and through what."""

    def __init__(self) -> None:
        super().__init__()
        self.contexts_opened = 0
        self.context_proxies: list[ProxyEndpoint | None] = []

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
    ) -> FakeSigningContext:
        self.contexts_opened += 1
        self.context_proxies.append(proxy)
        context = await super().open_signing_context(platform, geo, proxy)
        assert isinstance(context, FakeSigningContext)
        return context


class BrokenContext(FakeSigningContext):
    """A warm page that has lost its signing code."""

    async def sign(self, plan: SignPlan) -> dict[str, str]:
        raise BackendFailure("window.byted_acrawler is gone")


class FlakyBackend(CountingBackend):
    """Hands out broken contexts until `heal_after` have been opened."""

    def __init__(self, heal_after: int = 1) -> None:
        super().__init__()
        self.heal_after = heal_after

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
    ) -> FakeSigningContext:
        self.contexts_opened += 1
        if self.contexts_opened <= self.heal_after:
            return BrokenContext(platform, geo)
        return FakeSigningContext(platform, geo)


class SlowBackend(FakeBackend):
    """Takes longer than any budget the tests give it."""

    async def mint(self, plan: MintPlan) -> MintedProfile:
        await asyncio.sleep(5)
        return await super().mint(plan)


class FailingBackend(FakeBackend):
    async def mint(self, plan: MintPlan) -> MintedProfile:
        raise BackendFailure("the exit is blocked")


class ConcurrencyBackend(FakeBackend):
    """Records how many mints overlapped."""

    def __init__(self) -> None:
        super().__init__()
        self.current = 0
        self.peak = 0

    async def mint(self, plan: MintPlan) -> MintedProfile:
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(0.02)
            return await super().mint(plan)
        finally:
            self.current -= 1


class TestMinting:
    async def test_returns_cookies_and_fingerprint(self, service: BrowserRpcService) -> None:
        outcome = await service.mint(Platform.DOUYIN, None, {})
        assert set(outcome.profile.cookies) >= {"ttwid", "msToken"}
        assert outcome.profile.browser_family == "chrome"
        assert outcome.profile.browser_major is not None

    async def test_aligns_timezone_with_the_exit(self, service: BrowserRpcService) -> None:
        # A German exit reporting Asia/Shanghai is a tell given away for free.
        outcome = await service.mint(Platform.DOUYIN, "http://gate.example:8080", {"country": "DE"})
        assert outcome.geo.timezone == "Europe/Berlin"
        assert outcome.profile.timezone == "Europe/Berlin"
        assert outcome.profile.language == "de-DE"

    async def test_measured_exit_drives_alignment(
        self, settings: Settings, backend: FakeBackend
    ) -> None:
        service = BrowserRpcService(
            settings,
            backend,
            probe=probe_returning(ExitInfo(ip="203.0.113.9", country="JP")),
        )
        await service.start()
        try:
            outcome = await service.mint(Platform.TIKTOK, "http://gate.example:8080", None)
        finally:
            await service.close()
        assert outcome.geo.timezone == "Asia/Tokyo"
        assert outcome.exit_ip == "203.0.113.9"

    async def test_profiles_are_single_use_and_removed(
        self, service: BrowserRpcService, backend: FakeBackend
    ) -> None:
        await service.mint(Platform.DOUYIN, None, None)
        await service.mint(Platform.DOUYIN, None, None)

        dirs = backend.minted_profile_dirs
        assert len(dirs) == 2
        assert dirs[0] != dirs[1], "a reused profile carries the previous identity's traces"
        for path in dirs:
            assert not Path(path).exists(), "the profile must not survive its session"

    async def test_profile_is_removed_after_a_failure(self, settings: Settings) -> None:
        service = BrowserRpcService(settings, FailingBackend())
        await service.start()
        try:
            with pytest.raises(BackendFailure):
                await service.mint(Platform.DOUYIN, None, None)
        finally:
            await service.close()
        root = Path(settings.profile_root)
        if root.exists():
            assert list(root.iterdir()) == []

    async def test_timeout_is_reported_not_hung(self, settings: Settings) -> None:
        service = BrowserRpcService(replace(settings, mint_timeout_seconds=0.05), SlowBackend())
        await service.start()
        try:
            with pytest.raises(OperationTimeout):
                await service.mint(Platform.DOUYIN, None, None)
        finally:
            await service.close()

    async def test_concurrent_mints_are_capped(self, settings: Settings) -> None:
        backend = ConcurrencyBackend()
        service = BrowserRpcService(replace(settings, max_concurrent_mints=2), backend)
        await service.start()
        try:
            await asyncio.gather(*(service.mint(Platform.DOUYIN, None, None) for _ in range(6)))
        finally:
            await service.close()
        # Browsers are the memory-heavy part of the stack; six at once is how a
        # 2GB host starts swapping.
        assert backend.peak <= 2

    async def test_refuses_when_the_backend_never_started(
        self, settings: Settings, backend: FakeBackend
    ) -> None:
        service = BrowserRpcService(settings, backend)
        with pytest.raises(BackendUnavailable):
            await service.mint(Platform.DOUYIN, None, None)


class TestSigning:
    async def test_returns_signature_fields(self, service: BrowserRpcService) -> None:
        signed = await service.sign(Platform.DOUYIN, DOUYIN_URL, "aweme_id=7", {"aweme_id": "7"})
        assert signed["a_bogus"]
        assert signed["ms_token"]

    async def test_warm_context_is_reused(self, settings: Settings) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            for _ in range(3):
                await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        # Starting a browser costs seconds; the fallback path cannot pay that
        # on every call.
        assert backend.contexts_opened == 1

    async def test_stale_context_is_rebuilt(self, settings: Settings, clock: Any) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(
            replace(settings, warm_refresh_seconds=60.0), backend, clock=clock
        )
        await service.start()
        try:
            await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
            clock.advance(61)
            await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        # The platform ships new JavaScript regularly; a page loaded an hour ago
        # signs with the old code.
        assert backend.contexts_opened == 2

    async def test_broken_page_is_replaced_and_the_call_succeeds(self, settings: Settings) -> None:
        backend = FlakyBackend(heal_after=1)
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            signed = await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        assert signed["a_bogus"]
        assert backend.contexts_opened == 2

    async def test_gives_up_after_the_retry(self, settings: Settings) -> None:
        service = BrowserRpcService(settings, FlakyBackend(heal_after=99))
        await service.start()
        try:
            with pytest.raises(BackendFailure):
                await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()

    async def test_warm_pages_use_the_configured_exit(self, settings: Settings) -> None:
        # A warm page loads the platform's site. Without an exit it does so from
        # the container's own address, putting the deployment's IP in front of
        # the platform that every identity is trying to look ordinary to.
        backend = CountingBackend()
        service = BrowserRpcService(
            replace(settings, sign_proxy_url="http://user:secret@gate.example:8080"), backend
        )
        await service.start()
        try:
            await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        proxy = backend.context_proxies[0]
        assert proxy is not None
        assert proxy.server == "http://gate.example:8080"
        assert proxy.password == "secret"

    async def test_warm_pages_go_direct_without_one(self, settings: Settings) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        assert backend.context_proxies == [None]

    async def test_rejects_a_url_outside_the_allowlist(self, service: BrowserRpcService) -> None:
        with pytest.raises(InvalidRequest):
            await service.sign(Platform.DOUYIN, "https://example.com/sign", "a=1", {})


class TestHealth:
    async def test_reports_warm_contexts_and_versions(self, settings: Settings) -> None:
        service = BrowserRpcService(replace(settings, prewarm=True), FakeBackend())
        await service.start()
        try:
            body = service.health()
        finally:
            await service.close()
        assert body["status"] == "ok"
        assert body["warm_contexts"] == len(Platform)
        assert body["chromium_major"]
        assert body["backend_version"]
        assert body["uptime"] >= 0

    async def test_unstarted_service_answers_anyway(
        self, settings: Settings, backend: FakeBackend
    ) -> None:
        # An unhealthy service still has to answer the probe, or the operator
        # gets a container restart loop instead of a reason.
        body = BrowserRpcService(settings, backend).health()
        assert body["status"] == "unavailable"
        assert body["warm_contexts"] == 0

    async def test_startup_failure_is_recorded_not_raised(self, settings: Settings) -> None:
        class DeadBackend(FakeBackend):
            async def start(self) -> None:
                raise BackendUnavailable("cloakbrowser is not installed in this image")

        service = BrowserRpcService(settings, DeadBackend())
        await service.start()
        body = service.health()
        assert body["status"] == "unavailable"
        assert "cloakbrowser" in body["error"]
        with pytest.raises(BackendUnavailable):
            await service.mint(Platform.DOUYIN, None, None)
