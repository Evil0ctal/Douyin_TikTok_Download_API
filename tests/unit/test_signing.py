"""Unit tests for request signing.

V4 shipped 880 lines of signature algorithm with zero tests; this file is the
regression net that was missing.

About the pinned fixtures
-------------------------
Every hard-coded signature below was generated once by this repository's own
implementation and pasted in. They pin *behaviour*, not correctness: they will
catch any future refactor that changes a byte, which is the failure V4 could not
detect. They do NOT prove the platform accepts the value.

Two independent checks back them up:

* The ports were diffed against the V4 originals in ``vendor_salvage/`` across
  several User-Agents, queries, timestamps and methods before these values were
  frozen, so they carry V4's production-tested behaviour forward unchanged.
* :func:`test_ua_code_matches_the_value_v4_hard_coded` pins the one place where
  V4 had a magic constant instead of a derivation.

Validating the signatures against the live site is a browser task
(docs/design/16-salvage-and-debug.md, task 3: sign the same query in the page and
compare digit by digit). It cannot be done offline and is deliberately out of
scope here. When it is done, the fixtures below become the offline half of the
shadow comparison.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import SimpleNamespace
from typing import ClassVar, cast
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit

import httpx
import pytest

from dtk.core.config import RUNTIME_SETTINGS
from dtk.core.errors import SigningFailed, UpstreamChanged
from dtk.core.types import Outcome, Platform
from dtk.signing.base import (
    SIGNER_BROWSER,
    SIGNER_NATIVE,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    SignerHealth,
    SigningFingerprint,
    SigningSession,
    StaticFingerprint,
    endpoint_of,
    platform_of,
)
from dtk.signing.native import tiktok_sign, websign
from dtk.signing.native.abogus import (
    ALPHABETS,
    DEFAULT_BROWSER_INFO,
    FRAME_BROWSER_LEN_INDEX,
    LEGACY_UA_CODE,
    PAYLOAD_KEY,
    PREFIX_MASKS,
    STRUCTURED_PREFIX_LEN,
    ABogus,
    build_browser_info,
    decode_base64,
    encode_base64,
    generate_ua_code,
    rc4_encrypt,
    structure_error,
)
from dtk.signing.native.signer import (
    BROWSER_CHROME_PX,
    MS_TOKEN_PARAM,
    NativeSigner,
    browser_info_for,
    native_signers,
)
from dtk.signing.native.sm3 import sm3_hash, sm3_hexdigest, sm3_to_array
from dtk.signing.native.tokens import (
    DOUYIN_MS_TOKEN_LENGTH,
    DOUYIN_TTWID,
    MS_TOKEN_ALPHABET,
    TIKTOK_MS_TOKEN_LENGTH,
    VERIFY_FP_ALPHABET,
    MsTokenSpec,
    gen_false_ms_token,
    gen_ms_token,
    gen_odin_tt,
    gen_real_ms_token,
    gen_s_v_web_id,
    gen_ttwid,
    gen_verify_fp,
)
from dtk.signing.native.xbogus import CHARACTER, X_BOGUS_LENGTH, XBogus
from dtk.signing.protection import requires_browser_signature
from dtk.signing.registry import (
    ABogusComparator,
    Comparison,
    ExactComparator,
    RegistryPolicy,
    RiskSample,
    SignerRegistry,
    SigningMode,
    SlidingRiskWindow,
)
from dtk.signing.rpc import RpcSigner

# --------------------------------------------------------------------------
# shared inputs
# --------------------------------------------------------------------------

UA_CHROME90 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
)
UA_EDGE122 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
)
UA_SAFARI17 = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
USER_AGENTS = {"chrome90": UA_CHROME90, "edge122": UA_EDGE122, "safari17": UA_SAFARI17}

DETAIL_QUERY = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1"
    "&version_code=190500&version_name=19.5.0&cookie_enabled=true&browser_language=zh-CN"
    "&browser_platform=Win32&browser_name=Firefox&browser_online=true&engine_name=Gecko"
    "&os_name=Windows&os_version=10&platform=PC&screen_width=1920&screen_height=1080"
    "&browser_version=124.0&engine_version=122.0.0.0&cpu_core_num=12&device_memory=8"
    "&aweme_id=7345492945006595379"
)
POST_QUERY = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&sec_user_id=MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y"
    "&max_cursor=0&count=18&version_code=170400&version_name=17.4.0"
)
QUERIES = {"detail": DETAIL_QUERY, "post": POST_QUERY}

#: Pinned A-Bogus inputs. Every fixture below uses these, so only the variable
#: under test changes between rows.
AB_START_MS = 1700000000000
AB_END_MS = 1700000000006
AB_SEEDS = (1.5, 2.5, 3.5)

FINGERPRINT = StaticFingerprint(
    user_agent=UA_CHROME90,
    browser_platform="Win32",
    screen_width=1920,
    screen_height=1080,
)


# --------------------------------------------------------------------------
# SM3
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "digest"),
    [
        # GB/T 32905-2016 appendix A, and the value gmssl produces for both.
        (b"abc", "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"),
        (b"abcd" * 16, "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"),
    ],
)
def test_sm3_matches_the_published_vectors(message: bytes, digest: str) -> None:
    assert sm3_hexdigest(message) == digest


def test_sm3_pads_across_a_block_boundary() -> None:
    """55, 56 and 64 byte messages take the three different padding paths."""
    for size in (0, 55, 56, 57, 63, 64, 65, 119, 120):
        assert len(sm3_hash(b"x" * size)) == 32
    assert sm3_hash(b"x" * 56) != sm3_hash(b"x" * 57)


def test_sm3_to_array_accepts_text_bytes_and_integers() -> None:
    assert sm3_to_array("abc") == list(sm3_hash(b"abc"))
    assert sm3_to_array(b"abc") == list(sm3_hash(b"abc"))
    assert sm3_to_array([97, 98, 99]) == list(sm3_hash(b"abc"))


# --------------------------------------------------------------------------
# X-Bogus
# --------------------------------------------------------------------------

#: (user agent, query, unix seconds) -> signature. Self-generated; see the
#: module docstring.
X_BOGUS_FIXTURES: list[tuple[str, str, int, str]] = [
    ("chrome90", "detail", 1700000000, "DFSzswVYcuXANGVRtmWx-e9WX7r9"),
    ("chrome90", "detail", 1893456000, "DFSzswVYcuXANGVRV9SWae9WX7rd"),
    ("chrome90", "post", 1700000000, "DFSzswVYf2UANGVRtmWx-e9WX7jM"),
    ("chrome90", "post", 1893456000, "DFSzswVYf2UANGVRV9SWae9WX7ja"),
    ("edge122", "detail", 1700000000, "DFSzswVYcuXANxTQtmWx-e9WX7JE"),
    ("edge122", "detail", 1893456000, "DFSzswVYcuXANxTQV9SWae9WX7J6"),
    ("edge122", "post", 1700000000, "DFSzswVYf2UANxTQtmWx-e9WX7n2"),
    ("edge122", "post", 1893456000, "DFSzswVYf2UANxTQV9SWae9WX7nz"),
    ("safari17", "detail", 1700000000, "DFSzswVYcuXANGfTtmWx-e9WX7r0"),
    ("safari17", "detail", 1893456000, "DFSzswVYcuXANGfTV9SWae9WX7rG"),
    ("safari17", "post", 1700000000, "DFSzswVYf2UANGfTtmWx-e9WX7jy"),
    ("safari17", "post", 1893456000, "DFSzswVYf2UANGfTV9SWae9WX7jD"),
]


@pytest.mark.parametrize(("ua_key", "query_key", "timestamp", "expected"), X_BOGUS_FIXTURES)
def test_x_bogus_pinned_fixtures(
    ua_key: str, query_key: str, timestamp: int, expected: str
) -> None:
    assert XBogus(USER_AGENTS[ua_key]).sign(QUERIES[query_key], timestamp=timestamp) == expected


def test_x_bogus_is_deterministic_for_the_same_second() -> None:
    signer = XBogus(UA_CHROME90)
    first = signer.sign(DETAIL_QUERY, timestamp=1700000000)
    second = XBogus(UA_CHROME90).sign(DETAIL_QUERY, timestamp=1700000000)
    assert first == second


def test_x_bogus_changes_with_the_user_agent() -> None:
    values = {
        name: XBogus(ua).sign(DETAIL_QUERY, timestamp=1700000000)
        for name, ua in USER_AGENTS.items()
    }
    assert len(set(values.values())) == len(values)


def test_x_bogus_changes_with_the_query_and_the_timestamp() -> None:
    signer = XBogus(UA_CHROME90)
    base = signer.sign(DETAIL_QUERY, timestamp=1700000000)
    assert signer.sign(POST_QUERY, timestamp=1700000000) != base
    assert signer.sign(DETAIL_QUERY, timestamp=1700000001) != base


def test_x_bogus_shape() -> None:
    value = XBogus(UA_CHROME90).sign(DETAIL_QUERY, timestamp=1700000000)
    assert len(value) == X_BOGUS_LENGTH
    assert set(value) <= set(CHARACTER)


def test_x_bogus_sign_query_appends_without_escaping() -> None:
    signed = XBogus(UA_CHROME90).sign_query(DETAIL_QUERY, timestamp=1700000000)
    value = XBogus(UA_CHROME90).sign(DETAIL_QUERY, timestamp=1700000000)
    assert signed == f"{DETAIL_QUERY}&X-Bogus={value}"


def test_x_bogus_default_user_agent_is_used_when_none_is_given() -> None:
    assert XBogus(None).sign(DETAIL_QUERY, timestamp=1) == XBogus("").sign(
        DETAIL_QUERY, timestamp=1
    )


def test_x_bogus_rejects_a_short_non_hex_input() -> None:
    """V4 failed here with ``TypeError: unsupported operand for |: int and NoneType``."""
    with pytest.raises(ValueError, match="hex"):
        XBogus(UA_CHROME90).sign("a=1", timestamp=1700000000)


def test_x_bogus_split_and_interleave_round_trip() -> None:
    values: list[int | float] = [*range(18), 0.5]
    restored = XBogus.interleave(XBogus.split_even_odd(values))
    assert restored == [*range(18), 0]


# --------------------------------------------------------------------------
# A-Bogus
# --------------------------------------------------------------------------

#: (user agent, query, method) -> signature, with time and noise pinned to
#: AB_START_MS / AB_END_MS / AB_SEEDS. Self-generated; see the module docstring.
A_BOGUS_FIXTURES: list[tuple[str, str, str, str]] = [
    (
        "chrome90",
        "detail",
        "GET",
        "Df8hQD8DDDDpDf6D56KLfY3q6VWVYmQI0SVkMD2fn-DOqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzbD==",
    ),
    (
        "chrome90",
        "detail",
        "POST",
        "Df8hQD8DDDDpDf6D56KLfY3q6VlHYmQI0SVkMD2fnWfOqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzWf==",
    ),
    (
        "chrome90",
        "post",
        "GET",
        "Df8hQD8DDDDpDf6D56KLfY3q6l6VYmQI0SVkMD2ffBDOqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzKj==",
    ),
    (
        "chrome90",
        "post",
        "POST",
        "Df8hQD8DDDDpDf6D56KLfY3q6l1HYmQI0SVkMD2ff8fOqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzQE==",
    ),
    (
        "edge122",
        "detail",
        "GET",
        "Df8hQD8DDDDpDf6D56KLfY3q6VWVYkQI0SVkMD2fn-dVqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLznD==",
    ),
    (
        "edge122",
        "post",
        "POST",
        "Df8hQD8DDDDpDf6D56KLfY3q6l1HYkQI0SVkMD2ff8VVqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzDE==",
    ),
    (
        "safari17",
        "detail",
        "GET",
        "Df8hQD8DDDDpDf6D56KLfY3q6VWVYDQI0SVkMD2fn-pSqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzzE==",
    ),
    (
        "safari17",
        "post",
        "POST",
        "Df8hQD8DDDDpDf6D56KLfY3q6l1HYDQI0SVkMD2ff83SqL39HMY29exoIBGvXY8jwG/-IeEjy4hbT3ohrQ2y0Hwf9W0L/25ksDSkKl5Q5xSSs1X9eghgJ04qmkt5SMx2RvB-rOXmqhZHKRbp09oHmhK4b1dzFgf3qJLzlD==",
    ),
]

#: Same inputs as the first fixture row, but with the browser geometry derived
#: from FINGERPRINT instead of V4's constant. Pins that the fingerprint reaches
#: the signature at all - the bug this port fixes.
A_BOGUS_WITH_FINGERPRINT = "Df8hQD8DDDDpDf6D56KLfY3q6VWVYmQI0SVkMD2fn-DOqL39HMY29exoIBGvXY8jwG/-IeEjy4hbO3xprQC7M1wf7Wsx/2CZQg00t-P2so0j53intL6mE0hN4kb3SFlm5XNAEOJ0y75nFmT0WocamhK4bfebY7Y6i6trbD=="


def sign_a_bogus(
    user_agent: str, query: str, method: str = "GET", browser_info: str | None = None
) -> str:
    return ABogus(user_agent, browser_info=browser_info).get_value(
        query, method, AB_START_MS, AB_END_MS, *AB_SEEDS
    )


@pytest.mark.parametrize(("ua_key", "query_key", "method", "expected"), A_BOGUS_FIXTURES)
def test_a_bogus_pinned_fixtures(ua_key: str, query_key: str, method: str, expected: str) -> None:
    assert sign_a_bogus(USER_AGENTS[ua_key], QUERIES[query_key], method) == expected


def test_a_bogus_pinned_fixture_with_a_fingerprint_derived_browser() -> None:
    value = sign_a_bogus(
        UA_CHROME90,
        DETAIL_QUERY,
        "GET",
        browser_info=browser_info_for(FINGERPRINT),
    )
    assert value == A_BOGUS_WITH_FINGERPRINT


def test_ua_code_matches_the_value_v4_hard_coded() -> None:
    """V4 pasted this array in and commented the derivation out.

    Restoring the derivation is only safe if it reproduces the array for the
    User-Agent V4 pinned it to, which is what this asserts.
    """
    assert generate_ua_code(UA_CHROME90) == list(LEGACY_UA_CODE)
    assert ABogus(UA_CHROME90).ua_code == list(LEGACY_UA_CODE)


def test_a_bogus_is_deterministic_when_time_and_noise_are_pinned() -> None:
    first = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    second = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    assert first == second


def test_a_bogus_changes_with_the_user_agent() -> None:
    values = {name: sign_a_bogus(ua, DETAIL_QUERY) for name, ua in USER_AGENTS.items()}
    assert len(set(values.values())) == len(values)


def test_a_bogus_changes_with_method_query_time_and_browser() -> None:
    base = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    assert sign_a_bogus(UA_CHROME90, DETAIL_QUERY, "POST") != base
    assert sign_a_bogus(UA_CHROME90, POST_QUERY) != base
    assert (
        sign_a_bogus(
            UA_CHROME90,
            DETAIL_QUERY,
            browser_info="800|600|800|600|0|0|0|0|800|600|800|600|800|600|24|24|Win32",
        )
        != base
    )
    later = ABogus(UA_CHROME90).get_value(
        DETAIL_QUERY, "GET", AB_START_MS + 1, AB_END_MS, *AB_SEEDS
    )
    assert later != base


def test_a_bogus_noise_actually_varies_the_output() -> None:
    """Without pinned seeds two signatures of the same request must differ."""
    signer = ABogus(UA_CHROME90, rng=random.Random(3))
    first = signer.get_value(DETAIL_QUERY, "GET", AB_START_MS, AB_END_MS)
    second = signer.get_value(DETAIL_QUERY, "GET", AB_START_MS, AB_END_MS)
    assert first != second


def test_a_bogus_shape() -> None:
    value = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    # The alphabet itself has no padding character; "=" only ever trails.
    assert set(value.rstrip("=")) <= set(ALPHABETS["s4"])
    assert len(value) % 4 == 0
    assert value.endswith("==")
    assert len(value) == 168  # 12 noise + 44 frame + 67 browser + 1 checksum bytes


def test_a_bogus_accepts_a_mapping_and_encodes_it_like_a_form() -> None:
    params = {"aid": "6383", "keyword": "hello world", "count": "20"}
    from_mapping = ABogus(UA_CHROME90).get_value(params, "GET", AB_START_MS, AB_END_MS, *AB_SEEDS)
    from_string = sign_a_bogus(UA_CHROME90, urlencode(params))
    assert from_mapping == from_string


def test_a_bogus_payload_prefix_satisfies_the_documented_masks() -> None:
    """The invariant the shadow comparator relies on."""
    for name in USER_AGENTS.values():
        payload = decode_base64(sign_a_bogus(name, DETAIL_QUERY), "s4")
        for index, (and_mask, or_mask) in enumerate(PREFIX_MASKS):
            byte = payload[index]
            assert byte & or_mask == or_mask
            assert byte & ~(and_mask | or_mask) & 0xFF == 0


def test_base64_round_trips_through_every_alphabet() -> None:
    payload = "".join(chr(value) for value in range(0, 250, 7))
    for alphabet in ("s0", "s1", "s2", "s3", "s4"):
        encoded = encode_base64(payload, alphabet)
        assert decode_base64(encoded, alphabet).decode("latin-1").startswith(payload[:30])


def test_decode_base64_rejects_a_foreign_character() -> None:
    with pytest.raises(ValueError, match="alphabet"):
        decode_base64("!!!!", "s4")


def test_build_browser_info_field_order() -> None:
    info = build_browser_info(
        inner_width=1536,
        inner_height=742,
        outer_width=1536,
        outer_height=864,
        platform="MacIntel",
    )
    assert info == DEFAULT_BROWSER_INFO


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------


def test_false_ms_token_shape_and_determinism() -> None:
    token = gen_false_ms_token(rng=random.Random(7))
    assert token == gen_false_ms_token(rng=random.Random(7))
    assert token.endswith("==")
    assert len(token) == DOUYIN_MS_TOKEN_LENGTH + 2
    assert set(token[:-2]) <= set(MS_TOKEN_ALPHABET)
    assert token != gen_false_ms_token(rng=random.Random(8))


def test_false_ms_token_honours_the_requested_length() -> None:
    assert len(gen_false_ms_token(146, rng=random.Random(1))) == 148


def test_the_ms_token_alphabet_is_the_one_v4_actually_used() -> None:
    """Pinned against V4's literal, because the shape IS the point.

    The constant's whole justification is that a fabricated msToken looks like
    the ones V4 produced, and it had been mistranscribed: `J` and `j` were `G`
    and `g`, and the trailing `+-` was a single `=`. Nothing caught it, because
    the only other assertion about it - that a token's characters are drawn from
    MS_TOKEN_ALPHABET - is true of any alphabet whatsoever.

    The literal below is copied from `gen_random_str` in V4's
    crawlers/utils/utils.py (branch `main`), which is the source of record.
    """
    v4_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-"
    assert v4_alphabet == MS_TOKEN_ALPHABET

    # The two properties the typo actually broke, spelled out so a future
    # rewrite that keeps the length but loses the shape still fails.
    assert "j" in MS_TOKEN_ALPHABET and "J" in MS_TOKEN_ALPHABET
    assert "=" not in MS_TOKEN_ALPHABET, "= belongs in the padding, not the body"
    assert len(set(MS_TOKEN_ALPHABET)) == len(MS_TOKEN_ALPHABET), "a duplicated letter"


VERIFY_FP_RE = re.compile(
    r"^verify_[0-9a-z]+_[0-9A-Za-z]{8}_[0-9A-Za-z]{4}_[0-9A-Za-z]{4}_[0-9A-Za-z]{4}_[0-9A-Za-z]{12}$"
)


def test_verify_fp_format() -> None:
    value = gen_verify_fp(now_ms=1700000000000, rng=random.Random(7))
    assert VERIFY_FP_RE.match(value), value
    tail = value.split("_", 2)[2]
    assert len(tail) == 36
    assert [tail[index] for index in (8, 13, 18, 23)] == ["_"] * 4
    assert tail[14] == "4"
    assert tail[19] in "89ab"
    assert set(tail) <= set(VERIFY_FP_ALPHABET) | {"_"}


def test_verify_fp_encodes_the_timestamp_in_base36() -> None:
    value = gen_verify_fp(now_ms=1700000000000, rng=random.Random(7))
    stamp = value.split("_")[1]
    assert int(stamp, 36) == 1700000000000
    later = gen_verify_fp(now_ms=1700000001000, rng=random.Random(7))
    assert later.split("_")[1] != stamp


def test_verify_fp_is_random_per_call() -> None:
    values = {gen_verify_fp(now_ms=1700000000000) for _ in range(20)}
    assert len(values) == 20


def test_s_v_web_id_has_the_same_shape_as_verify_fp() -> None:
    value = gen_s_v_web_id(now_ms=1700000000000, rng=random.Random(7))
    assert VERIFY_FP_RE.match(value), value
    assert value == gen_verify_fp(now_ms=1700000000000, rng=random.Random(7))


def cookie_client(
    cookies: Mapping[str, str] | None = None,
    *,
    status: int = 200,
    error: Exception | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    """An AsyncClient whose transport answers with the given Set-Cookie headers."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if error is not None:
            raise error
        headers = [
            ("set-cookie", f"{name}={value}; Path=/") for name, value in (cookies or {}).items()
        ]
        return httpx.Response(status, headers=headers, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_gen_ttwid_returns_the_registered_cookie() -> None:
    seen: list[httpx.Request] = []
    async with cookie_client({"ttwid": "1%7Cabc%7C1700000000%7Cxyz"}, seen=seen) as client:
        ttwid = await gen_ttwid(client)
    assert ttwid == "1%7Cabc%7C1700000000%7Cxyz"
    assert str(seen[0].url) == DOUYIN_TTWID.url
    assert seen[0].method == "POST"
    assert b"ttwid" not in seen[0].content  # the body is the registration payload
    assert seen[0].content == DOUYIN_TTWID.data.encode()


async def test_gen_ttwid_sends_the_cookie_header_when_one_is_given() -> None:
    seen: list[httpx.Request] = []
    async with cookie_client({"ttwid": "abc"}, seen=seen) as client:
        await gen_ttwid(client, DOUYIN_TTWID, cookie="sessionid=x")
    assert seen[0].headers["cookie"] == "sessionid=x"


async def test_gen_ttwid_reports_a_missing_cookie_as_an_upstream_change() -> None:
    async with cookie_client({}) as client:
        with pytest.raises(UpstreamChanged) as excinfo:
            await gen_ttwid(client)
    assert excinfo.value.path == "cookies.ttwid"


async def test_gen_ttwid_reports_a_network_failure_as_a_signing_failure() -> None:
    async with cookie_client(error=httpx.ConnectError("no route")) as client:
        with pytest.raises(SigningFailed):
            await gen_ttwid(client)


async def test_gen_odin_tt_returns_the_cookie() -> None:
    seen: list[httpx.Request] = []
    async with cookie_client({"odin_tt": "deadbeef"}, seen=seen) as client:
        assert await gen_odin_tt(client) == "deadbeef"
    assert seen[0].method == "GET"


async def test_gen_odin_tt_reports_a_missing_cookie() -> None:
    async with cookie_client({}) as client:
        with pytest.raises(UpstreamChanged):
            await gen_odin_tt(client)


MS_TOKEN_SPEC = MsTokenSpec(
    url="https://example.invalid/web/report",
    magic=538969122,
    version=1,
    data_type=8,
    str_data="opaque-sdk-blob",
    user_agent=UA_CHROME90,
)


async def test_gen_real_ms_token_posts_the_sdk_payload() -> None:
    seen: list[httpx.Request] = []
    token = "T" * 128
    async with cookie_client({"msToken": token}, seen=seen) as client:
        assert await gen_real_ms_token(client, MS_TOKEN_SPEC) == token
    body = seen[0].read()
    assert b'"magic": 538969122' in body
    assert b'"strData": "opaque-sdk-blob"' in body
    assert b'"tspFromClient"' in body
    assert seen[0].headers["user-agent"] == UA_CHROME90


async def test_gen_real_ms_token_rejects_an_unexpected_length() -> None:
    async with cookie_client({"msToken": "short"}) as client:
        with pytest.raises(UpstreamChanged) as excinfo:
            await gen_real_ms_token(client, MS_TOKEN_SPEC)
    assert excinfo.value.path == "cookies.msToken.length"


async def test_gen_ms_token_falls_back_and_says_so() -> None:
    async with cookie_client(error=httpx.ConnectError("no route")) as client:
        token, real = await gen_ms_token(client, MS_TOKEN_SPEC, rng=random.Random(2))
    assert real is False
    assert token == gen_false_ms_token(rng=random.Random(2))


async def test_gen_ms_token_prefers_the_real_one() -> None:
    token = "R" * 120
    async with cookie_client({"msToken": token}) as client:
        assert await gen_ms_token(client, MS_TOKEN_SPEC) == (token, True)


# --------------------------------------------------------------------------
# contracts in base.py
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.douyin.com/aweme/v1/web/aweme/detail/", Platform.DOUYIN),
        ("https://live.douyin.com/webcast/room/web/enter/", Platform.DOUYIN),
        ("https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/", Platform.DOUYIN),
        ("https://www.tiktok.com/api/item/detail/", Platform.TIKTOK),
        ("https://m.tiktok.com/api/item/detail/", Platform.TIKTOK),
        ("https://example.com/anything", None),
        ("not a url", None),
        ("https://notdouyin.com/x", None),
    ],
)
def test_platform_inference(url: str, expected: Platform | None) -> None:
    assert platform_of(url) == expected


def test_endpoint_is_the_path() -> None:
    assert endpoint_of("https://www.douyin.com/aweme/v1/web/aweme/detail/?a=1") == (
        "/aweme/v1/web/aweme/detail/"
    )
    assert endpoint_of("https://www.douyin.com") == "/"


def test_request_spec_is_immutable_and_copies_on_change() -> None:
    params = {"a": "1"}
    spec = RequestSpec.get("https://www.douyin.com/x/", params=params)
    params["a"] = "2"  # mutating the source must not reach the spec
    assert spec.params == {"a": "1"}
    with pytest.raises(TypeError):
        spec.params["a"] = "3"  # type: ignore[index]
    updated = spec.with_params({"a": "9"})
    assert spec.params == {"a": "1"}
    assert updated.params == {"a": "9"}
    assert spec.method == "GET"


def test_request_spec_upper_cases_the_method() -> None:
    assert RequestSpec(method="post", url="https://www.douyin.com/x/").method == "POST"


def test_signed_params_builds_a_url() -> None:
    signed = SignedParams(query="a=1&a_bogus=xy")
    assert signed.signed_url("https://www.douyin.com/x/?old=1") == (
        "https://www.douyin.com/x/?a=1&a_bogus=xy"
    )
    assert SignedParams(query="").signed_url("https://www.douyin.com/x/") == (
        "https://www.douyin.com/x/"
    )


def test_static_fingerprint_satisfies_the_protocol() -> None:
    assert isinstance(FINGERPRINT, SigningFingerprint)


# --------------------------------------------------------------------------
# NativeSigner
# --------------------------------------------------------------------------

DOUYIN_SPEC = RequestSpec.get(
    "https://www.douyin.com/aweme/v1/web/aweme/detail/",
    params={"device_platform": "webapp", "aid": "6383", "aweme_id": "7345492945006595379"},
)
#: Shorthand for the tests that still exercise the X-Bogus algorithm. TikTok
#: stopped signing with it (its SDK sends the constant `1`), so the vehicle for
#: those is Douyin with the algorithm named explicitly.
X_BOGUS = SignatureAlgorithm.X_BOGUS

TIKTOK_SPEC = RequestSpec.get(
    "https://www.tiktok.com/api/item/detail/",
    params={"aid": "1988", "itemId": "7339393672959757570"},
)

#: A Douyin endpoint the platform does NOT sign itself, so `auto` leaves it on
#: the native signer and the risk-driven crossing is reachable. DOUYIN_SPEC
#: above is `/aweme/v1/web/aweme/detail/`, which IS in the SDK's webSign list -
#: `auto` sends that one to the browser whatever the risk rate says.
DOUYIN_UNPROTECTED_SPEC = RequestSpec.get(
    "https://www.douyin.com/aweme/v1/web/user/profile/other/",
    params={"device_platform": "webapp", "aid": "6383", "sec_user_id": "MS4wLjABAAAA"},
)


async def test_native_signer_signs_douyin_with_a_bogus() -> None:
    signer = NativeSigner(Platform.DOUYIN, rng=random.Random(11))
    signed = await signer.sign(DOUYIN_SPEC, FINGERPRINT)

    assert signed.signer == SIGNER_NATIVE
    assert signed.algorithm is SignatureAlgorithm.A_BOGUS
    assert set(signed.params) == {MS_TOKEN_PARAM, "a_bogus"}

    parsed = parse_qs(signed.query)
    assert parsed["aweme_id"] == ["7345492945006595379"]
    assert parsed["a_bogus"] == [signed.params["a_bogus"]]
    assert parsed[MS_TOKEN_PARAM] == [signed.params[MS_TOKEN_PARAM]]
    # The value goes into the URL percent-encoded: it may contain + and /.
    assert signed.query.endswith("&a_bogus=" + quote(signed.params["a_bogus"], safe=""))


async def test_native_signer_sends_tiktoks_own_parameter_set() -> None:
    """X-Bogus stopped being TikTok's signature and this signer stopped computing one.

    On HTTP the SDK sends the literal ``X-Bogus=1``; the 16-character value in
    ``xbogus.py`` only ever appears on websocket handshakes (docs/design/17).
    Computing one here would send a parameter set TikTok's own page never sends,
    and would leave the request without the signature it does check.
    """
    signer = NativeSigner(Platform.TIKTOK, fill_ms_token=False)
    signed = await signer.sign(TIKTOK_SPEC, FINGERPRINT)

    assert list(signed.params) == ["X-Dynosaur", "msToken", "X-Bogus", "X-Gnarly"]
    assert signed.params["X-Bogus"] == "1"
    assert signed.query.startswith("aid=1988&itemId=7339393672959757570&X-Dynosaur=")


async def test_native_signer_signs_the_bytes_it_returns() -> None:
    """The query is the signed byte sequence; re-encoding it would break it."""
    signer = NativeSigner(Platform.DOUYIN, algorithm=SignatureAlgorithm.X_BOGUS)
    signed = await signer.sign(DOUYIN_SPEC, FINGERPRINT)
    body, _, signature = signed.query.rpartition("&X-Bogus=")
    assert XBogus(FINGERPRINT.user_agent).sign(body) == signature


async def test_native_signer_fills_a_missing_ms_token_only() -> None:
    signer = NativeSigner(Platform.DOUYIN, rng=random.Random(5))
    spec = DOUYIN_SPEC.with_params({**DOUYIN_SPEC.params, MS_TOKEN_PARAM: "already-here"})
    signed = await signer.sign(spec, FINGERPRINT)
    assert MS_TOKEN_PARAM not in signed.params
    assert parse_qs(signed.query)[MS_TOKEN_PARAM] == ["already-here"]

    without = await NativeSigner(Platform.DOUYIN, fill_ms_token=False).sign(
        DOUYIN_SPEC, FINGERPRINT
    )
    assert MS_TOKEN_PARAM not in without.params
    assert MS_TOKEN_PARAM not in parse_qs(without.query)


async def test_native_signer_uses_the_fingerprint_user_agent() -> None:
    """TikTok's X-Dynosaur carries a hash of the User-Agent, so a signature
    computed for one identity cannot be sent as another."""
    signer = NativeSigner(Platform.TIKTOK, fill_ms_token=False)
    one = await signer.sign(TIKTOK_SPEC, StaticFingerprint(user_agent=UA_CHROME90))
    other = await signer.sign(TIKTOK_SPEC, StaticFingerprint(user_agent=UA_SAFARI17))
    assert one.params["X-Dynosaur"] != other.params["X-Dynosaur"]


async def test_native_signer_refuses_a_fingerprint_without_a_user_agent() -> None:
    signer = NativeSigner(Platform.DOUYIN)
    with pytest.raises(SigningFailed, match="user agent"):
        await signer.sign(DOUYIN_SPEC, StaticFingerprint(user_agent=""))


def test_native_signer_refuses_an_algorithm_it_cannot_implement() -> None:
    with pytest.raises(SigningFailed, match="_signature"):
        NativeSigner(Platform.TIKTOK, algorithm=SignatureAlgorithm.SIGNATURE)


async def test_native_signer_is_always_healthy() -> None:
    health = await NativeSigner(Platform.DOUYIN).health()
    assert health.healthy is True
    assert health.signer == SIGNER_NATIVE
    assert health.detail == "douyin:a_bogus"


def test_browser_info_follows_the_fingerprint_and_falls_back_cleanly() -> None:
    assert browser_info_for(FINGERPRINT) == (
        f"1920|{1080 - BROWSER_CHROME_PX}|1920|1080|0|0|0|0|1920|1080|1920|1080|"
        f"1920|{1080 - BROWSER_CHROME_PX}|24|24|Win32"
    )
    assert browser_info_for(StaticFingerprint(user_agent=UA_CHROME90)) == DEFAULT_BROWSER_INFO
    partial = StaticFingerprint(user_agent=UA_CHROME90, screen_width=800, browser_platform="Win32")
    assert browser_info_for(partial) == DEFAULT_BROWSER_INFO


def test_native_signers_covers_every_platform() -> None:
    signers = native_signers()
    assert set(signers) == {Platform.DOUYIN, Platform.TIKTOK}
    assert signers[Platform.DOUYIN].algorithm is SignatureAlgorithm.A_BOGUS
    assert signers[Platform.TIKTOK].algorithm is SignatureAlgorithm.X_BOGUS


# --------------------------------------------------------------------------
# RpcSigner
# --------------------------------------------------------------------------


def rpc_client(
    handler: object,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_rpc_signer_posts_the_exact_query_and_maps_the_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"a_bogus": "sig+with/chars", "ms_token": "abc"})

    async with rpc_client(handler) as client:
        signed = await RpcSigner(client, "http://browser-rpc:8000/").sign(DOUYIN_SPEC, FINGERPRINT)

    assert str(seen[0].url) == "http://browser-rpc:8000/rpc/sign"
    payload = json.loads(seen[0].read())
    assert payload["platform"] == "douyin"
    assert payload["method"] == "GET"
    assert payload["query"] == "device_platform=webapp&aid=6383&aweme_id=7345492945006595379"
    assert payload["user_agent"] == UA_CHROME90

    assert signed.signer == SIGNER_BROWSER
    assert signed.algorithm is SignatureAlgorithm.A_BOGUS
    assert signed.params["a_bogus"] == "sig+with/chars"
    assert signed.query == (
        "device_platform=webapp&aid=6383&aweme_id=7345492945006595379"
        "&a_bogus=sig%2Bwith%2Fchars&msToken=abc"
    )


async def test_rpc_signer_sends_the_session_the_request_will_be_sent_with() -> None:
    """The fix for the withheld-payload bug, at the wire.

    Measured against a live page on 2026-09-08: Douyin's `verifyFp` IS the
    `s_v_web_id` cookie of whatever browser computed the signature, read once
    when the document loads. Sending no cookies here made browser-rpc sign in
    its own warm session, so every request went out quoting one visitor in the
    query and a different one in the Cookie header - and both platforms answer
    that by withholding the payload rather than by returning an error.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"a_bogus": "sig", "verifyFp": "verify_a"})

    session = SigningSession(
        cookies={"s_v_web_id": "verify_a", "ttwid": "1|abc"},
        proxy_url="http://exit:8080",
        identity_id="ident-a",
    )
    async with rpc_client(handler) as client:
        await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT, session)

    payload = json.loads(seen[0].read())
    assert payload["cookies"] == {"s_v_web_id": "verify_a", "ttwid": "1|abc"}
    assert payload["proxy_url"] == "http://exit:8080"
    assert payload["identity_id"] == "ident-a"


async def test_rpc_signer_without_a_session_asks_for_an_anonymous_one() -> None:
    """Omitting the session must send an empty jar, not omit the field.

    An older service reading a missing key as "use your own warm page" is
    exactly the behaviour being removed; saying {} says what is meant.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"a_bogus": "sig"})

    async with rpc_client(handler) as client:
        await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)

    payload = json.loads(seen[0].read())
    assert payload["cookies"] == {}
    assert payload["proxy_url"] is None


async def test_a_signing_timeout_says_so_instead_of_logging_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`str()` on an httpx timeout is empty, and the log said `error=""`.

    That happened in production and cost a debugging session: a timeout and an
    unreachable service produced identical, empty evidence. The class name is
    the part that distinguishes them.
    """

    def timing_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    async with rpc_client(timing_out) as client:
        with pytest.raises(SigningFailed, match="ReadTimeout"):
            await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)


async def test_rpc_signer_leaves_x_bogus_unescaped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"x_bogus": "DFSzsw/VY-1"})

    async with rpc_client(handler) as client:
        signed = await RpcSigner(client, "http://rpc").sign(TIKTOK_SPEC, FINGERPRINT)
    assert signed.query.endswith("&X-Bogus=DFSzsw/VY-1")
    assert signed.algorithm is SignatureAlgorithm.X_BOGUS


async def test_rpc_signer_rejects_a_response_without_a_signature() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ms_token": "abc"})

    async with rpc_client(handler) as client:
        with pytest.raises(SigningFailed, match="no signature"):
            await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)


async def test_rpc_signer_maps_transport_and_status_failures() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with rpc_client(broken) as client:
        with pytest.raises(SigningFailed, match="unreachable"):
            await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    async with rpc_client(failing) as client:
        with pytest.raises(SigningFailed):
            await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)


async def test_rpc_signer_needs_to_know_the_platform() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"a_bogus": "x"})

    spec = RequestSpec.get("https://example.com/api/", params={"a": "1"})
    async with rpc_client(handler) as client:
        with pytest.raises(SigningFailed, match="platform"):
            await RpcSigner(client, "http://rpc").sign(spec, FINGERPRINT)
        signed = await RpcSigner(client, "http://rpc", platform=Platform.DOUYIN).sign(
            spec, FINGERPRINT
        )
    assert signed.params["a_bogus"] == "x"


async def test_rpc_signer_does_not_duplicate_a_token_the_caller_already_sent() -> None:
    """The browser's ms_token must not be appended on top of the caller's.

    It would put msToken in the URL twice and hand the platform a query the
    signature was not computed over.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"a_bogus": "SIG", "ms_token": "FROM-BROWSER"})

    spec = DOUYIN_SPEC.with_params({**DOUYIN_SPEC.params, MS_TOKEN_PARAM: "FROM-CALLER"})
    async with rpc_client(handler) as client:
        signed = await RpcSigner(client, "http://rpc").sign(spec, FINGERPRINT)
    assert signed.query.count("msToken=") == 1
    assert "FROM-BROWSER" not in signed.query
    assert MS_TOKEN_PARAM not in signed.params


@pytest.mark.parametrize("platform", [Platform.DOUYIN, Platform.TIKTOK])
async def test_both_signers_send_the_same_bytes_for_the_same_request(
    platform: Platform,
) -> None:
    """The cross-signer contract: swapping signers must not change the query.

    A base64 msToken ends in ``==``; encoding it one way natively and another way
    over RPC sends two different URLs for one request and guarantees a shadow
    mismatch that has nothing to do with the algorithm.
    """
    spec = RequestSpec.get(
        "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        if platform is Platform.DOUYIN
        else "https://www.tiktok.com/api/item/detail/",
        params={"aid": "6383", "keyword": "hello world", MS_TOKEN_PARAM: "AbC" * 40 + "=="},
    )
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["query"])
        return httpx.Response(200, json={"a_bogus": "SIG"})

    native_signed = await NativeSigner(platform).sign(spec, FINGERPRINT)
    async with rpc_client(handler) as client:
        await RpcSigner(client, "http://rpc").sign(spec, FINGERPRINT)

    native_query = native_signed.query.rsplit("&", 1)[0]
    if platform is Platform.DOUYIN:
        assert captured == [native_query]
        return
    # TikTok is the exception, and it is the platform's doing rather than a
    # relaxation. Its SDK appends msToken *between* X-Dynosaur and X-Bogus and
    # seals it there, so the token cannot also sit among the business
    # parameters; and the seal covers the query as the browser normalises it,
    # so a space has to be sent as %20 rather than raw. What the contract
    # protects - the token reaching the platform byte for byte, `==` and all -
    # still holds on both sides.
    token = str(spec.params[MS_TOKEN_PARAM])
    assert f"&{MS_TOKEN_PARAM}={token}&" in native_signed.query
    assert captured[0].endswith(f"&{MS_TOKEN_PARAM}={token}")
    assert native_signed.query.count(f"{MS_TOKEN_PARAM}=") == 1


async def test_native_signer_reports_an_unsignable_query_as_a_signing_failure() -> None:
    """X-Bogus hex-decodes queries of 32 characters or fewer and raises
    ValueError on the rest; the registry only falls back on a DtkError."""
    signer = NativeSigner(
        Platform.DOUYIN, algorithm=SignatureAlgorithm.X_BOGUS, fill_ms_token=False
    )
    short = RequestSpec.get("https://www.douyin.com/aweme/v1/web/aweme/detail/", {"a": "1"})
    with pytest.raises(SigningFailed, match="could not be computed"):
        await signer.sign(short, FINGERPRINT)


async def test_native_signer_reports_an_unencodable_user_agent_as_a_signing_failure() -> None:
    signer = NativeSigner(
        Platform.DOUYIN, algorithm=SignatureAlgorithm.X_BOGUS, fill_ms_token=False
    )
    with pytest.raises(SigningFailed, match="could not be computed"):
        # Latin Extended-A: outside ISO-8859-1, which is what X-Bogus encodes to.
        await signer.sign(DOUYIN_SPEC, StaticFingerprint(user_agent="Mozilla/5.0 \u0100"))


async def test_registry_falls_back_when_the_algorithm_rejects_the_input() -> None:
    """The point of the previous two: a plain ValueError would skip the fallback."""
    rpc = FakeSigner(SIGNER_BROWSER, value="rpc-sig")
    native = NativeSigner(
        Platform.DOUYIN, algorithm=SignatureAlgorithm.X_BOGUS, fill_ms_token=False
    )
    registry = SignerRegistry({Platform.DOUYIN: native}, rpc)
    short = RequestSpec.get("https://www.douyin.com/aweme/v1/web/comment/list/", {"a": "1"})
    signed = await registry.sign(short, FINGERPRINT)
    assert signed.signer == SIGNER_BROWSER


async def test_rpc_health_reports_warm_contexts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rpc/health"
        return httpx.Response(
            200, json={"warm_contexts": 2, "backend_version": "cloak-1.2.3", "uptime": 91.5}
        )

    async with rpc_client(handler) as client:
        health = await RpcSigner(client, "http://rpc").health()
    assert health.healthy is True
    assert health.warm_contexts == 2
    assert health.backend_version == "cloak-1.2.3"
    assert health.uptime_seconds == 91.5
    assert health.latency_ms is not None


async def test_rpc_health_without_a_warm_context_is_unhealthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"warm_contexts": 0})

    async with rpc_client(handler) as client:
        health = await RpcSigner(client, "http://rpc").health()
    assert health.healthy is False
    assert health.detail == "no warm signing context"


async def test_rpc_health_never_raises() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("gone")

    async with rpc_client(broken) as client:
        health = await RpcSigner(client, "http://rpc").health()
    assert health.healthy is False
    assert health.signer == SIGNER_BROWSER


# --------------------------------------------------------------------------
# registry: fake signers
# --------------------------------------------------------------------------


class FakeSigner:
    """A programmable ``Signer`` for the registry tests."""

    def __init__(
        self,
        name: str,
        *,
        value: str = "sig",
        param: str = "X-Bogus",
        healthy: bool = True,
        fail: Exception | None = None,
        values: list[str] | None = None,
    ) -> None:
        self.name = name
        self.param = param
        self.value = value
        self.values = values
        self.healthy = healthy
        self.fail = fail
        self.calls = 0
        self.health_calls = 0
        #: Every session handed to `sign`, so a test can prove the registry
        #: forwards the identity rather than dropping it.
        self.sessions: list[SigningSession | None] = []

    async def sign(
        self,
        spec: RequestSpec,
        identity_fingerprint: SigningFingerprint,
        session: SigningSession | None = None,
    ) -> SignedParams:
        self.sessions.append(session)
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        value = (
            self.values[min(self.calls - 1, len(self.values) - 1)] if self.values else self.value
        )
        return SignedParams(
            query=f"{urlencode(dict(spec.params or {}))}&{self.param}={value}",
            params={self.param: value},
            signer=self.name,
            algorithm=SignatureAlgorithm.X_BOGUS,
        )

    async def health(self) -> SignerHealth:
        self.health_calls += 1
        return SignerHealth(signer=self.name, healthy=self.healthy)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_registry(
    *,
    native: FakeSigner | None = None,
    rpc: FakeSigner | None = None,
    clock: FakeClock | None = None,
    policy: RegistryPolicy | None = None,
    mode: SigningMode = "auto",
    alerts: list[tuple[str, Mapping[str, object]]] | None = None,
) -> tuple[SignerRegistry, FakeSigner, FakeSigner | None, FakeClock]:
    """A registry in ``auto`` mode unless the caller says otherwise.

    Most tests below exercise the risk-driven crossover from native to the
    browser, and that behaviour *is* the auto mode. The shipped default is
    ``rpc`` - the native algorithms are stale - and is covered explicitly in the
    mode tests rather than left to be inherited here, so that changing the
    default cannot silently rewrite what these tests mean.
    """
    clock = clock or FakeClock()
    native = native or FakeSigner(SIGNER_NATIVE, value="native-sig")
    registry = SignerRegistry(
        {Platform.DOUYIN: native},
        rpc,
        policy=replace(policy or RegistryPolicy(), mode=mode),
        clock=clock,
        on_alert=(lambda event, context: alerts.append((event, context)))
        if alerts is not None
        else None,
    )
    return registry, native, rpc, clock


def push_risk(
    registry: SignerRegistry,
    count: int,
    risky: int,
    spec: RequestSpec = DOUYIN_UNPROTECTED_SPEC,
) -> None:
    """Record outcomes against the SAME key the signing call will look up.

    The key is (platform, endpoint) and the endpoint is the URL path, so risk
    pushed against one spec is invisible to a call made with another. It
    defaults to the unprotected spec because that is the only one whose
    risk-driven crossing is reachable: `auto` sends a platform-signed endpoint
    to the browser regardless of the risk rate.
    """
    for index in range(count):
        registry.observe(
            Platform.DOUYIN,
            spec.endpoint,
            Outcome.RISK_CONTROL if index < risky else Outcome.OK,
        )


async def test_auto_mode_starts_on_the_native_signer() -> None:
    """Named for the mode, not for "the default": the default is rpc."""
    registry, native, rpc, _ = build_registry(rpc=FakeSigner(SIGNER_BROWSER), mode="auto")
    signed = await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)
    assert signed.signer == SIGNER_NATIVE
    assert native.calls == 1
    assert rpc is not None and rpc.calls == 0


async def test_the_registry_hands_the_identitys_session_to_the_signer() -> None:
    """The registry is the only path from the fetch loop to a signer.

    Dropping the session here would restore the bug in full while every
    signer-level test kept passing: the browser would go back to signing in its
    own warm page, and the platform would go back to withholding the payload.
    """
    registry, native, _, _ = build_registry(mode="native")
    session = SigningSession(cookies={"s_v_web_id": "verify_a"}, identity_id="ident-a")
    await registry.sign(DOUYIN_SPEC, FINGERPRINT, session)
    assert native.sessions == [session]


async def test_the_session_survives_the_fallback_to_the_browser() -> None:
    """The crossover path is where a forwarded argument is easiest to lose."""
    registry, _, rpc, _ = build_registry(
        native=FakeSigner(SIGNER_NATIVE, fail=SigningFailed("stale")),
        rpc=FakeSigner(SIGNER_BROWSER),
        mode="native",
    )
    session = SigningSession(cookies={"s_v_web_id": "verify_a"}, identity_id="ident-a")
    signed = await registry.sign(DOUYIN_SPEC, FINGERPRINT, session)
    assert signed.signer == SIGNER_BROWSER
    assert rpc is not None and rpc.sessions == [session]


class TestWebSignature:
    """Douyin's own x-secsdk-web-signature, recovered and ported.

    The vectors below were produced by executing the platform's real SDK in
    Node and reading what it computed - an oracle independent of this
    implementation, which is the point of having them. The port was separately
    checked against signatures a live browser produced (recompute the md5 over
    the URL Douyin's SDK built: 32 hex characters, byte-identical) and against
    the platform itself (24 of 24 live requests returned data with no browser).

    The uifid values here are invented. Real ones are visitor identifiers and
    have no business in a test file.
    """

    UIFID = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff0011"

    @staticmethod
    def _sign(url: str, cookies: dict[str, str], stamp: int) -> str:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        uifid = dict(pairs).get("uifid") or websign.pick_uifid(cookies)
        assert uifid is not None
        _query, signature, _headers = websign.sign(pairs, uifid, timestamp=stamp)
        return signature

    def test_it_reproduces_the_platforms_own_signatures(self) -> None:
        vectors = [
            (
                "https://www.douyin.com/aweme/v1/web/aweme/detail/"
                "?device_platform=webapp&aid=6383&aweme_id=123",
                {"UIFID_TEMP": self.UIFID},
                1788848841,
                "6090b6162b96aeb107b51ab69140c764",
            ),
            (
                "https://www.douyin.com/x/?q=%E4%B8%AD&z=a b&e=%2F",
                {"UIFID": "AAAA1111", "UIFID_TEMP": "BBBB2222"},
                1788848901,
                "a79dcd330ecba4d98016189c3ca313cd",
            ),
            (
                "https://www.douyin.com/x/",
                {"UIFID": "AAAA1111"},
                1788848901,
                "0479f3eb2297110d45bfbd96ccccb292",
            ),
        ]
        for url, cookies, stamp, expected in vectors:
            assert self._sign(url, cookies, stamp) == expected, url

    def test_a_uifid_already_in_the_query_keeps_its_place(self) -> None:
        """Appending a second one changes the preimage, so the platform refuses."""
        assert (
            self._sign("https://www.douyin.com/x/?uifid=ZZZ&t=1", {"UIFID": "AAAA1111"}, 1788848901)
            == "df59a6887d4647239b9079d7d16ad784"
        )

    def test_it_is_a_pure_function_of_the_second(self) -> None:
        """No nonce, whatever the changing values on the wire suggest.

        Worth pinning because the opposite was assumed for a while: the value
        differs on every call in practice only because the timestamp is in whole
        seconds and a call takes longer than one to set up.
        """
        url = "https://www.douyin.com/x/?a=1"
        first = self._sign(url, {"UIFID_TEMP": self.UIFID}, 1788848841)
        again = self._sign(url, {"UIFID_TEMP": self.UIFID}, 1788848841)
        later = self._sign(url, {"UIFID_TEMP": self.UIFID}, 1788848842)
        assert first == again
        assert first != later

    def test_the_query_is_serialized_the_way_it_is_hashed(self) -> None:
        """The same bytes must be signed and sent, so one encoder does both."""
        query, signature, _ = websign.sign(
            [("z", "a b"), ("q", "\u4e2d")], self.UIFID, timestamp=1788848901
        )
        assert "a%20b" in query
        assert query.endswith(f"&{websign.SIGNATURE_PARAM}={signature}")
        covered = query.rsplit("&", 1)[0]
        assert (
            hashlib.md5(f"{self.UIFID}_1788848901_{websign.SALT}_{covered}".encode()).hexdigest()
            == signature
        )

    def test_a_jar_with_no_visitor_id_signs_nothing(self) -> None:
        assert websign.pick_uifid({}) is None
        assert websign.pick_uifid({"ttwid": "1|x"}) is None
        assert websign.pick_uifid({"UIFID_TEMP": "abc"}) == "abc"
        # The SDK's own order: the plain name wins over the _TEMP one.
        assert websign.pick_uifid({"UIFID_TEMP": "temp", "uifid": "plain"}) == "plain"


class TestNativeSignerWithASession:
    """With the identity's jar, the native signer covers Douyin end to end.

    Measured live on 2026-09-08: 6 of 6 on each of author_profile, comments,
    content_detail and author_posts, no browser involved.
    """

    JAR: ClassVar[dict[str, str]] = {
        "s_v_web_id": "verify_test_visitor",
        "UIFID_TEMP": "aabbccddeeff00112233445566778899",
        "ttwid": "1|abc",
    }

    async def test_it_adds_the_visitor_parameters_and_the_signature(self) -> None:
        signed = await NativeSigner(Platform.DOUYIN).sign(
            DOUYIN_SPEC, FINGERPRINT, SigningSession(cookies=self.JAR)
        )
        assert signed.params["verifyFp"] == "verify_test_visitor"
        assert signed.params["fp"] == "verify_test_visitor"
        assert signed.params["uifid"] == self.JAR["UIFID_TEMP"]
        assert len(signed.params[websign.SIGNATURE_PARAM]) == 32
        # The platform's own pages send these as headers too.
        assert signed.headers[websign.EXPIRE_HEADER].isdigit()
        assert signed.headers["uifid"] == self.JAR["UIFID_TEMP"]

    async def test_the_signature_covers_the_query_actually_sent(self) -> None:
        """The whole point: hashed bytes and sent bytes are the same bytes."""
        signed = await NativeSigner(Platform.DOUYIN).sign(
            DOUYIN_SPEC, FINGERPRINT, SigningSession(cookies=self.JAR)
        )
        covered, _, signature = signed.query.rpartition(f"&{websign.SIGNATURE_PARAM}=")
        stamp = signed.headers[websign.EXPIRE_HEADER]
        assert (
            hashlib.md5(
                f"{self.JAR['UIFID_TEMP']}_{stamp}_{websign.SALT}_{covered}".encode()
            ).hexdigest()
            == signature
        )

    async def test_without_a_session_it_signs_as_it_always_did(self) -> None:
        """No jar, no visitor parameters - and no invented ones."""
        signed = await NativeSigner(Platform.DOUYIN).sign(DOUYIN_SPEC, FINGERPRINT)
        assert websign.SIGNATURE_PARAM not in signed.params
        assert "uifid" not in signed.params
        assert not signed.headers

    async def test_a_jar_without_a_visitor_id_is_left_alone(self) -> None:
        signed = await NativeSigner(Platform.DOUYIN).sign(
            DOUYIN_SPEC, FINGERPRINT, SigningSession(cookies={"ttwid": "1|abc"})
        )
        assert websign.SIGNATURE_PARAM not in signed.params

    async def test_tiktok_is_untouched(self) -> None:
        """X-Gnarly has no port; adding a Douyin signature would be nonsense."""
        signed = await NativeSigner(Platform.TIKTOK).sign(
            TIKTOK_SPEC, FINGERPRINT, SigningSession(cookies=self.JAR)
        )
        assert websign.SIGNATURE_PARAM not in signed.params


class TestPlatformSignedEndpoints:
    """`auto` uses the browser only where the platform signs the endpoint itself.

    Douyin's own SDK carries the list (dtk.signing.protection). Measured live on
    2026-09-08, pure-Python signing only, eight requests each:

        /aweme/v1/web/user/profile/other/  unprotected  8/8 returned data
        /aweme/v1/web/comment/list/        unprotected  8/8 returned data
        /aweme/v1/web/aweme/detail/        protected    3/8
        /aweme/v1/web/aweme/post/          protected    3/8

    So on an unprotected endpoint the native signer is not a degraded option -
    it is the same request the site would send, for microseconds rather than a
    browser rebind, and it keeps working when browser-rpc is down.
    """

    async def test_auto_sends_a_platform_signed_endpoint_to_the_browser(self) -> None:
        registry, native, rpc, _ = build_registry(rpc=FakeSigner(SIGNER_BROWSER), mode="auto")
        signed = await registry.sign(DOUYIN_SPEC, FINGERPRINT)
        assert signed.signer == SIGNER_BROWSER
        assert native.calls == 0
        # No risk had to be observed for this - which matters, because
        # SignerRegistry.observe() has no caller in src/ and the risk-driven
        # crossing therefore never fires in production.
        assert rpc is not None and rpc.calls == 1

    async def test_auto_keeps_an_unprotected_endpoint_on_the_native_signer(self) -> None:
        registry, native, rpc, _ = build_registry(rpc=FakeSigner(SIGNER_BROWSER), mode="auto")
        signed = await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)
        assert signed.signer == SIGNER_NATIVE
        assert native.calls == 1
        assert rpc is not None and rpc.calls == 0

    async def test_the_table_is_the_platforms_own_list(self) -> None:
        """Every endpoint this project calls, classified as the SDK classifies it."""
        assert requires_browser_signature(
            Platform.DOUYIN, "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        )
        assert requires_browser_signature(
            Platform.DOUYIN, "https://www.douyin.com/aweme/v1/web/aweme/post/"
        )
        assert not requires_browser_signature(
            Platform.DOUYIN, "https://www.douyin.com/aweme/v1/web/user/profile/other/"
        )
        assert not requires_browser_signature(
            Platform.DOUYIN, "https://www.douyin.com/aweme/v1/web/comment/list/"
        )

    async def test_a_query_string_does_not_change_the_answer(self) -> None:
        assert requires_browser_signature(
            Platform.DOUYIN,
            "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=7&a_bogus=x",
        )

    async def test_tiktok_is_always_the_browsers(self) -> None:
        """Not a table: X-Gnarly and X-Dynosaur have no port at all."""
        assert requires_browser_signature(
            Platform.TIKTOK, "https://www.tiktok.com/api/item/detail/"
        )
        assert requires_browser_signature(Platform.TIKTOK, "https://www.tiktok.com/anything/")


# --------------------------------------------------------------------------
# registry: signing.mode
#
# The mode is an operator-facing setting, so each value is pinned to a test.
# Before this existed the registry carried a `prefer_rpc` flag that nothing
# read: the selector always tried native first, while the field and its
# docstring said the opposite. A setting the console can change has to be a
# setting the selector obeys, and that is what these assert.
# --------------------------------------------------------------------------


async def test_rpc_mode_is_the_shipped_default() -> None:
    """The default has to be rpc: the native algorithms no longer verify live."""
    assert RegistryPolicy().mode == "rpc"
    assert RUNTIME_SETTINGS["signing.mode"].default == "rpc"


async def test_rpc_mode_goes_to_the_browser_even_with_a_healthy_native_signer() -> None:
    registry, native, rpc, _ = build_registry(rpc=FakeSigner(SIGNER_BROWSER), mode="rpc")
    assert (await registry.sign(DOUYIN_SPEC, FINGERPRINT)).signer == SIGNER_BROWSER
    assert native.calls == 0
    assert rpc is not None and rpc.calls == 1


async def test_rpc_mode_never_crosses_to_native_on_risk_alone() -> None:
    """Risk moves auto to the browser; in rpc mode it is already there."""
    registry, native, _, _ = build_registry(rpc=FakeSigner(SIGNER_BROWSER), mode="rpc")
    push_risk(registry, 25, 20)
    assert (await registry.sign(DOUYIN_SPEC, FINGERPRINT)).signer == SIGNER_BROWSER
    assert native.calls == 0


async def test_native_mode_stays_native_however_bad_the_risk_rate_gets() -> None:
    """The escape hatch for a deployment with no browser, and for bisecting."""
    rpc = FakeSigner(SIGNER_BROWSER)
    registry, native, _, _ = build_registry(rpc=rpc, mode="native")
    push_risk(registry, 25, 25)  # every single sample risk-controlled
    assert (await registry.sign(DOUYIN_SPEC, FINGERPRINT)).signer == SIGNER_NATIVE
    assert native.calls == 1
    assert rpc.calls == 0


async def test_rpc_mode_falls_back_to_native_when_the_browser_is_unhealthy() -> None:
    rpc = FakeSigner(SIGNER_BROWSER, healthy=False)
    registry, native, _, _ = build_registry(rpc=rpc, mode="rpc")
    assert (await registry.sign(DOUYIN_SPEC, FINGERPRINT)).signer == SIGNER_NATIVE
    assert native.calls == 1


async def test_disabling_fallback_pins_the_mode_to_one_signer() -> None:
    """A stale signer has to be able to fail loudly.

    With fallback on, a signer that stopped working looks healthy because its
    traffic quietly moves to the other one - which is how the native signers
    went on passing their own tests while producing signatures no platform
    accepts. Turning fallback off is how an operator finds out which signer is
    actually carrying the traffic.
    """
    rpc = FakeSigner(SIGNER_BROWSER, healthy=False)
    registry, native, _, _ = build_registry(
        rpc=rpc, policy=RegistryPolicy(fallback_enabled=False), mode="rpc"
    )
    assert (await registry.sign(DOUYIN_SPEC, FINGERPRINT)).signer == SIGNER_BROWSER
    assert native.calls == 0, "fallback was disabled but native was used anyway"


async def test_rpc_mode_without_a_browser_configured_says_so() -> None:
    """The two ways this can go wrong get two different messages."""
    registry, _, _, _ = build_registry(mode="rpc")
    assert (await registry.sign(DOUYIN_SPEC, FINGERPRINT)).signer == SIGNER_NATIVE

    strict, _, _, _ = build_registry(policy=RegistryPolicy(fallback_enabled=False), mode="rpc")
    with pytest.raises(SigningFailed, match="browser-rpc is not configured"):
        await strict.sign(DOUYIN_SPEC, FINGERPRINT)

    with pytest.raises(SigningFailed, match="no signer available"):
        await strict.sign(TIKTOK_SPEC, FINGERPRINT, platform=Platform.TIKTOK)


async def test_registry_switches_an_endpoint_to_rpc_when_risk_spikes() -> None:
    alerts: list[tuple[str, Mapping[str, object]]] = []
    rpc = FakeSigner(SIGNER_BROWSER, value="rpc-sig")
    registry, native, _, clock = build_registry(rpc=rpc, alerts=alerts)

    push_risk(registry, 25, 20)  # 0.8 > 0.6 over 25 samples
    signed = await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)

    assert signed.signer == SIGNER_BROWSER
    assert rpc.calls == 1
    assert native.calls == 0
    # The alert names the signer taking over and why, because "fallback
    # engaged" on its own leaves an operator to guess between a stale algorithm,
    # an unhealthy browser and a platform with no native signer at all.
    assert alerts == [
        (
            "signing.fallback.engaged",
            {
                "platform": "douyin",
                "endpoint": DOUYIN_UNPROTECTED_SPEC.endpoint,
                "signer": SIGNER_BROWSER,
                "reason": "the endpoint's risk rate suggests the signature is being rejected",
            },
        )
    ]
    assert list(registry.fallback_endpoints()) == [
        (Platform.DOUYIN, DOUYIN_UNPROTECTED_SPEC.endpoint)
    ]

    # The alert fires on the transition, not on every request.
    clock.advance(10)
    await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)
    assert len(alerts) == 1


async def test_registry_stays_native_when_the_sample_is_too_small() -> None:
    rpc = FakeSigner(SIGNER_BROWSER)
    registry, _native, _, _ = build_registry(rpc=rpc)
    push_risk(registry, 5, 5)  # rate 1.0 but only five samples
    signed = await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)
    assert signed.signer == SIGNER_NATIVE
    assert rpc.calls == 0


async def test_registry_stays_native_when_rpc_is_unhealthy() -> None:
    rpc = FakeSigner(SIGNER_BROWSER, healthy=False)
    registry, _native, _, _ = build_registry(rpc=rpc)
    push_risk(registry, 25, 25)
    signed = await registry.sign(DOUYIN_SPEC, FINGERPRINT)
    assert signed.signer == SIGNER_NATIVE
    assert rpc.calls == 0


async def test_registry_returns_to_native_when_the_risk_rate_falls() -> None:
    rpc = FakeSigner(SIGNER_BROWSER)
    registry, _native, _, clock = build_registry(rpc=rpc)
    push_risk(registry, 25, 20)
    assert (await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)).signer == SIGNER_BROWSER

    push_risk(registry, 100, 0)
    clock.advance(10)
    assert (await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)).signer == SIGNER_NATIVE
    assert list(registry.fallback_endpoints()) == []


async def test_registry_caches_the_health_probe() -> None:
    rpc = FakeSigner(SIGNER_BROWSER)
    registry, _, _, clock = build_registry(rpc=rpc, policy=RegistryPolicy(health_ttl_seconds=30))
    push_risk(registry, 25, 25)
    for _ in range(3):
        clock.advance(1)
        await registry.sign(DOUYIN_SPEC, FINGERPRINT)
    assert rpc.health_calls == 1

    clock.advance(60)
    await registry.sign(DOUYIN_SPEC, FINGERPRINT)
    assert rpc.health_calls == 2


async def test_registry_falls_back_when_the_native_signer_raises() -> None:
    native = FakeSigner(SIGNER_NATIVE, fail=SigningFailed("algorithm blew up"))
    rpc = FakeSigner(SIGNER_BROWSER, value="rpc-sig")
    registry, _, _, _ = build_registry(native=native, rpc=rpc)
    signed = await registry.sign(DOUYIN_SPEC, FINGERPRINT)
    assert signed.signer == SIGNER_BROWSER


async def test_registry_propagates_the_failure_when_there_is_no_rpc() -> None:
    native = FakeSigner(SIGNER_NATIVE, fail=SigningFailed("algorithm blew up"))
    registry, _, _, _ = build_registry(native=native)
    with pytest.raises(SigningFailed, match="blew up"):
        await registry.sign(DOUYIN_SPEC, FINGERPRINT)


async def test_registry_refuses_an_unknown_platform() -> None:
    registry, _, _, _ = build_registry()
    spec = RequestSpec.get("https://example.com/api/", params={"a": "1"})
    with pytest.raises(SigningFailed, match="platform"):
        await registry.sign(spec, FINGERPRINT)


async def test_registry_without_a_signer_for_the_platform() -> None:
    registry, _, _, _ = build_registry()
    with pytest.raises(SigningFailed, match="no signer available"):
        await registry.sign(TIKTOK_SPEC, FINGERPRINT, platform=Platform.TIKTOK)


async def test_registry_accepts_an_injected_risk_source() -> None:
    calls: list[tuple[Platform, str]] = []

    async def risk_rate(platform: Platform, endpoint: str) -> RiskSample:
        calls.append((platform, endpoint))
        return RiskSample(rate=0.9, samples=50)

    rpc = FakeSigner(SIGNER_BROWSER)
    registry = SignerRegistry(
        {Platform.DOUYIN: FakeSigner(SIGNER_NATIVE)},
        rpc,
        policy=RegistryPolicy(mode="auto"),
        risk_rate=risk_rate,
        clock=FakeClock(),
    )
    registry.observe(Platform.DOUYIN, DOUYIN_UNPROTECTED_SPEC.endpoint, Outcome.OK)  # ignored
    signed = await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)
    assert signed.signer == SIGNER_BROWSER
    assert calls == [(Platform.DOUYIN, DOUYIN_UNPROTECTED_SPEC.endpoint)]


# --------------------------------------------------------------------------
# registry: shadow comparison
# --------------------------------------------------------------------------


async def test_shadow_sampling_is_rate_limited_per_endpoint() -> None:
    registry, _, _, clock = build_registry(
        rpc=FakeSigner(SIGNER_BROWSER), policy=RegistryPolicy(shadow_interval_seconds=600)
    )
    assert registry.should_shadow(DOUYIN_SPEC) is True
    assert registry.should_shadow(DOUYIN_SPEC) is False
    clock.advance(599)
    assert registry.should_shadow(DOUYIN_SPEC) is False
    clock.advance(2)
    assert registry.should_shadow(DOUYIN_SPEC) is True


def test_shadow_sampling_needs_a_second_signer() -> None:
    registry, _, _, _ = build_registry()
    assert registry.should_shadow(DOUYIN_SPEC) is False


async def test_shadow_comparison_reports_a_match() -> None:
    registry, _, _, _ = build_registry(
        native=FakeSigner(SIGNER_NATIVE, value="same"),
        rpc=FakeSigner(SIGNER_BROWSER, value="same"),
    )
    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None
    assert (result.compared, result.matched) == (True, True)
    assert result.verdicts == {"X-Bogus": Comparison.MATCH}
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_mismatch_disables_native_permanently_and_alerts() -> None:
    alerts: list[tuple[str, Mapping[str, object]]] = []
    native = FakeSigner(SIGNER_NATIVE, value="ours")
    rpc = FakeSigner(SIGNER_BROWSER, value="theirs")
    registry, _, _, _ = build_registry(native=native, rpc=rpc, alerts=alerts)

    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is False

    # Two attempts, each bracketing the remote call with a native signature.
    assert native.calls == 4
    assert rpc.calls == 2
    assert registry.native_enabled(Platform.DOUYIN) is False
    assert alerts[0][0] == "signing.shadow.mismatch"

    signed = await registry.sign(DOUYIN_SPEC, FINGERPRINT)
    assert signed.signer == SIGNER_BROWSER


async def test_shadow_bracket_absorbs_a_second_boundary() -> None:
    """The clock ticks during the remote call; the second native call catches it.

    A retry could not: it is an independent draw, so a boundary crossed during
    every attempt would look exactly like a changed algorithm.
    """
    native = FakeSigner(SIGNER_NATIVE, values=["second-0", "second-1"])
    rpc = FakeSigner(SIGNER_BROWSER, value="second-1")
    registry, _, _, _ = build_registry(
        native=native, rpc=rpc, policy=RegistryPolicy(shadow_retries=0)
    )
    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    assert native.calls == 2  # one attempt, bracketing the remote call
    assert rpc.calls == 1
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_bracket_absorbs_a_real_x_bogus_second_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same thing with the real algorithm and a real clock crossing."""
    seconds = iter([1700000000, 1700000001])
    monkeypatch.setattr(
        "dtk.signing.native.xbogus.time", SimpleNamespace(time=lambda: next(seconds))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # The browser signs during the round trip, after the clock has ticked.
        return httpx.Response(
            200,
            json={"x_bogus": XBogus(body["user_agent"]).sign(body["query"], timestamp=1700000001)},
        )

    async with rpc_client(handler) as client:
        registry = SignerRegistry(
            {Platform.DOUYIN: NativeSigner(Platform.DOUYIN, algorithm=X_BOGUS)},
            RpcSigner(client, "http://rpc", algorithm=X_BOGUS),
            policy=RegistryPolicy(shadow_retries=0),
        )
        assert await registry.compare_shadow(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_UNPROTECTED_SPEC.endpoint)
    assert result is not None
    assert (result.compared, result.matched) == (True, True)
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_agrees_with_a_browser_running_the_same_x_bogus() -> None:
    """Regression: a correct browser must never disable the native path.

    NativeSigner invents a random msToken when the caller omits one and
    RpcSigner passes the parameters through untouched, so an unpinned sample
    signs two different byte sequences. Before SignerRegistry pinned msToken for
    the sample, this test failed on the very first comparison and left the
    platform permanently switched to browser-rpc.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"x_bogus": XBogus(body["user_agent"]).sign(body["query"])})

    async with rpc_client(handler) as client:
        registry = SignerRegistry(
            {Platform.DOUYIN: NativeSigner(Platform.DOUYIN, algorithm=X_BOGUS)},
            RpcSigner(client, "http://rpc", algorithm=X_BOGUS),
        )
        assert await registry.compare_shadow(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_UNPROTECTED_SPEC.endpoint)
    assert result is not None
    assert (result.compared, result.matched) == (True, True)
    assert result.verdicts["X-Bogus"] is Comparison.MATCH
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_agrees_with_a_browser_whose_window_is_a_different_size() -> None:
    """The A-Bogus half of the same regression.

    The browser behind browser-rpc reports its own window geometry, which makes
    its a_bogus a different *length* from ours for the same request. Comparing
    lengths therefore mismatched on every correct sample.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # DEFAULT_BROWSER_INFO stands in for the browser's own geometry, which
        # is not the identity's (FINGERPRINT is 1920x1080 on Win32).
        return httpx.Response(
            200,
            json={"a_bogus": ABogus(body["user_agent"]).get_value(body["query"], body["method"])},
        )

    async with rpc_client(handler) as client:
        registry = SignerRegistry(
            {Platform.DOUYIN: NativeSigner(Platform.DOUYIN)}, RpcSigner(client, "http://rpc")
        )
        assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None
    assert result.verdicts["a_bogus"] is Comparison.MATCH
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_still_catches_a_browser_signing_a_different_algorithm() -> None:
    """The relaxations above must not blunt the detector they exist to keep."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Same alphabet and a plausible length, but not this algorithm.
        return httpx.Response(200, json={"a_bogus": encode_base64("z" * 124, "s4")})

    async with rpc_client(handler) as client:
        registry = SignerRegistry(
            {Platform.DOUYIN: NativeSigner(Platform.DOUYIN)}, RpcSigner(client, "http://rpc")
        )
        assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is False
    assert registry.native_enabled(Platform.DOUYIN) is False


async def test_shadow_skips_when_nothing_is_comparable() -> None:
    native = FakeSigner(SIGNER_NATIVE, param="msToken", value="a")
    rpc = FakeSigner(SIGNER_BROWSER, param="msToken", value="b")
    registry, _, _, _ = build_registry(native=native, rpc=rpc)

    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None
    assert result.compared is False
    assert result.detail == "every parameter skipped"
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_skips_when_the_parameters_do_not_overlap() -> None:
    native = FakeSigner(SIGNER_NATIVE, param="a_bogus", value="x")
    rpc = FakeSigner(SIGNER_BROWSER, param="X-Bogus", value="y")
    registry, _, _, _ = build_registry(native=native, rpc=rpc)
    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None and result.detail == "no comparable parameter"


async def test_shadow_skips_when_the_rpc_sample_fails() -> None:
    native = FakeSigner(SIGNER_NATIVE)
    rpc = FakeSigner(SIGNER_BROWSER, fail=SigningFailed("browser down"))
    registry, _, _, _ = build_registry(native=native, rpc=rpc)
    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None
    assert result.compared is False
    assert "browser down" in (result.detail or "")
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_shadow_without_an_rpc_signer_does_not_disable_native() -> None:
    registry, _, _, _ = build_registry()
    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None and result.compared is False
    assert registry.native_enabled(Platform.DOUYIN) is True


async def test_operator_can_disable_and_re_enable_native() -> None:
    alerts: list[tuple[str, Mapping[str, object]]] = []
    rpc = FakeSigner(SIGNER_BROWSER)
    registry, _, _, _ = build_registry(rpc=rpc, alerts=alerts)

    registry.disable_native(Platform.DOUYIN, reason="a-bogus rotated")
    assert (await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)).signer == SIGNER_BROWSER
    assert alerts[0][0] == "signing.native.disabled"
    assert registry.should_shadow(DOUYIN_UNPROTECTED_SPEC) is False

    registry.enable_native(Platform.DOUYIN)
    assert (await registry.sign(DOUYIN_UNPROTECTED_SPEC, FINGERPRINT)).signer == SIGNER_NATIVE


async def test_registry_health_covers_every_signer() -> None:
    registry, _, _, _ = build_registry(rpc=FakeSigner(SIGNER_BROWSER))
    report = await registry.health()
    assert set(report) == {"native:douyin", "rpc"}
    assert all(health.healthy for health in report.values())


# --------------------------------------------------------------------------
# registry: comparators and the risk window
# --------------------------------------------------------------------------


def test_exact_comparator() -> None:
    assert ExactComparator().compare("a", "a") is Comparison.MATCH
    assert ExactComparator().compare("a", "b") is Comparison.MISMATCH


def test_a_bogus_comparator_accepts_two_independent_signatures() -> None:
    """Two real A-Bogus values never match byte for byte but must compare equal."""
    first = ABogus(UA_CHROME90).get_value(DETAIL_QUERY)
    second = ABogus(UA_CHROME90).get_value(DETAIL_QUERY)
    assert first != second
    assert ABogusComparator().compare(first, second) is Comparison.MATCH


@pytest.mark.parametrize("index", [0, 1, 2, 3, 11])
def test_a_bogus_comparator_catches_a_broken_prefix(index: int) -> None:
    """Flip a structured prefix bit and the comparator must notice."""
    native = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    payload = bytearray(decode_base64(native, "s4"))
    and_mask, or_mask = PREFIX_MASKS[index]
    payload[index] = (payload[index] | ~(and_mask | or_mask)) & 0xFF
    tampered = encode_base64(payload.decode("latin-1"), "s4")
    assert ABogusComparator().compare(native, tampered) is Comparison.MISMATCH


def test_a_bogus_comparator_catches_truncation_and_alphabet_changes() -> None:
    native = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    assert ABogusComparator().compare(native, native[:-4]) is Comparison.MISMATCH
    foreign = "!" + native[1:]
    assert ABogusComparator().compare(native, foreign) is Comparison.MISMATCH


def test_a_bogus_comparator_ignores_a_different_window_size() -> None:
    """Two correct signers with different window geometry produce different
    *lengths*; comparing lengths would disable native on the first sample."""
    ours = sign_a_bogus(UA_CHROME90, DETAIL_QUERY, browser_info=browser_info_for(FINGERPRINT))
    theirs = sign_a_bogus(
        UA_CHROME90,
        DETAIL_QUERY,
        browser_info="1280|649|1280|800|0|0|0|0|1280|800|1280|800|1280|649|24|24|Linux x86_64",
    )
    assert len(ours) != len(theirs)
    assert ABogusComparator().compare(ours, theirs) is Comparison.MATCH


def tamper_a_bogus(value: str, frame_index: int, new_byte: int) -> str:
    """Re-encode ``value`` with one byte of its RC4'd frame changed."""
    payload = decode_base64(value, "s4")
    frame = bytearray(
        ord(char)
        for char in rc4_encrypt(payload[STRUCTURED_PREFIX_LEN:].decode("latin-1"), PAYLOAD_KEY)
    )
    frame[frame_index] = new_byte
    reencrypted = rc4_encrypt(frame.decode("latin-1"), PAYLOAD_KEY)
    return encode_base64(payload[:STRUCTURED_PREFIX_LEN].decode("latin-1") + reencrypted, "s4")


@pytest.mark.parametrize(
    ("frame_index", "new_byte", "reason"),
    [
        (0, 45, "frame byte 0"),
        (17, 238, "frame byte 17"),
        (39, 2, "frame byte 39"),
        (FRAME_BROWSER_LEN_INDEX, 66, "browser length slot"),
        (43, 7, "frame checksum"),
    ],
)
def test_a_bogus_structure_catches_a_changed_frame(
    frame_index: int, new_byte: int, reason: str
) -> None:
    """The invariants that replaced the length check must actually bite."""
    native = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    assert structure_error(native) is None
    tampered = tamper_a_bogus(native, frame_index, new_byte)
    assert structure_error(tampered) == reason
    assert ABogusComparator().compare(native, tampered) is Comparison.MISMATCH


def test_a_bogus_structure_accepts_every_fixture() -> None:
    for _ua, _query, _method, expected in A_BOGUS_FIXTURES:
        assert structure_error(expected) is None


def test_a_bogus_comparator_rejects_a_value_that_is_not_base64_of_the_alphabet() -> None:
    native = sign_a_bogus(UA_CHROME90, DETAIL_QUERY)
    same_length_but_wrong = (string.ascii_uppercase * 10)[: len(native)]
    assert ABogusComparator().compare(native, same_length_but_wrong) is Comparison.MISMATCH


async def test_sliding_risk_window_counts_only_risk_control() -> None:
    clock = FakeClock()
    window = SlidingRiskWindow(window_seconds=60, clock=clock)
    key = (Platform.DOUYIN, "/x/")
    assert await window(*key) is None

    for outcome in (
        Outcome.OK,
        Outcome.BUSINESS_ERROR,
        Outcome.NETWORK_ERROR,
        Outcome.RISK_CONTROL,
    ):
        window.observe(*key, outcome)
    sample = await window(*key)
    assert sample == RiskSample(rate=0.25, samples=4)


async def test_sliding_risk_window_forgets_old_samples() -> None:
    clock = FakeClock()
    window = SlidingRiskWindow(window_seconds=60, clock=clock)
    key = (Platform.DOUYIN, "/x/")
    for _ in range(10):
        window.observe(*key, Outcome.RISK_CONTROL)
    clock.advance(61)
    assert await window(*key) is None

    window.observe(*key, Outcome.OK)
    sample = await window(*key)
    assert sample == RiskSample(rate=0.0, samples=1)


# --------------------------------------------------------------------------
# error paths that only show up when something upstream misbehaves
# --------------------------------------------------------------------------


async def test_gen_real_ms_token_reports_a_missing_cookie() -> None:
    async with cookie_client({}) as client:
        with pytest.raises(UpstreamChanged) as excinfo:
            await gen_real_ms_token(client, MS_TOKEN_SPEC)
    assert excinfo.value.path == "cookies.msToken"


async def test_gen_odin_tt_reports_a_network_failure() -> None:
    async with cookie_client(error=httpx.ReadTimeout("slow")) as client:
        with pytest.raises(SigningFailed, match="unreachable"):
            await gen_odin_tt(client)


def test_verify_fp_survives_a_zero_timestamp() -> None:
    assert gen_verify_fp(now_ms=0, rng=random.Random(1)).startswith("verify_0_")


def test_x_bogus_md5_helper_passes_integer_lists_through() -> None:
    assert XBogus.md5_str_to_array([1, 2, 3]) == [1, 2, 3]
    assert XBogus.md5([0x61, 0x62, 0x63]) == "900150983cd24fb0d6963f7d28e17f72"


def test_a_bogus_comparator_rejects_a_payload_that_is_too_short() -> None:
    short = encode_base64("abc", "s4")
    assert ABogusComparator().compare(short, short) is Comparison.MISMATCH


async def test_registry_reraises_when_the_fallback_is_also_unavailable() -> None:
    native = FakeSigner(SIGNER_NATIVE, fail=SigningFailed("algorithm blew up"))
    rpc = FakeSigner(SIGNER_BROWSER, healthy=False)
    registry, _, _, _ = build_registry(native=native, rpc=rpc)
    with pytest.raises(SigningFailed, match="blew up"):
        await registry.sign(DOUYIN_SPEC, FINGERPRINT)
    assert rpc.calls == 0


async def test_shadow_skips_a_parameter_with_no_comparator() -> None:
    native = FakeSigner(SIGNER_NATIVE, param="future_sig", value="a")
    rpc = FakeSigner(SIGNER_BROWSER, param="future_sig", value="b")
    registry, _, _, _ = build_registry(native=native, rpc=rpc)
    assert await registry.compare_shadow(DOUYIN_SPEC, FINGERPRINT) is True
    result = registry.shadow_result(Platform.DOUYIN, DOUYIN_SPEC.endpoint)
    assert result is not None and result.detail == "no comparable parameter"


async def test_rpc_signer_rejects_a_body_that_is_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway</html>")

    async with rpc_client(handler) as client:
        with pytest.raises(SigningFailed, match="non-JSON"):
            await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)


async def test_rpc_signer_rejects_a_json_body_that_is_not_an_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["a_bogus"])

    async with rpc_client(handler) as client:
        with pytest.raises(SigningFailed, match="non-object"):
            await RpcSigner(client, "http://rpc").sign(DOUYIN_SPEC, FINGERPRINT)


async def test_rpc_health_rejects_a_body_that_is_not_an_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    async with rpc_client(handler) as client:
        health = await RpcSigner(client, "http://rpc").health()
    assert health.healthy is False
    assert health.detail == "health endpoint returned a non-object body"


class TestLiveContract:
    """Encodes what the platforms actually do, measured on 2026-09-07.

    These are not opinions about how signing ought to work; each one is a
    behaviour observed in a real browser against the live sites, written down so
    a future refactor that quietly breaks it fails here instead of in production.
    """

    def test_every_returned_parameter_is_carried_through(self):
        """Douyin adds six parameters and TikTok three, and neither set is the
        one this module used to recognise.

        An earlier version mapped four fixed names and would have dropped
        verifyFp, fp, uifid, timestamp, x-secsdk-web-signature, X-Gnarly and
        X-Dynosaur - which is every parameter that makes the request work.
        """
        from dtk.signing.rpc import RpcSigner

        douyin_response = {
            "a_bogus": "A" * 184,
            "verifyFp": "verify_synthetic_0000",
            "fp": "verify_synthetic_0000",
            "uifid": "f" * 320,
            "timestamp": "1788816533",
            "x-secsdk-web-signature": "d" * 32,
        }
        signed = RpcSigner._build("aid=6383", {"aid": "6383"}, douyin_response)
        for name in douyin_response:
            assert name in signed.params, f"{name} was dropped"

        tiktok_response = {
            "X-Gnarly": "G" * 332,
            "X-Dynosaur": "D" * 444,
            "msToken": "m" * 172,
            "X-Bogus": "1",
        }
        signed = RpcSigner._build("aid=1988", {"aid": "1988"}, tiktok_response)
        for name in tiktok_response:
            assert name in signed.params, f"{name} was dropped"

    def test_a_response_carrying_only_identity_is_refused(self):
        """verifyFp and friends say who is asking, not that the request is
        signed. The platform answers an unsigned request with an empty body, so
        failing here keeps the reason legible."""
        from dtk.core.errors import SigningFailed
        from dtk.signing.rpc import RpcSigner

        with pytest.raises(SigningFailed):
            RpcSigner._build(
                "aid=6383",
                {"aid": "6383"},
                {"verifyFp": "v", "fp": "v", "uifid": "u", "timestamp": "1"},
            )

    def test_x_bogus_is_no_longer_treated_as_the_signature(self):
        """TikTok reduced X-Bogus to a single character and signs with X-Gnarly.

        A response carrying both must not be reported as X-Bogus-signed, and the
        vestigial value must still be forwarded, because the platform still
        expects the parameter to be present.
        """
        from dtk.signing.rpc import RpcSigner

        signed = RpcSigner._build(
            "aid=1988", {"aid": "1988"}, {"X-Bogus": "1", "X-Gnarly": "G" * 332}
        )
        assert signed.params["X-Bogus"] == "1"
        assert signed.params["X-Gnarly"] == "G" * 332


TIKTOK_UA_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
TIKTOK_UA_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIKTOK_UA_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

#: Vectors produced by executing TikTok's genuine ``webmssdk 2.0.0.561`` in Node
#: under a DOM stub and reading what it computed - an oracle entirely independent
#: of the Python below, which is the only reason these are worth anything.
#:
#: Each signature carries the ChaCha key that produced it, spliced into its own
#: ciphertext (that is how the server decrypts it, and how the key was recovered
#: here). Feeding that key and the timestamp and nonces the SDK happened to draw
#: back into the port makes the comparison byte-exact despite the per-call nonce.
TIKTOK_VECTORS: list[dict[str, object]] = [
    {
        "query": "aid=1988&count=10",
        "user_agent": TIKTOK_UA_MAC,
        "ms_token": "",
        "timestamp": 1788856077,
        "nonce": 51705231,
        "sequence": 1,
        "dynosaur_key": (
            978422917,
            297502614,
            495476334,
            3283317692,
            2976530683,
            1833255788,
            640912066,
            837161194,
            1669461668,
            1494236015,
            394043533,
            3049628592,
        ),
        "dynosaur": (
            "MxmNRRv/B-"
            "a6aTHjkvVe9cmFRhMeLS613kBZFzkI/aJYxnjdutQ24acX9B9/EWqDls27AI1WNuyaY9yQV-fvFZ"
            "98HD8AuO8owhWWl/k/NA8VrqhUi8SisGI7yUrOkPYuhjSfZRLYkGGOgiGZxl3WaGSlTOfZpAFId0"
            "mkGwpCrZfrPHFoydYsLb/yWgabzJa1dqhKx2GbbaB6PX4aDjR9dLL7UrKk0s/DzRU/QVPXTcZV5S"
            "A-"
            "0iNUlMpHBc66oAf6S1vX5d6EOit0cum8dabUKE2owzmAG4jC9mibKZdkNQXy02gCJVgP31bnbFMM"
            "W3u-ZI4sE0Pi-gGzNtwdlNvF1ABzqrSpSockDCh84FYrmmOGRNL-"
            "vbyAwnYm7cmYIKuQ-a97tCivXvA="
        ),
        "gnarly_timestamp": 1788856077,
        "gnarly_nonce": 51705346,
        "nonce2": 4243271951,
        "gnarly_sequence": 1,
        "gnarly_key": (
            1630480152,
            2370805161,
            303572552,
            3085792651,
            989762755,
            2481426789,
            1111047770,
            4159623281,
            1698763817,
            2164255245,
            1245854549,
            1546723756,
        ),
        "gnarly_order": [0, 12, 1, 11, 15, 3, 6, 4, 5, 2, 8, 10, 9, 14, 16, 13],
        "gnarly": (
            "MC3KKmrckRFfMdp/YlNaTDaQpDXunCTZoDhfUcF5ak2ml87IfMPZmHnU4Rmo-BpWi3stE8dkD0LI"
            "z0I2EF3q2zc6M9AAbGYCHTdt4-"
            "/KooU2WCGcrzg8RrH5DVV0oZ5BP/0LrRgDj9b8sUAMK2vH8Gq/4-CPgq5874-"
            "RFPYho9vzV3omXS9BRFCK6d6FNpDaPVFaIw/hwJzNwu7lZJkb7tewprbTvuM5jZIDC9/0CjnDNT3"
            "FESXpDFqIY9XU-"
            "yGfFGxZMScDZRqgNZkRQxDAa2ojWjVhui4pZdBxy4E0UBsQ1DNu5pquE4wuNnxIEsajo3u="
        ),
    },
    {
        "query": "aid=1988&count=10",
        "user_agent": TIKTOK_UA_MAC,
        "ms_token": "",
        "timestamp": 1788856078,
        "nonce": 51779695,
        "sequence": 2,
        "dynosaur_key": (
            1572890829,
            1514151185,
            2529086182,
            2272987962,
            3955315074,
            2915286751,
            1302282518,
            1633274394,
            4243982449,
            293973822,
            1330316943,
            2520625085,
        ),
        "dynosaur": (
            "M8YFNBeheYzhVQ0idLWXK-cHoMFuj4F6UVad9YAGbVjJi9Fp87Tf5FwlCJLTC2Zne1uAQwijc09s"
            "r9Q-"
            "TPdhYbD7YIGKXw78CJ3LfKzcobObCkgCE5X/xbXNYsddUXwV0FpaK0aAiOUmaOO5X9XvaOKQ0iPQ"
            "q/ByjHzbnZ3lAeMmv5fNiZLDxwiIdi1OKRo-"
            "9xlUSYGEyb3jUIU6he07bVCupiTDyqAK95iUI4U0KacckKRpZoa63fqoAsbvaykcj/zVlkoEXHFy"
            "-ohrmS4//GNmDv3KATr1ZtIeD49yLKgX62mg-Z2xqbX4CoqJ3x-tWiyJB9aG63t4djh-"
            "/KgKq9KR/sMxElQoCfxrD80/JE5K45oyB1m5vT3-AmHPfz1hFtqcre7iHAA="
        ),
        "gnarly_timestamp": 1788856078,
        "gnarly_nonce": 51780642,
        "nonce2": 4243184428,
        "gnarly_sequence": 2,
        "gnarly_key": (
            106601913,
            1248264656,
            1435065838,
            700191209,
            1840019693,
            2160447077,
            450230889,
            4289322682,
            353692308,
            2723093055,
            373234936,
            2358079306,
        ),
        "gnarly_order": [14, 2, 0, 8, 5, 15, 9, 3, 10, 13, 6, 4, 1, 12, 11, 16],
        "gnarly": (
            "MOG4STmzqLn-PjdO0hhw/ImDkAp-wPz/3nRqpnKxBbCekrQ/NQSFLKeU-"
            "s57mCJB7owuAhiSK75i-"
            "GBi0OGUpsOQ5YcWopcOCMw9/IIExDsHer4CwSdFHp0b6C/pWw0OaX9BlPSykHk4WyJx2LG3RuS2M"
            "ix7QLsqbTkqHsQfTpsr8A0qcYzhiYKqPxXBS0zP3WcQc0kPSWqylA8rAJn3YZlUYjkrJl0VAwoO7"
            "LBpa3aR5-"
            "s2P/TMaZfMvVGxQOIGfe4vGqo2PzuGmgwMfu/jQS0IxJJS5jp4GVytAL/CaeVAkLzluB858pg82g"
            "KzeF8c4C/="
        ),
    },
    {
        "query": "WebIdLastTime=1788855000&aid=1988&app_language=en&app_name=tiktok_web&browser_language=en-US&browser_name=Mozilla&browser_online=true&browser_platform=Win32&browser_version=5.0%20%28Windows%29&channel=tiktok_web&cookie_enabled=true&device_id=7412345678901234567&device_platform=web_pc&focus_state=true&from_page=video&history_len=4&is_fullscreen=false&is_page_visible=true&itemId=7300000000000000000&language=en&os=windows&priority_region=US&referer=&region=US&root_referer=https%3A%2F%2Fwww.tiktok.com%2F&screen_height=1080&screen_width=1920&tz_name=America%2FLos_Angeles&webcast_language=en",
        "user_agent": TIKTOK_UA_MAC,
        "ms_token": "",
        "timestamp": 1788856078,
        "nonce": 51868859,
        "sequence": 3,
        "dynosaur_key": (
            2190810609,
            306857639,
            2828155586,
            4154581658,
            1361351321,
            3412939638,
            835289227,
            1217368521,
            2587671991,
            1849735486,
            2608946324,
            3731370325,
        ),
        "dynosaur": (
            "M8b5ILfE9/NebLbDN50HcY56HJvWp37ay1CafY4ueK2DCpSY6Q5rTAXxUTC9N/hkldxRxsPYXYKH"
            "AwHGIsxbs8snYQupSdvLHYVnzG8hgjBOTuHe4-"
            "eMn7itfJxfcHpRBFM1ryb74AD2VWGMklBMBgjQGdmoWQVV746YJ/iuJXUgWAavYHRw1lKxZ3Kzsv"
            "3TLXSG5lO7EAYE4XVYDjbKkvDoRn8e1SpIZPbNdR03NcRWk1H4PuGgbjtmbn468iMF29UJAaWZGu"
            "ZbePsPVoQP3pQiqznHzmjfVioN3yPridyR63p8zefoj/gW-LNhIgBp/uI7WqKriBNArzytXlaLj3"
            "BM44-UbyLEtvCJfXZNYCB/2vDx1rXWdWUtwo/035WsVy2Kmib7cuz="
        ),
        "gnarly_timestamp": 1788856078,
        "gnarly_nonce": 51868109,
        "nonce2": 4243109571,
        "gnarly_sequence": 3,
        "gnarly_key": (
            2738372001,
            1944646926,
            2891279605,
            1896260225,
            227315666,
            220078497,
            2505479263,
            3684765536,
            2408748093,
            2422444422,
            1903679261,
            289581807,
        ),
        "gnarly_order": [6, 13, 8, 5, 9, 15, 10, 2, 12, 3, 16, 4, 11, 1, 0, 14],
        "gnarly": (
            "MRs6ksz9FsQ2tTqeLpt1tsUEabU9U/m3qzRGrwl3YYus0ASncuGi/9XAAkCH58EvorqQ1HU-zgSZ"
            "NP9P/XnG02otnmAosbAHeZ/FzLiU4kHa7Udxyn6OV6mrvLlHbS2GZUlDBClOl4TP2NKWkIRAfQrx"
            "aAcV3xLmuEyaUvzQ/gTiNH72kYOnyrOr0D2d6hH0qM7ppW5z3S47zhJc0hVQDBlOwyd47ZcN-"
            "XQltysGnPLfLhJ0GIo8RGcethbNUICd4ShLAucNFOFzWGc3WprZU775nmcBZNbyai/20G7vf-"
            "AIVfKtMfeXnVujUH1enh0RQJw="
        ),
    },
    {
        "query": "a=%E4%B8%AD%E6%96%87&b=1",
        "user_agent": TIKTOK_UA_MAC,
        "ms_token": "",
        "timestamp": 1788856078,
        "nonce": 51954251,
        "sequence": 1,
        "dynosaur_key": (
            148355427,
            3502788418,
            3431304442,
            1739918888,
            2830223742,
            2575248196,
            2428446985,
            1803208299,
            3496835547,
            1928213672,
            1456868826,
            3826535118,
        ),
        "dynosaur": (
            "MCidR-YGswCSLYD8ftQdLpd9FMnu3P6I-"
            "LjbLrtxlbLxIFh5j4MffToKf5Pm9QnbdXXJTquCBewQvYtBdLCAKCjg0ZrfCBTkcbHM8sD-"
            "dQHCbpeDxir3zFoSaafDE7IQAoSVsABu4j4nKWgq9I6CwzONXXLli7nvZWPvR-BeNnkGtjL2df18"
            "BbZjdxWgDTcZhwoKhaYxT79G6rxJFI5pLfxDSrM2b3iAhdUDEV2wfVWFrhUtuNvn3Pf-PQYLC7bS"
            "z5g5B8g1sWdm3UTxcubJZ3hgnd5fNZtYlkT4ke1JODsyXqvCPPAHwJ1O5yTUxJdK4LZlUHr0Eb/5"
            "N5w2w1sNiZHV-VNYGgjfv30THKNI7-L44VsK7kdEzKKzY/u6gyNtN8MImueHV-z="
        ),
        "gnarly_timestamp": 1788856078,
        "gnarly_nonce": 51954545,
        "nonce2": 4242998399,
        "gnarly_sequence": 1,
        "gnarly_key": (
            877908535,
            162501319,
            1940362746,
            4290319688,
            3071880399,
            1359138359,
            3383752451,
            3474896168,
            1087659318,
            3629162626,
            2392967149,
            3055117667,
        ),
        "gnarly_order": [0, 4, 16, 13, 3, 11, 10, 14, 12, 9, 15, 6, 1, 8, 2, 5],
        "gnarly": (
            "MH91yiKqFPXeZw6eDj0Wux5yjpeGC7yL8OFH2bhp-jBoMHAM8bHcupNkJCZ47w4EvXmMwXofNxeb"
            "NNoXbUeGRChLKPYTqq3RpuqS8hscSL5Dq/tvpmQE6vpQ15FldSxSw5UUusY0r3VM3Y0QzIdYyldM"
            "zOXUWBwgcqp2nRILidP-E0156w/9zZ-5jwWGYVH-"
            "645zZvDIz6lY8K3rAFzoYQ1VP9fBdNzrfFhf49CmHQ7AZeoJFmj6WM3tq0QfZVRHvPSOddmHLVYf"
            "NJG7j-svyY1l3xe45XXO4tJA9OSlzv4I7L8INpFRHOJGDc9XxREEwzf="
        ),
    },
    {
        "query": "aid=1988&count=10",
        "user_agent": TIKTOK_UA_WINDOWS,
        "ms_token": "",
        "timestamp": 1788856078,
        "nonce": 52032174,
        "sequence": 1,
        "dynosaur_key": (
            3465629445,
            2818820546,
            789670997,
            997786302,
            4151189881,
            2806483319,
            3997642507,
            3527027,
            2786407739,
            2783534646,
            354464432,
            4065835603,
        ),
        "dynosaur": (
            "MJwYIg1RDerVcH/siw5vkDtpPq28/NbLOziIn1B3XVUYUNVQ2ci7wAA8jmtuQmWPEEs2Z8TfJXY-"
            "U3A0s54UwuL-WnH9JZrGLpkVEH/9mXYxvpH57/4UqkfyVcxORXzurOzLqlomKhpkfQuLzHqswleA"
            "FfCVtdEtwipwO6x-PaQ44Tu7c/9SyLU0kZVaVajxI/i2WJq8gbbNRQh-otlx773VrnnT/MfmxwR1"
            "1oVfAWtk9psmVAGZXiUq/QU9hTrGYvoBCGNrs9HAt4rDlNOgtQiRkyrfzcalKOd5s3sCrVuqEWTn"
            "TCMIZVS5uk95F6Ks1wVYI40YLgcvGdoRccEazFfJkxT/GXW8CVSwsQaypdrpS/luFv5rQjwkrD2D"
            "oZMKJgfinPrfPsZ="
        ),
        "gnarly_timestamp": 1788856078,
        "gnarly_nonce": 52032313,
        "nonce2": 4242945079,
        "gnarly_sequence": 1,
        "gnarly_key": (
            110199929,
            3425922960,
            2220260331,
            856875292,
            3342478878,
            2791964422,
            3961841004,
            2540417530,
            2664435235,
            3722301734,
            3636874694,
            625594914,
        ),
        "gnarly_order": [9, 2, 0, 10, 6, 16, 12, 13, 3, 1, 5, 4, 11, 14, 15, 8],
        "gnarly": (
            "MFXp1HcAedBouHs9i1YK40XlWBbHfX1Nc/W6usty3sPUeSe8UxL/eAeBEfF1NBQlZO6q3HaD5IOc"
            "5uho3zNHwz5DUnzr2WDrNODYjz8urn/IJwKhwWXuuTaBQRGf/R64QMKpRk4XYUvZ4a2qVR-"
            "y5ZNzBUtkBLe4qhuH6nhUNX8eKOwFicLyd0axNgb3W3wOOrYHLGZn/VvOUQ/K8kTPhhofxMXfcjS"
            "j4JerRg/1R7578W53F9EMMMzfJXrVWbSdW4QqpuJKAQqS9c4kZEFB2C7lhENadMOM3uzRzjDJ6zt"
            "3TsmhftfqMUU3Cz6Hn0pKyiR="
        ),
    },
    {
        "query": "aid=1988&count=35&cursor=0",
        "user_agent": TIKTOK_UA_LINUX,
        "ms_token": "",
        "timestamp": 1788856078,
        "nonce": 52111451,
        "sequence": 2,
        "dynosaur_key": (
            1941879376,
            1119390686,
            2213810504,
            1466769614,
            1863902398,
            2036712394,
            855853420,
            2717686558,
            743547759,
            1560501678,
            3066388893,
            1114757118,
        ),
        "dynosaur": (
            "MRyRC/bdNVWy9tPRAMzyyfYjXzIy9UXUer-"
            "pnk9wnu0v4WKDVD1PbsXyIOzJyloMz59rJ5VD-E0ZgdLxrNIo7XLiJ7Y5T0EvyeSmwI--1pBiLVV"
            "CTuhtZVWOajShcX2S3wb9HbYCG09iApZHD49Ky2-"
            "mQHN9M0URIw/ITLmcx0NyJf64mp8guOem1PJNTK6VEDx7uCF7AnpFPY78ZymmCA-JXQk2ZG5-"
            "xdEg0AiXP/udX3SCJ7S5ywPqxbtbI3GP2RDWIFvJJh93peuhrqLPOPoGmGnm6GLU1ofM3EVQFGCO"
            "nYYe/FUzHexTAG/c0/gpXSMudB/Qwy3LXjZ1qbXAYxzXlLQPSBsqF7LMVw5OcwrqvLzI0OP49ed7"
            "hLpChEmagX4VsG//c5I="
        ),
        "gnarly_timestamp": 1788856078,
        "gnarly_nonce": 52112096,
        "nonce2": 4242861550,
        "gnarly_sequence": 2,
        "gnarly_key": (
            2203665032,
            564731253,
            287839037,
            806186182,
            3568009729,
            1008360972,
            1075061277,
            42020168,
            1047637508,
            3731156290,
            4287507892,
            3410900369,
        ),
        "gnarly_order": [8, 4, 10, 14, 5, 11, 2, 15, 3, 16, 13, 12, 1, 0, 9, 6],
        "gnarly": (
            "McltgPKyjtMqSj/lQr5K9VvIHpekTR/JQNK7U2/ZnDQasir-pCSEcMZC5UwWHQMrjVPqNeSOFATv"
            "FoaSW59PSSDoNJqM2Gg0tbQvtZqtb/AwY5om2AInnqIOceo8LAvGWgezKCJ5Ib-TiLUWKNtkUYWL"
            "CChm6nZcpDs1F2Oe8h8pQLYHm/f7sCWRA9NahAiUAJxf2pEXAusfnSOfBQZJ1gj7eCivZ41t7VFq"
            "vXRXD0U3TuRkuADjCu8m3lk7vNVuM9F0uIMinXx9HpXmY9FrPxbCXffZXW4jpGLh91q9XKJYzQgF"
            "SAr8paxRj8tvOHgIMiI="
        ),
    },
]


class TestTikTokSignature:
    """TikTok's own X-Dynosaur and X-Gnarly, recovered and ported.

    docs/design/17 §6 is explicit that "the request succeeded" is not evidence,
    and TikTok makes the usual byte-for-byte check impossible: every signature
    is an encrypted blob under a key drawn per call, so two independent
    signatures of the same request never match.

    What replaces it is the observation that the blob is self-decrypting - the
    key is spliced into its own ciphertext so the server can recover it. So the
    oracle's key is recoverable too, and with it, and the timestamp and nonces
    the SDK happened to draw, this port can be asked to produce *that same
    signature*. It does, character for character, for every vector below.
    """

    @staticmethod
    def _dynosaur(vector: dict[str, object]) -> str:
        payload = tiktok_sign.dynosaur_payload(
            cast(str, vector["query"]),
            cast(str, vector["user_agent"]),
            timestamp=cast(int, vector["timestamp"]),
            nonce=cast(int, vector["nonce"]),
            sequence=cast(int, vector["sequence"]),
        )
        return tiktok_sign.seal(payload, cast("Sequence[int]", vector["dynosaur_key"]))

    @staticmethod
    def _gnarly(vector: dict[str, object]) -> str:
        sealed = (
            f"{vector['query']}&{tiktok_sign.DYNOSAUR_PARAM}={vector['dynosaur']}"
            f"&{tiktok_sign.MS_TOKEN_PARAM}={vector['ms_token']}"
        )
        payload = tiktok_sign.gnarly_payload(
            sealed,
            cast(str, vector["user_agent"]),
            timestamp=cast(int, vector["gnarly_timestamp"]),
            nonce=cast(int, vector["gnarly_nonce"]),
            nonce2=cast(int, vector["nonce2"]),
            sequence=cast(int, vector["gnarly_sequence"]),
            # The SDK's own emission order, so the comparison can be byte-exact.
            # It differs between processes, which is the evidence that the server
            # parses by key; this module ships ascending.
            order=cast("Sequence[int]", vector["gnarly_order"]),
        )
        return tiktok_sign.seal(payload, cast("Sequence[int]", vector["gnarly_key"]))

    @pytest.mark.parametrize("vector", TIKTOK_VECTORS, ids=range(len(TIKTOK_VECTORS)))
    def test_it_reproduces_the_sdks_own_x_dynosaur(self, vector: dict[str, object]) -> None:
        assert self._dynosaur(vector) == vector["dynosaur"]

    @pytest.mark.parametrize("vector", TIKTOK_VECTORS, ids=range(len(TIKTOK_VECTORS)))
    def test_it_reproduces_the_sdks_own_x_gnarly(self, vector: dict[str, object]) -> None:
        assert self._gnarly(vector) == vector["gnarly"]

    def test_the_seal_covers_x_dynosaur_and_ms_token(self) -> None:
        """The suffix is required even when the token is empty.

        Two vectors share the query ``aid=1988&count=10`` on different paths, so
        this is also where the port pins that the seal follows the query and not
        the endpoint.
        """
        vector = dict(TIKTOK_VECTORS[0])
        vector["ms_token"] = "not-the-token-that-was-signed"
        assert self._gnarly(vector) != TIKTOK_VECTORS[0]["gnarly"]

    def test_the_call_counter_is_in_the_payload(self) -> None:
        """The SDK counts its own calls from 1; two vectors differ only in that."""
        first, second = TIKTOK_VECTORS[0], TIKTOK_VECTORS[1]
        assert first["query"] == second["query"]
        assert (first["sequence"], second["sequence"]) == (1, 2)
        assert self._dynosaur(first) != self._dynosaur(dict(second, sequence=1))

    def test_the_payloads_have_the_shape_the_platform_parses(self) -> None:
        """Field count and length, which are what a bundle bump would change."""
        vector = TIKTOK_VECTORS[0]
        dynosaur = tiktok_sign.dynosaur_payload(
            cast(str, vector["query"]),
            cast(str, vector["user_agent"]),
            timestamp=cast(int, vector["timestamp"]),
            nonce=cast(int, vector["nonce"]),
        )
        # 25 entries, keys 0x20..0x38 ascending, no leading count byte.
        keys = _tlv_keys(dynosaur, lead=0)
        assert keys == list(range(0x20, 0x39))
        gnarly = tiktok_sign.gnarly_payload(
            "aid=1988&X-Dynosaur=x&msToken=",
            cast(str, vector["user_agent"]),
            timestamp=cast(int, vector["timestamp"]),
            nonce=cast(int, vector["nonce"]),
            nonce2=1,
        )
        # 16 entries behind a count byte, 0x07 absent - the SDK does not emit it,
        # whatever xvhuan's implementation does.
        assert gnarly[0] == 16
        assert _tlv_keys(gnarly, lead=1) == [*range(0x00, 0x07), *range(0x08, 0x11)]
        assert len(gnarly) == 193

    def test_the_query_is_serialized_the_way_it_is_sealed(self) -> None:
        """One encoder for both, or the seal covers bytes the platform never sees."""
        query, params = tiktok_sign.sign(
            [("browser_version", "5.0 (Windows)"), ("q", "\u4e2d")],
            TIKTOK_UA_MAC,
            rng=random.Random(11),
        )
        assert query.startswith("browser_version=5.0%20%28Windows%29&q=%E4%B8%AD&")
        assert [part.split("=", 1)[0] for part in query.split("&")] == [
            "browser_version",
            "q",
            tiktok_sign.DYNOSAUR_PARAM,
            tiktok_sign.MS_TOKEN_PARAM,
            tiktok_sign.BOGUS_PARAM,
            tiktok_sign.GNARLY_PARAM,
        ]
        assert params[tiktok_sign.BOGUS_PARAM] == "1"

    def test_x_bogus_is_the_constant_the_sdk_sends(self) -> None:
        """A computed 16-character X-Bogus belongs to websockets, not to HTTP."""
        _query, params = tiktok_sign.sign([("aid", "1988")], TIKTOK_UA_MAC, rng=random.Random(3))
        assert params[tiktok_sign.BOGUS_PARAM] == tiktok_sign.BOGUS_VALUE == "1"

    def test_two_signatures_of_one_request_differ(self) -> None:
        """The nonce is real, so a byte comparison between calls proves nothing.

        Pinned because the opposite was assumed for Douyin and cost a day: there
        the value changed only because the timestamp is in whole seconds. Here it
        changes because the key is drawn per call, which is why the vectors above
        have to supply the key rather than compare two fresh signatures.
        """
        pairs = [("aid", "1988"), ("count", "10")]
        first, _ = tiktok_sign.sign(pairs, TIKTOK_UA_MAC, timestamp=1788856077)
        again, _ = tiktok_sign.sign(pairs, TIKTOK_UA_MAC, timestamp=1788856077)
        assert first != again

    def test_the_versions_it_reports_are_the_bundle_it_was_read_from(self) -> None:
        """A bundle bump changes these, and a stale port looks like an outage."""
        assert tiktok_sign.SDK_VERSION == "5.3.2"
        assert tiktok_sign.SCM_VERSION == "2.0.0.561"

    def test_the_url_state_hash_matches_the_sdks(self) -> None:
        """FNV-1a with an extra ``* 33``; three payload fields depend on it."""
        assert tiktok_sign.hash_state("") == 0x811C9DC4
        assert tiktok_sign.hash_state("aid=1988&count=10") == 0x52B0CB44
        assert tiktok_sign.hash_state(TIKTOK_UA_MAC) == 0x8F015CA0

    def test_the_token_comes_from_the_identitys_own_jar(self) -> None:
        assert tiktok_sign.pick_ms_token(None) == ""
        assert tiktok_sign.pick_ms_token({"ttwid": "1|x"}) == ""
        assert tiktok_sign.pick_ms_token({"msToken": "abc"}) == "abc"


def _tlv_keys(payload: bytes, *, lead: int) -> list[int]:
    """Walk the TLV entries the platform parses, returning their keys in order."""
    keys: list[int] = []
    offset = lead
    while offset + 3 <= len(payload):
        key, zero, length = payload[offset], payload[offset + 1], payload[offset + 2]
        assert zero == 0, f"entry at {offset} is not [key][0x00][len]"
        keys.append(key)
        offset += 3 + length
    assert offset == len(payload), "payload has a trailing partial entry"
    return keys


class TestNativeSignerOnTikTok:
    """The signer emits the SDK's four parameters and nothing else.

    Live on 2026-09-08, pure Python only and no browser anywhere in the path:
    ``/api/recommend/item_list/`` answered 200 with a full payload and no
    ``tt_orcas_res`` gate. See the module docstring of
    :mod:`dtk.signing.native.tiktok_sign`.
    """

    FINGERPRINT = StaticFingerprint(
        user_agent=TIKTOK_UA_WINDOWS,
        browser_platform="Win32",
        screen_width=1920,
        screen_height=1080,
    )

    async def _sign(self, params: Mapping[str, str], **kwargs) -> SignedParams:
        signer = NativeSigner(Platform.TIKTOK, rng=random.Random(5), **kwargs)
        spec = RequestSpec.get("https://www.tiktok.com/api/recommend/item_list/", params)
        return await signer.sign(spec, self.FINGERPRINT, kwargs.pop("session", None))

    async def test_it_appends_the_four_parameters_in_the_platforms_order(self):
        signed = await self._sign({"aid": "1988", "count": "10"})
        assert list(signed.params) == ["X-Dynosaur", "msToken", "X-Bogus", "X-Gnarly"]
        assert signed.query.startswith("aid=1988&count=10&X-Dynosaur=")
        assert signed.headers == {}
        assert signed.signer == SIGNER_NATIVE

    async def test_the_token_is_the_identitys_and_appears_once(self):
        """A second copy among the business parameters would seal a query TikTok
        never sends, so the signer moves it rather than duplicating it."""
        signer = NativeSigner(Platform.TIKTOK, rng=random.Random(5))
        spec = RequestSpec.get("https://www.tiktok.com/api/recommend/item_list/", {"aid": "1988"})
        signed = await signer.sign(
            spec, self.FINGERPRINT, SigningSession(cookies={"msToken": "JARTOKEN"})
        )
        assert signed.params["msToken"] == "JARTOKEN"
        assert signed.query.count("msToken=") == 1

    async def test_a_pinned_token_wins_over_the_jar(self):
        """What the shadow comparison relies on: both signers sign the same bytes."""
        signer = NativeSigner(Platform.TIKTOK, rng=random.Random(5))
        spec = RequestSpec.get(
            "https://www.tiktok.com/api/recommend/item_list/",
            {"aid": "1988", "msToken": "PINNED"},
        )
        signed = await signer.sign(
            spec, self.FINGERPRINT, SigningSession(cookies={"msToken": "JARTOKEN"})
        )
        assert signed.params["msToken"] == "PINNED"
        assert "msToken=PINNED" in signed.query

    async def test_without_a_jar_it_invents_one_only_when_asked(self):
        signed = await self._sign({"aid": "1988"}, fill_ms_token=False)
        assert signed.params["msToken"] == ""
        assert "&msToken=&" in signed.query
        filled = await self._sign({"aid": "1988"})
        assert len(filled.params["msToken"]) == TIKTOK_MS_TOKEN_LENGTH + 2

    async def test_it_no_longer_computes_a_sixteen_character_x_bogus(self):
        signed = await self._sign({"aid": "1988"})
        assert signed.params["X-Bogus"] == "1"
