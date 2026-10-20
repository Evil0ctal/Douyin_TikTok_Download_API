"""The pieces of the identity routes that need no database to be true.

What the console's Identities page renders comes from these: the role each
pasted cookie plays, the platform a paste points at, which logins rotation can
still reach, and the session verdict per platform. Each one used to be either
missing or kept as a copy on the console side that knew only ByteDance.

All cookie values are synthetic.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from dtk.api.routes.admin.identities import (
    _cookie_role,
    _count_logins,
    _login_required_endpoints,
    _session_health,
    _summary,
    _warnings,
)
from dtk.core.types import IdentityState, Language, Platform
from dtk.identity.importing import build_report

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BILIBILI_LOGIN = (
    "buvid3=SYNTHETIC-1234-5678-ABCDinfoc; buvid4=SYNTHETIC-b4; "
    "SESSDATA=SYNTHETICa1b2%2C1792300000%2Cabcd%2A42; bili_jct=SYNTHETICjct0123456789"
)

ACTIVE = IdentityState.ACTIVE.value
COOLING = IdentityState.COOLING.value
DEGRADED = IdentityState.DEGRADED.value


class TestSessionVerdict:
    def test_a_bilibili_identity_has_a_verdict_rather_than_unknown(self) -> None:
        """Every Bilibili row read "Unknown" on a column where that means
        "this build cannot tell" - about a cookie it will not import without."""
        assert _session_health("bilibili", {"buvid3": "SYNTHETIC-b3infoc"}) == {
            "cookie": "buvid3",
            "verdict": "ok",
            "held": True,
        }

    def test_a_bilibili_jar_without_its_visitor_id_is_missing_one(self) -> None:
        assert _session_health("bilibili", {"SESSDATA": "x"})["verdict"] == "missing"

    def test_a_douyin_login_from_a_browser_holds_its_visitor_id_as_uifid(self) -> None:
        """A browser's jar spells it `UIFID`; only a mint yields `UIFID_TEMP`.

        Judged on `UIFID_TEMP` alone, every imported login read "missing", and
        the Identities page hid it as a spent session - the one identity the
        operator had just imported, while the signer was signing with it.
        """
        jar = {"UIFID": "SYNTHETIC" + "a" * 300, "sessionid": "SYNTHETICsession"}
        assert _session_health("douyin", jar) == {"cookie": "UIFID", "verdict": "ok", "held": True}

    def test_a_douyin_verdict_is_on_the_value_the_signer_signs_with(self) -> None:
        """Both spellings present: the one `pick_uifid` reads first decides."""
        jar = {"UIFID_TEMP": "SYNTHETIC" + "a" * 40, "uifid": "SYNTHETICshort"}
        assert _session_health("douyin", jar) == {
            "cookie": "uifid",
            "verdict": "too_short",
            "held": True,
        }

    def test_a_douyin_jar_without_one_names_the_cookie_a_mint_yields(self) -> None:
        assert _session_health("douyin", {"ttwid": "SYNTHETIC"}) == {
            "cookie": "UIFID_TEMP",
            "verdict": "missing",
            "held": False,
        }


class TestCookieRoles:
    def test_required_is_judged_for_the_platform_asked_about(self) -> None:
        assert _cookie_role("buvid3", Platform.BILIBILI) == "required"
        assert _cookie_role("ttwid", Platform.DOUYIN) == "required"
        # A ByteDance cookie pasted into a Bilibili import is not required there.
        assert _cookie_role("ttwid", Platform.BILIBILI) == "other"

    def test_bilibilis_login_cookies_read_as_the_login(self) -> None:
        assert _cookie_role("SESSDATA", Platform.BILIBILI) == "session"
        assert _cookie_role("bili_jct", Platform.BILIBILI) == "session"

    def test_without_a_platform_any_platforms_requirement_counts(self) -> None:
        assert _cookie_role("ttwid") == "required"
        assert _cookie_role("buvid3") == "required"


class TestImportSummary:
    def test_the_preview_says_each_cookies_role_and_where_the_jar_came_from(self) -> None:
        report = build_report(BILIBILI_LOGIN, Platform.BILIBILI, user_agent=CHROME_UA)
        summary = _summary(report, _warnings(report, Language.EN))

        assert summary["detected_platform"] == "bilibili"
        assert summary["cookie_roles"] == {
            "SESSDATA": "session",
            "bili_jct": "session",
            "buvid3": "required",
            "buvid4": "useful",
        }
        assert summary["authenticated"] is True
        assert summary["expires_at"] == "2026-10-18T05:06:40+00:00"
        assert summary["usable"] is True

    def test_a_jar_pasted_under_the_wrong_platform_still_says_which_it_is(self) -> None:
        report = build_report(BILIBILI_LOGIN, Platform.DOUYIN, user_agent=CHROME_UA)
        summary = _summary(report, _warnings(report, Language.EN))

        assert summary["detected_platform"] == "bilibili"
        assert summary["missing_required"] == ["ttwid"]
        assert summary["usable"] is False

    def test_no_value_leaves_in_the_summary(self) -> None:
        report = build_report(BILIBILI_LOGIN, Platform.BILIBILI, user_agent=CHROME_UA)
        rendered = repr(_summary(report, _warnings(report, Language.EN)))
        for secret in ("SYNTHETICa1b2", "SYNTHETICjct0123456789", "1234-5678-ABCD"):
            assert secret not in rendered


#: Which endpoints refuse a guest, as `pool_level` hands it to the counter.
NEEDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "douyin": frozenset({"douyin.author_posts"}),
        "tiktok": frozenset(),
        "bilibili": frozenset({"bilibili.comments", "bilibili.comment_replies"}),
    }
)


class TestLoginCoverage:
    def test_each_login_lands_in_one_bucket(self) -> None:
        coverage = _count_logins(
            [
                ("douyin", ACTIVE, None),
                ("douyin", DEGRADED, None),
                ("douyin", ACTIVE, []),
                ("douyin", COOLING, None),
                ("bilibili", ACTIVE, ["bilibili.comments"]),
                ("bilibili", ACTIVE, ["bilibili.video_detail"]),
            ],
            NEEDS,
        )

        assert coverage["douyin"] == {"rotating": 2, "limited": 0, "reserved": 1, "cooling": 1}
        assert coverage["bilibili"] == {"rotating": 1, "limited": 1, "reserved": 0, "cooling": 0}
        assert coverage["tiktok"] == {"rotating": 0, "limited": 0, "reserved": 0, "cooling": 0}

    def test_a_reserved_login_is_reserved_even_while_it_cools(self) -> None:
        """The scheduler skips it either way, so it must not read as coming back."""
        coverage = _count_logins([("douyin", COOLING, [])], NEEDS)
        assert coverage["douyin"]["reserved"] == 1
        assert coverage["douyin"]["cooling"] == 0

    def test_a_platform_with_no_login_only_endpoints_counts_every_list_as_rotating(self) -> None:
        coverage = _count_logins([("tiktok", ACTIVE, ["tiktok.video_detail"])], NEEDS)
        assert coverage["tiktok"]["rotating"] == 1

    def test_the_endpoints_that_need_a_login_come_from_the_adapters(self) -> None:
        assert "douyin.author_posts" in _login_required_endpoints(Platform.DOUYIN)
        assert {"bilibili.comments", "bilibili.comment_replies"} <= _login_required_endpoints(
            Platform.BILIBILI
        )
