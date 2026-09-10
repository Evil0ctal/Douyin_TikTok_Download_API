"""Golden vectors for X-Bogus, captured from the V4 implementation.

Why this file exists: `xbogus.py` (247 lines) is the part of V4 that was ported
rather than rewritten, and V4 shipped it with zero tests - its correctness was
established solely by whether production happened to work. Porting it without a
differential check would have been a coin flip.

Every expectation below was produced by running the ORIGINAL V4 code
(`git show main:crawlers/douyin/web/xbogus.py`) and comparing byte for byte
against this port: 15/15 cases across three User-Agents.

X-Bogus mixes in the wall clock, so every vector here pins it. That is not a
testing convenience: it is why a naive "call it twice and compare" check passes
locally and then fails minutes later.

These vectors are self-referential by construction: they prove the port is
faithful to V4, NOT that V4 is still what the platform expects today. Validating
against the live site is a browser task - see docs/design/16-salvage-and-debug.md.

Why there is no A-Bogus half any more
-------------------------------------
There was one, and it asserted that our `a_bogus` still equalled V4's, prefix by
prefix, with the clock and three random draws pinned. Deleted on 2026-09-09 and
not to be restored, for two reasons that are each sufficient.

The first is licensing. V4's `abogus.py` was a port of GPL-3.0 code
(JoeanAmier/TikTokDownloader) sitting inside an Apache-2.0 project, which is the
reason the algorithm was re-derived from Douyin's own `bdms.js` in the first
place. A golden vector is a copy of that implementation's output frozen into
this repository, and a test that fails unless we reproduce it is a test that
requires the licence conflict to stay.

The second is that the assertion had become exactly backwards. V4's algorithm is
a generation behind the bundle Douyin ships; matching it is now the bug, not the
contract. The replacement oracle is real: `tests/fixtures/signing/abogus_browser.json`
holds three signatures produced by Douyin's own SDK with every input that fed
them, and `tests/unit/test_signing.py` checks that our fifty scalar fields
reproduce the browser's byte for byte. That is a stronger check than this file
ever made, because the reference is the platform rather than ourselves.
"""

from __future__ import annotations

import pytest

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
