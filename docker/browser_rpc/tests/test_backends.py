"""The backend registry and the CloakBrowser seam.

The adapter itself cannot be exercised without a browser. What can be tested -
and is what actually breaks - is everything around the import: the registry
refusing an unknown name, the missing-library message being actionable, the
User-Agent parsing that decides whether an identity is usable at all, and the
snippet table covering every platform.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from browser_rpc.backends import build_backend, cloak
from browser_rpc.backends.base import MintPlan, SignPlan
from browser_rpc.backends.cloak import (
    CAPTURE_INIT_SCRIPT,
    PAGE_LEFT_PLATFORM,
    QUIET_MEDIA_SCRIPT,
    SIGN_SCRIPT,
    CloakBackend,
    CloakSigningContext,
    browser_family_of,
    browser_major_of,
    off_platform,
    proxy_settings,
)
from browser_rpc.backends.fake import FakeBackend
from browser_rpc.errors import BackendFailure, BackendUnavailable, ConfigError, Suspect
from browser_rpc.geo import FALLBACK_PROFILE
from browser_rpc.settings import Settings
from browser_rpc.validation import Platform, ProxyEndpoint

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0"
SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Safari/605.1.15"
)


class TestRegistry:
    def test_builds_the_named_backend(self) -> None:
        assert isinstance(build_backend(Settings(backend="fake")), FakeBackend)
        assert isinstance(build_backend(Settings(backend="cloak")), CloakBackend)

    def test_refuses_an_unknown_name(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            build_backend(Settings(backend="puppeteer"))
        # No fallback: a pool quietly filling with synthetic identities looks
        # healthy until every request comes back risk-controlled.
        assert "fake" in str(excinfo.value) and "cloak" in str(excinfo.value)


class TestCloakSeam:
    async def test_missing_library_is_actionable(self) -> None:
        # CloakBrowser is not installed in the test environment, which is
        # exactly the state of an image built without a pinned commit.
        backend = CloakBackend(Settings(backend="cloak"))
        with pytest.raises(BackendUnavailable) as excinfo:
            await backend.start()
        message = str(excinfo.value)
        assert "CLOAKBROWSER_COMMIT" in message
        assert "DTK_BROWSER_BACKEND=fake" in message

    def test_the_signing_scripts_encode_what_the_live_analysis_found(self) -> None:
        """The scripts are data, so their shape is what there is to assert.

        Verified against both live sites on 2026-09-07: neither platform exposes
        a signing function. Both patch fetch and XMLHttpRequest and sign in the
        transport layer, and the SDK bundles contain no ``a_bogus`` literal at
        all because the names are built at runtime inside a bytecode VM. The
        earlier version of this file called ``byted_acrawler.sign`` and
        ``window.generateABogus``; neither exists on either site.
        """
        # The capture shim has to take the natives before the SDK replaces them,
        # or it ends up above the patch and only ever sees the unsigned URL.
        assert "const nativeFetch = window.fetch" in CAPTURE_INIT_SCRIPT
        assert "XMLHttpRequest.prototype.open" in CAPTURE_INIT_SCRIPT
        assert "AbortError" in CAPTURE_INIT_SCRIPT, (
            "the captured request must be stopped; a signature should not cost an upstream call"
        )

        # The signer returns whatever the SDK added rather than a fixed list.
        # Douyin currently adds a_bogus / verifyFp / fp / uifid / timestamp /
        # x-secsdk-web-signature and TikTok X-Gnarly / X-Dynosaur / msToken;
        # both sets have changed before.
        assert "searchParams" in SIGN_SCRIPT
        assert "before.has(k)" in SIGN_SCRIPT
        for hardcoded in ("a_bogus", "X-Bogus", "X-Gnarly", "_signature"):
            assert hardcoded not in SIGN_SCRIPT, (
                f"{hardcoded} is hardcoded; the script must return whatever the "
                "SDK appends, or the next added parameter is silently dropped"
            )

    @pytest.mark.parametrize(
        ("user_agent", "family"),
        [
            (CHROME_UA, "chrome"),
            (FIREFOX_UA, "firefox"),
            (SAFARI_UA, "safari"),
            (None, "chrome"),
        ],
    )
    def test_browser_family(self, user_agent: str | None, family: str) -> None:
        assert browser_family_of(user_agent) == family

    @pytest.mark.parametrize(
        ("user_agent", "major"),
        [(CHROME_UA, 149), (FIREFOX_UA, 151), (SAFARI_UA, 18), ("nonsense", None)],
    )
    def test_browser_major(self, user_agent: str, major: int | None) -> None:
        # None is a real answer: the pool refuses an identity whose emulation
        # profile cannot be chosen rather than guessing one.
        assert browser_major_of(user_agent) == major

    def test_proxy_settings_shape(self) -> None:
        assert proxy_settings(None) is None
        assert proxy_settings(ProxyEndpoint("http://gate:8080")) == {"server": "http://gate:8080"}
        assert proxy_settings(ProxyEndpoint("http://gate:8080", "user", "secret")) == {
            "server": "http://gate:8080",
            "username": "user",
            "password": "secret",
        }


class StubContext:
    """Stands in for a driver context: records closes, nothing else."""

    def __init__(self) -> None:
        self.pages: list[object] = []
        self.closed = 0

    async def new_page(self) -> StubPage:
        page = StubPage()
        self.pages.append(page)
        return page

    async def add_cookies(self, cookies: list[dict[str, str]]) -> None:
        self.cookies_added = cookies

    async def close(self) -> None:
        self.closed += 1


class StubPage:
    def __init__(self) -> None:
        # Recorded so a test can assert the capture shim was installed at all;
        # without it the signer sits above the SDK's patch and only ever sees
        # the unsigned URL.
        self.init_scripts: list[str] = []
        self.evaluated: list[str] = []
        self.in_front = False

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def goto(self, url: str, **_: object) -> None:
        self.url = url

    async def bring_to_front(self) -> None:
        self.in_front = True

    async def evaluate(self, script: str, *_: object) -> dict[str, object]:
        self.evaluated.append(script)
        # Two different questions arrive on this seam, and answering both with a
        # fingerprint made the readiness probe wait out its entire budget.
        if "__dtkSign" in script:
            return {"params": {"a_bogus": "stub"}}
        return {"userAgent": CHROME_UA, "platform": "Win32"}


class TestWarmProfileDirectories:
    """Chromium locks a persistent profile, so two warm pages cannot share one."""

    @staticmethod
    def _backend(tmp_path: Path) -> tuple[CloakBackend, list[str]]:
        backend = CloakBackend(replace(Settings(backend="cloak"), profile_root=str(tmp_path)))
        opened: list[str] = []

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            # A real persistent context would raise here on a locked directory;
            # recording it is what lets the test see the collision instead.
            assert profile_dir not in opened, f"profile directory reused: {profile_dir}"
            opened.append(profile_dir)
            Path(profile_dir).mkdir(parents=True)
            return StubContext()

        backend._launch_context = launch  # type: ignore[method-assign]
        return backend, opened

    async def test_each_context_gets_its_own_directory(self, tmp_path: Path) -> None:
        backend, opened = self._backend(tmp_path)
        first = await backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE)
        second = await backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE)
        assert len(set(opened)) == 2
        await first.close()
        await second.close()

    async def test_directory_is_removed_on_close(self, tmp_path: Path) -> None:
        backend, opened = self._backend(tmp_path)
        context = await backend.open_signing_context(Platform.TIKTOK, FALLBACK_PROFILE)
        assert Path(opened[0]).exists()
        await context.close()
        # Rebuilt every 30 minutes; a directory left behind each time fills the
        # profile tmpfs.
        assert not Path(opened[0]).exists()


class TestWarmPagesGoToTheBackground:
    """A warm page is a platform's home feed, which autoplays to nobody.

    The demo's Douyin page held a renderer at 84% and a GPU process at 34% of a
    2-core server for the 7.5 hours after it was opened, with no signature asked
    of it in that time. So a warm page pauses its media and goes behind a blank
    tab as soon as it can sign - and neither step may cost a page that works.
    """

    @staticmethod
    def _backend(tmp_path: Path, context: StubContext) -> CloakBackend:
        backend = CloakBackend(replace(Settings(backend="cloak"), profile_root=str(tmp_path)))

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True)
            return context

        backend._launch_context = launch  # type: ignore[method-assign]
        return backend

    @staticmethod
    def _plan() -> SignPlan:
        return SignPlan(
            platform=Platform.DOUYIN,
            url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
            query="aweme_id=7",
            params={},
        )

    async def test_the_page_pauses_its_media_and_goes_behind_a_blank_tab(
        self, tmp_path: Path
    ) -> None:
        browser = StubContext()
        signing = await self._backend(tmp_path, browser).open_signing_context(
            Platform.DOUYIN, FALLBACK_PROFILE
        )
        warm, cover = browser.pages
        assert isinstance(warm, StubPage) and isinstance(cover, StubPage)
        assert QUIET_MEDIA_SCRIPT in warm.evaluated
        assert cover.in_front and not warm.in_front
        # The blank tab is only a cover: signatures still come from the page
        # that has the SDK loaded.
        assert await signing.sign(self._plan()) == {"a_bogus": "stub"}
        assert cover.evaluated == []
        await signing.close()

    async def test_a_page_that_cannot_be_quieted_still_signs(self, tmp_path: Path) -> None:
        class LoudPage(StubPage):
            async def evaluate(self, script: str, *rest: object) -> dict[str, object]:
                if script == QUIET_MEDIA_SCRIPT:
                    raise RuntimeError("Execution context was destroyed")
                return await super().evaluate(script, *rest)

        class NoTabs(StubContext):
            async def new_page(self) -> StubPage:
                if self.pages:
                    raise RuntimeError("Target page, context or browser has been closed")
                page = LoudPage()
                self.pages.append(page)
                return page

        browser = NoTabs()
        signing = await self._backend(tmp_path, browser).open_signing_context(
            Platform.DOUYIN, FALLBACK_PROFILE
        )
        assert len(browser.pages) == 1
        assert await signing.sign(self._plan()) == {"a_bogus": "stub"}
        await signing.close()


class TestFakeBackend:
    async def test_cookies_differ_per_session(self, tmp_path: object) -> None:
        backend = FakeBackend()
        await backend.start()
        first = await backend.mint(
            MintPlan(
                platform=Platform.DOUYIN,
                landing_url="https://www.douyin.com/",
                profile_dir=f"{tmp_path}/a",
                geo=FALLBACK_PROFILE,
            )
        )
        second = await backend.mint(
            MintPlan(
                platform=Platform.DOUYIN,
                landing_url="https://www.douyin.com/",
                profile_dir=f"{tmp_path}/b",
                geo=FALLBACK_PROFILE,
            )
        )
        assert first.cookies != second.cookies
        assert set(first.cookies) == set(second.cookies)
        await backend.close()

    async def test_refuses_a_reused_profile_directory(self, tmp_path: object) -> None:
        backend = FakeBackend()
        await backend.start()
        plan = MintPlan(
            platform=Platform.TIKTOK,
            landing_url="https://www.tiktok.com/",
            profile_dir=f"{tmp_path}/once",
            geo=FALLBACK_PROFILE,
        )
        await backend.mint(plan)
        with pytest.raises(BackendFailure):
            await backend.mint(plan)
        await backend.close()


class TestSigningReadiness:
    """A page is navigable long before it can sign.

    TikTok was asked for a signature 1.1 seconds after its context opened, twice
    in a row, and answered "the SDK added nothing" both times - which reaches the
    caller as a 502 and reads like the platform changed its algorithm. It had
    simply not finished loading its security bundle.

    The first fix attempted here was "wait until something wraps window.fetch",
    and it is recorded as rejected: TikTok satisfies that within a second, well
    before its signing code exists. The only sound readiness question is the one
    the caller will ask - can this page sign? - so that is what is asked.
    """

    @staticmethod
    def _backend(tmp_path: Path, page: object) -> CloakBackend:
        backend = CloakBackend(
            replace(
                Settings(backend="cloak"),
                profile_root=str(tmp_path),
                sdk_ready_timeout_seconds=2.0,
            )
        )

        async def launch(profile_dir: str, *_: object, **__: object) -> object:
            Path(profile_dir).mkdir(parents=True, exist_ok=True)
            context = StubContext()
            context.pages.append(page)
            return context

        backend._launch_context = launch  # type: ignore[method-assign]
        return backend

    async def test_a_context_is_not_handed_over_until_it_can_sign(self, tmp_path: Path) -> None:
        class SlowSdkPage(StubPage):
            """Answers the probe with nothing until the bundle has "loaded"."""

            def __init__(self, ready_after: int) -> None:
                super().__init__()
                self.ready_after = ready_after
                self.probes = 0

            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                if "__dtkSign" not in script:
                    return {"userAgent": CHROME_UA, "platform": "Win32"}
                self.probes += 1
                if self.probes <= self.ready_after:
                    return {"error": "the SDK did not dispatch a request"}
                return {"params": {"a_bogus": "stub"}}

        page = SlowSdkPage(ready_after=3)
        context = await self._backend(tmp_path, page).open_signing_context(
            Platform.TIKTOK, FALLBACK_PROFILE
        )
        # It kept asking rather than handing over a page that would have failed.
        assert page.probes == 4
        await context.close()

    async def test_the_probe_backs_off_instead_of_hammering_the_page(self, tmp_path: Path) -> None:
        """Each probe runs a fetch through the SDK; a flat interval killed pages.

        At a flat 0.25s a page that never became ready was probed about a
        hundred times inside its budget, and Chromium died with "Target
        crashed" - the readiness check destroying the page it was waiting for.
        The bound below is what makes that impossible to reintroduce.
        """

        class DeadSdkPage(StubPage):
            def __init__(self) -> None:
                super().__init__()
                self.probes = 0

            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                if "__dtkSign" not in script:
                    return {"userAgent": CHROME_UA, "platform": "Win32"}
                self.probes += 1
                return {"error": "the SDK did not dispatch a request"}

        page = DeadSdkPage()
        with pytest.raises(BackendFailure):
            await self._backend(tmp_path, page).open_signing_context(
                Platform.DOUYIN, FALLBACK_PROFILE
            )
        # 2s of budget: flat 0.25s polling would be 8, backing off is far fewer.
        assert page.probes <= 6, f"{page.probes} probes in a 2s budget is hammering"
        assert page.probes >= 2, "it gave up without really waiting"

    async def test_a_page_that_never_signs_is_not_handed_out(self, tmp_path: Path) -> None:
        """The budget ends in a failed open that carries the SDK's own answer.

        This used to return the page anyway, reasoning that the caller's
        signature would then fail with the SDK's message, which says more than
        "opening a context timed out". The message half of that was right and
        is kept - the probe's last answer is in the error. The other half put a
        page that could not sign into the pool, where every attempt paid a full
        open to rediscover it (#830).
        """

        class DeadSdkPage(StubPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                if "__dtkSign" not in script:
                    return {"userAgent": CHROME_UA, "platform": "Win32"}
                return {"error": "the SDK did not dispatch a request"}

        browser = StubContext()
        browser.pages.append(DeadSdkPage())
        backend = self._backend(tmp_path, DeadSdkPage())

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            # This test's own context, so it can see the page being closed.
            Path(profile_dir).mkdir(parents=True, exist_ok=True)
            return browser

        backend._launch_context = launch  # type: ignore[method-assign]
        with pytest.raises(BackendFailure) as excinfo:
            await backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE)
        message = str(excinfo.value)
        assert "could not sign within 2s" in message
        assert "the SDK did not dispatch a request" in message
        # Both of the things it can mean, and the remedy for the one that is
        # the platform moving - not the exit, which a review found the service
        # blaming for this (see TestARepeatedFailureNamesWhatThePagesShare).
        assert "did not load, or the platform changed how it signs" in message
        assert "if this exit loads the site, open an issue" in message
        assert message.index("open an issue") < message.index("Its last answer")
        # Nothing an operator cannot follow: docs/ is not shipped.
        assert "docs/design" not in message
        assert excinfo.value.suspect is Suspect.BUNDLE
        # Not kept warm: the context it opened is closed again.
        assert browser.closed == 1


class SealingPage(StubPage):
    """A TikTok page whose SDK seals the msToken it holds at each probe, in turn."""

    def __init__(self, tokens: list[str | None]) -> None:
        super().__init__()
        self.tokens = tokens
        self.probes = 0

    async def evaluate(self, script: str, *_: object) -> dict[str, object]:
        if "__dtkSign" not in script:
            return {"userAgent": CHROME_UA, "platform": "Win32"}
        token = self.tokens[min(self.probes, len(self.tokens) - 1)]
        self.probes += 1
        params: dict[str, str] = {"X-Dynosaur": "DYN", "X-Bogus": "1", "X-Gnarly": "GNA"}
        if token is not None:
            params["msToken"] = token
        return {"params": params}


class TestATikTokPageIsReadyWhenItsTokenIs:
    """Issue #832: a page that signs is not ready while the msToken it seals is not.

    Measured 2026-11-20: a page bound to a jar holding a 152-character token
    sealed an empty msToken for its first 0.1-1.4s, longer through the service;
    a page whose jar had none sealed the 128-character bootstrap token for
    seconds. Signatures from the first 0.3s were refused even with the jar's
    live token in the Cookie header, 4 of 4, and readiness answered yes on the
    first probe that signed - so the first caller after every rebind got one.
    """

    _backend = staticmethod(TestSigningReadiness._backend)

    #: An identity's jar: the pages a request is signed in are loaded with one.
    JAR: Final = MappingProxyType({"ttwid": "t", "msToken": "j" * 152})

    async def test_the_page_is_handed_over_once_its_token_is_usable(self, tmp_path: Path) -> None:
        page = SealingPage(["", "", "t" * 128, "t" * 152])
        context = await self._backend(tmp_path, page).open_signing_context(
            Platform.TIKTOK, FALLBACK_PROFILE, cookies=self.JAR
        )
        assert page.probes == 4
        await context.close()

    @pytest.mark.parametrize(
        "jar",
        [None, {"ttwid": "t"}, {"ttwid": "t", "msToken": "b" * 128}],
        ids=["anonymous", "no-token", "bootstrap-token"],
    )
    async def test_a_page_for_a_jar_with_no_usable_token_is_judged_as_before(
        self, tmp_path: Path, jar: dict[str, str] | None
    ) -> None:
        """Measured: an anonymous page and one for a jar with no token each
        sealed only the bootstrap token for the whole 25s, while every page for
        a jar holding a usable one sealed its own within 0.8-3.5s."""
        page = SealingPage(["t" * 128])
        context = await self._backend(tmp_path, page).open_signing_context(
            Platform.TIKTOK, FALLBACK_PROFILE, cookies=jar
        )
        assert page.probes == 1
        await context.close()

    async def test_a_page_that_seals_no_token_at_all_is_judged_as_before(
        self, tmp_path: Path
    ) -> None:
        """Absence is not a short token: an SDK that stopped appending one still signs."""
        page = SealingPage([None])
        context = await self._backend(tmp_path, page).open_signing_context(
            Platform.TIKTOK, FALLBACK_PROFILE, cookies=self.JAR
        )
        assert page.probes == 1
        await context.close()

    async def test_douyin_has_no_token_to_wait_for(self, tmp_path: Path) -> None:
        page = SealingPage([""])
        context = await self._backend(tmp_path, page).open_signing_context(
            Platform.DOUYIN, FALLBACK_PROFILE, cookies={"ttwid": "t", "s_v_web_id": "v"}
        )
        assert page.probes == 1
        await context.close()

    @pytest.mark.parametrize(
        ("token", "says", "suspect"),
        [
            ("", "nothing of the platform's app answered through this exit", Suspect.EXIT),
            ("t" * 128, "only the bootstrap token arrived", Suspect.BUNDLE),
        ],
    )
    async def test_a_token_that_never_matures_is_not_handed_out(
        self, tmp_path: Path, token: str, says: str, suspect: Suspect
    ) -> None:
        browser = StubContext()
        backend = self._backend(tmp_path, SealingPage([token]))

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True, exist_ok=True)
            browser.pages.append(SealingPage([token]))
            return browser

        backend._launch_context = launch  # type: ignore[method-assign]
        with pytest.raises(BackendFailure) as excinfo:
            await backend.open_signing_context(Platform.TIKTOK, FALLBACK_PROFILE, cookies=self.JAR)
        message = str(excinfo.value)
        assert "could not seal a usable msToken within 2s (" in message
        assert f"it seals one of {len(token)} characters" in message
        assert "at least 144" in message
        assert says in message
        assert excinfo.value.suspect is suspect
        assert browser.closed == 1


class LeavingPage(StubPage):
    """A platform page that a failed navigation carries to chrome-error://.

    Modelled on issue #830 as reproduced in a real CloakBrowser: the document
    loads, a navigation after load fails through the exit, and the page lands
    on chrome-error://chromewebdata/. The capture shim is reinstalled there -
    init scripts run on every document - so the old script got past its guard
    and died on `new URL(..., location.origin)`, where the origin is "null".
    """

    INVALID_BASE = "Page.evaluate: TypeError: Failed to construct 'URL': Invalid base URL"

    def __init__(self, leave_after: int = 0) -> None:
        super().__init__()
        self.leave_after = leave_after
        self.probes = 0

    async def evaluate(self, script: str, *_: object) -> dict[str, object]:
        if "__dtkSign" not in script:
            return {"userAgent": CHROME_UA, "platform": "Win32"}
        self.probes += 1
        if self.probes > self.leave_after:
            self.url = "chrome-error://chromewebdata/"
            raise RuntimeError(f"{self.INVALID_BASE}\n    at eval (<anonymous>:27:18)")
        return {"params": {"X-Gnarly": "stub"}}


class TestAPageThatLeftThePlatform:
    """Issue #830: "Invalid base URL" from a page that was not on TikTok any more.

    The page had left tiktok.com and nothing noticed: the readiness probe
    logged three exceptions and handed it out, the service's two attempts each
    signed on such a page, and the caller read a JavaScript TypeError that
    named neither the page's state nor the exit that put it there.
    """

    @staticmethod
    def _backend(tmp_path: Path, page: StubPage) -> tuple[CloakBackend, StubContext]:
        backend = CloakBackend(
            replace(
                Settings(backend="cloak"),
                profile_root=str(tmp_path),
                sdk_ready_timeout_seconds=2.0,
            )
        )
        browser = StubContext()

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True, exist_ok=True)
            browser.pages.append(page)
            return browser

        backend._launch_context = launch  # type: ignore[method-assign]
        return backend, browser

    @staticmethod
    def _plan() -> SignPlan:
        return SignPlan(
            platform=Platform.TIKTOK,
            url="https://www.tiktok.com/api/item/detail/",
            query="itemId=7&aid=1988",
            params={},
        )

    def test_the_script_never_resolves_against_the_pages_own_origin(self) -> None:
        # The target is absolute and is all the parameters are read from; the
        # page's origin is "null" on exactly the pages this went wrong on.
        assert "location.origin" not in SIGN_SCRIPT
        assert "new URL(state.url, input.url)" in SIGN_SCRIPT
        assert PAGE_LEFT_PLATFORM in SIGN_SCRIPT

    @pytest.mark.parametrize(
        ("platform", "url", "where"),
        [
            (Platform.TIKTOK, "https://www.tiktok.com/", None),
            (Platform.TIKTOK, "https://www.tiktok.com/explore?lang=en", None),
            (Platform.DOUYIN, "https://www.douyin.com/?recommend=1", None),
            (Platform.TIKTOK, "chrome-error://chromewebdata/", "chrome-error://chromewebdata"),
            (Platform.TIKTOK, "about:blank", "about:blank"),
            (Platform.TIKTOK, "data:text/html,<p>a whole document</p>", "data:"),
            (Platform.TIKTOK, "https://www.douyin.com/", "https://www.douyin.com"),
            (Platform.TIKTOK, "https://nottiktok.com/", "https://nottiktok.com"),
            # Only scheme and host are ever reported: a path or query on a
            # platform page can carry the identity's tokens.
            (Platform.TIKTOK, "https://verify.example/?msToken=SECRET", "https://verify.example"),
            (Platform.TIKTOK, None, None),
        ],
    )
    def test_where_a_page_is(self, platform: Platform, url: str | None, where: str | None) -> None:
        assert off_platform(platform, url) == where

    async def test_a_warm_page_on_an_error_page_says_where_it_is(self) -> None:
        page = LeavingPage()
        page.url = "chrome-error://chromewebdata/"
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), page)

        assert context.problem == "it left tiktok.com for chrome-error://chromewebdata"
        with pytest.raises(BackendFailure) as excinfo:
            await context.sign(self._plan())
        message = str(excinfo.value)
        assert "no longer on tiktok.com" in message
        assert "chrome-error://chromewebdata" in message
        assert "Invalid base URL" not in message
        # Named from the driver's own record; nothing ran in the dead page.
        assert page.probes == 0

    async def test_the_script_names_the_page_when_the_driver_has_not_caught_up(self) -> None:
        class StillLooksFine(StubPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                return {"error": PAGE_LEFT_PLATFORM, "at": "about:"}

        page = StillLooksFine()
        page.url = "https://www.tiktok.com/"
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), page)

        with pytest.raises(BackendFailure, match=r"no longer on tiktok\.com \(it is on about:\)"):
            await context.sign(self._plan())

    async def test_a_crashed_renderer_is_a_problem_before_anyone_signs(self) -> None:
        class EventedPage(StubPage):
            def __init__(self) -> None:
                super().__init__()
                self.listeners: dict[str, object] = {}

            def on(self, event: str, handler: object) -> None:
                self.listeners[event] = handler

        page = EventedPage()
        page.url = "https://www.tiktok.com/"
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), page)
        assert context.problem is None

        crashed = page.listeners["crash"]
        assert callable(crashed)
        crashed(page)
        # The phrase the troubleshooting guide tells operators to search for.
        assert context.problem == "its renderer crashed (Target crashed)"
        with pytest.raises(BackendFailure, match=r"renderer crashed \(Target crashed\)") as exc:
            await context.sign(self._plan())
        assert exc.value.suspect is Suspect.BROWSER

    async def test_a_page_that_leaves_while_warming_up_is_not_handed_out(
        self, tmp_path: Path
    ) -> None:
        """The exact shape reproduced in CloakBrowser, and the open now fails.

        Before: three probe exceptions, `ready_page_gone`, and the page went
        into the pool after 1.3s. Now the open fails at the first probe that
        finds the page gone, with where it went - which the service treats as
        a failed open and answers with a fresh page.
        """
        page = LeavingPage(leave_after=0)
        backend, browser = self._backend(tmp_path, page)

        with pytest.raises(BackendFailure) as excinfo:
            await backend.open_signing_context(Platform.TIKTOK, FALLBACK_PROFILE)
        message = str(excinfo.value)
        assert message.startswith("could not warm a signing page for tiktok")
        assert "no longer on tiktok.com (it is on chrome-error://chromewebdata)" in message
        assert "Invalid base URL" not in message
        assert page.probes == 1, "it kept probing a page that had already gone"
        assert browser.closed == 1

    async def test_a_page_that_stops_answering_on_the_platform_says_so(
        self, tmp_path: Path
    ) -> None:
        class CrashedPage(StubPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                if "__dtkSign" not in script:
                    return {"userAgent": CHROME_UA, "platform": "Win32"}
                raise RuntimeError("Page.evaluate: Target crashed\n    at <anonymous>")

        backend, browser = self._backend(tmp_path, CrashedPage())
        with pytest.raises(BackendFailure) as excinfo:
            await backend.open_signing_context(Platform.TIKTOK, FALLBACK_PROFILE)
        message = str(excinfo.value)
        assert "stopped answering" in message
        assert "Page.evaluate: Target crashed" in message
        # The first line is the finding; the JavaScript stack is noise.
        assert "<anonymous>" not in message
        assert browser.closed == 1


class TestAMintWhoseLandingPageLeft:
    """The same state at mint time read "msToken is 0 characters after 15s".

    That reads as impatience and was an exit that could not keep a TikTok page
    on tiktok.com: the first msToken arrives on an app API response, so no
    token at all means none of the app answered.
    """

    class JarContext(StubContext):
        def __init__(self, cookies: list[dict[str, str]]) -> None:
            super().__init__()
            self.jar = cookies

        async def cookies(self) -> list[dict[str, str]]:
            return self.jar

    @staticmethod
    def _plan(tmp_path: Path) -> MintPlan:
        return MintPlan(
            platform=Platform.TIKTOK,
            landing_url="https://www.tiktok.com/",
            profile_dir=str(tmp_path / "mint"),
            geo=FALLBACK_PROFILE,
        )

    async def test_a_page_that_left_ends_the_wait_and_says_where_it_went(
        self, tmp_path: Path
    ) -> None:
        backend = CloakBackend(Settings(backend="cloak"))
        page = StubPage()
        page.url = "chrome-error://chromewebdata/"
        context = self.JarContext([{"name": "ttwid", "value": "x"}])

        started = asyncio.get_running_loop().time()
        with pytest.raises(BackendFailure) as excinfo:
            await backend._await_session_cookie(context, self._plan(tmp_path), page)
        assert asyncio.get_running_loop().time() - started < 1.0, "it waited out the budget"
        message = str(excinfo.value)
        assert "msToken is 0 characters" in message
        assert "left tiktok.com for chrome-error://chromewebdata" in message

    @pytest.mark.parametrize(
        ("token", "says"),
        [
            ("", "nothing of the platform's app answered"),
            ("t" * 128, "only the bootstrap token arrived"),
        ],
    )
    async def test_how_short_the_token_fell_says_what_to_look_at(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str, says: str
    ) -> None:
        monkeypatch.setattr(cloak, "SESSION_COOKIE_WAIT_SECONDS", 0.0)
        backend = CloakBackend(Settings(backend="cloak"))
        page = StubPage()
        page.url = "https://www.tiktok.com/"
        jar = [{"name": "msToken", "value": token}] if token else []

        with pytest.raises(BackendFailure) as excinfo:
            await backend._await_session_cookie(self.JarContext(jar), self._plan(tmp_path), page)
        message = str(excinfo.value)
        assert f"msToken is {len(token)} characters after 0s" in message
        assert says in message


class TestTheSdkUrlIsTheWholeAnswer:
    """The backend must return the SDK's parameters and not one more.

    Douyin's a_bogus covers the exact query string, so appending anything the
    SDK did not put there forges the signature and the platform answers
    "403 Blocked by ArgusSecurityPlugin Sign Invalid".

    This existed: the backend used to append the page's msToken cookie whenever
    the SDK had not put one in the query. A freshly opened page has no msToken
    cookie, so it did nothing and signing worked; Douyin's scripts set one after
    the page has been alive a while, and from that moment every signature from
    that page was refused. Measured on 2026-09-08, the same warm context went
    from 8/8 returning data to 6/6 refused, msToken the only difference.
    """

    class SdkPage(StubPage):
        def __init__(self, params: dict[str, str]) -> None:
            super().__init__()
            self.params = params

        async def evaluate(self, script: str, *_: object) -> dict[str, object]:
            if "__dtkSign" in script:
                return {"params": dict(self.params)}
            return {"userAgent": CHROME_UA, "platform": "Win32"}

    async def test_nothing_is_added_to_what_the_sdk_signed(self) -> None:
        sdk = {"a_bogus": "AAA", "verifyFp": "verify_x", "uifid": "ff00"}
        page = self.SdkPage(sdk)
        context = CloakSigningContext(Platform.DOUYIN, StubContext(), page)
        signed = await context.sign(
            SignPlan(
                platform=Platform.DOUYIN,
                url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
                query="aweme_id=7",
                params={},
            )
        )
        assert signed == sdk, "the backend invented a parameter the SDK did not sign"

    async def test_a_page_holding_an_ms_token_cookie_changes_nothing(self) -> None:
        """The exact shape of the bug: a cookie must not reach the query."""

        class PageWithCookie(TestTheSdkUrlIsTheWholeAnswer.SdkPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                if "document.cookie" in script:
                    return {"msToken": "a-real-looking-token"}
                return await super().evaluate(script, *_)

        sdk = {"a_bogus": "AAA", "verifyFp": "verify_x"}
        context = CloakSigningContext(Platform.DOUYIN, StubContext(), PageWithCookie(sdk))
        signed = await context.sign(
            SignPlan(
                platform=Platform.DOUYIN,
                url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
                query="aweme_id=7",
                params={},
            )
        )
        assert "msToken" not in signed
        assert signed == sdk

    async def test_an_empty_value_the_sdk_sealed_is_kept(self) -> None:
        """Nothing taken away either: X-Gnarly hashes `&msToken=` when it is empty.

        Issue #832. A TikTok page seals an empty msToken while its SDK has no
        token yet, and the empty pair used to be dropped here as "not
        reported", so the request carried a query its seal did not describe.
        """
        sdk = {"X-Dynosaur": "DYN", "msToken": "", "X-Bogus": "1", "X-Gnarly": "GNA"}
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), self.SdkPage(sdk))
        signed = await context.sign(
            SignPlan(
                platform=Platform.TIKTOK,
                url="https://www.tiktok.com/api/user/detail/",
                query="uniqueId=tiktok",
                params={},
            )
        )
        assert signed == sdk
        assert list(signed) == list(sdk), "the order is the order the SDK appended"

    async def test_a_page_that_added_only_empty_values_added_nothing(self) -> None:
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), self.SdkPage({"msToken": ""}))
        with pytest.raises(BackendFailure, match="added no parameters"):
            await context.sign(
                SignPlan(
                    platform=Platform.TIKTOK,
                    url="https://www.tiktok.com/api/user/detail/",
                    query="uniqueId=tiktok",
                    params={},
                )
            )


class NavigatingPage(StubPage):
    """A page whose document is replaced while a signature is running in it.

    The driver reports the new URL a moment after the old document is torn
    down, so the precheck in `sign` reads the platform's address and lets the
    script run; the evaluate then dies with "Execution context was destroyed".
    Seen in dtk-sig2 after a reload through a failing exit, on both platforms.
    """

    DESTROYED = (
        "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
    )

    def __init__(self, lands_on: str, after: float = 0.1) -> None:
        super().__init__()
        self.url = "https://www.tiktok.com/"
        self.lands_on = lands_on
        self.after = after

    async def evaluate(self, script: str, *_: object) -> dict[str, object]:
        # The driver hears about the new document a little after the old
        # context is gone, which is the window this models.
        asyncio.get_running_loop().call_later(self.after, setattr, self, "url", self.lands_on)
        raise RuntimeError(f"{self.DESTROYED}\n    at <anonymous>")


class TestASignatureThatMeetsANavigation:
    """The precheck reads a URL that lags; a failed evaluate asks the page again."""

    PLAN = SignPlan(
        platform=Platform.TIKTOK,
        url="https://www.tiktok.com/api/item/detail/",
        query="itemId=7&aid=1988",
        params={},
    )

    async def test_a_page_that_lands_off_the_platform_is_named(self) -> None:
        page = NavigatingPage("chrome-error://chromewebdata/")
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), page)
        assert context.problem is None, "the driver has not caught up yet"

        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(BackendFailure) as excinfo:
            await context.sign(self.PLAN)
        message = str(excinfo.value)
        assert "no longer on tiktok.com (it is on chrome-error://chromewebdata)" in message
        assert "Execution context was destroyed" not in message
        assert excinfo.value.suspect is Suspect.EXIT
        # It stopped watching as soon as the driver said where the page went.
        assert loop.time() - started < cloak.NAVIGATION_SETTLE_SECONDS

    async def test_a_page_that_stays_on_the_platform_says_it_was_navigating(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cloak, "NAVIGATION_SETTLE_SECONDS", 0.2)
        page = NavigatingPage("https://www.tiktok.com/foryou")
        context = CloakSigningContext(Platform.TIKTOK, StubContext(), page)

        with pytest.raises(BackendFailure) as excinfo:
            await context.sign(self.PLAN)
        message = str(excinfo.value)
        assert message.startswith("the tiktok signing page was navigating when it was asked")
        assert "still on tiktok.com" in message
        assert "signing script failed" not in message
        # Within the platform it is not evidence about the exit.
        assert excinfo.value.suspect is None

    async def test_a_page_already_gone_is_named_without_waiting(self) -> None:
        class OldDriverPage(StubPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                self.url = "about:blank"
                raise RuntimeError(NavigatingPage.DESTROYED)

        page = OldDriverPage()
        page.url = "https://www.tiktok.com/"
        with pytest.raises(
            BackendFailure, match=r"no longer on tiktok\.com \(it is on about:blank\)"
        ):
            await CloakSigningContext(Platform.TIKTOK, StubContext(), page).sign(self.PLAN)

    async def test_a_crash_mid_signature_is_named_as_one(self) -> None:
        class Crashes(StubPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                raise RuntimeError("Page.evaluate: Target crashed\n    at <anonymous>")

        page = Crashes()
        page.url = "https://www.tiktok.com/"
        with pytest.raises(BackendFailure) as excinfo:
            await CloakSigningContext(Platform.TIKTOK, StubContext(), page).sign(self.PLAN)
        assert str(excinfo.value) == ("the tiktok signing page's renderer crashed (Target crashed)")
        assert excinfo.value.suspect is Suspect.BROWSER

    async def test_any_other_script_failure_is_reported_as_before(self) -> None:
        class Throws(StubPage):
            async def evaluate(self, script: str, *_: object) -> dict[str, object]:
                raise RuntimeError("Page.evaluate: ReferenceError: x is not defined\n  at y")

        page = Throws()
        page.url = "https://www.tiktok.com/"
        with pytest.raises(BackendFailure) as excinfo:
            await CloakSigningContext(Platform.TIKTOK, StubContext(), page).sign(self.PLAN)
        assert str(excinfo.value) == (
            "signing script failed: Page.evaluate: ReferenceError: x is not defined"
        )
        assert excinfo.value.suspect is None


class TestAFailedOpenLeavesNothingBehind:
    """Every warm profile is 13-16 MiB on a 1g tmpfs; a failed open must not keep one."""

    @staticmethod
    def _backend(tmp_path: Path) -> CloakBackend:
        return CloakBackend(
            replace(
                Settings(backend="cloak"),
                profile_root=str(tmp_path),
                sdk_ready_timeout_seconds=1.0,
            )
        )

    async def test_a_launch_that_fails_removes_the_directory_it_created(
        self, tmp_path: Path
    ) -> None:
        backend = self._backend(tmp_path)

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            # What Chromium does: the directory exists before the launch fails.
            Path(profile_dir).mkdir(parents=True)
            raise BackendFailure("could not open a browser context: Target page has been closed")

        backend._launch_context = launch  # type: ignore[method-assign]
        with pytest.raises(BackendFailure):
            await backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE)
        assert list(tmp_path.iterdir()) == []

    async def test_an_open_cancelled_by_the_service_closes_its_browser(
        self, tmp_path: Path
    ) -> None:
        """The service's budget cancels the open; it used to skip the cleanup entirely."""

        class HangingPage(StubPage):
            async def goto(self, url: str, **_: object) -> None:
                await asyncio.sleep(10)

        backend = self._backend(tmp_path)
        browser = StubContext()
        browser.pages.append(HangingPage())

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True)
            return browser

        backend._launch_context = launch  # type: ignore[method-assign]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE), 0.05
            )
        assert browser.closed == 1
        assert list(tmp_path.iterdir()) == []

    async def test_a_close_that_hangs_does_not_hold_the_cancelled_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cleanup is bounded: a browser too wedged to close must not keep
        the cancelled open - and the caller's budget - waiting on it."""
        monkeypatch.setattr(cloak, "CLOSE_TIMEOUT_SECONDS", 0.1)

        class HangingPage(StubPage):
            async def goto(self, url: str, **_: object) -> None:
                await asyncio.sleep(10)

        class WedgedContext(StubContext):
            async def close(self) -> None:
                self.closed += 1
                await asyncio.sleep(10)

        backend = self._backend(tmp_path)
        browser = WedgedContext()
        browser.pages.append(HangingPage())

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True)
            return browser

        backend._launch_context = launch  # type: ignore[method-assign]
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE), 0.05
            )
        assert loop.time() - started < 1.0
        assert browser.closed == 1
        assert list(tmp_path.iterdir()) == []

    async def test_a_launch_cancelled_midway_removes_its_directory(self, tmp_path: Path) -> None:
        """Cancellation is not an Exception: a handler that caught only those
        left the profile of a launch the service gave up on."""
        backend = self._backend(tmp_path)

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True)
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

        backend._launch_context = launch  # type: ignore[method-assign]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE), 0.05
            )
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        ("error", "suspect"),
        [
            (
                "Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://www.douyin.com/",
                Suspect.EXIT,
            ),
            ("Page.goto: Target crashed", Suspect.BROWSER),
        ],
    )
    async def test_a_page_that_never_loaded_says_so(
        self, tmp_path: Path, error: str, suspect: Suspect
    ) -> None:
        class Unreachable(StubPage):
            async def goto(self, url: str, **_: object) -> None:
                raise RuntimeError(f"{error}\nCall log:\n  - navigating to {url}")

        backend = self._backend(tmp_path)
        browser = StubContext()
        browser.pages.append(Unreachable())

        async def launch(profile_dir: str, *_: object, **__: object) -> StubContext:
            Path(profile_dir).mkdir(parents=True)
            return browser

        backend._launch_context = launch  # type: ignore[method-assign]
        with pytest.raises(BackendFailure) as excinfo:
            await backend.open_signing_context(Platform.DOUYIN, FALLBACK_PROFILE)
        assert str(excinfo.value) == (
            f"could not warm a signing page for douyin: could not load "
            f"https://www.douyin.com/: {error}"
        )
        assert excinfo.value.suspect is suspect
        assert browser.closed == 1
        assert list(tmp_path.iterdir()) == []


class TestADeadBrowserIsNamedAsOne:
    """Playwright says a page or its browser died in more than one way; a load
    killed while goto waited said "Page crashed" and was put to the exit."""

    @pytest.mark.parametrize(
        "text",
        [
            "Page.goto: Target crashed",
            "Page.goto: Page crashed",
            "Page.goto: Navigation failed because page crashed!",
            "Page.goto: Target page, context or browser has been closed",
            "BrowserContext.new_page: Browser has been closed",
        ],
    )
    def test_each_phrasing_implicates_the_browser(self, text: str) -> None:
        assert cloak.suspect_of(RuntimeError(text)) is Suspect.BROWSER

    def test_a_network_error_implicates_nothing_by_itself(self) -> None:
        assert cloak.suspect_of(RuntimeError("Page.goto: net::ERR_CONNECTION_RESET")) is None
