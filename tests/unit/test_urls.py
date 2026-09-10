"""Unit tests for URL recognition, normalization and short-link expansion.

Doc 13 asks for this layer to be thick: what users paste is share text with
noise around the link, and every unhandled shape is a support ticket. The
recognition cases below are therefore exhaustive by URL family rather than
representative, and the rejection cases cover the SSRF shapes doc 08 names.

Some inputs contain Chinese characters because Douyin's share sheet wraps the
link in Chinese copy and that is literally what lands in the paste buffer. They
are written as escape sequences so the repository itself stays ASCII; the
comment above each one says what it means.
"""

from __future__ import annotations

import pytest

from dtk.core.errors import DtkError, ErrorCode, InvalidUrl
from dtk.core.types import ContentKind, Platform
from dtk.urls import (
    MAX_REDIRECTS,
    ResourceKind,
    expand,
    extract_urls,
    first_url,
    identify,
    is_allowed_host,
    is_private_host,
    normalize,
    require_supported,
    resolve,
    sanitize_extra_hosts,
)

DOUYIN = Platform.DOUYIN
TIKTOK = Platform.TIKTOK

# Douyin share-sheet copy: "copy, open Douyin, take a look at [xxx's work]".
DOUYIN_SHARE_PREFIX = "7.61 gTa:/ \u590d\u5236\u6253\u5f00\u6296\u97f3\uff0c\u770b\u770b\u3010xxx\u7684\u4f5c\u54c1\u3011"
# Douyin share-sheet trailer: "copy this link, open Douyin and search".
DOUYIN_SHARE_SUFFIX = " \u590d\u5236\u6b64\u94fe\u63a5\uff0c\u6253\u5f00Dou\u97f3\u641c\u7d22"
DOUYIN_SHORT = "https://v.douyin.com/abc123/"
DOUYIN_SHARE_TEXT = f"{DOUYIN_SHARE_PREFIX}{DOUYIN_SHORT}{DOUYIN_SHARE_SUFFIX}"

AWEME_ID = "7345492945006595379"
SEC_USER_ID = "MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y"
TIKTOK_ID = "7255716763118226715"


class FakeRedirects:
    """Stand-in for the transport layer's single-hop redirect fetcher.

    Maps a URL to the ``Location`` it answers with; anything absent from the
    map is a final destination. Records calls so tests can assert that no
    request was made when none was needed.
    """

    def __init__(self, hops: dict[str, str] | None = None) -> None:
        self.hops = hops or {}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> str | None:
        self.calls.append(url)
        return self.hops.get(url)


# ---------------------------------------------------------------------------
# extract_urls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "https://www.douyin.com/video/7345492945006595379",
            ["https://www.douyin.com/video/7345492945006595379"],
            id="bare-url",
        ),
        pytest.param(DOUYIN_SHARE_TEXT, [DOUYIN_SHORT], id="douyin-share-text"),
        pytest.param(
            "Check out this video on TikTok! https://vm.tiktok.com/ZSxYzAbC/ thanks",
            ["https://vm.tiktok.com/ZSxYzAbC/"],
            id="tiktok-share-text",
        ),
        pytest.param(
            # CJK full stop glued straight onto the link, no separating space.
            "\u770b\u770b\u8fd9\u4e2a https://v.douyin.com/abc123/\u3002",
            ["https://v.douyin.com/abc123/"],
            id="trailing-cjk-punctuation",
        ),
        pytest.param(
            "see https://www.douyin.com/video/7345492945006595379.",
            ["https://www.douyin.com/video/7345492945006595379"],
            id="trailing-ascii-period",
        ),
        pytest.param(
            "(https://www.douyin.com/video/7345492945006595379)",
            ["https://www.douyin.com/video/7345492945006595379"],
            id="wrapped-in-parens",
        ),
        pytest.param(
            "\u3010https://www.douyin.com/video/7345492945006595379\u3011",
            ["https://www.douyin.com/video/7345492945006595379"],
            id="wrapped-in-cjk-brackets",
        ),
        pytest.param(
            "a https://v.douyin.com/aaa/ b https://vm.tiktok.com/bbb/ c",
            ["https://v.douyin.com/aaa/", "https://vm.tiktok.com/bbb/"],
            id="two-urls-keep-order",
        ),
        pytest.param(
            "https://v.douyin.com/aaa/ and again https://v.douyin.com/aaa/",
            ["https://v.douyin.com/aaa/"],
            id="duplicates-collapsed",
        ),
        pytest.param(
            "\u6253\u5f00 v.douyin.com/abc123/ \u770b\u770b",
            ["https://v.douyin.com/abc123/"],
            id="scheme-less-short-link",
        ),
        pytest.param(
            "www.tiktok.com/@tiktok/video/7255716763118226715",
            ["https://www.tiktok.com/@tiktok/video/7255716763118226715"],
            id="scheme-less-full-link",
        ),
        pytest.param("no link here at all", [], id="no-url"),
        pytest.param("", [], id="empty"),
        pytest.param(
            "mention of douyin.com in prose",
            [],
            id="bare-domain-without-path-is-not-a-link",
        ),
        pytest.param(
            "notdouyin.com/video/1",
            [],
            id="lookalike-domain-not-matched-scheme-less",
        ),
        pytest.param(
            "http://example.com/a https://www.douyin.com/video/1",
            ["http://example.com/a", "https://www.douyin.com/video/1"],
            id="off-platform-urls-are-candidates-too",
        ),
        pytest.param(
            "https://www.tiktok.com/@u/video/7255716763118226715?is_from_webapp=1&sender_device=pc",
            [
                "https://www.tiktok.com/@u/video/7255716763118226715?is_from_webapp=1&sender_device=pc"
            ],
            id="query-string-preserved",
        ),
        pytest.param(
            "line one\nhttps://v.douyin.com/aaa/\nline three",
            ["https://v.douyin.com/aaa/"],
            id="newline-delimited",
        ),
    ],
)
def test_extract_urls(text: str, expected: list[str]) -> None:
    assert extract_urls(text) == expected


def test_first_url_returns_none_without_a_url() -> None:
    assert first_url("nothing to see") is None


def test_first_url_picks_the_leading_link() -> None:
    assert first_url(DOUYIN_SHARE_TEXT) == DOUYIN_SHORT


# ---------------------------------------------------------------------------
# identify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "platform", "resource", "resource_id"),
    [
        # -- Douyin works ---------------------------------------------------
        pytest.param(
            f"https://www.douyin.com/video/{AWEME_ID}",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-video",
        ),
        pytest.param(
            f"https://www.douyin.com/video/{AWEME_ID}?previous_page=web_code_link",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-video-with-query",
        ),
        pytest.param(
            f"https://www.douyin.com/note/{AWEME_ID}",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-note",
        ),
        pytest.param(
            f"https://www.douyin.com/slides/{AWEME_ID}",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-slides",
        ),
        pytest.param(
            f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN&titleType=title",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-legacy-share-video",
        ),
        pytest.param(
            f"https://www.iesdouyin.com/share/note/{AWEME_ID}/",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-legacy-share-note",
        ),
        pytest.param(
            f"https://www.douyin.com/discover?modal_id={AWEME_ID}",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-discover-modal",
        ),
        pytest.param(
            f"https://www.douyin.com/user/{SEC_USER_ID}?modal_id={AWEME_ID}",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-work-opened-on-a-profile-wins-over-the-profile",
        ),
        pytest.param(
            f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?vid={AWEME_ID}",
            DOUYIN,
            ResourceKind.VIDEO,
            AWEME_ID,
            id="douyin-vid-query-new-format",
        ),
        # -- Douyin authors, live, collections ------------------------------
        pytest.param(
            f"https://www.douyin.com/user/{SEC_USER_ID}",
            DOUYIN,
            ResourceKind.USER,
            SEC_USER_ID,
            id="douyin-user",
        ),
        pytest.param(
            f"https://www.iesdouyin.com/share/user/{SEC_USER_ID}",
            DOUYIN,
            ResourceKind.USER,
            SEC_USER_ID,
            id="douyin-legacy-share-user",
        ),
        pytest.param(
            # What a profile short link actually expands to: the path segment
            # is empty and the author is only in the query. V4's
            # SecUserIdFetcher matched exactly this shape for v.douyin.com.
            f"https://www.iesdouyin.com/share/user/?sec_uid={SEC_USER_ID}",
            DOUYIN,
            ResourceKind.USER,
            SEC_USER_ID,
            id="douyin-share-user-with-sec-uid-only-in-the-query",
        ),
        pytest.param(
            f"https://www.douyin.com/share/user?sec_user_id={SEC_USER_ID}",
            DOUYIN,
            ResourceKind.USER,
            SEC_USER_ID,
            id="douyin-share-user-sec-user-id-spelling",
        ),
        pytest.param(
            "https://live.douyin.com/766545142636?cover_type=0&web_live_tab=all",
            DOUYIN,
            ResourceKind.LIVE,
            "766545142636",
            id="douyin-live-room",
        ),
        pytest.param(
            "https://live.douyin.com/766545142636",
            DOUYIN,
            ResourceKind.LIVE,
            "766545142636",
            id="douyin-live-room-bare",
        ),
        pytest.param(
            "https://webcast.amemv.com/douyin/webcast/reflow/7318296342189919011?u_code=l1j9bkbd",
            DOUYIN,
            ResourceKind.LIVE_ROOM,
            "7318296342189919011",
            id="douyin-live-reflow-carries-a-room-id",
        ),
        pytest.param(
            "https://www.douyin.com/collection/7123456789012345678",
            DOUYIN,
            ResourceKind.MIX,
            "7123456789012345678",
            id="douyin-collection",
        ),
        pytest.param(
            "https://www.douyin.com/video/7123?mix_id=7999",
            DOUYIN,
            ResourceKind.VIDEO,
            "7123",
            id="douyin-video-inside-a-mix-is-still-the-video",
        ),
        pytest.param(
            "https://www.douyin.com/music/7123456789012345678",
            DOUYIN,
            ResourceKind.MUSIC,
            "7123456789012345678",
            id="douyin-music",
        ),
        pytest.param(
            "https://www.douyin.com/hashtag/1668396086001165",
            DOUYIN,
            ResourceKind.CHALLENGE,
            "1668396086001165",
            id="douyin-hashtag",
        ),
        pytest.param(
            "https://www.douyin.com/challenge/1668396086001165",
            DOUYIN,
            ResourceKind.CHALLENGE,
            "1668396086001165",
            id="douyin-challenge-alias",
        ),
        pytest.param(
            "https://www.douyin.com/search/cat%20videos",
            DOUYIN,
            ResourceKind.SEARCH,
            "cat videos",
            id="douyin-search-keyword-is-decoded",
        ),
        # -- Douyin short links ---------------------------------------------
        pytest.param(
            "https://v.douyin.com/iRNBho6u/",
            DOUYIN,
            ResourceKind.SHORT_LINK,
            "iRNBho6u",
            id="douyin-short-link",
        ),
        pytest.param(
            "HTTPS://V.DOUYIN.COM/iRNBho6u/",
            DOUYIN,
            ResourceKind.SHORT_LINK,
            "iRNBho6u",
            id="douyin-short-link-uppercase-host",
        ),
        # -- TikTok ---------------------------------------------------------
        pytest.param(
            f"https://www.tiktok.com/@scarlettjonesuk/video/{TIKTOK_ID}",
            TIKTOK,
            ResourceKind.VIDEO,
            TIKTOK_ID,
            id="tiktok-video",
        ),
        pytest.param(
            f"https://www.tiktok.com/@scarlettjonesuk/video/{TIKTOK_ID}"
            "?is_from_webapp=1&sender_device=pc&web_id=7306060721837852167",
            TIKTOK,
            ResourceKind.VIDEO,
            TIKTOK_ID,
            id="tiktok-video-with-share-query",
        ),
        pytest.param(
            "https://www.tiktok.com/@zoyapea5/photo/7370061866879454469",
            TIKTOK,
            ResourceKind.VIDEO,
            "7370061866879454469",
            id="tiktok-photo-mode",
        ),
        pytest.param(
            "https://www.tiktok.com/@tiktok",
            TIKTOK,
            ResourceKind.USER,
            "tiktok",
            id="tiktok-user",
        ),
        pytest.param(
            "https://www.tiktok.com/@tiktok/",
            TIKTOK,
            ResourceKind.USER,
            "tiktok",
            id="tiktok-user-trailing-slash",
        ),
        pytest.param(
            "https://www.tiktok.com/@tiktok?lang=en",
            TIKTOK,
            ResourceKind.USER,
            "tiktok",
            id="tiktok-user-with-query",
        ),
        pytest.param(
            "https://www.tiktok.com/@tiktok/live",
            TIKTOK,
            ResourceKind.LIVE,
            "tiktok",
            id="tiktok-live",
        ),
        pytest.param(
            "https://vt.tiktok.com/ZSxYzAbC/",
            TIKTOK,
            ResourceKind.SHORT_LINK,
            "ZSxYzAbC",
            id="tiktok-vt-short-link",
        ),
        pytest.param(
            "https://vm.tiktok.com/ZSxYzAbC",
            TIKTOK,
            ResourceKind.SHORT_LINK,
            "ZSxYzAbC",
            id="tiktok-vm-short-link",
        ),
        pytest.param(
            "https://www.tiktok.com/t/ZTRQxAbcd/",
            TIKTOK,
            ResourceKind.SHORT_LINK,
            "ZTRQxAbcd",
            id="tiktok-path-short-link",
        ),
        pytest.param(
            "https://m.tiktok.com/v/6977813984663211269.html",
            TIKTOK,
            ResourceKind.VIDEO,
            "6977813984663211269",
            id="tiktok-legacy-mobile-link",
        ),
        pytest.param(
            "https://www.tiktok.com/embed/v2/6977813984663211269",
            TIKTOK,
            ResourceKind.VIDEO,
            "6977813984663211269",
            id="tiktok-embed",
        ),
        pytest.param(
            "https://www.tiktok.com/music/Original-Sound-7123456789012345678",
            TIKTOK,
            ResourceKind.MUSIC,
            "7123456789012345678",
            id="tiktok-music-slug-with-hyphens",
        ),
        pytest.param(
            "https://www.tiktok.com/tag/fyp",
            TIKTOK,
            ResourceKind.CHALLENGE,
            "fyp",
            id="tiktok-tag",
        ),
        pytest.param(
            "https://www.tiktok.com/search?q=cats&lang=en",
            TIKTOK,
            ResourceKind.SEARCH,
            "cats",
            id="tiktok-search",
        ),
        pytest.param(
            # "+" is a space in a query string, so the keyword is two words.
            "https://www.tiktok.com/search?q=cat+videos",
            TIKTOK,
            ResourceKind.SEARCH,
            "cat videos",
            id="tiktok-search-plus-is-a-space-in-a-query",
        ),
        pytest.param(
            # ... but "+" in a path segment is a literal plus, not a space.
            "https://www.douyin.com/search/cat+videos",
            DOUYIN,
            ResourceKind.SEARCH,
            "cat+videos",
            id="douyin-search-plus-is-literal-in-a-path",
        ),
    ],
)
def test_identify(url: str, platform: Platform, resource: ResourceKind, resource_id: str) -> None:
    kind = identify(url)
    assert kind.platform is platform
    assert kind.resource is resource
    assert kind.resource_id == resource_id
    assert kind.allowed
    assert kind.recognized


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(f"https://www.douyin.com/video/{AWEME_ID}", ContentKind.VIDEO, id="video"),
        pytest.param(
            f"https://www.douyin.com/note/{AWEME_ID}", ContentKind.IMAGE_ALBUM, id="douyin-note"
        ),
        pytest.param(
            "https://www.tiktok.com/@u/photo/7370061866879454469",
            ContentKind.IMAGE_ALBUM,
            id="tiktok-photo",
        ),
        pytest.param("https://live.douyin.com/766545142636", ContentKind.LIVE, id="live"),
        pytest.param(
            f"https://www.douyin.com/discover?modal_id={AWEME_ID}",
            None,
            id="modal-id-says-nothing-about-the-kind",
        ),
        pytest.param(f"https://www.douyin.com/user/{SEC_USER_ID}", None, id="user-has-no-kind"),
    ],
)
def test_identify_content_kind_hint(url: str, expected: ContentKind | None) -> None:
    assert identify(url).content_kind is expected


def test_identify_keeps_the_tiktok_handle() -> None:
    kind = identify(f"https://www.tiktok.com/@scarlettjonesuk/video/{TIKTOK_ID}")
    assert kind.handle == "scarlettjonesuk"


def test_a_work_share_link_that_also_carries_the_author_is_still_the_work() -> None:
    """The ``sec_uid`` fallback must not outrank a path that names a work."""
    kind = identify(
        f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN&sec_uid={SEC_USER_ID}"
    )
    assert kind.resource is ResourceKind.VIDEO
    assert kind.resource_id == AWEME_ID


def test_normalize_keeps_the_author_id_it_is_the_only_identifier() -> None:
    """``sec_uid``/``sec_user_id`` are identifiers, not share noise."""
    assert normalize(f"https://www.douyin.com/share/user?sec_user_id={SEC_USER_ID}") == (
        f"https://www.douyin.com/user/{SEC_USER_ID}"
    )


def test_identify_douyin_video_has_no_handle() -> None:
    assert identify(f"https://www.douyin.com/video/{AWEME_ID}").handle is None


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://v.douyin.com/abc123/", id="douyin-short"),
        pytest.param("https://vm.tiktok.com/ZSxx/", id="tiktok-vm-short"),
        pytest.param("https://www.tiktok.com/t/ZTxx/", id="tiktok-path-short"),
    ],
)
def test_short_links_are_flagged_for_expansion(url: str) -> None:
    assert identify(url).needs_expansion is True


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(f"https://www.douyin.com/video/{AWEME_ID}", id="douyin-video"),
        pytest.param(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}", id="tiktok-video"),
    ],
)
def test_full_links_are_not_flagged_for_expansion(url: str) -> None:
    assert identify(url).needs_expansion is False


def test_platform_host_without_a_known_resource_is_allowed_but_unrecognized() -> None:
    kind = identify("https://www.douyin.com/")
    assert kind.platform is DOUYIN
    assert kind.resource is ResourceKind.UNKNOWN
    assert kind.allowed
    assert not kind.recognized


# ---------------------------------------------------------------------------
# Rejection: the SSRF surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://localhost/video/1", id="localhost"),
        pytest.param("http://localhost:8080/video/1", id="localhost-with-port"),
        pytest.param("http://127.0.0.1/video/1", id="loopback-v4"),
        pytest.param("http://127.1/video/1", id="short-loopback-form"),
        pytest.param("http://[::1]/video/1", id="loopback-v6"),
        pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
        pytest.param("http://10.0.0.5/video/1", id="private-10"),
        pytest.param("http://192.168.1.1/video/1", id="private-192"),
        pytest.param("http://172.16.0.1/video/1", id="private-172"),
        pytest.param("http://0.0.0.0/video/1", id="unspecified"),
        pytest.param("http://2130706433/video/1", id="decimal-loopback"),
        pytest.param("http://0x7f000001/video/1", id="hex-loopback"),
        pytest.param("http://[fd00::1]/video/1", id="unique-local-v6"),
        pytest.param("http://metadata.google.internal/", id="internal-suffix"),
        pytest.param("http://nas.local/video/1", id="mdns-suffix"),
        pytest.param("http://intranet/video/1", id="single-label-host"),
        pytest.param("https://douyin.com.evil.com/video/1", id="suffix-appended"),
        pytest.param("https://evildouyin.com/video/1", id="domain-prefix-glued"),
        pytest.param("https://tiktok.com.attacker.io/@u/video/1", id="tiktok-suffix-appended"),
        pytest.param("https://www.douyin.com@evil.com/video/1", id="userinfo-hides-real-host"),
        pytest.param("https://evil.com@www.douyin.com/video/1", id="userinfo-before-real-host"),
        pytest.param("https://www.douyin.com:8080/video/1", id="non-default-port"),
        pytest.param("javascript:alert(1)", id="javascript-scheme"),
        pytest.param("data:text/html,<script>x</script>", id="data-scheme"),
        pytest.param("file:///etc/passwd", id="file-scheme"),
        pytest.param("ftp://www.douyin.com/video/1", id="ftp-scheme"),
        pytest.param("gopher://www.douyin.com:70/_", id="gopher-scheme"),
        pytest.param("https://www.dou\u0443in.com/video/1", id="cyrillic-homograph"),
        pytest.param("https://example.com/video/1", id="off-platform"),
        pytest.param("not a url at all", id="not-a-url"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("https://", id="scheme-only"),
    ],
)
def test_rejected_targets(url: str) -> None:
    kind = identify(url)
    assert kind.platform is None
    assert kind.resource is ResourceKind.UNKNOWN
    assert kind.url is None
    assert not kind.allowed
    assert is_allowed_host(url) is False


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(f"https://www.douyin.com/video/{AWEME_ID}", id="douyin"),
        pytest.param("https://www.douyin.com./video/1", id="trailing-dot-host"),
        pytest.param("http://www.douyin.com/video/1", id="plain-http"),
        pytest.param("https://www.douyin.com:443/video/1", id="explicit-default-port"),
        pytest.param("https://v.douyin.com/abc/", id="short-link"),
        pytest.param(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}", id="tiktok"),
        pytest.param("https://WWW.TIKTOK.COM/@u", id="uppercase-host"),
    ],
)
def test_allowed_targets(url: str) -> None:
    assert is_allowed_host(url) is True


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "::1",
        "[::1]",
        "0.0.0.0",
        "fd00::1",
        "100.64.0.1",
        "::ffff:127.0.0.1",
        "2130706433",
        "0x7f000001",
        "printer",
        "db.internal",
        "foo.local",
    ],
)
def test_is_private_host(host: str) -> None:
    assert is_private_host(host) is True


@pytest.mark.parametrize("host", ["www.douyin.com", "v.douyin.com", "www.tiktok.com", "8.8.8.8"])
def test_is_private_host_allows_public_names(host: str) -> None:
    assert is_private_host(host) is False


# ---------------------------------------------------------------------------
# security.url_allowlist: the hosts an operator adds
#
# The console calls this the SSRF boundary and warns about it in red, so these
# assert the shape of what an entry may do as tightly as what it may not. An
# entry is one exact host, it carries no platform, and it is checked after the
# scheme, port, userinfo and private-range rules rather than instead of them.
# ---------------------------------------------------------------------------

EXTRA = frozenset({"cdn.example.com"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(["cdn.example.com"], {"cdn.example.com"}, id="plain"),
        pytest.param(["  CDN.Example.COM.  "], {"cdn.example.com"}, id="case-and-trailing-dot"),
        pytest.param(["a.example.com", "a.example.com"], {"a.example.com"}, id="deduplicated"),
        pytest.param(["", "  "], set(), id="blank-entries-dropped"),
    ],
)
def test_sanitize_extra_hosts(raw: list[str], expected: set[str]) -> None:
    assert sanitize_extra_hosts(raw) == expected


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param("localhost", id="loopback-name"),
        pytest.param("127.0.0.1", id="loopback-literal"),
        pytest.param("169.254.169.254", id="cloud-metadata"),
        pytest.param("10.0.0.1", id="private-range"),
        pytest.param("db.internal", id="intranet-suffix"),
        pytest.param("printer", id="single-label"),
        pytest.param("*.example.com", id="wildcard"),
        pytest.param("https://cdn.example.com/x", id="a-whole-url"),
        pytest.param("cdn.example.com:8080", id="host-and-port"),
        pytest.param("cdn.ex\u00e4mple.com", id="non-ascii"),
        pytest.param(7, id="not-even-a-string"),
    ],
)
def test_sanitize_extra_hosts_refuses(entry: object) -> None:
    with pytest.raises(ValueError):
        sanitize_extra_hosts([entry])  # type: ignore[list-item]


def test_an_extra_host_is_allowed_but_belongs_to_no_platform() -> None:
    """It may be fetched. It can never become a call: there is no route table."""
    kind = identify("https://cdn.example.com/whatever", extra_hosts=EXTRA)
    assert kind.allowed
    assert kind.platform is None
    assert kind.resource is ResourceKind.UNKNOWN
    assert not kind.recognized


def test_an_extra_host_matches_exactly_and_nothing_beneath_it() -> None:
    assert is_allowed_host("https://cdn.example.com/x", extra_hosts=EXTRA) is True
    assert is_allowed_host("https://sub.cdn.example.com/x", extra_hosts=EXTRA) is False
    assert is_allowed_host("https://example.com/x", extra_hosts=EXTRA) is False
    assert is_allowed_host("https://cdn.example.com.evil.io/x", extra_hosts=EXTRA) is False


def test_the_allowlist_is_empty_unless_it_is_passed() -> None:
    """The default is not "whatever the last caller used": it is nothing."""
    assert is_allowed_host("https://cdn.example.com/x") is False


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://169.254.169.254/latest/meta-data/", id="link-local"),
        pytest.param("http://127.0.0.1/x", id="loopback"),
        pytest.param("https://cdn.example.com:8080/x", id="odd-port"),
        pytest.param("https://evil.com@cdn.example.com/x", id="userinfo"),
        pytest.param("file:///etc/passwd", id="file-scheme"),
    ],
)
def test_an_entry_cannot_buy_its_way_past_the_other_rules(url: str) -> None:
    """Even handed the host verbatim, the checks ahead of the allowlist stand.

    sanitize_extra_hosts refuses these entries, so this is the second line: a
    list hand-edited into the database still cannot reach any of them.
    """
    hosts = frozenset({"169.254.169.254", "127.0.0.1", "cdn.example.com"})
    assert is_allowed_host(url, extra_hosts=hosts) is False


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            f"HTTPS://WWW.DOUYIN.COM/video/{AWEME_ID}",
            f"https://www.douyin.com/video/{AWEME_ID}",
            id="host-lowercased",
        ),
        pytest.param(
            f"http://www.douyin.com/video/{AWEME_ID}",
            f"https://www.douyin.com/video/{AWEME_ID}",
            id="http-upgraded-to-https",
        ),
        pytest.param(
            f"https://www.douyin.com/video/{AWEME_ID}?is_from_webapp=1&sender_device=pc",
            f"https://www.douyin.com/video/{AWEME_ID}",
            id="tracking-params-dropped",
        ),
        pytest.param(
            f"https://www.douyin.com/video/{AWEME_ID}#comments",
            f"https://www.douyin.com/video/{AWEME_ID}",
            id="fragment-dropped",
        ),
        pytest.param(
            f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN&u_code=abc",
            f"https://www.douyin.com/video/{AWEME_ID}",
            id="legacy-share-link-rewritten-to-the-web-page",
        ),
        pytest.param(
            f"https://www.douyin.com/user/{SEC_USER_ID}/",
            f"https://www.douyin.com/user/{SEC_USER_ID}",
            id="trailing-slash-dropped",
        ),
        pytest.param(
            "https://vm.tiktok.com/ZSxYzAbC/",
            "https://vm.tiktok.com/ZSxYzAbC",
            id="short-link-keeps-code-case",
        ),
        pytest.param(
            f"https://www.tiktok.com/@Scarlett/video/{TIKTOK_ID}?is_from_webapp=1",
            f"https://www.tiktok.com/@Scarlett/video/{TIKTOK_ID}",
            id="tiktok-handle-case-preserved",
        ),
        pytest.param(
            "https://www.douyin.com/unknown/path/?b=2&a=1&web_id=9",
            "https://www.douyin.com/unknown/path?a=1&b=2",
            id="unknown-path-keeps-sorted-meaningful-query",
        ),
        pytest.param(
            "https://www.douyin.com",
            "https://www.douyin.com/",
            id="apex-normalizes-to-root",
        ),
        pytest.param(
            "www.tiktok.com/@tiktok",
            "https://www.tiktok.com/@tiktok",
            id="missing-scheme-added",
        ),
    ],
)
def test_normalize(url: str, expected: str) -> None:
    assert normalize(url) == expected


def test_normalize_strips_signature_and_session_params() -> None:
    """A URL copied out of DevTools must not carry its credentials any further."""
    normalized = normalize(
        "https://www.douyin.com/unknown/page?msToken=SECRET&a_bogus=SECRET&sessionid=SECRET&keep=1"
    )
    assert normalized == "https://www.douyin.com/unknown/page?keep=1"


def test_normalize_is_idempotent() -> None:
    once = normalize(f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN")
    assert normalize(once) == once


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "https://douyin.com.evil.com/video/1", "nonsense"],
)
def test_normalize_rejects_disallowed_targets(url: str) -> None:
    with pytest.raises(InvalidUrl) as excinfo:
        normalize(url)
    assert excinfo.value.code is ErrorCode.INVALID_URL
    assert excinfo.value.retryable is False


def test_require_supported_rejects_a_platform_page_with_no_resource() -> None:
    with pytest.raises(InvalidUrl) as excinfo:
        require_supported("https://www.douyin.com/")
    assert excinfo.value.details["reason"] == "unknown_resource"


def test_require_supported_accepts_a_short_link() -> None:
    kind = require_supported("https://v.douyin.com/abc123/")
    assert kind.resource is ResourceKind.SHORT_LINK


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------


async def test_expand_follows_one_hop() -> None:
    fetcher = FakeRedirects(
        {
            "https://v.douyin.com/abc123": (
                f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN&u_code=x"
            )
        }
    )
    assert await expand("https://v.douyin.com/abc123", fetcher) == (
        f"https://www.douyin.com/video/{AWEME_ID}"
    )
    assert len(fetcher.calls) == 2


async def test_expand_follows_several_hops() -> None:
    fetcher = FakeRedirects(
        {
            "https://v.douyin.com/abc123": "https://www.douyin.com/hop/1",
            "https://www.douyin.com/hop/1": "https://www.douyin.com/hop/2",
            "https://www.douyin.com/hop/2": f"https://www.douyin.com/video/{AWEME_ID}",
        }
    )
    assert await expand("https://v.douyin.com/abc123", fetcher) == (
        f"https://www.douyin.com/video/{AWEME_ID}"
    )


async def test_expand_resolves_a_relative_location() -> None:
    fetcher = FakeRedirects({"https://www.douyin.com/hop/1": f"/video/{AWEME_ID}"})
    assert await expand("https://www.douyin.com/hop/1", fetcher) == (
        f"https://www.douyin.com/video/{AWEME_ID}"
    )


async def test_expand_returns_the_start_when_nothing_redirects() -> None:
    fetcher = FakeRedirects()
    assert await expand(f"https://www.douyin.com/video/{AWEME_ID}", fetcher) == (
        f"https://www.douyin.com/video/{AWEME_ID}"
    )


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("https://evil.com/payload", id="off-platform"),
        pytest.param("http://127.0.0.1:6379/", id="loopback"),
        pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
        pytest.param("file:///etc/passwd", id="scheme-downgrade"),
        pytest.param("https://douyin.com.evil.com/video/1", id="lookalike-host"),
    ],
)
async def test_expand_revalidates_every_hop(location: str) -> None:
    """A short link is opaque until followed; doc 08 requires this second check."""
    fetcher = FakeRedirects({"https://v.douyin.com/abc123": location})
    with pytest.raises(InvalidUrl) as excinfo:
        await expand("https://v.douyin.com/abc123", fetcher)
    assert excinfo.value.details["reason"] == "redirect_not_allowed"


async def test_expand_rejects_a_disallowed_start_without_fetching() -> None:
    fetcher = FakeRedirects()
    with pytest.raises(InvalidUrl):
        await expand("http://127.0.0.1/", fetcher)
    assert fetcher.calls == []


async def test_expand_stops_when_a_loop_comes_back_round() -> None:
    """It terminates, and hands back what it has rather than an error.

    A short link that leads back to itself is not a URL problem, and calling it
    one used to hide the real answer: `resolve` sees a SHORT_LINK come out and
    reports `unresolved_short_link`, which is what actually happened.
    """
    fetcher = FakeRedirects(
        {
            "https://v.douyin.com/abc123": "https://www.douyin.com/hop/1",
            "https://www.douyin.com/hop/1": "https://v.douyin.com/abc123",
        }
    )
    assert await expand("https://v.douyin.com/abc123", fetcher) == "https://v.douyin.com/abc123"


async def test_the_two_hop_douyin_share_chain_is_not_a_loop() -> None:
    """The regression this exists for: the commonest share link there is.

    `iesdouyin.com/share/video/123` and `douyin.com/video/123` normalize to one
    canonical URL, so comparing canonical forms called the second hop a repeat
    of the first and refused every `v.douyin.com` link with `redirect_loop`.
    """
    fetcher = FakeRedirects(
        {
            "https://v.douyin.com/L4NpDJ6/": (
                "https://www.iesdouyin.com/share/video/6914948781100338440/"
                "?region=CN&from=web_code_link"
            ),
            # Keyed with the query the first hop's Location actually carries:
            # `expand` follows the raw URL, not its canonical form, so a key
            # without it would settle a hop early and never walk the chain.
            "https://www.iesdouyin.com/share/video/6914948781100338440/"
            "?region=CN&from=web_code_link": (
                "https://www.douyin.com/video/6914948781100338440?previous_page=web_code_link"
            ),
        }
    )
    final = await expand("https://v.douyin.com/L4NpDJ6/", fetcher)
    assert final == "https://www.douyin.com/video/6914948781100338440"
    assert len(fetcher.calls) == 2, "both hops have to be walked for this to prove anything"


async def test_expand_stops_after_max_hops() -> None:
    fetcher = FakeRedirects(
        {f"https://www.douyin.com/hop/{n}": f"https://www.douyin.com/hop/{n + 1}" for n in range(9)}
    )
    with pytest.raises(InvalidUrl) as excinfo:
        await expand("https://www.douyin.com/hop/0", fetcher, max_hops=3)
    assert excinfo.value.details["reason"] == "too_many_redirects"
    assert len(fetcher.calls) == 3


async def test_expand_default_hop_budget_is_bounded() -> None:
    fetcher = FakeRedirects(
        {
            f"https://www.douyin.com/hop/{n}": f"https://www.douyin.com/hop/{n + 1}"
            for n in range(MAX_REDIRECTS + 5)
        }
    )
    with pytest.raises(InvalidUrl):
        await expand("https://www.douyin.com/hop/0", fetcher)
    assert len(fetcher.calls) == MAX_REDIRECTS


async def test_expand_follows_a_hop_through_an_operator_allowlisted_host() -> None:
    """The whole reason security.url_allowlist exists.

    A chain that detours through a host nobody anticipated dies at the hop
    re-check, and the operator has no other lever for it.
    """
    hops = {
        "https://v.douyin.com/abc123": "https://cdn.example.com/r/abc123",
        "https://cdn.example.com/r/abc123": f"https://www.douyin.com/video/{AWEME_ID}",
    }
    assert (
        await expand("https://v.douyin.com/abc123", FakeRedirects(hops), extra_hosts=EXTRA)
        == f"https://www.douyin.com/video/{AWEME_ID}"
    )

    with pytest.raises(InvalidUrl) as excinfo:
        await expand("https://v.douyin.com/abc123", FakeRedirects(hops))
    assert excinfo.value.details["reason"] == "redirect_not_allowed"


async def test_expand_rejects_a_zero_hop_budget() -> None:
    with pytest.raises(ValueError, match="max_hops"):
        await expand("https://v.douyin.com/abc123", FakeRedirects(), max_hops=0)


# ---------------------------------------------------------------------------
# resolve: the end-to-end path a pasted string takes
# ---------------------------------------------------------------------------


async def test_resolve_share_text_with_a_short_link() -> None:
    fetcher = FakeRedirects(
        {"https://v.douyin.com/abc123": f"https://www.iesdouyin.com/share/video/{AWEME_ID}/"}
    )
    kind = await resolve(DOUYIN_SHARE_TEXT, fetcher)
    assert kind.platform is DOUYIN
    assert kind.resource is ResourceKind.VIDEO
    assert kind.resource_id == AWEME_ID
    assert kind.url == f"https://www.douyin.com/video/{AWEME_ID}"


async def test_resolve_a_profile_short_link() -> None:
    """The shape V4's SecUserIdFetcher existed for: author only in the query."""
    fetcher = FakeRedirects(
        {
            "https://v.douyin.com/abc123": (
                f"https://www.iesdouyin.com/share/user/?sec_uid={SEC_USER_ID}&u_code=x"
            )
        }
    )
    kind = await resolve("https://v.douyin.com/abc123/", fetcher)
    assert kind.resource is ResourceKind.USER
    assert kind.resource_id == SEC_USER_ID
    assert kind.url == f"https://www.douyin.com/user/{SEC_USER_ID}"


async def test_resolve_does_not_fetch_a_full_link() -> None:
    fetcher = FakeRedirects()
    kind = await resolve(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}", fetcher)
    assert kind.resource_id == TIKTOK_ID
    assert fetcher.calls == []


async def test_resolve_without_any_url() -> None:
    with pytest.raises(InvalidUrl) as excinfo:
        await resolve("just some text", FakeRedirects())
    assert excinfo.value.details["reason"] == "no_url"


async def test_resolve_rejects_an_off_platform_url() -> None:
    with pytest.raises(InvalidUrl) as excinfo:
        await resolve("https://example.com/video/1", FakeRedirects())
    assert excinfo.value.details["reason"] == "host_not_allowed"


async def test_resolve_rejects_a_short_link_landing_on_a_page_we_cannot_use() -> None:
    """Named apart from a URL nobody recognises: the paste was fine, the chain
    was not, and only one of those is the reader's to fix."""
    fetcher = FakeRedirects({"https://v.douyin.com/abc123": "https://www.douyin.com/"})
    with pytest.raises(InvalidUrl) as excinfo:
        await resolve("https://v.douyin.com/abc123/", fetcher)
    assert excinfo.value.details["reason"] == "short_link_dead_end"
    assert excinfo.value.details["resolved_to"] == "https://www.douyin.com/"


async def test_resolve_refuses_a_chain_that_ends_on_an_allowlisted_host() -> None:
    """Widening expansion is not widening what may be fetched as a resource."""
    fetcher = FakeRedirects({"https://v.douyin.com/abc123": "https://cdn.example.com/landing"})
    with pytest.raises(InvalidUrl) as excinfo:
        await resolve("https://v.douyin.com/abc123/", fetcher, extra_hosts=EXTRA)
    assert excinfo.value.details["reason"] == "short_link_dead_end"


async def test_resolve_errors_are_not_retryable() -> None:
    with pytest.raises(DtkError) as excinfo:
        await resolve("https://example.com/", FakeRedirects())
    assert excinfo.value.http_status == 400
    assert excinfo.value.retryable is False


async def test_a_short_link_that_lands_on_the_front_page_says_so() -> None:
    """Measured against TikTok: `/t/<slug>` answers a server-side client with a
    302 to its own home page. The link is fine and the paste is fine, so an
    error that reads "you pasted something unrecognisable" sends the reader off
    to check the one thing that is not wrong."""
    # Keyed on the canonical form, which is what `resolve` hands to `expand`.
    fetcher = FakeRedirects({"https://www.tiktok.com/t/ZTR9nkkmL": "https://www.tiktok.com/?_r=1"})
    with pytest.raises(InvalidUrl) as excinfo:
        await resolve("https://www.tiktok.com/t/ZTR9nkkmL/", fetcher)
    assert excinfo.value.details["reason"] == "short_link_dead_end"


async def test_an_unrecognised_url_that_was_never_expanded_keeps_its_own_reason() -> None:
    fetcher = FakeRedirects()
    with pytest.raises(InvalidUrl) as excinfo:
        await resolve("https://www.douyin.com/discover", fetcher)
    assert excinfo.value.details["reason"] == "unknown_resource"
