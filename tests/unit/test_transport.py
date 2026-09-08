"""Unit tests for the transport layer. No network, no database.

The emulation mapping is checked against the real `wreq.Emulation` enum: that is
genuinely verifiable offline and is the part most likely to rot on a wreq
upgrade. Everything else runs against a recording client double injected through
`WreqTransport(client_factory=...)`, which is the module's only network seam.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest
import wreq

from dtk.core.errors import InvalidParam
from dtk.core.types import BrowserFamily, Outcome, Platform
from dtk.transport.base import (
    MAX_TIMEOUT_SECONDS,
    Fingerprint,
    RawResponse,
    RequestSpec,
    TransportFailure,
    TransportIdentity,
    mask_proxy_url,
)
from dtk.transport.classify import (
    Classification,
    ClassificationRule,
    Classifier,
    classify,
    classify_detailed,
)
from dtk.transport.emulation import (
    EMULATION_TABLE,
    DriftBand,
    EmulationUnavailable,
    UnknownBrowserFamily,
    build_table,
    emulation_drift,
    emulation_for,
    known_majors,
    profile_for,
    select_profile,
)
from dtk.transport.headers import accept_language, build_headers, platform_hint, sec_ch_ua
from dtk.transport.wreq_transport import (
    DEFAULT_TIMEOUT_SECONDS,
    ClientOptions,
    WreqTransport,
    default_client_factory,
)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"

CHROME_FP = Fingerprint(
    browser_family=BrowserFamily.CHROME,
    browser_major=149,
    user_agent=CHROME_UA,
    platform="Win32",
    screen="1920x1080",
    language="en-US",
    timezone="America/Los_Angeles",
)
FIREFOX_FP = Fingerprint(
    browser_family=BrowserFamily.FIREFOX,
    browser_major=143,
    user_agent=FIREFOX_UA,
    platform="Linux x86_64",
    screen="1680x1050",
    language="de-DE",
    timezone="Europe/Berlin",
)


def make_identity(
    identity_id: str = "id-1",
    *,
    proxy_url: str | None = None,
    cookies: Mapping[str, str] | None = None,
    fingerprint: Fingerprint = CHROME_FP,
) -> TransportIdentity:
    return TransportIdentity(
        id=identity_id,
        platform=Platform.DOUYIN,
        fingerprint=fingerprint,
        proxy_url=proxy_url,
        cookies=dict(cookies or {}),
    )


# --------------------------------------------------------------------- doubles


class FakeCookie:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class FakeStatus:
    """Mimics `wreq.StatusCode`, which is not an int."""

    def __init__(self, code: int) -> None:
        self._code = code

    def as_int(self) -> int:
        return self._code

    def __str__(self) -> str:
        return f"{self._code} OK"


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b"{}",
        headers: Mapping[str, str] | None = None,
        url: str = "https://www.douyin.com/final",
        cookies: tuple[FakeCookie, ...] = (),
        header_pairs: tuple[tuple[bytes, bytes], ...] = (),
        body_error: Exception | None = None,
        body_delay: float = 0.0,
    ) -> None:
        self.status = FakeStatus(status)
        self._body = body
        self._body_error = body_error
        self._body_delay = body_delay
        # wreq hands back a header map that iterates as (bytes, bytes) pairs.
        self.headers = [
            *((k.encode(), v.encode()) for k, v in (headers or {}).items()),
            *header_pairs,
        ]
        self.url = url
        self.cookies = cookies
        self.closed = 0

    async def bytes(self) -> bytes:
        if self._body_delay:
            await asyncio.sleep(self._body_delay)
        if self._body_error is not None:
            raise self._body_error
        return self._body

    async def close(self) -> None:
        self.closed += 1


class FakeClient:
    def __init__(self, options: ClientOptions) -> None:
        self.options = options
        self.calls: list[dict[str, Any]] = []
        self.closed = 0
        self.response = FakeResponse()
        self.error: Exception | None = None

    async def request(self, method: Any, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response

    def close(self) -> None:
        self.closed += 1


class RecordingFactory:
    def __init__(self) -> None:
        self.built: list[FakeClient] = []

    def __call__(self, options: ClientOptions) -> FakeClient:
        client = FakeClient(options)
        self.built.append(client)
        return client


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, identity_id: str, cookies: Mapping[str, str]) -> None:
        self.calls.append((identity_id, dict(cookies)))


def make_transport(**kwargs: Any) -> tuple[WreqTransport, RecordingFactory]:
    factory = RecordingFactory()
    return WreqTransport(client_factory=factory, **kwargs), factory


# ------------------------------------------------------------------- emulation


class TestEmulationTable:
    def test_table_is_built_from_the_installed_wreq(self) -> None:
        for family in ("chrome", "firefox", "safari"):
            assert family in EMULATION_TABLE, f"wreq exposes no {family} profiles"
        chrome = EMULATION_TABLE["chrome"]
        assert chrome[149].name == "Chrome149"
        assert chrome[149].emulation is wreq.Emulation.Chrome149

    def test_versionless_members_are_ignored(self) -> None:
        # `Emulation.random` picks a profile at random, which is the exact
        # opposite of what a stable identity needs.
        names = {
            profile.name for by_major in EMULATION_TABLE.values() for profile in by_major.values()
        }
        assert "random" not in names

    def test_variants_do_not_pollute_the_desktop_families(self) -> None:
        for profile in EMULATION_TABLE["safari"].values():
            assert profile.name.startswith("Safari")
            assert not profile.name.lower().startswith(("safariios", "safariipad"))
        for profile in EMULATION_TABLE["firefox"].values():
            assert "Android" not in profile.name
            assert "Private" not in profile.name

    def test_highest_patch_wins_within_a_major(self) -> None:
        # wreq ships Safari26 .. Safari26_4; a real Safari 26 is on the latest.
        assert select_profile(BrowserFamily.SAFARI, 26).profile.name == "Safari26_4"

    def test_build_table_parses_a_synthetic_namespace(self) -> None:
        class StubEmulation:
            Chrome149 = "chrome-149"
            Chrome150 = "chrome-150"
            Safari26 = "safari-26"
            Safari26_2 = "safari-26-2"
            SafariIos26 = "safari-ios-26"
            OkHttp3_11 = "okhttp-3-11"
            random = "random"
            Nightly = "nightly"

        table = build_table(StubEmulation)

        assert set(table) == {"chrome", "safari", "safariios", "okhttp"}
        assert table["chrome"][150].emulation == "chrome-150"
        assert table["safari"][26].name == "Safari26_2"
        assert table["okhttp"][3].version == (3, 11)
        assert table["safariios"][26].name == "SafariIos26"


class TestProfileSelection:
    def test_exact_match(self) -> None:
        match = select_profile(BrowserFamily.CHROME, 149)
        assert match.exact is True
        assert match.drift == 0
        assert match.profile.emulation is wreq.Emulation.Chrome149

    def test_nearest_below_when_the_major_is_missing(self) -> None:
        # wreq has 101 and 104 but no 102/103.
        match = select_profile(BrowserFamily.CHROME, 103)
        assert match.exact is False
        assert match.profile.name == "Chrome101"
        assert match.profile.major < 103

    def test_never_reaches_upward(self) -> None:
        majors = known_majors(BrowserFamily.CHROME)
        newest = max(majors)
        match = select_profile(BrowserFamily.CHROME, newest + 1)
        assert match.profile.major == newest

    def test_major_below_every_profile_is_refused(self) -> None:
        oldest = min(known_majors(BrowserFamily.CHROME))
        with pytest.raises(EmulationUnavailable):
            select_profile(BrowserFamily.CHROME, oldest - 1)

    def test_unknown_family_is_refused_rather_than_defaulted(self) -> None:
        with pytest.raises(UnknownBrowserFamily):
            profile_for(Fingerprint(browser_major=149, user_agent=CHROME_UA))

    def test_missing_major_is_refused(self) -> None:
        with pytest.raises(EmulationUnavailable):
            profile_for(Fingerprint(browser_family=BrowserFamily.CHROME, user_agent=CHROME_UA))

    def test_emulation_for_returns_the_wreq_member(self) -> None:
        assert emulation_for(CHROME_FP) is wreq.Emulation.Chrome149

    def test_far_future_major_is_refused(self) -> None:
        newest = max(known_majors(BrowserFamily.CHROME))
        far = Fingerprint(
            browser_family=BrowserFamily.CHROME,
            browser_major=newest + 20,
            user_agent=CHROME_UA,
        )
        with pytest.raises(EmulationUnavailable):
            profile_for(far)

    def test_warn_band_still_resolves(self) -> None:
        newest = max(known_majors(BrowserFamily.CHROME))
        match = profile_for(
            Fingerprint(
                browser_family=BrowserFamily.CHROME,
                browser_major=newest + 3,
                user_agent=CHROME_UA,
            )
        )
        assert match.band is DriftBand.WARN
        assert match.profile.major == newest


class TestDriftBands:
    @pytest.mark.parametrize(
        ("chromium", "profile", "expected"),
        [
            (149, 149, DriftBand.OK),
            (149, 148, DriftBand.OK),
            (149, 147, DriftBand.OK),
            (149, 146, DriftBand.WARN),
            (149, 145, DriftBand.WARN),
            (149, 144, DriftBand.FAIL),
            (140, 149, DriftBand.FAIL),
            (151, 149, DriftBand.OK),
        ],
    )
    def test_bands(self, chromium: int, profile: int, expected: DriftBand) -> None:
        assert emulation_drift(chromium, profile) is expected

    def test_band_is_symmetric(self) -> None:
        assert emulation_drift(130, 136) is emulation_drift(136, 130)


# --------------------------------------------------------------------- headers


class TestHeaders:
    def test_chromium_gets_client_hints(self) -> None:
        headers = build_headers(CHROME_FP)
        assert headers["User-Agent"] == CHROME_UA
        assert headers["Accept-Language"] == "en-US,en;q=0.9"
        assert headers["sec-ch-ua"] == sec_ch_ua(149)
        assert '"Chromium";v="149"' in headers["sec-ch-ua"]
        assert headers["sec-ch-ua-mobile"] == "?0"
        assert headers["sec-ch-ua-platform"] == '"Windows"'

    def test_non_chromium_sends_no_client_hints(self) -> None:
        headers = build_headers(FIREFOX_FP)
        assert headers["User-Agent"] == FIREFOX_UA
        assert headers["Accept-Language"] == "de-DE,de;q=0.9"
        assert not [name for name in headers if name.startswith("sec-ch-ua")]

    def test_mobile_platform_hint(self) -> None:
        mobile = Fingerprint(
            browser_family=BrowserFamily.CHROME,
            browser_major=149,
            user_agent=CHROME_UA,
            platform="Linux armv8l",
            language="en-US",
        )
        headers = build_headers(mobile)
        assert headers["sec-ch-ua-mobile"] == "?1"
        assert headers["sec-ch-ua-platform"] == '"Android"'

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("Win32", "Windows"),
            ("MacIntel", "macOS"),
            ("Linux x86_64", "Linux"),
            ("Linux armv8l", "Android"),
            ("Linux aarch64", "Linux"),
            ("iPhone", "iOS"),
            ("iPad", "iOS"),
            (None, None),
            ("Amiga", None),
        ],
    )
    def test_platform_hints(self, platform: str | None, expected: str | None) -> None:
        assert platform_hint(platform) == expected

    @pytest.mark.parametrize(
        ("language", "expected"),
        [
            ("en-US", "en-US,en;q=0.9"),
            ("en", "en"),
            ("zh-CN,zh;q=0.9,en;q=0.8", "zh-CN,zh;q=0.9,en;q=0.8"),
            (None, None),
            ("", None),
        ],
    )
    def test_accept_language(self, language: str | None, expected: str | None) -> None:
        assert accept_language(language) == expected

    def test_extra_headers_override_case_insensitively(self) -> None:
        headers = build_headers(CHROME_FP, {"user-agent": "override", "Referer": "https://x"})
        assert headers["user-agent"] == "override"
        assert "User-Agent" not in headers
        assert headers["Referer"] == "https://x"

    def test_missing_user_agent_is_refused(self) -> None:
        with pytest.raises(Exception, match="user agent"):
            build_headers(Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=149))


# ------------------------------------------------------------------ raw response


class TestRawResponse:
    def test_json_and_text(self) -> None:
        raw = RawResponse(
            status=200,
            headers={"content-type": "application/json; charset=utf-8"},
            body=b'{"status_code": 0}',
            final_url="https://www.douyin.com/x",
            elapsed_ms=12,
        )
        assert raw.ok is True
        assert raw.json() == {"status_code": 0}
        assert raw.text == '{"status_code": 0}'
        assert raw.charset == "utf-8"
        assert raw.header("Content-Type") is not None

    def test_json_or_none_on_html(self) -> None:
        raw = RawResponse(status=200, body=b"<html>captcha</html>")
        assert raw.json_or_none() is None
        with pytest.raises(ValueError, match="Expecting value"):
            raw.json()

    def test_unknown_charset_falls_back(self) -> None:
        raw = RawResponse(
            status=200,
            headers={"content-type": "text/html; charset=not-a-charset"},
            body=b"hello",
        )
        assert raw.text == "hello"


# ------------------------------------------------------------------ classifier


#: Douyin's `filter_detail.detail_msg` for a deleted or private post, captured
#: verbatim on 2026-09-08. It means "this post is private or has been deleted
#: and cannot be viewed; go and look at other posts". Kept as the platform sent
#: it because the rule under test carries the message through to the console,
#: and paraphrasing it would stop testing that.
DELETED_POST_MESSAGE = "\u56e0\u4f5c\u54c1\u6743\u9650\u6216\u5df2\u88ab\u5220\u9664\uff0c\u65e0\u6cd5\u89c2\u770b\uff0c\u53bb\u770b\u770b\u5176\u4ed6\u4f5c\u54c1\u5427"


def json_response(payload: Any, status: int = 200) -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


class TestClassifier:
    def test_normal_payload_is_ok(self) -> None:
        response = json_response({"status_code": 0, "aweme_detail": {"aweme_id": "7"}})
        assert classify(response) is Outcome.OK

    def test_empty_aweme_detail_at_200_is_risk_control(self) -> None:
        response = json_response({"status_code": 0, "aweme_detail": None})
        result = classify_detailed(response)
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "payload.withheld"
        assert result.detail == "empty aweme_detail"

    def test_an_absence_the_platform_explained_is_a_business_error(self) -> None:
        """Douyin's answer for a deleted or private post, captured live.

        Verified on 2026-09-08 against the identity probe's own smoke URL, whose
        video had been deleted: HTTP 200 with a null `aweme_detail` and a
        `filter_detail` saying why. The empty payload alone is the risk
        signature, so this used to cool the identity that asked and count toward
        the endpoint's risk rate - meaning a caller walking a list of older
        posts could trip the circuit breaker and take the endpoint down for
        every identity. The platform told us why; that makes it an answer.
        """
        response = json_response(
            {
                "aweme_detail": None,
                "filter_detail": {
                    "aweme_id": "7298145681699622182",
                    "detail_msg": DELETED_POST_MESSAGE,
                },
            }
        )
        result = classify_detailed(response)
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert result.rule == "payload.explained"
        # The platform's own words are carried through, so the console shows the
        # caller why the post is missing rather than a generic refusal.
        assert result.detail == DELETED_POST_MESSAGE

    def test_a_marker_with_no_message_is_still_risk_control(self) -> None:
        """The rule above must not swallow the signature it sits in front of.

        Captured live on 2026-09-08 alongside the explained case: the same shape,
        a `filter_reason` nobody outside the platform can read, and every message
        field empty. Douyin is not telling the caller why - so this is still a
        withheld payload, and accepting the container alone as an explanation
        would hide real withholding behind an empty envelope.
        """
        response = json_response(
            {
                "status_code": 0,
                "aweme_detail": None,
                "filter_detail": {
                    "aweme_id": "7397714385177335090",
                    "detail_msg": "",
                    "filter_reason": "core_dep",
                    "icon": "",
                    "notice": "",
                },
            }
        )
        result = classify_detailed(response)
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "payload.withheld"

    def test_empty_list_page_is_not_risk_control(self) -> None:
        # End of pagination. Cooling an identity here would punish normal use.
        response = json_response({"status_code": 0, "aweme_list": [], "has_more": 0})
        assert classify(response) is Outcome.OK

    def test_deleted_content_is_a_business_error(self) -> None:
        response = json_response({"status_code": 2053, "status_msg": "unavailable"})
        result = classify_detailed(response)
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert result.rule == "envelope.business_code"

    def test_structured_refusal_is_a_business_error(self) -> None:
        # An unknown non-zero code still means the platform answered us.
        response = json_response({"status_code": 4041, "status_msg": "no such user"})
        result = classify_detailed(response)
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert result.rule == "envelope.nonzero"

    def test_verification_envelope_is_risk_control(self) -> None:
        response = json_response({"status_code": 10000, "status_msg": "verify"})
        result = classify_detailed(response)
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "envelope.risk_code"

    def test_captcha_page_is_risk_control(self) -> None:
        response = RawResponse(
            status=200,
            headers={"content-type": "text/html"},
            body=b"<html><script src='/verify_center/captcha.js'></script></html>",
        )
        result = classify_detailed(response)
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "body.challenge_marker"

    def test_markers_inside_user_content_are_not_risk_control(self) -> None:
        # A description that mentions a captcha must not cool the identity.
        response = json_response(
            {"status_code": 0, "aweme_detail": {"desc": "how to solve a captcha", "id": "7"}}
        )
        assert classify(response) is Outcome.OK

    def test_empty_body_at_200_is_risk_control(self) -> None:
        result = classify_detailed(RawResponse(status=200, body=b""))
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "body.empty"

    @pytest.mark.parametrize("status", [401, 403, 429, 444])
    def test_refusal_statuses_are_risk_control(self, status: int) -> None:
        assert classify(RawResponse(status=status, body=b"nope")) is Outcome.RISK_CONTROL

    @pytest.mark.parametrize("status", [400, 404, 410, 451])
    def test_content_statuses_are_business_errors(self, status: int) -> None:
        assert classify(RawResponse(status=status, body=b"gone")) is Outcome.BUSINESS_ERROR

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 522])
    def test_server_statuses_are_network_errors(self, status: int) -> None:
        assert classify(RawResponse(status=status, body=b"")) is Outcome.NETWORK_ERROR

    def test_exception_is_a_network_error(self) -> None:
        result = classify_detailed(exception=TimeoutError("proxy timed out"))
        assert result.outcome is Outcome.NETWORK_ERROR
        assert result.rule == "exception.TimeoutError"

    def test_transport_failure_is_unwrapped(self) -> None:
        failure = TransportFailure(
            "boom",
            identity_id="id-1",
            url="https://www.douyin.com/x",
            elapsed_ms=3,
            cause=ConnectionResetError("peer reset"),
        )
        result = classify_detailed(exception=failure)
        assert result.outcome is Outcome.NETWORK_ERROR
        assert result.rule == "exception.ConnectionResetError"

    def test_unknown_exception_is_still_network_but_named(self) -> None:
        class WeirdError(Exception):
            pass

        result = classify_detailed(exception=WeirdError("?"))
        assert result.outcome is Outcome.NETWORK_ERROR
        assert result.rule == "exception.unclassified"

    def test_nothing_to_classify_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="response or an exception"):
            classify()

    def test_extend_returns_a_new_classifier(self) -> None:
        rule = ClassificationRule(
            "test.teapot",
            Outcome.RISK_CONTROL,
            lambda view: "teapot" if view.status == 418 else False,
        )
        base = Classifier()
        extended = base.extend([rule], first=True)

        teapot = RawResponse(status=418, body=b"{}")
        assert extended.classify(teapot) == Classification(
            Outcome.RISK_CONTROL, "test.teapot", "teapot"
        )
        assert base.classify(teapot).outcome is Outcome.BUSINESS_ERROR
        assert len(extended.rules) == len(base.rules) + 1


# ------------------------------------------------------------------- transport


class TestClientCache:
    async def test_one_client_per_identity_reused(self) -> None:
        transport, factory = make_transport()
        identity = make_identity()
        spec = RequestSpec(url="https://www.douyin.com/a", endpoint="aweme.detail")

        await transport.request(identity, spec)
        await transport.request(identity, spec)

        assert len(factory.built) == 1
        assert transport.client_count == 1
        assert len(factory.built[0].calls) == 2

    async def test_identities_never_share_a_client(self) -> None:
        transport, factory = make_transport()
        spec = RequestSpec(url="https://www.douyin.com/a")

        await transport.request(make_identity("id-1", proxy_url="http://p1:8080"), spec)
        await transport.request(make_identity("id-2", proxy_url="http://p2:8080"), spec)

        assert len(factory.built) == 2
        assert transport.client_count == 2
        assert [c.options.proxy_url for c in factory.built] == [
            "http://p1:8080",
            "http://p2:8080",
        ]

    async def test_changed_proxy_rebuilds_the_client(self) -> None:
        transport, factory = make_transport()
        spec = RequestSpec(url="https://www.douyin.com/a")

        await transport.request(make_identity("id-1", proxy_url="http://p1:8080"), spec)
        await transport.request(make_identity("id-1", proxy_url="http://p2:8080"), spec)

        assert len(factory.built) == 2
        assert factory.built[0].closed == 1
        assert transport.client_count == 1

    async def test_least_recently_used_client_is_evicted(self) -> None:
        transport, factory = make_transport(max_clients=2)
        spec = RequestSpec(url="https://www.douyin.com/a")

        await transport.request(make_identity("id-1"), spec)
        await transport.request(make_identity("id-2"), spec)
        await transport.request(make_identity("id-1"), spec)  # id-1 becomes recent
        await transport.request(make_identity("id-3"), spec)

        assert transport.client_count == 2
        assert transport.has_client("id-1")
        assert transport.has_client("id-3")
        assert not transport.has_client("id-2")
        assert factory.built[1].closed == 1

    async def test_explicit_eviction_closes_the_client(self) -> None:
        transport, factory = make_transport()
        await transport.request(make_identity("id-1"), RequestSpec(url="https://x/a"))

        await transport.evict("id-1")
        await transport.evict("id-1")  # idempotent

        assert transport.client_count == 0
        assert factory.built[0].closed == 1

    async def test_close_closes_every_client(self) -> None:
        transport, factory = make_transport()
        spec = RequestSpec(url="https://x/a")
        await transport.request(make_identity("id-1"), spec)
        await transport.request(make_identity("id-2"), spec)

        await transport.close()
        await transport.close()

        assert transport.client_count == 0
        assert all(client.closed == 1 for client in factory.built)

    async def test_stats_report_the_profile_in_use(self) -> None:
        transport, _ = make_transport()
        await transport.request(make_identity("id-1"), RequestSpec(url="https://x/a"))

        stats = transport.stats()
        assert stats.clients == 1
        assert stats.requests == 1
        assert stats.profiles == {"id-1": "Chrome149"}

    async def test_unemulatable_identity_never_gets_a_client(self) -> None:
        transport, factory = make_transport()
        identity = make_identity(fingerprint=Fingerprint(user_agent=CHROME_UA))

        with pytest.raises(UnknownBrowserFamily):
            await transport.request(identity, RequestSpec(url="https://x/a"))

        assert factory.built == []
        assert transport.client_count == 0


class TestRequestWiring:
    async def test_cookies_headers_and_timeout_are_wired(self) -> None:
        transport, factory = make_transport()
        identity = make_identity(cookies={"ttwid": "abc", "msToken": "def"})
        spec = RequestSpec(
            url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
            params={"aweme_id": "7"},
            headers={"Referer": "https://www.douyin.com/"},
            endpoint="douyin.aweme.detail",
        )

        await transport.request(identity, spec)

        call = factory.built[0].calls[0]
        assert call["method"] is wreq.Method.GET
        assert call["url"] == spec.url
        assert call["query"] == {"aweme_id": "7"}
        assert call["cookies"] == {"ttwid": "abc", "msToken": "def"}
        assert call["headers"]["User-Agent"] == CHROME_UA
        assert call["headers"]["Referer"] == "https://www.douyin.com/"
        assert call["headers"]["sec-ch-ua-platform"] == '"Windows"'
        assert call["timeout"] == timedelta(seconds=DEFAULT_TIMEOUT_SECONDS)

    async def test_empty_params_and_cookies_are_omitted(self) -> None:
        transport, factory = make_transport()
        await transport.request(make_identity(), RequestSpec(url="https://x/a"))

        call = factory.built[0].calls[0]
        assert "query" not in call
        assert "cookies" not in call
        assert "body" not in call

    async def test_json_body_wins_over_raw_body(self) -> None:
        transport, factory = make_transport()
        spec = RequestSpec(url="https://x/a", method="post", json_body={"a": 1}, body=b"ignored")
        await transport.request(make_identity(), spec)

        call = factory.built[0].calls[0]
        assert call["method"] is wreq.Method.POST
        assert call["json"] == {"a": 1}
        assert "body" not in call

    async def test_raw_body_is_passed_through(self) -> None:
        transport, factory = make_transport()
        spec = RequestSpec(url="https://x/a", method="POST", body=b"raw")
        await transport.request(make_identity(), spec)

        assert factory.built[0].calls[0]["body"] == b"raw"

    async def test_timeout_is_clamped(self) -> None:
        transport, factory = make_transport()
        await transport.request(make_identity(), RequestSpec(url="https://x/a"), timeout=10_000)

        assert factory.built[0].calls[0]["timeout"] == timedelta(seconds=MAX_TIMEOUT_SECONDS)

    async def test_non_positive_timeout_is_rejected(self) -> None:
        transport, _ = make_transport()
        with pytest.raises(InvalidParam):
            await transport.request(make_identity(), RequestSpec(url="https://x/a"), timeout=0)

    async def test_unknown_method_is_rejected(self) -> None:
        transport, _ = make_transport()
        with pytest.raises(InvalidParam):
            await transport.request(make_identity(), RequestSpec(url="https://x/a", method="FROB"))

    async def test_response_is_adapted(self) -> None:
        transport, factory = make_transport()
        identity = make_identity()
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].response = FakeResponse(
            status=404,
            body=b'{"status_code": 2053}',
            headers={"Content-Type": "application/json"},
            url="https://www.douyin.com/redirected",
        )

        raw = await transport.request(identity, RequestSpec(url="https://x/a"))

        assert raw.status == 404
        assert raw.headers["content-type"] == "application/json"
        assert raw.final_url == "https://www.douyin.com/redirected"
        assert raw.json() == {"status_code": 2053}
        assert raw.elapsed_ms >= 0
        assert transport.classify(raw).outcome is Outcome.BUSINESS_ERROR

    async def test_set_cookies_go_back_to_the_pool(self) -> None:
        sink = RecordingSink()
        transport, factory = make_transport(cookie_sink=sink)
        identity = make_identity(cookies={"ttwid": "old"})
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].response = FakeResponse(
            cookies=(FakeCookie("ttwid", "rotated"), FakeCookie("msToken", "fresh"))
        )

        await transport.request(identity, RequestSpec(url="https://x/a"))

        assert sink.calls == [("id-1", {"ttwid": "rotated", "msToken": "fresh"})]

    async def test_a_failing_sink_does_not_lose_the_response(self) -> None:
        async def broken_sink(identity_id: str, cookies: Mapping[str, str]) -> None:
            raise RuntimeError("database is down")

        transport, factory = make_transport(cookie_sink=broken_sink)
        identity = make_identity()
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].response = FakeResponse(
            body=b'{"status_code": 0, "aweme_detail": {"aweme_id": "7"}}',
            cookies=(FakeCookie("ttwid", "rotated"),),
        )

        raw = await transport.request(identity, RequestSpec(url="https://x/a"))

        assert transport.classify(raw).outcome is Outcome.OK

    async def test_no_cookies_no_sink_call(self) -> None:
        sink = RecordingSink()
        transport, _ = make_transport(cookie_sink=sink)
        await transport.request(make_identity(), RequestSpec(url="https://x/a"))

        assert sink.calls == []

    async def test_network_failure_raises_and_classifies(self) -> None:
        transport, factory = make_transport()
        identity = make_identity(proxy_url="http://dead:8080")
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].error = ConnectionResetError("peer reset")

        with pytest.raises(TransportFailure) as excinfo:
            await transport.request(identity, RequestSpec(url="https://x/a", endpoint="e"))

        failure = excinfo.value
        assert failure.identity_id == "id-1"
        assert isinstance(failure.cause, ConnectionResetError)
        assert transport.classify(exception=failure).outcome is Outcome.NETWORK_ERROR
        # The client stays: whether to retire it is the pool's decision.
        assert transport.client_count == 1

    async def test_rejects_a_zero_client_budget(self) -> None:
        with pytest.raises(InvalidParam):
            WreqTransport(max_clients=0)


class TestDefaultFactory:
    """The real wreq client is built here, but never used to reach the network."""

    def test_builds_a_client_with_a_proxy(self) -> None:
        options = ClientOptions(
            identity_id="id-1",
            emulation=wreq.Emulation.Chrome149,
            emulation_name="Chrome149",
            proxy_url="http://127.0.0.1:9",
            connect_timeout=5.0,
            pool_idle_timeout=30.0,
        )
        client = default_client_factory(options)
        try:
            assert isinstance(client, wreq.Client)
        finally:
            client.close()

    def test_builds_a_direct_client(self) -> None:
        options = ClientOptions(
            identity_id="id-2",
            emulation=wreq.Emulation.Firefox143,
            emulation_name="Firefox143",
            proxy_url=None,
            connect_timeout=5.0,
            pool_idle_timeout=30.0,
        )
        client = default_client_factory(options)
        try:
            assert isinstance(client, wreq.Client)
        finally:
            client.close()


# --------------------------------------------------------------------- secrets


class TestSecretsStayOutOfRepr:
    """docs/design/08-security.md: an identity in a traceback must be safe."""

    def test_identity_repr_hides_cookies_and_proxy_credentials(self) -> None:
        identity = TransportIdentity(
            id="id-1",
            platform=Platform.DOUYIN,
            fingerprint=CHROME_FP,
            proxy_url="http://acct-42:s3cr3t@proxy.example:8080",
            cookies={"sessionid": "LIVE-SESSION-VALUE", "ttwid": "TTWID-VALUE"},
        )

        text = repr(identity)

        assert "LIVE-SESSION-VALUE" not in text
        assert "TTWID-VALUE" not in text
        assert "s3cr3t" not in text
        assert "acct-42" not in text
        # Still useful: which cookies exist and which exit was in use.
        assert "sessionid" in text
        assert "proxy.example:8080" in text

    def test_client_options_repr_hides_proxy_credentials(self) -> None:
        options = ClientOptions(
            identity_id="id-1",
            emulation=wreq.Emulation.Chrome149,
            emulation_name="Chrome149",
            proxy_url="socks5://user:hunter2@exit.example:1080",
            connect_timeout=5.0,
            pool_idle_timeout=30.0,
        )

        text = repr(options)

        assert "hunter2" not in text
        assert "user:" not in text
        assert "exit.example:1080" in text

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("http://u:p@host:8080", "http://host:8080"),
            ("http://host:8080", "http://host:8080"),
            ("socks5://u:p@h:1080", "socks5://h:1080"),
            ("host:8080", "[redacted]"),
            (None, None),
            ("", ""),
        ],
    )
    def test_mask_proxy_url(self, url: str | None, expected: str | None) -> None:
        assert mask_proxy_url(url) == expected

    async def test_transport_failure_details_carry_no_query_string(self) -> None:
        # `DtkError.details` is serialized verbatim into the public API error
        # envelope, so a signed URL there would hand the caller live tokens.
        transport, factory = make_transport()
        identity = make_identity()
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].error = ConnectionResetError("peer reset")
        signed = "https://www.douyin.com/aweme/v1/web/aweme/detail/?a_bogus=SECRET&msToken=SECRET2"

        with pytest.raises(TransportFailure) as excinfo:
            await transport.request(identity, RequestSpec(url=signed))

        details = excinfo.value.details
        assert details["url"] == "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        assert "SECRET" not in json.dumps(details)
        # The full URL is still available internally for the log processor.
        assert excinfo.value.url == signed

    async def test_set_cookie_is_not_folded_into_the_header_map(self) -> None:
        transport, factory = make_transport()
        identity = make_identity()
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].response = FakeResponse(
            header_pairs=(
                (b"Set-Cookie", b"ttwid=v1; Expires=Tue, 01 Jan 2030 00:00:00 GMT"),
                (b"Set-Cookie", b"msToken=v2; Path=/"),
                (b"Content-Type", b"application/json"),
            )
        )

        raw = await transport.request(identity, RequestSpec(url="https://x/a"))

        assert "set-cookie" not in raw.headers
        assert raw.headers["content-type"] == "application/json"


# ------------------------------------------------------- raw response invariants


class TestRawResponseHeaderCasing:
    def test_mixed_case_headers_are_lowercased_by_the_constructor(self) -> None:
        # A response replayed from a cache or built in a fixture must behave
        # like one built by the transport.
        raw = RawResponse(status=200, headers={"Content-Type": "text/html; charset=gbk"})

        assert raw.headers == {"content-type": "text/html; charset=gbk"}
        assert raw.header("Content-Type") == "text/html; charset=gbk"
        assert raw.charset == "gbk"

    def test_non_utf8_json_envelope_is_still_read(self) -> None:
        # Escaped rather than literal so this file stays ASCII: the point is
        # only that the bytes are not valid UTF-8.
        message = "\u5df2\u5220\u9664"
        payload = {"status_code": 2053, "status_msg": message}
        raw = RawResponse(
            status=200,
            headers={"Content-Type": "application/json; charset=gbk"},
            body=json.dumps(payload, ensure_ascii=False).encode("gbk"),
        )

        assert raw.json_or_none() == payload
        assert classify(raw) is Outcome.BUSINESS_ERROR

    def test_binary_body_is_still_not_json(self) -> None:
        raw = RawResponse(status=200, headers={"content-type": "video/mp4"}, body=b"\x00\x01\x02")
        assert raw.json_or_none() is None


# ------------------------------------------------- classifier: proxy and no body


class TestClassifierEdges:
    @pytest.mark.parametrize("status", [407, 408])
    def test_proxy_and_timeout_statuses_are_network_errors(self, status: int) -> None:
        # 407 comes from the proxy, never from the platform; filing it as a
        # business error hides a dead exit from the proxy health probe.
        result = classify_detailed(RawResponse(status=status, body=b"proxy"))
        assert result.outcome is Outcome.NETWORK_ERROR
        assert result.rule == "http.network_status"

    @pytest.mark.parametrize("status", [204, 205])
    def test_bodyless_success_is_not_risk_control(self, status: int) -> None:
        assert classify(RawResponse(status=status, body=b"")) is Outcome.OK

    def test_an_ordinary_empty_200_is_still_risk_control(self) -> None:
        assert classify(RawResponse(status=200, body=b"")) is Outcome.RISK_CONTROL


# ------------------------------------------------------ client lifetime under load


class TestClientLifetime:
    async def test_capacity_eviction_never_closes_a_live_client(self) -> None:
        transport, factory = make_transport(max_clients=1)
        spec = RequestSpec(url="https://x/a")
        await transport.request(make_identity("id-1"), spec)  # warm the client
        factory.built[0].response = FakeResponse(body=b'{"status_code": 0}', body_delay=0.05)

        task = asyncio.ensure_future(transport.request(make_identity("id-1"), spec))
        await asyncio.sleep(0.01)  # id-1's request is on the wire

        # A different identity arriving is what pushes id-1 out of the cache.
        await transport.request(make_identity("id-2"), spec)
        assert not transport.has_client("id-1")
        assert factory.built[0].closed == 0

        raw = await task

        assert raw.status == 200
        # Closed exactly once, and only after the request finished.
        assert factory.built[0].closed == 1

    async def test_explicit_eviction_defers_the_close_until_the_request_ends(self) -> None:
        transport, factory = make_transport()
        identity = make_identity("id-1")
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].response = FakeResponse(body=b"{}", body_delay=0.05)

        task = asyncio.ensure_future(transport.request(identity, RequestSpec(url="https://x/a")))
        await asyncio.sleep(0.01)
        await transport.evict("id-1")
        assert factory.built[0].closed == 0

        await task
        assert factory.built[0].closed == 1

    async def test_close_is_not_repeated_when_several_requests_share_a_client(self) -> None:
        transport, factory = make_transport()
        identity = make_identity("id-1")
        await transport.request(identity, RequestSpec(url="https://x/a"))
        factory.built[0].response = FakeResponse(body=b"{}", body_delay=0.02)

        tasks = [
            asyncio.ensure_future(transport.request(identity, RequestSpec(url="https://x/a")))
            for _ in range(3)
        ]
        await asyncio.sleep(0.005)
        await transport.close()
        await asyncio.gather(*tasks)

        assert factory.built[0].closed == 1

    async def test_a_failed_body_read_closes_the_response(self) -> None:
        transport, factory = make_transport()
        identity = make_identity()
        await transport.request(identity, RequestSpec(url="https://x/a"))
        broken = FakeResponse(body_error=ConnectionResetError("body truncated"))
        factory.built[0].response = broken

        with pytest.raises(TransportFailure):
            await transport.request(identity, RequestSpec(url="https://x/a"))

        assert broken.closed == 1
        # The client itself survives; retiring it is the pool's decision.
        assert transport.client_count == 1
