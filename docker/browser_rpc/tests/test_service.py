"""Service behaviour: single-use mint profiles, warm signing contexts, budgets.

These are the rules the backends do not own, which is why they can be tested
without a browser at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
from browser_rpc.service import ANONYMOUS, BrowserRpcService, binding_key
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
        #: The jar each context was opened with, so a test can prove a
        #: signature was taken in the identity's own session.
        self.context_cookies: list[dict[str, str]] = []

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> FakeSigningContext:
        self.contexts_opened += 1
        self.context_proxies.append(proxy)
        self.context_cookies.append(dict(cookies or {}))
        context = await super().open_signing_context(platform, geo, proxy, cookies)
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
        cookies: Mapping[str, str] | None = None,
    ) -> FakeSigningContext:
        self.contexts_opened += 1
        if self.contexts_opened <= self.heal_after:
            return BrokenContext(platform, geo, cookies)
        return FakeSigningContext(platform, geo, cookies)


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


class TestSigningInTheCallersOwnSession:
    """The bug that made every scraped payload come back empty.

    Signatures used to be taken in one shared warm page while the request went
    out with a pool identity's cookies. Measured against a live page on
    2026-09-08: Douyin's `verifyFp` IS the `s_v_web_id` of whatever browser
    signed, so the query named one visitor and the Cookie header named another.
    Both platforms answer that with a withheld payload rather than an error,
    which is why it read as rate limiting for so long.

    `FakeSigningContext` reproduces the coupling - it reports the jar it was
    OPENED with, not the jar of whoever asks it to sign - so these tests fail
    against the old behaviour without needing a browser.
    """

    async def test_the_signature_names_the_callers_own_visitor(self, settings: Settings) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            signed = await service.sign(
                Platform.DOUYIN,
                DOUYIN_URL,
                "a=1",
                {"a": "1"},
                cookies={"s_v_web_id": "verify_caller", "ttwid": "1|abc"},
                identity_id="ident-a",
            )
        finally:
            await service.close()

        assert signed["verifyFp"] == "verify_caller"
        assert backend.context_cookies == [{"s_v_web_id": "verify_caller", "ttwid": "1|abc"}]

    async def test_two_identities_never_share_a_page(self, settings: Settings) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            first = await service.sign(
                Platform.DOUYIN,
                DOUYIN_URL,
                "a=1",
                {"a": "1"},
                cookies={"s_v_web_id": "verify_a"},
                identity_id="a",
            )
            second = await service.sign(
                Platform.DOUYIN,
                DOUYIN_URL,
                "a=1",
                {"a": "1"},
                cookies={"s_v_web_id": "verify_b"},
                identity_id="b",
            )
        finally:
            await service.close()

        # The whole point: B's request must not go out quoting A's visitor.
        assert first["verifyFp"] == "verify_a"
        assert second["verifyFp"] == "verify_b"
        assert backend.contexts_opened == 2

    async def test_one_identity_keeps_its_page(self, settings: Settings) -> None:
        """Coherence must not cost a browser launch per request."""
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            for _ in range(4):
                await service.sign(
                    Platform.DOUYIN,
                    DOUYIN_URL,
                    "a=1",
                    {"a": "1"},
                    cookies={"s_v_web_id": "verify_a"},
                    identity_id="a",
                )
        finally:
            await service.close()
        assert backend.contexts_opened == 1

    async def test_the_page_follows_the_identitys_own_exit(self, settings: Settings) -> None:
        """Same jar, different exit: still a different page.

        Reusing a page opened through one proxy would show the platform this
        identity's cookies arriving from an address that is not its own, which
        is exactly the link the pool exists to avoid.
        """
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            for exit_url in ("http://proxy-a:8080", "http://proxy-b:8080"):
                await service.sign(
                    Platform.DOUYIN,
                    DOUYIN_URL,
                    "a=1",
                    {"a": "1"},
                    cookies={"s_v_web_id": "verify_a"},
                    proxy_url=exit_url,
                    identity_id="a",
                )
        finally:
            await service.close()

        assert backend.contexts_opened == 2
        assert [p.server if p else None for p in backend.context_proxies] == [
            "http://proxy-a:8080",
            "http://proxy-b:8080",
        ]

    async def test_rebinding_never_exceeds_the_configured_browser_count(
        self, settings: Settings
    ) -> None:
        """Rebinding replaces a page; it must not quietly add one.

        `warm_contexts` is a memory budget - each Chromium holds hundreds of
        megabytes of the container's tmpfs - so a pool that grew by one on every
        identity switch would exhaust it and crash mid-navigation, which reaches
        the caller looking like a platform block.
        """
        backend = CountingBackend()
        service = BrowserRpcService(replace(settings, warm_contexts=1), backend)
        await service.start()
        try:
            for name in ("a", "b", "a", "c"):
                await service.sign(
                    Platform.DOUYIN,
                    DOUYIN_URL,
                    "a=1",
                    {"a": "1"},
                    cookies={"s_v_web_id": f"verify_{name}"},
                    identity_id=name,
                )
                assert service._pools[Platform.DOUYIN].live <= 1
        finally:
            await service.close()
        # Four signatures, four rebinds, but never two browsers at once.
        assert backend.contexts_opened == 4

    async def test_concurrent_signatures_do_not_overrun_the_budget(
        self, settings: Settings
    ) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(replace(settings, warm_contexts=2), backend)
        await service.start()
        try:
            await asyncio.gather(
                *[
                    service.sign(
                        Platform.DOUYIN,
                        DOUYIN_URL,
                        "a=1",
                        {"a": "1"},
                        cookies={"s_v_web_id": f"verify_{i}"},
                        identity_id=str(i),
                    )
                    for i in range(8)
                ]
            )
        finally:
            await service.close()
        assert service._pools[Platform.DOUYIN].live == 0

    async def test_an_anonymous_caller_still_gets_a_signature(
        self, service: BrowserRpcService
    ) -> None:
        """No cookies is a coherent request too: the caller sends none either."""
        signed = await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        assert signed["a_bogus"]
        assert "verifyFp" not in signed


class TestBindingKey:
    def test_the_same_session_is_the_same_binding(self) -> None:
        jar = {"s_v_web_id": "verify_a", "ttwid": "1|abc"}
        assert binding_key(jar, None) == binding_key(dict(reversed(list(jar.items()))), None)

    def test_a_different_jar_is_a_different_binding(self) -> None:
        assert binding_key({"s_v_web_id": "a"}, None) != binding_key({"s_v_web_id": "b"}, None)

    def test_a_different_exit_is_a_different_binding(self) -> None:
        jar = {"s_v_web_id": "a"}
        assert binding_key(jar, "http://one:8080") != binding_key(jar, "http://two:8080")

    def test_nothing_at_all_is_the_anonymous_binding(self) -> None:
        assert binding_key({}, None) == ANONYMOUS
        assert binding_key(None, None) == ANONYMOUS

    def test_the_binding_does_not_carry_the_cookies_around(self) -> None:
        """It is logged and compared, and a cookie jar is a credential."""
        key = binding_key({"s_v_web_id": "verify_secret", "sessionid": "s3cr3t"}, "http://u:p@h:1")
        assert "verify_secret" not in key
        assert "s3cr3t" not in key
        assert "p@h" not in key
