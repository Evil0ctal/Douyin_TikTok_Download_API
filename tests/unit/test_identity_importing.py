"""Cookie import: format detection, browser inference and session expiry.

All cookie values here are synthetic. The sid_guard *shape* mirrors the real
one, which is what the expiry parser has to cope with.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from dtk.core.types import BrowserFamily, Language, Platform
from dtk.i18n.catalog import humanize, t
from dtk.identity.importing import (
    REQUIRED,
    SESSION_MARKERS,
    USEFUL,
    WARNING_KEY_PREFIX,
    CookieFormat,
    CookieWarning,
    ImportReport,
    WarningCode,
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

#: Cookie values a warning must never repeat. Long and distinctive on purpose:
#: a realistic short value such as "x" occurs inside ordinary English prose, so
#: a leak check built on one would fail for reasons that have nothing to do
#: with leaking.
SENTINELS = {
    "ttwid": "SYNTHETIC-ttwid-1a2b3c4d5e6f7a8b",
    "sessionid": "SYNTHETIC-sessionid-9c8d7e6f5a4b",
    "sid_guard": "SYNTHETIC-guard%7C1756134824%7C21600%7Cx",
    # A value shaped like a template placeholder: if one ever reached the
    # catalogue as an argument, this is the shape that would do damage.
    "custom_marker": "SYNTHETIC-{optional}-{platform}",
}


_CJK = re.compile("[\\u4e00-\\u9fff]")


def _codes(report: ImportReport) -> set[WarningCode]:
    return {warning.code for warning in report.warnings}


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
        assert WarningCode.UNKNOWN_BROWSER in _codes(r)

    def test_logged_in_set_is_flagged_and_dated(self):
        guard = "SYNTHETIC%7C1756134824%7C21600%7Cx"
        r = build_report(
            f"ttwid=abc; sessionid=zzz; sid_guard={guard}",
            Platform.DOUYIN,
            user_agent=CHROME_UA,
        )
        assert r.authenticated
        assert r.expires_at is not None
        assert WarningCode.LOGGED_IN_SESSION in _codes(r)

    def test_values_are_masked_for_display(self):
        r = build_report("ttwid=abcdefghijklmnop", Platform.DOUYIN, user_agent=CHROME_UA)
        masked = r.masked()
        assert "abcdefghijklmnop" not in masked["ttwid"]
        assert masked["ttwid"].startswith("abcd")

    def test_round_trip_to_header(self):
        _, cookies = parse_cookies("ttwid=abc; odin_tt=def")
        assert to_cookie_header(cookies) == "ttwid=abc; odin_tt=def"


class TestWarnings:
    """Warnings are codes, and the sentence is chosen by the caller's language.

    Asserting on codes rather than on rendered prose is deliberate: the English
    sentence is a translator's to change, and a test that pins it turns every
    wording fix into a test failure.
    """

    def test_each_condition_has_its_own_code(self):
        weak = build_report("ttwid=abc", Platform.DOUYIN, user_agent=CHROME_UA)
        assert _codes(weak) == {WarningCode.NO_USEFUL_COOKIES}

        nothing = build_report("not a cookie at all", Platform.DOUYIN, user_agent=CHROME_UA)
        assert WarningCode.NO_COOKIES in _codes(nothing)

        unknown_browser = build_report("ttwid=abc; odin_tt=def", Platform.DOUYIN)
        assert _codes(unknown_browser) == {WarningCode.UNKNOWN_BROWSER}

    def test_a_logged_in_set_is_not_also_called_weak(self):
        """A session jar is the opposite of weak; two warnings would contradict."""
        report = build_report("ttwid=abc; sessionid=zzz", Platform.DOUYIN, user_agent=CHROME_UA)
        assert _codes(report) == {WarningCode.LOGGED_IN_SESSION}

    def test_every_code_has_a_key_in_one_namespace(self):
        """The merge script and the console both key off this shape."""
        for code in WarningCode:
            assert CookieWarning(code).key == f"{WARNING_KEY_PREFIX}{code.value}"

    def test_the_weak_identity_warning_names_the_cookies_that_would_help(self):
        """The list is an argument so that USEFUL stays the single source."""
        report = build_report("ttwid=abc", Platform.DOUYIN, user_agent=CHROME_UA)
        (warning,) = report.warnings
        assert warning.args["optional"] == ", ".join(sorted(USEFUL))

    @pytest.mark.parametrize("language", list(Language))
    def test_no_cookie_value_can_reach_a_warning(self, language):
        """The security warning is displayed, logged and pasted into bug reports.

        Nothing read out of the jar may ride along - which is why arguments are
        built from this module's constants and never from parsed input. Every
        trigger is exercised, in both languages, against values chosen to be
        recognisable if they escaped.
        """
        pastes = [
            # A logged-in jar: the case where a leak would cost the most.
            "; ".join(f"{name}={value}" for name, value in SENTINELS.items()),
            # No required cookie, no useful cookie, no inferable browser.
            f"custom_marker={SENTINELS['custom_marker']}",
            # Nothing parseable at all.
            SENTINELS["ttwid"],
        ]
        for paste in pastes:
            for user_agent in (CHROME_UA, None):
                report = build_report(paste, Platform.DOUYIN, user_agent=user_agent)
                assert report.warnings, paste
                for warning in report.warnings:
                    rendered = t(warning.key, language, **warning.args)
                    surface = f"{warning.key} {json.dumps(dict(warning.args))} {rendered}"
                    for value in SENTINELS.values():
                        assert value not in surface, (warning.code, value)

    def test_warning_arguments_come_only_from_module_constants(self):
        """The structural half of the rule above.

        A future warning that interpolates a cookie name would pass the leak
        test - names are not values - and still hand the console a string the
        paste chose. Pinning arguments to the constants closes that door.
        """
        allowed = set(USEFUL) | set(SESSION_MARKERS) | {n for v in REQUIRED.values() for n in v}
        pastes = [
            "; ".join(f"{name}={value}" for name, value in SENTINELS.items()),
            f"custom_marker={SENTINELS['custom_marker']}",
            "",
        ]
        for paste in pastes:
            for warning in build_report(paste, Platform.DOUYIN).warnings:
                for value in warning.args.values():
                    if isinstance(value, int):
                        continue
                    assert set(str(value).split(", ")) <= allowed, (warning.code, value)

    def test_the_same_warning_reads_differently_in_each_language(self):
        """The point of the exercise: a Chinese user reads Chinese.

        Skipped until the catalogue entries are merged, because an unmerged key
        renders as the same humanized fallback in every language.
        """
        report = build_report("ttwid=abc; sessionid=zzz", Platform.DOUYIN, user_agent=CHROME_UA)
        (warning,) = report.warnings
        english = t(warning.key, Language.EN, **warning.args)
        chinese = t(warning.key, Language.ZH, **warning.args)
        if english == humanize(warning.key):
            pytest.skip(f"{warning.key} is not in the catalogue yet")
        assert english != chinese
        assert _CJK.search(chinese)

    def test_the_route_renders_one_sentence_per_warning(self):
        """The console reads ``warnings``; it never sees a code object.

        Imported here rather than in an API test because this is the last step
        of the import pipeline the rest of this file describes, and it needs no
        database to be true.
        """
        from dtk.api.routes.admin.identities import _warnings

        report = build_report("ttwid=abc", Platform.DOUYIN)
        for language in Language:
            rendered = _warnings(report, language)
            assert len(rendered) == len(report.warnings)
            assert all(isinstance(line, str) and line.strip() for line in rendered)
