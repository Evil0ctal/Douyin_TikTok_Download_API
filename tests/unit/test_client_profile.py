"""The query string has to agree with the User-Agent sent beside it.

Both platforms echo the screen, the language, the OS and the browser version
back in every query, and the identity states the same facts in its User-Agent.
A disagreement between the two is a free signal - no browser can produce one -
so these tests pin the derivation rather than the constants it replaced.

The values are not invented here. `operating_system` follows the OS table lifted
from Douyin's own `chunk-43693` bundle, the browser version is the substring its
detector takes out of the User-Agent, and TikTok's `5.0 (Windows)` is a live
capture.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dtk.platforms.base import (
    ClientProfile,
    base_language,
    browser_version,
    operating_system,
    primary_language,
    region_of,
    screen_size,
)
from dtk.platforms.douyin import params as douyin
from dtk.platforms.tiktok import params as tiktok

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"


@dataclass(frozen=True, slots=True)
class Fake:
    """A fingerprint, structurally. The real one lives in the transport."""

    user_agent: str | None = None
    platform: str | None = None
    screen: str | None = None
    language: str | None = None
    timezone: str | None = None


WINDOWS = Fake(CHROME_WINDOWS, "Win32", "1920x1080", "en-US", "America/New_York")
MAC = Fake(CHROME_MAC, "MacIntel", "1512x982", "zh-CN", "Asia/Shanghai")


class TestDerivations:
    @pytest.mark.parametrize(
        ("user_agent", "expected"),
        [
            (CHROME_WINDOWS, ("Windows", "10")),
            (CHROME_MAC, ("Mac OS X", "10.15.7")),
            (FIREFOX_LINUX, ("Linux", "")),
            (
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36",
                ("Android", "14"),
            ),
            (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/604.1",
                ("iOS", "17.5"),
            ),
            (
                "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
                ("Windows", "7"),
            ),
        ],
    )
    def test_the_operating_system_comes_out_of_the_user_agent(
        self, user_agent: str, expected: tuple[str, str]
    ) -> None:
        assert operating_system(user_agent) == expected

    def test_android_wins_over_linux_because_the_platforms_table_says_so(self) -> None:
        """An Android User-Agent contains "Linux". Order in the table decides it."""
        android = (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36"
        )
        assert operating_system(android) is not None
        assert operating_system(android)[0] == "Android"

    @pytest.mark.parametrize("user_agent", [None, "", "curl/8.4.0", "something else"])
    def test_an_unrecognised_user_agent_states_nothing(self, user_agent: str | None) -> None:
        """None, so the caller keeps its default rather than inventing an OS."""
        assert operating_system(user_agent) is None

    def test_the_browser_version_is_the_one_the_user_agent_carries(self) -> None:
        assert browser_version(CHROME_WINDOWS, "Chrome") == "146.0.0.0"
        assert browser_version(FIREFOX_LINUX, "Firefox") == "131.0"
        assert browser_version(CHROME_WINDOWS, "Firefox") is None

    def test_the_small_parsers(self) -> None:
        assert screen_size("1920x1080") == (1920, 1080)
        assert screen_size("wide") is None
        assert screen_size(None) is None
        assert primary_language("en-US,en;q=0.9") == "en-US"
        assert primary_language("zh-CN") == "zh-CN"
        assert base_language("en-US") == "en"
        assert region_of("en-US") == "US"
        assert region_of("en") is None
        assert region_of("zh-Hans") is None

    def test_a_field_the_fingerprint_cannot_state_keeps_its_default(self) -> None:
        base = ClientProfile()
        assert base.with_fingerprint(Fake()) == base


class TestDouyin:
    def test_the_query_agrees_with_the_user_agent(self) -> None:
        profile = douyin.profile_for(WINDOWS)
        params = douyin.base_params(profile)
        assert params["browser_version"] == "146.0.0.0"
        assert params["engine_version"] == "146.0.0.0"
        assert params["browser_name"] == "Chrome"
        assert params["engine_name"] == "Blink"
        assert params["os_name"] == "Windows"
        assert params["os_version"] == "10"
        assert params["browser_platform"] == "Win32"
        assert params["browser_language"] == "en-US"
        assert params["screen_width"] == "1920"
        assert params["screen_height"] == "1080"

    def test_the_version_in_the_query_is_the_version_in_the_header(self) -> None:
        """The defect this replaced: a constant 130 under any User-Agent."""
        for fingerprint in (WINDOWS, MAC):
            profile = douyin.profile_for(fingerprint)
            assert profile.browser_version in (fingerprint.user_agent or "")

    def test_a_mac_identity_is_not_described_as_a_windows_one(self) -> None:
        params = douyin.base_params(douyin.profile_for(MAC))
        assert params["os_name"] == "Mac OS X"
        assert params["os_version"] == "10.15.7"
        assert params["browser_platform"] == "MacIntel"
        assert params["screen_width"] == "1512"
        assert params["browser_language"] == "zh-CN"

    def test_firefox_is_named_as_firefox_on_gecko(self) -> None:
        profile = douyin.profile_for(Fake(FIREFOX_LINUX, "Linux x86_64", "2560x1440", "de-DE"))
        assert (profile.browser_name, profile.engine_name) == ("Firefox", "Gecko")
        assert profile.browser_version == "131.0"

    def test_an_unreadable_user_agent_leaves_the_default_whole(self) -> None:
        assert douyin.profile_for(Fake("curl/8.4.0")) == douyin.DEFAULT_PROFILE.with_fingerprint(
            Fake("curl/8.4.0")
        )


class TestTikTok:
    def test_the_captured_windows_values_are_reproduced_exactly(self) -> None:
        """`5.0 (Windows)` and `os=windows` are what a live capture sends."""
        params = tiktok.base_params(tiktok.profile_for(WINDOWS))
        assert params["browser_version"] == "5.0 (Windows)"
        assert params["browser_name"] == "Mozilla"
        assert params["os"] == "windows"
        assert params["browser_platform"] == "Win32"

    def test_the_bare_subtag_and_the_full_tag_stay_different_fields(self) -> None:
        """TikTok sends both, and sends the bare one three times."""
        params = tiktok.base_params(tiktok.profile_for(WINDOWS))
        assert params["browser_language"] == "en-US"
        assert params["language"] == "en"
        assert params["app_language"] == "en"
        assert params["webcast_language"] == "en"

    def test_the_region_and_zone_follow_the_identity(self) -> None:
        params = tiktok.base_params(tiktok.profile_for(WINDOWS))
        assert params["region"] == "US"
        assert params["priority_region"] == "US"
        assert params["tz_name"] == "America/New_York"

    def test_a_mac_identity_says_mac(self) -> None:
        params = tiktok.base_params(tiktok.profile_for(MAC))
        assert params["os"] == "mac"
        assert params["browser_version"] == "5.0 (Macintosh)"
        assert params["region"] == "CN"
        assert params["screen_width"] == "1512"


class TestBothPlatforms:
    @pytest.mark.parametrize("module", [douyin, tiktok])
    @pytest.mark.parametrize("fingerprint", [WINDOWS, MAC])
    def test_the_screen_in_the_query_is_the_screen_in_the_fingerprint(
        self, module: object, fingerprint: Fake
    ) -> None:
        profile = module.profile_for(fingerprint)  # type: ignore[attr-defined]
        assert f"{profile.screen_width}x{profile.screen_height}" == fingerprint.screen

    @pytest.mark.parametrize("module", [douyin, tiktok])
    def test_nothing_is_carried_over_from_the_previous_identity(self, module: object) -> None:
        """Derivation is a pure function of the fingerprint, not a mutation."""
        first = module.profile_for(WINDOWS)  # type: ignore[attr-defined]
        module.profile_for(MAC)  # type: ignore[attr-defined]
        assert module.profile_for(WINDOWS) == first  # type: ignore[attr-defined]

    @pytest.mark.parametrize("fingerprint", [WINDOWS, MAC])
    def test_the_signature_and_the_query_describe_the_same_screen(self, fingerprint: Fake) -> None:
        """The cross-layer property, and the one that was actually broken.

        A-Bogus carries the geometry inside the signature while the query
        carries it in plain sight, and the two are computed in different
        modules. Passing only a User-Agent to the signer made every signature
        claim the fallback 1920x1080 Win32 desktop whatever the query said.
        """
        from dtk.signing.base import StaticFingerprint
        from dtk.signing.native.signer import browser_info_for

        profile = douyin.profile_for(fingerprint)
        size = screen_size(fingerprint.screen)
        assert size is not None
        info = browser_info_for(
            StaticFingerprint(
                user_agent=fingerprint.user_agent or "",
                browser_platform=fingerprint.platform,
                screen_width=size[0],
                screen_height=size[1],
            )
        )
        fields = info.split("|")
        assert fields[-1] == profile.browser_platform
        assert fields[-2] == str(profile.screen_height)
        assert fields[-3] == str(profile.screen_width)
