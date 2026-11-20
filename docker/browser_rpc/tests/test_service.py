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
from browser_rpc import service as service_module
from browser_rpc.backends.base import BackendInfo, MintedProfile, MintPlan, SignPlan
from browser_rpc.backends.fake import FAKE_USER_AGENT, FakeBackend, FakeSigningContext
from browser_rpc.errors import (
    BackendFailure,
    BackendUnavailable,
    InvalidRequest,
    OperationTimeout,
    RpcError,
    Suspect,
    UserAgentMismatch,
)
from browser_rpc.geo import ExitInfo, GeoProfile
from browser_rpc.service import ANONYMOUS, BrowserRpcService, binding_key, describe_agent
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


class RecordingContext(FakeSigningContext):
    """Keeps every plan it was asked to sign, as the page would receive it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.plans: list[SignPlan] = []

    async def sign(self, plan: SignPlan) -> dict[str, str]:
        self.plans.append(plan)
        return await super().sign(plan)


class RecordingBackend(CountingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[RecordingContext] = []

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> FakeSigningContext:
        await super().open_signing_context(platform, geo, proxy, cookies)
        context = RecordingContext(platform, geo, cookies)
        self.contexts.append(context)
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
        try:
            with pytest.raises(OperationTimeout):
                await service.mint(Platform.DOUYIN, None, None)
        finally:
            await service.close()

    async def test_concurrent_mints_are_capped(self, settings: Settings) -> None:
        backend = ConcurrencyBackend()
        service = BrowserRpcService(replace(settings, max_concurrent_mints=2), backend)
        await service.start()
        await service.prewarmed()
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

    async def test_the_verb_and_the_body_reach_the_page(self, settings: Settings) -> None:
        """The request always accepted `method`; the plan had nowhere to put it.

        So every signature was taken over a body-less GET, which cost nothing
        until something posted a body: Douyin's a_bogus hashes it, and a POST
        signed without it carries a signature unlike the one its own page sends.
        """
        backend = RecordingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        await service.prewarmed()
        try:
            await service.sign(
                Platform.DOUYIN,
                DOUYIN_URL,
                "a=1",
                {"a": "1"},
                method="post",
                body="count=10&cursor=0",
                content_type="application/x-www-form-urlencoded; charset=UTF-8",
            )
        finally:
            await service.close()

        plans = [plan for context in backend.contexts for plan in context.plans]
        signed = [plan for plan in plans if plan.query == "a=1"]
        assert len(signed) == 1
        assert signed[0].method == "POST"
        assert signed[0].body == "count=10&cursor=0"
        assert signed[0].content_type == "application/x-www-form-urlencoded; charset=UTF-8"

    async def test_warm_context_is_reused(self, settings: Settings) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
        try:
            signed = await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        assert signed["a_bogus"]
        assert backend.contexts_opened == 2

    async def test_gives_up_after_the_retry(self, settings: Settings) -> None:
        service = BrowserRpcService(settings, FlakyBackend(heal_after=99))
        await service.start()
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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
        await service.prewarmed()
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


class TestOpeningFailuresAreRetried:
    """A page that dies while warming up must not become the caller's 502.

    Chromium renderers die under memory pressure - on the machine this was found
    on, swap was exhausted and "Target crashed" arrived mid-warm-up. Acquiring
    the slot used to sit outside the retry, so one unlucky launch failed the
    whole signing call even though a second launch would have worked.
    """

    async def test_a_crash_while_warming_up_is_retried(self, settings: Settings) -> None:
        class CrashesOnceBackend(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                if self.contexts_opened == 0:
                    self.contexts_opened += 1
                    raise BackendFailure(
                        "could not warm a signing page for douyin: Page.evaluate: Target crashed"
                    )
                return await super().open_signing_context(platform, geo, proxy, cookies)

        backend = CrashesOnceBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        await service.prewarmed()
        try:
            signed = await service.sign(
                Platform.DOUYIN,
                DOUYIN_URL,
                "a=1",
                {"a": "1"},
                cookies={"s_v_web_id": "verify_a"},
                identity_id="a",
            )
        finally:
            await service.close()
        assert signed["a_bogus"]
        assert backend.contexts_opened == 2

    async def test_a_pool_that_never_opens_still_reports_the_reason(
        self, settings: Settings
    ) -> None:
        """Retrying must not turn a real outage into a generic failure."""

        class DeadBackend(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                raise BackendFailure("the renderer keeps dying")

        service = BrowserRpcService(settings, DeadBackend())
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure, match="renderer keeps dying"):
                await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()

    async def test_a_failed_open_does_not_leak_the_slot(self, settings: Settings) -> None:
        """The live count is a browser budget; a failed launch must give it back."""

        class DeadBackend(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                raise BackendFailure("the renderer keeps dying")

        service = BrowserRpcService(settings, DeadBackend())
        await service.start()
        await service.prewarmed()
        try:
            for _ in range(3):
                with pytest.raises(BackendFailure):
                    await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
            assert service._pools[Platform.DOUYIN].live == 0
        finally:
            await service.close()


TIKTOK_URL = "https://www.tiktok.com/api/item/detail/"


class TestAPageThatWentBadIsNeverReused:
    """Issue #830, at the service: a dead page must cost nobody an attempt.

    A warm TikTok page that a failed navigation had carried to chrome-error://
    answered every signature with "Invalid base URL". Each of the two attempts
    paid for a page and then failed, and the reporter read two identical lines
    with nothing saying whether the page or the exit was at fault.
    """

    LEFT = "it left tiktok.com for chrome-error://chromewebdata"

    async def test_a_page_that_went_bad_while_idle_is_rebuilt_not_handed_out(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = RecordingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        await service.prewarmed()
        try:
            await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
            first = backend.contexts[0]
            first.broken = self.LEFT
            caplog.set_level("INFO", logger="browser_rpc.service")
            signed = await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
        finally:
            await service.close()

        assert signed
        assert len(backend.contexts) == 2
        assert first.closed
        # It never reached the caller: the bad page was not asked to sign again.
        assert len(first.plans) == 1
        assert backend.contexts[1].plans
        # The reason it was dropped is the line an operator looks for.
        lines = [record.getMessage() for record in caplog.records]
        discarded = [line for line in lines if "warm.discarded" in line]
        assert len(discarded) == 1 and self.LEFT in discarded[0]
        assert not any("attempt_failed" in line for line in lines)

    async def test_the_retry_signs_on_a_new_page_not_the_one_that_failed(
        self, settings: Settings
    ) -> None:
        class AlwaysLeaves(RecordingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                context = await super().open_signing_context(platform, geo, proxy, cookies)
                context.broken = TestAPageThatWentBadIsNeverReused.LEFT
                context.broken_suspect = Suspect.EXIT
                return context

        backend = AlwaysLeaves()
        service = BrowserRpcService(settings, backend)
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure) as excinfo:
                await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
            assert service._pools[Platform.TIKTOK].live == 0
        finally:
            await service.close()

        first, second = backend.contexts
        assert first is not second and first.closed and second.closed
        # A page that left the platform, twice on two pages: the exit, named.
        # The cause leads and the note trails, so a reader that keeps only the
        # head of the message keeps the cause.
        message = excinfo.value.message
        assert message.startswith(f"the tiktok signing page {self.LEFT}")
        assert message.endswith(
            "; both attempts failed this way, on two different pages through the same exit (direct)"
        )

    @pytest.mark.parametrize(
        ("sign_proxy", "identity_proxy", "kind"),
        [
            (None, None, "direct"),
            ("http://gate.example:8080", None, "sign_proxy"),
            ("http://gate.example:8080", "http://user:pw@exit.example:3128", "identity_proxy"),
        ],
    )
    async def test_the_exit_is_named_by_kind_never_by_address(
        self,
        settings: Settings,
        sign_proxy: str | None,
        identity_proxy: str | None,
        kind: str,
    ) -> None:
        class LeavesThroughItsExit(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                context = await super().open_signing_context(platform, geo, proxy, cookies)
                context.broken = "left douyin.com for chrome-error://chromewebdata"
                context.broken_suspect = Suspect.EXIT
                return context

        service = BrowserRpcService(
            replace(settings, sign_proxy_url=sign_proxy), LeavesThroughItsExit()
        )
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure) as excinfo:
                await service.sign(
                    Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"}, proxy_url=identity_proxy
                )
        finally:
            await service.close()
        message = excinfo.value.message
        assert f"through the same exit ({kind})" in message
        for secret in ("gate.example", "exit.example", "pw@"):
            assert secret not in message

    async def test_two_different_failures_report_the_last_as_it_was(
        self, settings: Settings
    ) -> None:
        """Only a repeated failure is evidence about the exit; two unlike ones are not."""

        class DiesDifferently(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                self.contexts_opened += 1
                raise BackendFailure(f"failure number {self.contexts_opened}")

        service = BrowserRpcService(settings, DiesDifferently())
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure) as excinfo:
                await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
        finally:
            await service.close()
        assert excinfo.value.message == "failure number 2"


class _RepeatsItself(CountingBackend):
    """Every open fails with the same message and the same suspect."""

    def __init__(self, message: str, suspect: Suspect | None) -> None:
        super().__init__()
        self.message = message
        self.suspect = suspect

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> FakeSigningContext:
        self.contexts_opened += 1
        raise BackendFailure(self.message, suspect=self.suspect)


class TestARepeatedFailureNamesWhatThePagesShare:
    """Two pages failing alike share an exit, a platform bundle and a browser.

    The note used to name the exit for every repeated failure - "the only thing
    they always share" - and a platform whose SDK stopped signing came out as
    "both attempts ... through the same exit (direct) failed the same way: ...
    could not sign within 25s", which the troubleshooting guide answered with
    "the exit is the usual suspect". The bundle had moved; the proxy was fine.
    """

    NEVER_SIGNED = (
        "could not warm a signing page for tiktok: the tiktok signing page could not sign "
        "within 25s (15 probes): either its security bundle did not load, or the platform "
        "changed how it signs - if this exit loads the site, open an issue. Its last answer: "
        "the SDK added nothing"
    )

    @pytest.mark.parametrize(
        ("suspect", "note"),
        [
            (Suspect.EXIT, "on two different pages through the same exit (direct)"),
            (
                Suspect.BUNDLE,
                "on two different pages loading the same platform bundle through the same "
                "exit (direct)",
            ),
            (Suspect.BROWSER, "on two different pages in the same browser container"),
            (None, "on two different pages"),
        ],
    )
    async def test_the_note_fits_the_failure(
        self, settings: Settings, suspect: Suspect | None, note: str
    ) -> None:
        service = BrowserRpcService(settings, _RepeatsItself("it went wrong", suspect))
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure) as excinfo:
                await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
        finally:
            await service.close()
        assert excinfo.value.message == f"it went wrong; both attempts failed this way, {note}"
        assert excinfo.value.suspect is suspect

    async def test_an_sdk_that_never_signs_is_not_pinned_on_the_proxy(
        self, settings: Settings
    ) -> None:
        backend = _RepeatsItself(self.NEVER_SIGNED, Suspect.BUNDLE)
        service = BrowserRpcService(replace(settings, sign_proxy_url="http://gate:8080"), backend)
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure) as excinfo:
                await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
        finally:
            await service.close()
        message = excinfo.value.message
        assert message.startswith(self.NEVER_SIGNED)
        assert "the platform changed how it signs" in message
        assert "loading the same platform bundle through the same exit (sign_proxy)" in message

    async def test_two_pages_that_fit_different_probe_counts_are_one_failure(
        self, settings: Settings
    ) -> None:
        """The count of probes that fit in the budget differs by one between two
        pages often enough that comparing whole messages dropped the note."""

        class CountsDiffer(_RepeatsItself):
            async def open_signing_context(self, *args: Any, **kwargs: Any) -> FakeSigningContext:
                self.contexts_opened += 1
                raise BackendFailure(
                    self.message.replace("(15 probes)", f"({13 + self.contexts_opened} probes)"),
                    suspect=self.suspect,
                )

        backend = CountsDiffer(self.NEVER_SIGNED, Suspect.BUNDLE)
        service = BrowserRpcService(settings, backend)
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(BackendFailure) as excinfo:
                await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
        finally:
            await service.close()
        assert "(15 probes)" in excinfo.value.message
        assert "loading the same platform bundle through the same exit (direct)" in (
            excinfo.value.message
        )

    async def test_an_open_that_hangs_is_put_to_the_exit(self, settings: Settings) -> None:
        class Hangs(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                await asyncio.sleep(5)
                raise AssertionError("the budget should have ended this")

        service = BrowserRpcService(replace(settings, context_open_timeout_seconds=0.05), Hangs())
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(OperationTimeout) as excinfo:
                await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
        finally:
            await service.close()
        assert excinfo.value.message == (
            "opening a signing context for tiktok exceeded 0.05s; both attempts failed this "
            "way, on two different pages through the same exit (direct)"
        )

    async def test_the_830_message_fits_what_dtk_keeps(self, settings: Settings) -> None:
        """dtk keeps the first 600 characters (`dtk.signing.rpc.RPC_ERROR_EXCERPT_CHARS`).

        At 300 it cut the #830 sentence inside its remedy, "look at the exit it
        lo", so the list of exits to check reached neither the API error, nor
        the log line, nor the self-check. Composed here from the real parts.
        """
        from browser_rpc.backends.cloak import _warm_failure, left_platform

        failure = _warm_failure(
            Platform.TIKTOK, left_platform(Platform.TIKTOK, "chrome-error://chromewebdata")
        )
        service = BrowserRpcService(settings, _RepeatsItself(failure.message, failure.suspect))
        await service.start()
        await service.prewarmed()
        try:
            with pytest.raises(RpcError) as excinfo:
                await service.sign(
                    Platform.TIKTOK, TIKTOK_URL, "a=1", proxy_url="http://user:pw@exit.example:1"
                )
        finally:
            await service.close()
        message = excinfo.value.message
        assert len(message) <= 600
        assert message.index("DTK_BROWSER_SIGN_PROXY_URL") < message.index("both attempts")
        assert message.endswith("through the same exit (identity_proxy)")


class TestPrewarmGoesOutTheConfiguredExit:
    """Prewarm opened its pages directly, under a binding no request asked for.

    With DTK_BROWSER_SIGN_PROXY_URL set, an identity-less signature binds to
    that proxy, so the prewarmed pages were never used; the host's own address
    still reached both platforms at every start; and /rpc/health said two warm
    contexts while a dead signing proxy failed every signature.
    """

    async def test_prewarm_loads_through_the_signing_proxy_and_is_used(
        self, settings: Settings
    ) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(
            replace(settings, prewarm=True, sign_proxy_url="http://user:secret@gate.example:8080"),
            backend,
        )
        await service.start()
        await service.prewarmed()
        try:
            assert backend.contexts_opened == len(Platform)
            assert all(
                proxy is not None and proxy.server == "http://gate.example:8080"
                for proxy in backend.context_proxies
            )
            await service.sign(Platform.DOUYIN, DOUYIN_URL, "a=1", {"a": "1"})
            # The prewarmed page served it: nothing new was opened.
            assert backend.contexts_opened == len(Platform)
        finally:
            await service.close()

    async def test_without_a_signing_proxy_prewarm_stays_direct_and_is_used(
        self, settings: Settings
    ) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(replace(settings, prewarm=True), backend)
        await service.start()
        await service.prewarmed()
        try:
            assert backend.context_proxies == [None] * len(Platform)
            await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
            assert backend.contexts_opened == len(Platform)
            assert service._pools[Platform.TIKTOK].idle[0].binding == ANONYMOUS
        finally:
            await service.close()

    async def test_a_signing_proxy_prewarm_cannot_use_is_reported_not_raised(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = CountingBackend()
        service = BrowserRpcService(
            replace(settings, prewarm=True, sign_proxy_url="ftp://gate.example:21"), backend
        )
        caplog.set_level("WARNING", logger="browser_rpc.service")
        await service.start()
        await service.prewarmed()
        try:
            assert service.health()["status"] == "ok"
            assert service.health()["warm_contexts"] == 0
        finally:
            await service.close()
        assert backend.contexts_opened == 0
        lines = [record.getMessage() for record in caplog.records]
        assert any("prewarm_failed exit=sign_proxy" in line for line in lines)

    async def test_a_dead_signing_proxy_shows_on_health(self, settings: Settings) -> None:
        """The prewarm failure is measured through the exit actually in use."""

        class DeadExit(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                if proxy is not None:
                    raise BackendFailure("net::ERR_PROXY_CONNECTION_FAILED", suspect=Suspect.EXIT)
                return await super().open_signing_context(platform, geo, proxy, cookies)

        service = BrowserRpcService(
            replace(settings, prewarm=True, sign_proxy_url="http://127.0.0.1:9"), DeadExit()
        )
        await service.start()
        await service.prewarmed()
        try:
            assert service.health()["warm_contexts"] == 0
        finally:
            await service.close()

    async def test_a_proxy_that_comes_back_fills_the_pool_without_a_request(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dtk routes here only while /rpc/health shows a warm context, so the
        request that would have filled an empty pool never came: a signing
        proxy that was down at boot left browser-rpc unused after it recovered."""
        monkeypatch.setattr(service_module, "PREWARM_RETRY_SECONDS", 0.01)
        monkeypatch.setattr(service_module, "PREWARM_RETRY_MAX_SECONDS", 0.02)

        class RecoveringExit(CountingBackend):
            def __init__(self) -> None:
                super().__init__()
                self.refusals = 3

            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                if self.refusals > 0:
                    self.refusals -= 1
                    raise BackendFailure("net::ERR_PROXY_CONNECTION_FAILED", suspect=Suspect.EXIT)
                return await super().open_signing_context(platform, geo, proxy, cookies)

        service = BrowserRpcService(
            replace(settings, prewarm=True, sign_proxy_url="http://127.0.0.1:9"), RecoveringExit()
        )
        await service.start()
        try:
            for _ in range(200):
                if service.health()["warm_contexts"] == len(Platform):
                    break
                await asyncio.sleep(0.01)
            assert service.health()["warm_contexts"] == len(Platform)
        finally:
            await service.close()

    async def test_the_service_answers_while_a_prewarm_hangs(self, settings: Settings) -> None:
        """Prewarm used to be awaited before the port opened: an exit that
        neither answered nor refused held /rpc/health and /rpc/mint shut."""
        opened = asyncio.Event()

        class HangingExit(CountingBackend):
            async def open_signing_context(
                self,
                platform: Platform,
                geo: GeoProfile,
                proxy: ProxyEndpoint | None = None,
                cookies: Mapping[str, str] | None = None,
            ) -> FakeSigningContext:
                opened.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        service = BrowserRpcService(replace(settings, prewarm=True), HangingExit())
        await asyncio.wait_for(service.start(), timeout=1.0)
        try:
            await asyncio.wait_for(opened.wait(), timeout=1.0)
            # Answered while the open is still in flight.
            assert service.health()["status"] == "ok"
        finally:
            # Closing cancels the hung open rather than waiting it out.
            await asyncio.wait_for(service.close(), timeout=2.0)


TIKTOK_URL = "https://www.tiktok.com/api/user/detail/"
FOREIGN_AGENT = FAKE_USER_AGENT.replace("Chrome/149", "Chrome/145")


class UnreportedAgentBackend(CountingBackend):
    """A backend that has not read a page's User-Agent yet, as before its first open."""

    def info(self) -> BackendInfo:
        return replace(super().info(), user_agent=None)


class TestAPageSignsUnderItsOwnUserAgent:
    """Issue #832: a signature carries the page's User-Agent, whatever was asked.

    The page never applied the one in the request, and the answer echoed the
    request's own back as the one signed under - so dtk compared its UA with
    itself and sent the request under it, and TikTok refused it (measured
    2026-11-20: sealed Chrome 146, sent 145, 54 bytes and tt_orcas_res=1). That
    reached dtk as risk control and cooled an identity with nothing wrong with
    it. The service now refuses such a request, and says why.
    """

    async def test_a_known_foreign_agent_is_refused_before_a_page_opens(
        self, settings: Settings
    ) -> None:
        """Refused before the identity's jar is loaded under a UA not its own."""
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            with pytest.raises(UserAgentMismatch) as excinfo:
                await service.sign(
                    Platform.TIKTOK,
                    TIKTOK_URL,
                    "uniqueId=tiktok",
                    user_agent=FOREIGN_AGENT,
                    cookies={"msToken": "m", "ttwid": "t"},
                )
        finally:
            await service.close()
        assert backend.contexts_opened == 0
        assert excinfo.value.status_code == 409
        assert "as Chrome/149 on Windows and this identity's is Chrome/145 on Windows" in str(
            excinfo.value
        )

    async def test_a_page_found_to_run_another_agent_is_kept_and_not_retried(
        self, settings: Settings
    ) -> None:
        """The page is healthy and stays warm; a second page would answer the same."""
        backend = UnreportedAgentBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        jar = {"msToken": "m", "ttwid": "t"}
        try:
            with pytest.raises(UserAgentMismatch):
                await service.sign(
                    Platform.TIKTOK, TIKTOK_URL, "a=1", user_agent=FOREIGN_AGENT, cookies=jar
                )
            assert backend.contexts_opened == 1
            pool = service._pools[Platform.TIKTOK]
            assert pool.live == 1
            assert len(pool.idle) == 1 and not pool.idle[0].context.problem
            # The same identity under the page's own UA signs on that page.
            await service.sign(
                Platform.TIKTOK, TIKTOK_URL, "a=1", user_agent=FAKE_USER_AGENT, cookies=jar
            )
            assert backend.contexts_opened == 1
        finally:
            await service.close()

    async def test_a_douyin_identity_on_another_version_is_signed(self, settings: Settings) -> None:
        """#832 review: Douyin answered the page's signature for identities on
        other Chrome majors and Edge (measured 2026-11-20), so only another
        operating system is refused there."""
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            signed = await service.sign(
                Platform.DOUYIN,
                "https://www.douyin.com/aweme/v1/web/aweme/detail/",
                "aweme_id=7",
                user_agent=FOREIGN_AGENT,
                cookies={"s_v_web_id": "v", "ttwid": "t"},
            )
        finally:
            await service.close()
        assert signed["a_bogus"]
        assert backend.contexts_opened == 1

    async def test_a_douyin_identity_on_another_system_is_refused(self, settings: Settings) -> None:
        """A query naming macOS, signed on a Windows page, was refused 403
        "Blocked by ArgusSecurityPlugin" 11 of 11 under either header
        (measured 2026-11-20); the native signer answered for the same
        identity 3 of 3."""
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        mac = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        )
        try:
            with pytest.raises(UserAgentMismatch) as excinfo:
                await service.sign(
                    Platform.DOUYIN,
                    "https://www.douyin.com/aweme/v1/web/aweme/detail/",
                    "aweme_id=7",
                    user_agent=mac,
                    cookies={"s_v_web_id": "v", "ttwid": "t"},
                )
        finally:
            await service.close()
        assert backend.contexts_opened == 0
        assert "as Chrome/149 on Windows and this identity's is Chrome/149 on macOS" in str(
            excinfo.value
        )

    async def test_two_agents_that_read_alike_are_told_apart(self, settings: Settings) -> None:
        """An Edge or a rebuilt Chrome on the same major reads like the page's
        own; the refusal then names digests instead of "Chrome/149 and
        Chrome/149"."""
        backend = CountingBackend()
        service = BrowserRpcService(settings, backend)
        await service.start()
        try:
            with pytest.raises(UserAgentMismatch) as excinfo:
                await service.sign(
                    Platform.TIKTOK,
                    TIKTOK_URL,
                    "a=1",
                    user_agent=FAKE_USER_AGENT.replace("149.0.0.0", "149.0.7000.12"),
                )
        finally:
            await service.close()
        message = str(excinfo.value)
        assert "is a different Chrome/149 on Windows string (sha256 " in message
        assert "this browser's " in message

    async def test_the_pages_agent_and_no_agent_at_all_are_signed(
        self, service: BrowserRpcService
    ) -> None:
        assert await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1", user_agent=FAKE_USER_AGENT)
        assert await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1", user_agent=None)
        assert await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1", user_agent="")

    async def test_the_agent_reported_is_the_pages_own(self, service: BrowserRpcService) -> None:
        await service.sign(Platform.TIKTOK, TIKTOK_URL, "a=1")
        assert service.signing_user_agent() == FAKE_USER_AGENT
        assert service.health()["user_agent"] == FAKE_USER_AGENT

    @pytest.mark.parametrize(
        ("agent", "described"),
        [
            (FAKE_USER_AGENT, "Chrome/149 on Windows"),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Chrome/145 on macOS",
            ),
            (
                "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36",
                "Chrome/146 on Android",
            ),
            (
                "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0",
                "Firefox/151 on Linux",
            ),
            ("Mozilla/5.0", "an unrecognised browser"),
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
                "Edge/146 on Windows",
            ),
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 OPR/131.0.0.0",
                "Opera/131 on Windows",
            ),
            (
                "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Mobile Safari/537.36 EdgA/146.0.0.0",
                "Edge/146 on Android",
            ),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
                "Safari/17 on macOS",
            ),
        ],
    )
    def test_a_user_agent_is_described_by_what_a_person_compares(
        self, agent: str, described: str
    ) -> None:
        assert describe_agent(agent) == described
