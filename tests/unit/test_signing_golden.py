"""Golden vectors for the signing algorithms, captured from the V4 implementation.

Why this file exists: `abogus.py` (635 lines) and `xbogus.py` (247 lines) are the
only parts of V4 that could not realistically be rewritten, and V4 shipped them
with zero tests - their correctness was established solely by whether production
happened to work. Porting them without a differential check would have been a
coin flip.

Every expectation below was produced by running the ORIGINAL V4 code
(`git show main:crawlers/douyin/web/{abogus,xbogus}.py`) under gmssl and
comparing byte for byte against this port. X-Bogus matched on 15/15 cases across
three User-Agents, A-Bogus on 6/6 with the clock and RNG pinned.

Both algorithms mix in the wall clock, so every vector here pins it. That is not
a testing convenience: it is why a naive "call it twice and compare" check passes
locally and then fails minutes later.

These vectors are self-referential by construction: they prove the port is
faithful to V4, NOT that V4 is still what the platform expects today. Validating
against the live site is a browser task - see docs/design/16-salvage-and-debug.md.
"""

from __future__ import annotations

import pytest

from dtk.signing.native.abogus import ABogus
from dtk.signing.native.xbogus import XBogus

UA_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
UA_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
UA_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

Q_DETAIL = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&aweme_id=7372484719365098803&pc_client_type=1&version_code=190500"
    "&version_name=19.5.0"
)
Q_POSTS = (
    "device_platform=webapp&aid=6383"
    "&sec_user_id=MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE"
    "&max_cursor=0&count=20"
)
Q_COMMENTS = (
    "aid=6383&count=20&cursor=0&item_id=7372484719365098803"
    "&device_platform=webapp&channel=channel_pc_web"
)
Q_SEARCH = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&keyword=%E6%B5%8B%E8%AF%95&search_source=normal_search&offset=0&count=15"
)
Q_LIVE = (
    "device_platform=webapp&aid=6383&webcast_sdk_version=1.0.14-beta.0"
    "&room_id=7372484719365098803&web_rid=123456"
)

#: 2025-01-01T00:00:00Z. X-Bogus embeds the clock, so the V4 run these vectors
#: came from had time.time() frozen to this value.
XBOGUS_TIMESTAMP = 1735689600

# (user_agent, query, expected X-Bogus) - all verified against V4 at that instant.
XBOGUS_VECTORS = [
    (UA_WINDOWS, Q_DETAIL, "DFSzswVYLFJANSqMt8kkae9WX7ro"),
    (UA_WINDOWS, Q_POSTS, "DFSzswVYajJANSqMt8kkae9WX7JP"),
    (UA_WINDOWS, Q_COMMENTS, "DFSzswVYzlxANSqMt8kkae9WX7rD"),
    (UA_WINDOWS, Q_SEARCH, "DFSzswVYdShANSqMt8kkae9WX7Jd"),
    (UA_WINDOWS, Q_LIVE, "DFSzswVY1nkANSqMt8kkae9WX7rH"),
    (UA_MAC, Q_DETAIL, "DFSzswVYLFJANylzt8kkae9WX7JQ"),
    (UA_MAC, Q_POSTS, "DFSzswVYajJANylzt8kkae9WX7rZ"),
    (UA_MAC, Q_COMMENTS, "DFSzswVYzlxANylzt8kkae9WX7Ju"),
    (UA_MAC, Q_SEARCH, "DFSzswVYdShANylzt8kkae9WX7rm"),
    (UA_MAC, Q_LIVE, "DFSzswVY1nkANylzt8kkae9WX7J-"),
    (UA_LINUX, Q_DETAIL, "DFSzswVYLFJANHayt8kkae9WX7JO"),
    (UA_LINUX, Q_POSTS, "DFSzswVYajJANHayt8kkae9WX7rq"),
    (UA_LINUX, Q_COMMENTS, "DFSzswVYzlxANHayt8kkae9WX7J8"),
    (UA_LINUX, Q_SEARCH, "DFSzswVYdShANHayt8kkae9WX7r/"),
    (UA_LINUX, Q_LIVE, "DFSzswVY1nkANHayt8kkae9WX7Ja"),
]


@pytest.mark.parametrize(("user_agent", "query", "expected"), XBOGUS_VECTORS)
def test_xbogus_matches_the_v4_implementation(user_agent, query, expected):
    assert XBogus(user_agent=user_agent).sign(query, timestamp=XBOGUS_TIMESTAMP) == expected


def test_xbogus_depends_on_the_user_agent():
    """A signature that ignored the UA would let one identity's TLS profile and
    its claimed browser drift apart without detection."""
    signatures = {
        XBogus(user_agent=ua).sign(Q_DETAIL, timestamp=XBOGUS_TIMESTAMP)
        for ua in (UA_WINDOWS, UA_MAC, UA_LINUX)
    }
    assert len(signatures) == 3


def test_xbogus_is_deterministic_for_a_fixed_clock():
    first = XBogus(user_agent=UA_WINDOWS).sign(Q_DETAIL, timestamp=XBOGUS_TIMESTAMP)
    assert all(
        XBogus(user_agent=UA_WINDOWS).sign(Q_DETAIL, timestamp=XBOGUS_TIMESTAMP) == first
        for _ in range(20)
    )


def test_xbogus_changes_with_the_clock():
    """The timestamp is part of the payload, so two moments must not collide.

    Worth asserting explicitly: a check that signs twice in a row and compares
    passes locally and then fails minutes later, which is a confusing way to
    learn that time is an input.
    """
    a = XBogus(user_agent=UA_WINDOWS).sign(Q_DETAIL, timestamp=XBOGUS_TIMESTAMP)
    b = XBogus(user_agent=UA_WINDOWS).sign(Q_DETAIL, timestamp=XBOGUS_TIMESTAMP + 3600)
    assert a != b


def test_xbogus_length_is_stable():
    for _ua, query, expected in XBOGUS_VECTORS:
        assert len(expected) == 28
        assert len(XBogus(user_agent=UA_WINDOWS).sign(query)) == 28


def test_xbogus_uses_the_current_clock_by_default():
    assert len(XBogus(user_agent=UA_WINDOWS).sign(Q_DETAIL)) == 28


def test_xbogus_rejects_input_it_cannot_decode():
    """V4 mapped unknown characters through a lookup table and carried on,
    producing a plausible-looking but wrong signature. Failing loudly is better:
    a silently wrong signature is indistinguishable from rate limiting."""
    with pytest.raises(ValueError):
        XBogus(user_agent=UA_WINDOWS).sign("a=1")


# A-Bogus mixes in the wall clock and three random draws; pinning them is what
# makes it comparable at all.
ABOGUS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
)
ABOGUS_FIXED = {
    "start_time": 1735689600000,
    "end_time": 1735689601000,
    "random_num_1": 0.1234,
    "random_num_2": 0.5678,
    "random_num_3": 0.9012,
}

ABOGUS_QUERIES = [
    "device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&aweme_id=7372484719365098803&pc_client_type=1",
    "device_platform=webapp&aid=6383"
    "&sec_user_id=MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE"
    "&max_cursor=0&count=20",
    "aid=6383&count=20&cursor=0&item_id=7372484719365098803",
]

# Prefixes verified against V4; the full strings are long, and a prefix mismatch
# is enough to catch any algorithmic drift.
ABOGUS_PREFIXES = {
    (0, "GET"): "DfmhQDgDDDDkDD6D54KLfY3q6vmVYms50SVkMD2f0-DOx639HMPY9exoxZsvfY8j",
    (0, "POST"): "DfmhQDgDDDDkDD6D54KLfY3q6vBHYms50SVkMD2f0WfOx639HMPY9exoxZsvfY8j",
    (1, "GET"): "DfmhQDgDDDDkDD6D54KLfY3q6fgVYms50SVkMD2fuBDOx639HMPY9exoxZsvfY8j",
    (1, "POST"): "DfmhQDgDDDDkDD6D54KLfY3q6fZHYms50SVkMD2fu8fOx639HMPY9exoxZsvfY8j",
    (2, "GET"): "DfmhQDgDDDDkDD6D54KLfY3q6IDVYms50SVkMD2fTPDOx639HMPY9exoxZsvfY8j",
    (2, "POST"): "DfmhQDgDDDDkDD6D54KLfY3q6IpHYms50SVkMD2fTufOx639HMPY9exoxZsvfY8j",
}


@pytest.mark.parametrize(("index", "method"), list(ABOGUS_PREFIXES))
def test_abogus_matches_the_v4_implementation(index, method):
    got = ABogus(user_agent=ABOGUS_UA).get_value(
        ABOGUS_QUERIES[index], method=method, **ABOGUS_FIXED
    )
    assert got.startswith(ABOGUS_PREFIXES[(index, method)])


def test_abogus_distinguishes_get_from_post():
    get = ABogus(user_agent=ABOGUS_UA).get_value(ABOGUS_QUERIES[0], method="GET", **ABOGUS_FIXED)
    post = ABogus(user_agent=ABOGUS_UA).get_value(ABOGUS_QUERIES[0], method="POST", **ABOGUS_FIXED)
    assert get != post


def test_abogus_is_deterministic_when_clock_and_rng_are_pinned():
    first = ABogus(user_agent=ABOGUS_UA).get_value(ABOGUS_QUERIES[0], **ABOGUS_FIXED)
    for _ in range(10):
        assert ABogus(user_agent=ABOGUS_UA).get_value(ABOGUS_QUERIES[0], **ABOGUS_FIXED) == first


def test_abogus_varies_when_the_rng_is_not_pinned():
    """Without this, every request would carry an identical signature - a
    trivially detectable pattern."""
    seen = {ABogus(user_agent=ABOGUS_UA).get_value(ABOGUS_QUERIES[0]) for _ in range(8)}
    assert len(seen) > 1
