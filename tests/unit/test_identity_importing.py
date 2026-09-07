"""Cookie import: format detection, browser inference and session expiry.

All cookie values here are synthetic. The sid_guard *shape* mirrors the real
one, which is what the expiry parser has to cope with.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from dtk.core.types import BrowserFamily, Platform
from dtk.identity.importing import (
    CookieFormat,
    build_report,
    detect_format,
    infer_browser,
    parse_cookies,
    session_expiry,
    to_cookie_header,
)

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


class TestFormatDetection:
    def test_header_string_from_devtools(self):
        fmt, cookies = parse_cookies("ttwid=abc123; odin_tt=def456; msToken=ghi789")
        assert fmt is CookieFormat.HEADER
        assert cookies == {"ttwid": "abc123", "odin_tt": "def456", "msToken": "ghi789"}

    def test_header_string_keeps_its_cookie_prefix(self):
        _, cookies = parse_cookies("Cookie: ttwid=abc123; odin_tt=def456")
        assert cookies == {"ttwid": "abc123", "odin_tt": "def456"}

    def test_json_array_from_extension(self):
        payload = json.dumps(
            [
                {"name": "ttwid", "value": "abc123", "domain": ".douyin.com", "path": "/"},
                {"name": "odin_tt", "value": "def456", "domain": ".douyin.com"},
            ]
        )
        fmt, cookies = parse_cookies(payload)
        assert fmt is CookieFormat.JSON_ARRAY
        assert cookies == {"ttwid": "abc123", "odin_tt": "def456"}

    def test_json_object_mapping(self):
        _, cookies = parse_cookies('{"ttwid": "abc123", "odin_tt": "def456"}')
        assert cookies == {"ttwid": "abc123", "odin_tt": "def456"}

    def test_netscape_cookies_txt(self):
        payload = (
            "# Netscape HTTP Cookie File\n"
            ".douyin.com\tTRUE\t/\tFALSE\t1893456000\tttwid\tabc123\n"
            ".douyin.com\tTRUE\t/\tTRUE\t1893456000\todin_tt\tdef456\n"
        )
        fmt, cookies = parse_cookies(payload)
        assert fmt is CookieFormat.NETSCAPE
        assert cookies == {"ttwid": "abc123", "odin_tt": "def456"}

    def test_loose_lines_hand_copied(self):
        fmt, cookies = parse_cookies("ttwid=abc123\nodin_tt=def456\nmsToken=ghi789")
        assert fmt is CookieFormat.LOOSE
        assert len(cookies) == 3

    def test_malformed_json_falls_back_instead_of_failing(self):
        """Detection is a heuristic; a wrong guess must not lose the input."""
        _, cookies = parse_cookies("[this is not json\nttwid=abc123")
        assert cookies.get("ttwid") == "abc123"

    def test_empty_input_yields_nothing_but_does_not_raise(self):
        assert parse_cookies("")[1] == {}
        assert parse_cookies("   \n  ")[1] == {}

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("a=1; b=2", CookieFormat.HEADER),
            ('[{"name":"a","value":"1"}]', CookieFormat.JSON_ARRAY),
            (".x.com\tTRUE\t/\tFALSE\t0\ta\t1", CookieFormat.NETSCAPE),
            ("a=1\nb=2", CookieFormat.LOOSE),
        ],
    )
    def test_detect_format(self, text, expected):
        assert detect_format(text) is expected


class TestBrowserInference:
    @pytest.mark.parametrize(
        "ua,family,major",
        [
            (CHROME_UA, BrowserFamily.CHROME, 149),
            ("Mozilla/5.0 ... Firefox/151.0", BrowserFamily.FIREFOX, 151),
            (
                "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.2 Safari/605.1.15",
                BrowserFamily.SAFARI,
                18,
            ),
            # Edge and Opera are Chromium and share Chrome's TLS profile. Their
            # tokens sit next to "Chrome/", so ordering decides the answer.
            (
                "Mozilla/5.0 ... Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
                BrowserFamily.CHROME,
                149,
            ),
            (
                "Mozilla/5.0 ... Chrome/140.0.0.0 Safari/537.36 OPR/125.0.0.0",
                BrowserFamily.CHROME,
                125,
            ),
        ],
    )
    def test_known_agents(self, ua, family, major):
        assert infer_browser(ua) == (family, major)

    def test_unknown_agent_returns_nothing(self):
        """Doc 02 requires refusing the identity, not guessing a default."""
        assert infer_browser("curl/8.4.0") == (None, None)
        assert infer_browser(None) == (None, None)
        assert infer_browser("") == (None, None)


class TestSessionExpiry:
    def test_parses_the_real_sid_guard_shape(self):
        # "<sessionid>|<issued>|<max_age>|<http date>", URL-encoded.
        guard = "SYNTHETIC%7C1756134824%7C21600%7CMon%2C+25-Aug-2025+21%3A13%3A44+GMT"
        got = session_expiry({"sid_guard": guard})
        assert got == datetime.fromtimestamp(1756134824 + 21600, tz=UTC)

    def test_unencoded_form_also_works(self):
        got = session_expiry({"sid_guard": "SYNTHETIC|1756134824|21600|whatever"})
        assert got is not None

    @pytest.mark.parametrize(
        "guard", ["", "garbage", "a|notanumber|21600|x", "a|1756134824|0|x", "a|1|"]
    )
    def test_unparseable_returns_none(self, guard):
        assert session_expiry({"sid_guard": guard}) is None

    def test_absent_sid_guard_returns_none(self):
        assert session_expiry({"ttwid": "abc"}) is None


class TestReport:
    def test_guest_cookies_are_usable(self):
        r = build_report("ttwid=abc; odin_tt=def", Platform.DOUYIN, user_agent=CHROME_UA)
        assert r.usable
        assert not r.authenticated
        assert r.expires_at is None
        assert r.missing_required == ()

    def test_missing_required_cookie_is_reported(self):
        r = build_report("odin_tt=def", Platform.DOUYIN, user_agent=CHROME_UA)
        assert not r.usable
        assert r.missing_required == ("ttwid",)

    def test_unknown_browser_makes_the_set_unusable(self):
        r = build_report("ttwid=abc", Platform.DOUYIN, user_agent="curl/8.4.0")
        assert not r.usable
        assert any("browser could not be inferred" in w for w in r.warnings)

    def test_logged_in_set_is_flagged_and_dated(self):
        guard = "SYNTHETIC%7C1756134824%7C21600%7Cx"
        r = build_report(
            f"ttwid=abc; sessionid=zzz; sid_guard={guard}",
            Platform.DOUYIN,
            user_agent=CHROME_UA,
        )
        assert r.authenticated
        assert r.expires_at is not None
        assert any("logged-in session" in w for w in r.warnings)

    def test_values_are_masked_for_display(self):
        r = build_report("ttwid=abcdefghijklmnop", Platform.DOUYIN, user_agent=CHROME_UA)
        masked = r.masked()
        assert "abcdefghijklmnop" not in masked["ttwid"]
        assert masked["ttwid"].startswith("abcd")

    def test_round_trip_to_header(self):
        _, cookies = parse_cookies("ttwid=abc; odin_tt=def")
        assert to_cookie_header(cookies) == "ttwid=abc; odin_tt=def"
