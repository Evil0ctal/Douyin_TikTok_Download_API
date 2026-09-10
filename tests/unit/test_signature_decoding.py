"""The decoder, checked against the generators it inverts - and against a browser.

The round-trip tests are necessary and not sufficient: a decoder written from the
same misunderstanding as the generator round-trips perfectly and is still wrong.
:class:`TestBrowserCapture` is the one that settles it, because those signatures
were produced by Douyin's own ``bdms.js`` and nothing in this repository chose
what is inside them.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from dtk.core.types import Platform
from dtk.signing import SigningSession, native_signers
from dtk.signing.base import RequestSpec, StaticFingerprint
from dtk.signing.native import abogus, decoding, tiktok_sign, tokens, websign, xbogus

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
)
QUERY = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&aweme_id=7300000000000000000&pc_client_type=1&version_code=290100"
)

#: A pinned clock, for the same reason `test_signing.AB_NOW_MS` is pinned, and
#: it has to be repeated rather than shared because this file is about the
#: decoder rather than the signer.
#:
#: The SDK's 3->4 expansion has a tail branch that drops a trailing zero byte -
#: recovered from the bundle, where `b` is a byte array and `if (b[i+1])` is
#: therefore falsy on zero exactly as in Python. So when the body length leaves
#: a remainder of two, which is the tail's clock byte printing as two digits,
#: and the checksum happens to be zero, the checksum cannot be recovered and
#: `structure_error` correctly reports the declared lengths overrunning the
#: frame. Measured: 1 in 690.
#:
#: That is a property of the format, not a defect, and `ABogusComparator` is
#: built around it. Left unpinned here it is a test that fails one run in a few
#: hundred and reads like flake, which is how a real regression would end up
#: dismissed. This clock's tail is three digits.
NOW_MS = 1789010742989

FIXTURE = pathlib.Path(__file__).parents[1] / "fixtures" / "signing" / "abogus_browser.json"


def _fields(decoded: decoding.Decoded) -> dict[str, str]:
    return {item.name: item.value for item in decoded.fields}


def _checks(decoded: decoding.Decoded) -> dict[str, str]:
    return {item.name: item.status for item in decoded.checks}


class TestABogus:
    def test_a_signature_decodes_to_the_constants_that_made_it(self) -> None:
        decoded = decoding.decode_a_bogus(abogus.ABogus(CHROME).get_value(QUERY, now_ms=NOW_MS))
        fields = _fields(decoded)
        assert decoded.recovered
        assert fields["aid"] == str(abogus.AID)
        assert fields["page_id"] == str(abogus.PAGE_ID)
        assert fields["browser_info"] == abogus.DEFAULT_BROWSER_INFO
        assert "checksum_verified" in decoded.notes

    def test_the_three_chains_confirm_the_inputs_that_produced_them(self) -> None:
        value = abogus.ABogus(CHROME).get_value(QUERY, now_ms=NOW_MS)
        decoded = decoding.decode_a_bogus(value, query=QUERY, body="", user_agent=CHROME)
        assert _checks(decoded) == {
            "query": decoding.CHECK_MATCH,
            "body": decoding.CHECK_MATCH,
            "user_agent": decoding.CHECK_MATCH,
        }

    @pytest.mark.parametrize("changed", ["query", "user_agent"])
    def test_a_different_input_is_reported_as_different(self, changed: str) -> None:
        value = abogus.ABogus(CHROME).get_value(QUERY, now_ms=NOW_MS)
        inputs = {"query": QUERY, "user_agent": CHROME}
        inputs[changed] += "x"
        decoded = decoding.decode_a_bogus(value, body="", **inputs)  # type: ignore[arg-type]
        assert _checks(decoded)[changed] == decoding.CHECK_DIFFERS

    def test_an_unsupplied_input_is_not_reported_as_a_mismatch(self) -> None:
        """The distinction the whole panel rests on: unknown is not wrong."""
        decoded = decoding.decode_a_bogus(abogus.ABogus(CHROME).get_value(QUERY, now_ms=NOW_MS))
        assert set(_checks(decoded).values()) == {decoding.CHECK_NOT_SUPPLIED}

    def test_the_clock_comes_back_as_the_one_that_went_in(self) -> None:
        stamp = 1_780_000_000_123
        value = abogus.ABogus(CHROME).get_value(QUERY, now_ms=stamp)
        assert _fields(decoding.decode_a_bogus(value))["now_ms"].startswith(f"{stamp} (")

    def test_the_screen_a_fingerprint_claims_is_what_comes_back(self) -> None:
        info = "1512|778|1512|860|1512|860|1512|982|MacIntel"
        value = abogus.ABogus(CHROME, browser_info=info).get_value(QUERY, now_ms=NOW_MS)
        assert _fields(decoding.decode_a_bogus(value))["browser_info"] == info

    @pytest.mark.parametrize("junk", ["", "hello", "%%%%", "a" * 400, "=" * 8])
    def test_junk_is_refused_rather_than_raised(self, junk: str) -> None:
        decoded = decoding.decode_a_bogus(junk)
        assert not decoded.recovered
        assert decoded.reason is not None
        assert decoded.reason.startswith(decoding.REASON_MALFORMED)


class TestBrowserCapture:
    """The oracle. Real signatures, produced by Douyin, decoded here."""

    @pytest.fixture(scope="class")
    def samples(self) -> list[dict[str, str]]:
        return list(json.loads(FIXTURE.read_text())["samples"])

    def test_every_capture_decodes_and_confirms_its_own_inputs(
        self, samples: list[dict[str, str]]
    ) -> None:
        assert samples
        for sample in samples:
            decoded = decoding.decode_a_bogus(
                sample["a_bogus"],
                query=sample["query"],
                body=sample["body"],
                user_agent=sample["user_agent"],
            )
            assert decoded.recovered, sample["a_bogus"][:24]
            assert _checks(decoded) == {
                "query": decoding.CHECK_MATCH,
                "body": decoding.CHECK_MATCH,
                "user_agent": decoding.CHECK_MATCH,
            }

    def test_the_screen_read_back_is_the_screen_the_page_reported(
        self, samples: list[dict[str, str]]
    ) -> None:
        for sample in samples:
            decoded = decoding.decode_a_bogus(sample["a_bogus"])
            assert _fields(decoded)["browser_info"] == sample["browser_info"]

    def test_the_clock_read_back_agrees_with_the_timestamp_sent_beside_it(
        self, samples: list[dict[str, str]]
    ) -> None:
        """Nothing in the decoder is told the timestamp; it comes out of the blob."""
        for sample in samples:
            decoded = decoding.decode_a_bogus(sample["a_bogus"])
            recovered = int(_fields(decoded)["now_ms"].split(" ", 1)[0])
            # The sibling is the `timestamp` query parameter, in seconds; the
            # blob's own clock is in milliseconds.
            assert abs(recovered // 1000 - int(sample["sibling_timestamp"])) <= 5


class TestTikTok:
    @pytest.fixture(scope="class")
    def signed(self) -> tuple[str, dict[str, str]]:
        pairs = [("aid", "1988"), ("app_language", "en"), ("secUid", "MS4wLjABAAAA")]
        return tiktok_sign.sign(pairs, CHROME, ms_token="")

    def test_x_gnarly_recomputes_its_own_two_checksums(
        self, signed: tuple[str, dict[str, str]]
    ) -> None:
        """Every input to the fold is itself a field, so agreement proves all 16."""
        decoded = decoding.decode_x_gnarly(signed[1][tiktok_sign.GNARLY_PARAM])
        assert decoded.recovered
        assert "checksum_verified" in decoded.notes
        assert "mixed_verified" in decoded.notes

    def test_x_gnarly_confirms_the_query_it_sealed(
        self, signed: tuple[str, dict[str, str]]
    ) -> None:
        query, params = signed
        covered = query.partition(f"&{tiktok_sign.BOGUS_PARAM}=")[0]
        decoded = decoding.decode_x_gnarly(
            params[tiktok_sign.GNARLY_PARAM], query=covered, user_agent=CHROME, body=b""
        )
        assert _checks(decoded) == {
            "query": decoding.CHECK_MATCH,
            "user_agent": decoding.CHECK_MATCH,
            "body": decoding.CHECK_MATCH,
        }

    def test_x_gnarly_rejects_the_business_query_alone(
        self, signed: tuple[str, dict[str, str]]
    ) -> None:
        """The boundary that is worth being able to see: the seal covers more."""
        query, params = signed
        business = query.partition(f"&{tiktok_sign.DYNOSAUR_PARAM}=")[0]
        decoded = decoding.decode_x_gnarly(params[tiktok_sign.GNARLY_PARAM], query=business)
        assert _checks(decoded)["query"] == decoding.CHECK_DIFFERS

    def test_x_dynosaur_reports_the_versions_and_codes_it_was_built_with(
        self, signed: tuple[str, dict[str, str]]
    ) -> None:
        decoded = decoding.decode_x_dynosaur(signed[1][tiktok_sign.DYNOSAUR_PARAM])
        fields = _fields(decoded)
        assert decoded.recovered
        assert fields["sdk_version"] == tiktok_sign.SDK_VERSION
        assert fields["scm_version"] == tiktok_sign.SCM_VERSION
        assert fields["env_code"] == str(tiktok_sign.ENV_CODE)
        assert fields["ub_code"] == str(tiktok_sign.UB_CODE)
        assert fields["page"] == tiktok_sign.PAGE

    def test_x_dynosaur_confirms_the_business_query_and_not_the_sealed_one(
        self, signed: tuple[str, dict[str, str]]
    ) -> None:
        query, params = signed
        business = query.partition(f"&{tiktok_sign.DYNOSAUR_PARAM}=")[0]
        decoded = decoding.decode_x_dynosaur(
            params[tiktok_sign.DYNOSAUR_PARAM], query=business, user_agent=CHROME, body=""
        )
        assert _checks(decoded)["query"] == decoding.CHECK_MATCH
        assert _checks(decoded)["user_agent"] == decoding.CHECK_MATCH

    def test_the_x_bogus_tiktok_sends_is_named_as_a_constant_not_a_fault(self) -> None:
        decoded = decoding.decode_x_bogus(tiktok_sign.BOGUS_VALUE)
        assert decoded.recovered
        assert decoded.platform == "tiktok"
        assert "constant_placeholder" in decoded.notes

    @pytest.mark.parametrize("junk", ["", "not-a-signature", "AAAA"])
    def test_junk_is_refused_rather_than_raised(self, junk: str) -> None:
        assert not decoding.decode_x_gnarly(junk).recovered
        assert not decoding.decode_x_dynosaur(junk).recovered


class TestXBogus:
    def test_the_payload_survives_the_round_trip(self) -> None:
        value = xbogus.XBogus(EDGE).sign(QUERY, timestamp=1_780_000_000)
        decoded = decoding.decode_x_bogus(value, query=QUERY, user_agent=EDGE)
        fields = _fields(decoded)
        assert decoded.recovered
        assert fields["timestamp"].startswith("1780000000000 (")
        assert fields["canvas_constant"] == str(xbogus.CANVAS_CONSTANT)
        assert fields["checksum"].isdigit()
        assert _checks(decoded) == {
            "query": decoding.CHECK_MATCH,
            "user_agent": decoding.CHECK_MATCH,
        }

    def test_a_candidate_too_short_to_hash_is_reported_as_different(self) -> None:
        """The site's own hex/text switch cannot hash a short string. Not a crash."""
        value = xbogus.XBogus(EDGE).sign(QUERY)
        decoded = decoding.decode_x_bogus(value, query="a=1", user_agent="x")
        assert set(_checks(decoded).values()) == {decoding.CHECK_DIFFERS}


class TestTheOtherParameters:
    def test_websign_is_named_one_way_and_still_rebuilds_its_preimage(self) -> None:
        pairs = [("aid", "6383"), ("aweme_id", "7300000000000000000")]
        query, signature, headers = websign.sign(pairs, "abc123def456")
        covered = query.rpartition(f"&{websign.SIGNATURE_PARAM}=")[0]
        decoded = decoding.decode_websign(
            signature,
            query=covered,
            uifid="abc123def456",
            stamp=headers[websign.EXPIRE_HEADER],
        )
        assert not decoded.recovered
        assert decoded.reason == decoding.REASON_ONE_WAY
        check = decoded.checks[0]
        assert check.status == decoding.CHECK_MATCH
        assert check.covered is not None
        assert websign.SALT in check.covered

    def test_a_wrong_preimage_is_never_published(self) -> None:
        decoded = decoding.decode_websign(
            "0" * 32, query="aid=6383", uifid="abc", stamp="1780000000"
        )
        assert decoded.checks[0].status == decoding.CHECK_DIFFERS
        assert decoded.checks[0].covered is None

    def test_ms_token_says_it_is_issued_rather_than_failing_to_decode(self) -> None:
        decoded = decoding.decode_ms_token(tokens.gen_false_ms_token())
        assert not decoded.recovered
        assert decoded.reason == decoding.REASON_NOT_COMPUTED
        assert decoded.platform == "douyin"
        assert _fields(decoded)["alphabet"] == "matches"

    def test_a_tiktok_length_token_is_named_as_one(self) -> None:
        token = tokens.gen_false_ms_token(tokens.TIKTOK_MS_TOKEN_LENGTH)
        assert decoding.decode_ms_token(token).platform == "tiktok"

    def test_verify_fp_yields_the_moment_it_was_minted(self) -> None:
        value = tokens.gen_verify_fp(now_ms=1_780_000_000_123)
        decoded = decoding.decode_verify_fp(value)
        assert decoded.recovered
        assert _fields(decoded)["minted"].startswith("1780000000123 (")


class TestIdentify:
    def test_each_parameter_is_recognised_from_its_value_alone(self) -> None:
        _query, params = tiktok_sign.sign([("aid", "1988")], CHROME, ms_token="")
        assert decoding.identify(abogus.ABogus(CHROME).get_value(QUERY, now_ms=NOW_MS)) == "a_bogus"
        assert decoding.identify(xbogus.XBogus(EDGE).sign(QUERY)) == "X-Bogus"
        assert decoding.identify(params[tiktok_sign.GNARLY_PARAM]) == tiktok_sign.GNARLY_PARAM
        assert decoding.identify(params[tiktok_sign.DYNOSAUR_PARAM]) == tiktok_sign.DYNOSAUR_PARAM
        assert decoding.identify(tokens.gen_verify_fp()) == "verifyFp"
        assert decoding.identify("0" * 32) == websign.SIGNATURE_PARAM
        assert decoding.identify(tokens.gen_false_ms_token()) == tiktok_sign.MS_TOKEN_PARAM

    @pytest.mark.parametrize("junk", ["", "   ", "hello world", "1"])
    def test_an_unrecognised_value_is_not_guessed_at(self, junk: str) -> None:
        assert decoding.identify(junk) is None


class TestDecodeUrl:
    def test_a_signed_tiktok_url_yields_every_parameter_confirmed(self) -> None:
        pairs = [("aid", "1988"), ("app_language", "en")]
        query, _ = tiktok_sign.sign(pairs, CHROME, ms_token="")
        found = {
            item.parameter: item
            for item in decoding.decode_url(f"/api/?{query}", user_agent=CHROME)
        }
        assert set(found) >= {
            tiktok_sign.DYNOSAUR_PARAM,
            tiktok_sign.GNARLY_PARAM,
            tiktok_sign.BOGUS_PARAM,
        }
        for name in (tiktok_sign.DYNOSAUR_PARAM, tiktok_sign.GNARLY_PARAM):
            assert _checks(found[name])["query"] == decoding.CHECK_MATCH
            assert _checks(found[name])["user_agent"] == decoding.CHECK_MATCH

    def test_a_signed_douyin_url_yields_a_bogus_and_the_web_signature(self) -> None:
        """Through the real signer, which reads the clock and cannot be pinned.

        Signed twice when the first lands on the tail case documented on
        `NOW_MS` - the same thing `SignerRegistry` does before it believes a
        mismatch, and for the same reason: two independent draws never both fall
        in it, so a retry tells a one-in-690 format property apart from a
        regression instead of leaving the test to fail occasionally.
        """
        base = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        params = {"device_platform": "webapp", "aid": "6383", "aweme_id": "7300000000000000000"}
        jar = {"UIFID_TEMP": "abc123def456", websign.VERIFY_FP_COOKIE: tokens.gen_verify_fp()}
        signer = native_signers()[Platform.DOUYIN]

        def sign_once() -> dict[str, decoding.Decoded]:
            signed = asyncio.run(
                signer.sign(
                    RequestSpec(method="GET", url=base, params=params),
                    StaticFingerprint(user_agent=CHROME, browser_platform="Win32"),
                    SigningSession(cookies=jar),
                )
            )
            return {
                item.parameter: item
                for item in decoding.decode_url(signed.signed_url(base), user_agent=CHROME)
            }

        found = sign_once()
        if not found["a_bogus"].recovered:
            found = sign_once()
        assert _checks(found["a_bogus"])["query"] == decoding.CHECK_MATCH
        assert _checks(found["a_bogus"])["user_agent"] == decoding.CHECK_MATCH
        assert _checks(found[websign.SIGNATURE_PARAM])["query"] == decoding.CHECK_MATCH

    def test_a_percent_encoded_a_bogus_is_decoded_before_it_is_read(self) -> None:
        """It travels escaped, because its alphabet contains `/` and `-`."""
        from urllib.parse import quote

        value = abogus.ABogus(CHROME).get_value(QUERY, now_ms=NOW_MS)
        url = f"https://www.douyin.com/x?{QUERY}&a_bogus={quote(value, safe='')}"
        found = decoding.decode_url(url, user_agent=CHROME)
        assert [item.recovered for item in found] == [True]
        assert _checks(found[0])["query"] == decoding.CHECK_MATCH

    def test_an_unsigned_url_yields_nothing_rather_than_a_guess(self) -> None:
        assert decoding.decode_url("https://www.douyin.com/video/7300000000000000000") == []
