"""Replay tests for the Douyin parsers.

Every fixture is parsed and asserted field by field. Coverage alone proves
nothing here: a test that walks every branch without asserting values passes
while the parser silently drops half the payload, which is the exact failure
``docs/design/13-testing.md`` is written against.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dtk.core.errors import ErrorCode, UpstreamChanged, UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.models import AuthorStats, ContentStats
from dtk.platforms.douyin import ADAPTER, endpoints, parser
from tests.replay.support import FETCHED_AT, load, nulled, without

PLATFORM = "douyin"

SEC_UID = "MS4wLjABAAAA_SYNTHETIC_DOUYIN_AUTHOR_0001"
NORMAL_ID = "7000000000000000001"
ALBUM_ID = "7000000000000000002"
DELETED_ID = "7000000000000000003"
PRIVATE_ID = "7000000000000000004"
LONG_ID = "7000000000000000005"

AVATAR = (
    "https://p3-pc-sign.example-cdn.invalid/aweme/1080x1080/douyin-avatar/synthetic-author-01.jpeg"
)


def fixture(name: str) -> dict:
    return load(PLATFORM, name)


# --------------------------------------------------------------------------
# Content detail: the happy path, asserted exhaustively
# --------------------------------------------------------------------------


def test_content_model_has_no_unasserted_fields() -> None:
    """Guard: adding a Content field must force these tests to be updated."""
    from dtk.models import Content

    assert set(Content.model_fields) == {
        "platform",
        "content_id",
        "kind",
        "web_url",
        "title",
        "description",
        "created_at",
        "duration_ms",
        "is_deleted",
        "is_private",
        "author",
        "stats",
        "media",
        "music",
        "tags",
        "location",
        "fetched_at",
        "raw",
    }


def test_video_normal_every_field() -> None:
    payload = fixture("video_normal")
    content = parser.parse_content(payload, fetched_at=FETCHED_AT)

    assert content.platform is Platform.DOUYIN
    assert content.content_id == NORMAL_ID
    assert content.kind is ContentKind.VIDEO
    assert content.web_url == f"https://www.douyin.com/video/{NORMAL_ID}"
    # Douyin has one desc field: first line is the title, the whole text is the
    # description.
    assert content.title == "Sunrise over the harbour"
    assert content.description == (
        "Sunrise over the harbour\nShot on a very cold morning #sunrise #harbour"
    )
    assert content.created_at == datetime(2024, 5, 20, 10, 13, 20, tzinfo=UTC)
    assert content.duration_ms == 15320
    assert content.is_deleted is False
    assert content.is_private is False
    assert content.tags == ["sunrise", "harbour"]
    assert content.location == "Synthetic Harbour Lookout"
    assert content.fetched_at == FETCHED_AT
    assert content.raw == payload["aweme_detail"]

    author = content.author
    assert author.platform is Platform.DOUYIN
    # The stable key is sec_uid, never the rotating numeric uid.
    assert author.uid == SEC_UID
    assert author.unique_id == "synthetic_creator"
    assert author.nickname == "Synthetic Creator"
    assert author.signature == "Fixture account for tests. Not a real person."
    assert author.avatar is not None
    assert author.avatar.url == AVATAR
    assert len(author.avatar.urls) == 2
    assert author.avatar.width == 720
    assert author.avatar.height == 720
    assert author.web_url == f"https://www.douyin.com/user/{SEC_UID}"
    assert author.verified is False
    assert author.stats == AuthorStats(
        follower_count=128400,
        following_count=42,
        content_count=213,
        total_digg=3894512,
    )
    # Nested authors do not duplicate the payload; only a standalone profile
    # parse carries raw.
    assert author.raw is None

    assert content.stats == ContentStats(
        # Douyin's web API always reports 0 plays. That is a placeholder, not a
        # measurement, so it normalizes to None.
        play_count=None,
        digg_count=98230,
        comment_count=1284,
        share_count=3020,
        collect_count=5121,
    )

    media = content.media
    assert [cover.url for cover in media.covers] == [
        "https://p3-pc-sign.example-cdn.invalid/tos-cn-p-0015/synthetic-normal-01-cover.jpeg",
        "https://p3-pc-sign.example-cdn.invalid/tos-cn-p-0015/synthetic-normal-01-origin-cover.jpeg",
        "https://p3-pc-sign.example-cdn.invalid/tos-cn-p-0015/synthetic-normal-01-dynamic-cover.webp",
    ]
    assert media.images == []
    assert media.video is not None
    assert media.video.url == (
        "https://v3-web.example-cdn.invalid/video/tos/cn/synthetic-normal-01/?ratio=1080p&a=6383"
    )
    # Every CDN mirror is kept: downloads go browser-to-CDN, so a client with
    # one dead link has nowhere to fall back to.
    assert len(media.video.urls) == 2
    assert media.video.width == 1080
    assert media.video.height == 1920
    assert media.video.format == "mp4"
    assert media.video.size_bytes == 4183220
    assert media.video.watermark is False
    assert media.video.bitrate is None

    assert [(s.bitrate, s.width, s.watermark) for s in media.streams] == [
        (2185330, 1080, False),
        (1120450, 720, False),
        (None, 1080, True),
    ]
    assert media.streams[-1].size_bytes == 4519880

    assert content.music is not None
    assert content.music.music_id == "6800000000000000001"
    assert content.music.title == "original sound - Synthetic Creator"
    assert content.music.author == "Synthetic Creator"
    assert content.music.duration_ms == 15000
    assert content.music.play_url == (
        "https://sf-web.example-cdn.invalid/obj/synthetic-music-01.mp3"
    )
    assert content.music.cover is not None


def test_image_album() -> None:
    content = parser.parse_content(fixture("video_image_album"), fetched_at=FETCHED_AT)

    assert content.content_id == ALBUM_ID
    assert content.kind is ContentKind.IMAGE_ALBUM
    # Albums live under /note/, not /video/.
    assert content.web_url == f"https://www.douyin.com/note/{ALBUM_ID}"
    assert content.title == "Six frames from the market"
    # An album has no duration; the contract says None, not 0.
    assert content.duration_ms is None
    assert content.media.video is None
    assert content.media.streams == []
    assert len(content.media.images) == 3
    assert content.media.images[0].url == (
        "https://p3-pc-sign.example-cdn.invalid/tos-cn-i-0813/synthetic-album-1.jpeg"
    )
    assert content.media.images[0].urls == [
        "https://p3-pc-sign.example-cdn.invalid/tos-cn-i-0813/synthetic-album-1.jpeg",
        "https://p9-pc-sign.example-cdn.invalid/tos-cn-i-0813/synthetic-album-1.jpeg",
    ]
    assert content.media.images[0].width == 1440
    assert content.media.images[0].height == 1920
    assert len(content.media.covers) == 3
    assert content.tags == ["filmphotography", "market"]
    assert content.location is None


def test_deleted_video_parses_instead_of_raising() -> None:
    """A withdrawn post loses its media URLs but is still a real answer.

    Requiring media here would report 'the platform changed' every time someone
    looks up a deleted video, and send users to file a bug report.
    """
    content = parser.parse_content(fixture("video_deleted"), fetched_at=FETCHED_AT)

    assert content.content_id == DELETED_ID
    assert content.is_deleted is True
    assert content.is_private is False
    assert content.title == ""
    assert content.description == ""
    assert content.media.video is None
    assert content.media.streams == []
    assert content.media.images == []
    assert len(content.media.covers) == 1
    # Duration 0 on a deleted post is a placeholder, not a zero-length video.
    assert content.duration_ms is None
    assert content.stats == ContentStats(
        play_count=None,
        digg_count=0,
        comment_count=0,
        share_count=0,
        collect_count=0,
    )
    # The abbreviated author in this response carries no counters at all.
    assert content.author.stats is None
    assert content.music is None


def test_private_video_parses_instead_of_raising() -> None:
    content = parser.parse_content(fixture("video_private"), fetched_at=FETCHED_AT)

    assert content.content_id == PRIVATE_ID
    assert content.is_private is True
    assert content.is_deleted is False
    assert content.media.video is None
    assert content.duration_ms == 8100
    assert content.stats.digg_count == 2


def test_long_description_with_emoji_and_hashtags() -> None:
    content = parser.parse_content(fixture("video_long_desc"), fetched_at=FETCHED_AT)

    assert content.content_id == LONG_ID
    assert content.title == "Day 12 of the road trip \U0001f305\U0001f697"
    assert content.description.startswith("Day 12 of the road trip")
    assert content.description.endswith("#dayinthelife")
    assert "\n" in content.description
    assert "☕️" in content.description
    # Hashtags come from text_extra, in order, without the leading '#'.
    assert content.tags == [
        "roadtrip",
        "vanlife",
        "sunrise",
        "coffee",
        "travel",
        "dayinthelife",
    ]
    assert content.duration_ms == 183000


# --------------------------------------------------------------------------
# Author profile
# --------------------------------------------------------------------------


def test_user_profile() -> None:
    payload = fixture("user_profile")
    author = parser.parse_author(payload)

    assert author.platform is Platform.DOUYIN
    assert author.uid == SEC_UID
    assert author.unique_id == "synthetic_creator"
    assert author.nickname == "Synthetic Creator"
    assert author.signature == "Fixture account for tests. Not a real person."
    assert author.avatar is not None and author.avatar.url == AVATAR
    assert author.web_url == f"https://www.douyin.com/user/{SEC_UID}"
    assert author.verified is False
    assert author.stats == AuthorStats(
        follower_count=128400,
        following_count=42,
        content_count=213,
        total_digg=3894512,
    )
    # A standalone profile parse keeps the payload for later re-derivation.
    assert author.raw == payload["user"]


def test_unique_id_falls_back_to_short_id() -> None:
    payload = fixture("user_profile")
    payload["user"]["unique_id"] = ""
    author = parser.parse_author(payload)
    assert author.unique_id == "88881111"


def test_custom_verify_marks_the_author_verified() -> None:
    payload = fixture("user_profile")
    payload["user"]["custom_verify"] = "Synthetic verified badge"
    assert parser.parse_author(payload).verified is True


# --------------------------------------------------------------------------
# Author post list
# --------------------------------------------------------------------------


def test_user_posts_page1() -> None:
    page = parser.parse_author_posts(fixture("user_posts_page1"), fetched_at=FETCHED_AT)

    assert [item.content_id for item in page.items] == [NORMAL_ID, ALBUM_ID]
    assert [item.kind for item in page.items] == [
        ContentKind.VIDEO,
        ContentKind.IMAGE_ALBUM,
    ]
    # The cursor is Douyin's max_cursor timestamp, stringified and opaque.
    assert page.cursor == "1716300000000"
    assert page.has_more is True
    assert all(item.fetched_at == FETCHED_AT for item in page.items)


def test_last_post_page_has_no_cursor() -> None:
    payload = fixture("user_posts_page1")
    payload["has_more"] = 0
    page = parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert page.has_more is False
    assert page.cursor is None


def test_empty_post_list_is_not_risk_control() -> None:
    """A creator with no posts is a legitimate answer, not a block."""
    payload = fixture("user_posts_page1")
    payload["aweme_list"] = []
    payload["has_more"] = 0
    page = parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert page.items == []
    assert page.has_more is False


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------


def test_comments_with_replies() -> None:
    page = parser.parse_comments(fixture("comments_with_replies"))

    assert page.has_more is True
    assert page.cursor == "20"
    assert len(page.items) == 2

    first = page.items[0]
    assert first.platform is Platform.DOUYIN
    assert first.comment_id == "7000000000000001001"
    assert first.content_id == NORMAL_ID
    # reply_id "0" means top level, which normalizes to None.
    assert first.parent_id is None
    assert first.text == "That sunrise is unreal."
    assert first.created_at == datetime(2024, 5, 20, 10, 30, 0, tzinfo=UTC)
    assert first.digg_count == 420
    assert first.reply_count == 2
    assert first.is_author_liked is True
    assert first.is_pinned is True
    assert first.author.uid == "MS4wLjABAAAA_SYNTHETIC_DOUYIN_VIEWER_0010"
    assert first.author.unique_id == "synthetic_viewer_a"
    assert first.author.nickname == "Synthetic Viewer A"
    assert first.author.stats is None

    second = page.items[1]
    assert second.comment_id == "7000000000000001002"
    assert second.is_pinned is False
    assert second.is_author_liked is False
    assert second.reply_count == 0
    assert second.digg_count == 3


def test_inline_reply_previews_are_available_separately() -> None:
    """The reply previews are real data, but not part of the comment page.

    Folding them into ``items`` would duplicate whatever the reply endpoint
    returns on the next call.
    """
    payload = fixture("comments_with_replies")
    assert [c.comment_id for c in parser.parse_comments(payload).items] == [
        "7000000000000001001",
        "7000000000000001002",
    ]
    previews = parser.parse_reply_previews(payload)
    assert [c.comment_id for c in previews] == [
        "7000000000000001101",
        "7000000000000001102",
    ]
    assert all(c.parent_id == "7000000000000001001" for c in previews)
    assert previews[1].author.uid == SEC_UID
    assert previews[1].is_author_liked is True


def test_comment_replies_page() -> None:
    page = parser.parse_comment_replies(
        fixture("comment_replies_page1"),
        content_id=NORMAL_ID,
        parent_id="7000000000000001001",
    )

    assert page.has_more is False
    assert page.cursor is None
    assert [c.comment_id for c in page.items] == [
        "7000000000000001101",
        "7000000000000001102",
    ]
    assert all(c.parent_id == "7000000000000001001" for c in page.items)
    assert all(c.content_id == NORMAL_ID for c in page.items)
    assert page.items[0].created_at == datetime(2024, 5, 20, 10, 53, 20, tzinfo=UTC)


def test_comment_falls_back_to_the_caller_supplied_content_id() -> None:
    payload = fixture("comment_replies_page1")
    for comment in payload["comments"]:
        del comment["aweme_id"]
    page = parser.parse_comment_replies(payload, content_id=NORMAL_ID)
    assert all(c.content_id == NORMAL_ID for c in page.items)


def test_comment_without_any_content_id_raises() -> None:
    payload = fixture("comment_replies_page1")
    for comment in payload["comments"]:
        del comment["aweme_id"]
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comment_replies(payload)
    assert excinfo.value.path == "comments[0].aweme_id"


# --------------------------------------------------------------------------
# Risk control: a block is not a schema change
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "signal"),
    [
        ("risk_control_empty", "empty:aweme_detail"),
        ("risk_control_captcha", "status_code:10000"),
    ],
)
def test_risk_control_is_reported_as_risk_control(name: str, signal: str) -> None:
    payload = fixture(name)
    assert parser.detect_risk_control(payload) == signal
    with pytest.raises(UpstreamRiskControl) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    # Retryable, unlike UPSTREAM_CHANGED. Conflating the two is what made V4
    # users retry forever on real bugs.
    assert excinfo.value.code is ErrorCode.UPSTREAM_RISK_CONTROL
    assert excinfo.value.retryable is True
    assert excinfo.value.details["signal"] == signal


def test_healthy_payloads_carry_no_risk_signal() -> None:
    for name in ("video_normal", "user_profile", "user_posts_page1", "comments_with_replies"):
        assert parser.detect_risk_control(fixture(name)) is None


def test_deleted_video_is_not_mistaken_for_risk_control() -> None:
    """Cooling an identity because a video was deleted is the worst outcome."""
    assert parser.detect_risk_control(fixture("video_deleted")) is None


# --------------------------------------------------------------------------
# Truncated responses: UpstreamChanged with the exact missing path
# --------------------------------------------------------------------------

_CONTENT_REQUIRED = [
    "aweme_detail",
    "aweme_detail.aweme_id",
    "aweme_detail.desc",
    "aweme_detail.status",
    "aweme_detail.statistics",
    "aweme_detail.author",
    "aweme_detail.author.sec_uid",
    "aweme_detail.author.nickname",
    "aweme_detail.video.play_addr",
]


@pytest.mark.parametrize("path", _CONTENT_REQUIRED)
def test_missing_content_field_raises_with_path(path: str) -> None:
    payload = fixture("video_normal")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(without(payload, path), fetched_at=FETCHED_AT)
    assert excinfo.value.path == path
    assert excinfo.value.code is ErrorCode.UPSTREAM_CHANGED
    # A schema change is a project bug: retrying cannot help.
    assert excinfo.value.retryable is False
    assert excinfo.value.details["path"] == path


@pytest.mark.parametrize("path", [p for p in _CONTENT_REQUIRED if p != "aweme_detail"])
def test_nulled_content_field_raises_with_path(path: str) -> None:
    """A nulled field is as broken as a dropped one."""
    payload = fixture("video_normal")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(nulled(payload, path), fetched_at=FETCHED_AT)
    assert excinfo.value.path == path


def test_missing_video_block_reports_the_video_path() -> None:
    payload = without(fixture("video_normal"), "aweme_detail.video")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "aweme_detail.video"


def test_album_with_an_unreadable_image_raises() -> None:
    payload = fixture("video_image_album")
    payload["aweme_detail"]["images"][1]["url_list"] = []
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "aweme_detail.images[1].url_list"


@pytest.mark.parametrize("path", ["user", "user.sec_uid", "user.nickname"])
def test_missing_author_field_raises_with_path(path: str) -> None:
    payload = fixture("user_profile")
    if path == "user":
        # An emptied user object is a block, not a schema change; drop the key
        # entirely to test the structural path.
        del payload["user"]
        with pytest.raises(UpstreamChanged) as excinfo:
            parser.parse_author(payload)
        assert excinfo.value.path == "user"
        return
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author(without(payload, path))
    assert excinfo.value.path == path


@pytest.mark.parametrize("path", ["aweme_list", "has_more"])
def test_missing_post_list_field_raises_with_path(path: str) -> None:
    payload = fixture("user_posts_page1")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(without(payload, path), fetched_at=FETCHED_AT)
    assert excinfo.value.path == path


def test_post_list_claiming_more_without_a_cursor_raises() -> None:
    payload = without(fixture("user_posts_page1"), "max_cursor")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "max_cursor"


def test_broken_item_inside_a_post_list_reports_its_index() -> None:
    payload = without(fixture("user_posts_page1"), "aweme_list[1].aweme_id")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "aweme_list[1].aweme_id"


@pytest.mark.parametrize(
    "path",
    ["comments", "has_more", "comments[0].cid", "comments[0].text", "comments[0].user"],
)
def test_missing_comment_field_raises_with_path(path: str) -> None:
    payload = fixture("comments_with_replies")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(without(payload, path))
    assert excinfo.value.path == path


def test_a_null_item_in_the_post_list_is_reported_not_skipped() -> None:
    """A page that quietly comes back one item short is silent degradation."""
    payload = fixture("user_posts_page1")
    payload["aweme_list"].insert(1, None)
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "aweme_list[1]"


def test_a_null_comment_is_reported_not_skipped() -> None:
    payload = fixture("comments_with_replies")
    payload["comments"].append(None)
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(payload)
    assert excinfo.value.path == "comments[2]"


def test_comment_page_claiming_more_with_a_zero_cursor_raises() -> None:
    """Cursor 0 means 'first page': handing it back would page for ever."""
    payload = fixture("comments_with_replies")
    payload["cursor"] = 0
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(payload)
    assert excinfo.value.path == "cursor"


def test_music_without_a_playable_url_still_parses() -> None:
    """A play_url container with no url_list must not crash the parser."""
    payload = fixture("video_normal")
    payload["aweme_detail"]["music"]["play_url"] = {"uri": "synthetic-music-01"}
    content = parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert content.music is not None
    assert content.music.play_url is None
    assert content.music.title == "original sound - Synthetic Creator"

    payload["aweme_detail"]["music"]["play_url"] = {"url_list": None}
    assert parser.parse_content(payload, fetched_at=FETCHED_AT).music is not None


def test_comment_page_claiming_more_without_a_cursor_raises() -> None:
    payload = without(fixture("comments_with_replies"), "cursor")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(payload)
    assert excinfo.value.path == "cursor"


# --------------------------------------------------------------------------
# Endpoint table and request building
# --------------------------------------------------------------------------


def test_endpoint_table_covers_the_p0_set() -> None:
    assert set(ADAPTER.endpoints.names()) == {
        "douyin.content_detail",
        "douyin.author_profile",
        "douyin.author_posts",
        "douyin.comments",
        "douyin.comment_replies",
    }


def test_build_content_detail_request() -> None:
    spec = ADAPTER.build_request(endpoints.CONTENT_DETAIL, aweme_id=NORMAL_ID)

    assert spec["method"] == "GET"
    assert spec["url"] == "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    assert spec["params"]["aweme_id"] == NORMAL_ID
    assert spec["params"]["aid"] == "6383"
    assert spec["params"]["device_platform"] == "webapp"
    assert spec["headers"]["Referer"] == "https://www.douyin.com/"
    assert spec["body"] is None
    # Signature parameters belong to the signing layer, which owns the identity.
    assert "msToken" not in spec["params"]
    assert "a_bogus" not in spec["params"]


def test_build_author_posts_request_defaults_to_the_first_page() -> None:
    spec = ADAPTER.build_request(endpoints.AUTHOR_POSTS, sec_user_id=SEC_UID)
    assert spec["params"]["max_cursor"] == "0"
    assert spec["params"]["count"] == "20"
    assert spec["params"]["sec_user_id"] == SEC_UID


def test_build_author_posts_request_round_trips_an_opaque_cursor() -> None:
    page = parser.parse_author_posts(fixture("user_posts_page1"), fetched_at=FETCHED_AT)
    spec = ADAPTER.build_request(
        endpoints.AUTHOR_POSTS, sec_user_id=SEC_UID, cursor=page.cursor, count=50
    )
    assert spec["params"]["max_cursor"] == "1716300000000"
    assert spec["params"]["count"] == "50"


def test_build_comment_replies_request() -> None:
    spec = ADAPTER.build_request(
        endpoints.COMMENT_REPLIES, item_id=NORMAL_ID, comment_id="7000000000000001001"
    )
    assert spec["url"] == "https://www.douyin.com/aweme/v1/web/comment/list/reply/"
    assert spec["params"]["item_id"] == NORMAL_ID
    assert spec["params"]["comment_id"] == "7000000000000001001"
    assert spec["params"]["cursor"] == "0"


def test_missing_required_parameter_is_rejected() -> None:
    from dtk.core.errors import DtkError

    with pytest.raises(DtkError) as excinfo:
        ADAPTER.build_request(endpoints.CONTENT_DETAIL, aweme_id="  ")
    assert excinfo.value.code is ErrorCode.INVALID_PARAM
    assert excinfo.value.details["missing"] == ["aweme_id"]


def test_unknown_endpoint_is_rejected() -> None:
    from dtk.core.errors import DtkError

    with pytest.raises(DtkError) as excinfo:
        ADAPTER.build_request("douyin.does_not_exist")
    assert excinfo.value.code is ErrorCode.INVALID_PARAM


def test_client_profile_is_injected_not_hardcoded() -> None:
    """Query fingerprint values must be able to follow the identity."""
    from dtk.platforms.base import ClientProfile

    profile = ClientProfile(screen_width=1366, screen_height=768, browser_language="en-GB")
    spec = ADAPTER.build_request(endpoints.CONTENT_DETAIL, aweme_id=NORMAL_ID, profile=profile)
    assert spec["params"]["screen_width"] == "1366"
    assert spec["params"]["screen_height"] == "768"
    assert spec["params"]["browser_language"] == "en-GB"
