"""The backend registry and the CloakBrowser seam.

The adapter itself cannot be exercised without a browser. What can be tested -
and is what actually breaks - is everything around the import: the registry
refusing an unknown name, the missing-library message being actionable, the
User-Agent parsing that decides whether an identity is usable at all, and the
snippet table covering every platform.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from browser_rpc.backends import build_backend
from browser_rpc.backends.base import MintPlan
from browser_rpc.backends.cloak import (
    CAPTURE_INIT_SCRIPT,
    SIGN_SCRIPT,
    CloakBackend,
    browser_family_of,
    browser_major_of,
    proxy_settings,
)
from browser_rpc.backends.fake import FakeBackend
from browser_rpc.errors import BackendFailure, BackendUnavailable, ConfigError
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

    async def close(self) -> None:
        self.closed += 1


class StubPage:
    def __init__(self) -> None:
        # Recorded so a test can assert the capture shim was installed at all;
        # without it the signer sits above the SDK's patch and only ever sees
        # the unsigned URL.
        self.init_scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def goto(self, url: str, **_: object) -> None:
        self.url = url

    async def evaluate(self, script: str, *_: object) -> dict[str, str]:
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
