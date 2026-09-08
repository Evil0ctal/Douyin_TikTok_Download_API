"""Replay tests for the TikTok parsers.

Mirrors ``test_douyin_parser.py`` case for case. The two suites are deliberately
parallel: the contract requires both platforms to produce the same field set,
and divergence is easiest to spot when the tests line up.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dtk.core.errors import DtkError, ErrorCode, UpstreamChanged, UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.models import AuthorStats, ContentStats
from dtk.platforms.tiktok import ADAPTER, endpoints, parser
from tests.replay.support import FETCHED_AT, load, nulled, without

PLATFORM = "tiktok"

AUTHOR_ID = "6900000000000000001"
AUTHOR_SEC_UID = "MS4wLjABAAAA_SYNTHETIC_TIKTOK_AUTHOR_0001"
NORMAL_ID = "7100000000000000001"
ALBUM_ID = "7100000000000000002"
DELETED_ID = "7100000000000000003"
PRIVATE_ID = "7100000000000000004"
LONG_ID = "7100000000000000005"

AVATAR = (
    "https://p16-sign-va.example-cdn.invalid/tos-maliva-avt/synthetic-author-01~c5_1080x1080.jpeg"
)


def fixture(name: str) -> dict:
    return load(PLATFORM, name)


# --------------------------------------------------------------------------
# Content detail
# --------------------------------------------------------------------------


def test_video_normal_every_field() -> None:
    payload = fixture("video_normal")
    content = parser.parse_content(payload, fetched_at=FETCHED_AT)

    assert content.platform is Platform.TIKTOK
    assert content.content_id == NORMAL_ID
    assert content.kind is ContentKind.VIDEO
    assert content.web_url == (f"https://www.tiktok.com/@synthetic.creator/video/{NORMAL_ID}")
    assert content.title == (
        "Sunrise over the harbour, shot on a very cold morning #sunrise #harbour"
    )
    assert content.description == content.title
    assert content.created_at == datetime(2024, 5, 20, 10, 13, 20, tzinfo=UTC)
    # TikTok reports duration in seconds; the contract stores milliseconds.
    assert content.duration_ms == 21000
    assert content.is_deleted is False
    assert content.is_private is False
    assert content.tags == ["sunrise", "harbour"]
    assert content.location == "Synthetic Harbour Lookout"
    assert content.fetched_at == FETCHED_AT
    assert content.raw == payload["itemInfo"]["itemStruct"]

    author = content.author
    assert author.platform is Platform.TIKTOK
    # TikTok's stable key is the numeric id; secUid survives only in raw.
    assert author.uid == AUTHOR_ID
    assert author.unique_id == "synthetic.creator"
    assert author.nickname == "Synthetic Creator"
    assert author.signature == "Fixture account for tests. Not a real person."
    assert author.avatar is not None
    assert author.avatar.url == AVATAR
    assert len(author.avatar.urls) == 3
    assert author.web_url == "https://www.tiktok.com/@synthetic.creator"
    assert author.verified is False
    assert author.stats == AuthorStats(
        follower_count=128400,
        following_count=42,
        content_count=213,
        # heartCount is likes received; diggCount (likes given) is not it.
        total_digg=3894512,
    )
    assert author.raw is None

    # Unlike Douyin, TikTok really does expose play counts.
    assert content.stats == ContentStats(
        play_count=1204331,
        digg_count=98230,
        comment_count=1284,
        share_count=3020,
        collect_count=5121,
    )

    media = content.media
    assert [cover.url for cover in media.covers] == [
        "https://p16-sign-va.example-cdn.invalid/obj/tos-useast-p-0068/"
        "synthetic-normal-01-cover~tplv-photomode.jpeg",
        "https://p16-sign-va.example-cdn.invalid/obj/tos-useast-p-0068/"
        "synthetic-normal-01-origin-cover~tplv-photomode.jpeg",
        "https://p16-sign-va.example-cdn.invalid/obj/tos-useast-p-0068/"
        "synthetic-normal-01-dynamic-cover~tplv-photomode.webp",
    ]
    assert media.images == []
    assert media.video is not None
    assert media.video.url == (
        "https://v16-webapp-prime.example-cdn.invalid/video/tos/useast2a/"
        "synthetic-normal-01/?a=1988&br=1018"
    )
    # downloadAddr is kept as a mirror rather than dropped.
    assert len(media.video.urls) == 2
    assert media.video.width == 576
    assert media.video.height == 1024
    assert media.video.bitrate == 1043057
    assert media.video.format == "mp4"
    assert media.video.size_bytes is None
    assert media.video.watermark is False

    assert [(s.bitrate, s.format, s.size_bytes) for s in media.streams] == [
        (1043057, "h264", 2737510),
        (615432, "h265_hvc1", 1615224),
    ]
    assert media.streams[0].urls == [
        "https://v16-webapp-prime.example-cdn.invalid/video/tos/useast2a/"
        "synthetic-normal-01/?br=1018&h264=1",
        "https://v19-webapp-prime.example-cdn.invalid/video/tos/useast2a/"
        "synthetic-normal-01/?br=1018&h264=1",
    ]

    assert content.music is not None
    assert content.music.music_id == "6900000000000000101"
    assert content.music.title == "original sound - synthetic.creator"
    assert content.music.author == "Synthetic Creator"
    assert content.music.duration_ms == 21000
    assert content.music.play_url == (
        "https://p16-sign-va.example-cdn.invalid/obj/tos-useast-synthetic-music-1.mp3"
    )
    assert content.music.cover is not None


def test_stats_v2_string_counters_are_preferred() -> None:
    """statsV2 carries counters as strings so huge numbers survive JSON."""
    payload = fixture("video_normal")
    payload["itemInfo"]["itemStruct"]["statsV2"]["playCount"] = "9007199254740993"
    content = parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert content.stats.play_count == 9007199254740993


def test_stats_falls_back_when_stats_v2_is_absent() -> None:
    payload = without(fixture("video_normal"), "itemInfo.itemStruct.statsV2")
    content = parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert content.stats.play_count == 1204331


def test_photo_mode_album() -> None:
    content = parser.parse_content(fixture("video_image_album"), fetched_at=FETCHED_AT)

    assert content.content_id == ALBUM_ID
    assert content.kind is ContentKind.IMAGE_ALBUM
    # Photo mode lives under /photo/, not /video/.
    assert content.web_url == f"https://www.tiktok.com/@synthetic.creator/photo/{ALBUM_ID}"
    assert content.duration_ms is None
    assert content.media.video is None
    assert content.media.streams == []
    assert len(content.media.images) == 3
    assert content.media.images[0].url == (
        "https://p16-sign-va.example-cdn.invalid/obj/tos-useast-i-0068/"
        "synthetic-photo-1~tplv-photomode-image.jpeg"
    )
    assert len(content.media.images[0].urls) == 2
    assert content.media.images[0].width == 1080
    assert content.media.images[0].height == 1440
    # Three video covers plus the imagePost cover.
    assert len(content.media.covers) == 4
    assert content.tags == ["filmphotography", "market"]
    assert content.location is None


def test_taken_down_video_parses_instead_of_raising() -> None:
    content = parser.parse_content(fixture("video_deleted"), fetched_at=FETCHED_AT)

    assert content.content_id == DELETED_ID
    assert content.is_deleted is True
    assert content.is_private is False
    assert content.title == ""
    assert content.description == ""
    assert content.media.video is None
    assert content.media.streams == []
    assert content.duration_ms is None
    # A real zero play count, unlike Douyin's placeholder: TikTok fills it in.
    assert content.stats.play_count == 0
    assert content.stats.digg_count == 0


def test_muted_audio_is_not_a_takedown() -> None:
    """itemMute means the audio was muted, not that the post was withdrawn.

    Reading it as a takedown would both mislabel a playable video and switch
    off the media requirement for it, so a real schema change would come back
    as a half-filled model instead of UPSTREAM_CHANGED.
    """
    payload = fixture("video_normal")
    payload["itemInfo"]["itemStruct"]["itemMute"] = True
    content = parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert content.is_deleted is False
    assert content.media.video is not None

    payload["itemInfo"]["itemStruct"]["video"]["playAddr"] = ""
    payload["itemInfo"]["itemStruct"]["video"]["downloadAddr"] = ""
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemInfo.itemStruct.video.playAddr"


def test_a_stated_zero_like_total_is_not_turned_into_none() -> None:
    """0 likes received is a measurement; only an absent field is None."""
    payload = fixture("user_profile")
    payload["userInfo"]["stats"]["heartCount"] = 0
    payload["userInfo"]["stats"].pop("heart", None)
    stats = parser.parse_author(payload).stats
    assert stats is not None
    assert stats.total_digg == 0


def test_private_video_parses_instead_of_raising() -> None:
    content = parser.parse_content(fixture("video_private"), fetched_at=FETCHED_AT)

    assert content.content_id == PRIVATE_ID
    assert content.is_private is True
    assert content.is_deleted is False
    assert content.media.video is None
    assert content.duration_ms == 8000
    assert content.stats.digg_count == 2


def test_long_description_with_emoji_and_hashtags() -> None:
    content = parser.parse_content(fixture("video_long_desc"), fetched_at=FETCHED_AT)

    assert content.content_id == LONG_ID
    assert content.title == "Day 12 of the road trip \U0001f305\U0001f697"
    assert content.description.endswith("#dayinthelife")
    assert "☕️" in content.description
    # textExtra first, challenges second, de-duplicated.
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

    assert author.platform is Platform.TIKTOK
    assert author.uid == AUTHOR_ID
    assert author.unique_id == "synthetic.creator"
    assert author.nickname == "Synthetic Creator"
    assert author.signature == "Fixture account for tests. Not a real person."
    assert author.avatar is not None and author.avatar.url == AVATAR
    assert author.web_url == "https://www.tiktok.com/@synthetic.creator"
    assert author.verified is False
    assert author.stats == AuthorStats(
        follower_count=128400,
        following_count=42,
        content_count=213,
        total_digg=3894512,
    )
    assert author.raw is not None
    assert author.raw == payload["userInfo"]["user"]
    # secUid is not a normalized field; it stays reachable through raw.
    assert author.raw["secUid"] == AUTHOR_SEC_UID


def test_verified_flag_is_read_from_the_user_object() -> None:
    payload = fixture("user_profile")
    payload["userInfo"]["user"]["verified"] = True
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
    assert page.cursor == "1716300000000"
    assert page.has_more is True
    assert all(item.fetched_at == FETCHED_AT for item in page.items)


def test_last_post_page_has_no_cursor() -> None:
    payload = fixture("user_posts_page1")
    payload["hasMore"] = False
    page = parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert page.has_more is False
    assert page.cursor is None


def test_empty_post_list_is_not_risk_control() -> None:
    payload = fixture("user_posts_page1")
    payload["itemList"] = []
    payload["hasMore"] = False
    page = parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert page.items == []


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------


def test_comments_with_replies() -> None:
    page = parser.parse_comments(fixture("comments_with_replies"))

    assert page.has_more is True
    assert page.cursor == "20"
    assert len(page.items) == 2

    first = page.items[0]
    assert first.platform is Platform.TIKTOK
    assert first.comment_id == "7100000000000001001"
    assert first.content_id == NORMAL_ID
    assert first.parent_id is None
    assert first.text == "That sunrise is unreal."
    assert first.created_at == datetime(2024, 5, 20, 10, 30, 0, tzinfo=UTC)
    assert first.digg_count == 420
    assert first.reply_count == 2
    assert first.is_author_liked is True
    assert first.is_pinned is True
    # The comment endpoints speak the snake_case aweme dialect, so the author
    # comes from a different shape than itemStruct's - same normalized result.
    assert first.author.uid == "6900000000000000010"
    assert first.author.unique_id == "synthetic.viewer.a"
    assert first.author.nickname == "Synthetic Viewer A"
    assert first.author.web_url == "https://www.tiktok.com/@synthetic.viewer.a"
    assert first.author.avatar is not None
    assert len(first.author.avatar.urls) == 2
    assert first.author.stats is None

    second = page.items[1]
    assert second.comment_id == "7100000000000001002"
    assert second.is_pinned is False
    assert second.is_author_liked is False
    assert second.reply_count == 0


def test_inline_reply_previews_are_available_separately() -> None:
    payload = fixture("comments_with_replies")
    assert [c.comment_id for c in parser.parse_comments(payload).items] == [
        "7100000000000001001",
        "7100000000000001002",
    ]
    previews = parser.parse_reply_previews(payload)
    assert [c.comment_id for c in previews] == [
        "7100000000000001101",
        "7100000000000001102",
    ]
    assert all(c.parent_id == "7100000000000001001" for c in previews)
    assert previews[1].author.uid == AUTHOR_ID


def test_comment_replies_page() -> None:
    page = parser.parse_comment_replies(
        fixture("comment_replies_page1"),
        content_id=NORMAL_ID,
        parent_id="7100000000000001001",
    )

    assert page.has_more is False
    assert page.cursor is None
    assert [c.comment_id for c in page.items] == [
        "7100000000000001101",
        "7100000000000001102",
    ]
    assert all(c.parent_id == "7100000000000001001" for c in page.items)
    assert all(c.content_id == NORMAL_ID for c in page.items)


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
# Risk control
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "signal"),
    [
        ("risk_control_empty", "empty:itemInfo.itemStruct"),
        ("risk_control_captcha", "statusCode:10000"),
    ],
)
def test_risk_control_is_reported_as_risk_control(name: str, signal: str) -> None:
    payload = fixture(name)
    assert parser.detect_risk_control(payload) == signal
    with pytest.raises(UpstreamRiskControl) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.code is ErrorCode.UPSTREAM_RISK_CONTROL
    assert excinfo.value.retryable is True
    assert excinfo.value.details["signal"] == signal


def test_healthy_payloads_carry_no_risk_signal() -> None:
    for name in ("video_normal", "user_profile", "user_posts_page1", "comments_with_replies"):
        assert parser.detect_risk_control(fixture(name)) is None


def test_taken_down_video_is_not_mistaken_for_risk_control() -> None:
    assert parser.detect_risk_control(fixture("video_deleted")) is None


# --------------------------------------------------------------------------
# Truncated responses
# --------------------------------------------------------------------------

_CONTENT_REQUIRED = [
    "itemInfo",
    "itemInfo.itemStruct.id",
    "itemInfo.itemStruct.desc",
    "itemInfo.itemStruct.author",
    "itemInfo.itemStruct.author.id",
    "itemInfo.itemStruct.author.uniqueId",
    "itemInfo.itemStruct.author.nickname",
    "itemInfo.itemStruct.video",
]


@pytest.mark.parametrize("path", _CONTENT_REQUIRED)
def test_missing_content_field_raises_with_path(path: str) -> None:
    payload = fixture("video_normal")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(without(payload, path), fetched_at=FETCHED_AT)
    assert excinfo.value.path == path
    assert excinfo.value.code is ErrorCode.UPSTREAM_CHANGED
    assert excinfo.value.retryable is False
    assert excinfo.value.details["path"] == path


@pytest.mark.parametrize("path", [p for p in _CONTENT_REQUIRED if p != "itemInfo"])
def test_nulled_content_field_raises_with_path(path: str) -> None:
    payload = fixture("video_normal")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(nulled(payload, path), fetched_at=FETCHED_AT)
    assert excinfo.value.path == path


def test_emptied_item_info_is_risk_control_not_a_schema_change() -> None:
    """An envelope with the payload emptied out is a block, not a rename."""
    payload = without(fixture("video_normal"), "itemInfo.itemStruct")
    with pytest.raises(UpstreamRiskControl) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.details["signal"] == "empty:itemInfo"


def test_renamed_item_struct_reports_the_structural_path() -> None:
    """A populated envelope missing itemStruct really is a schema change."""
    payload = fixture("video_normal")
    payload["itemInfo"] = {"itemStructV2": payload["itemInfo"]["itemStruct"]}
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemInfo.itemStruct"


def test_video_without_any_playable_address_raises() -> None:
    """downloadAddr is an accepted mirror, so both must be gone to fail."""
    payload = fixture("video_normal")
    payload["itemInfo"]["itemStruct"]["video"]["playAddr"] = ""
    payload["itemInfo"]["itemStruct"]["video"]["downloadAddr"] = ""
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemInfo.itemStruct.video.playAddr"


def test_content_without_any_stats_block_raises() -> None:
    payload = without(fixture("video_normal"), "itemInfo.itemStruct.stats")
    payload = without(payload, "itemInfo.itemStruct.statsV2")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemInfo.itemStruct.stats"


def test_album_with_an_unreadable_image_raises() -> None:
    payload = fixture("video_image_album")
    del payload["itemInfo"]["itemStruct"]["imagePost"]["images"][1]["imageURL"]
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_content(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemInfo.itemStruct.imagePost.images[1].imageURL"


@pytest.mark.parametrize(
    "path",
    [
        "userInfo",
        "userInfo.user",
        "userInfo.user.id",
        "userInfo.user.uniqueId",
        "userInfo.user.nickname",
    ],
)
def test_missing_author_field_raises_with_path(path: str) -> None:
    payload = fixture("user_profile")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author(without(payload, path))
    assert excinfo.value.path == path


@pytest.mark.parametrize("path", ["itemList", "hasMore"])
def test_missing_post_list_field_raises_with_path(path: str) -> None:
    payload = fixture("user_posts_page1")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(without(payload, path), fetched_at=FETCHED_AT)
    assert excinfo.value.path == path


def test_post_list_claiming_more_without_a_cursor_raises() -> None:
    payload = without(fixture("user_posts_page1"), "cursor")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "cursor"


def test_broken_item_inside_a_post_list_reports_its_index() -> None:
    payload = without(fixture("user_posts_page1"), "itemList[1].id")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemList[1].id"


@pytest.mark.parametrize(
    "path",
    [
        "comments",
        "has_more",
        "comments[0].cid",
        "comments[0].text",
        "comments[0].user",
        "comments[0].user.uid",
        "comments[0].user.nickname",
    ],
)
def test_missing_comment_field_raises_with_path(path: str) -> None:
    payload = fixture("comments_with_replies")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(without(payload, path))
    assert excinfo.value.path == path


def test_a_null_item_in_the_post_list_is_reported_not_skipped() -> None:
    payload = fixture("user_posts_page1")
    payload["itemList"].insert(1, None)
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_author_posts(payload, fetched_at=FETCHED_AT)
    assert excinfo.value.path == "itemList[1]"


def test_comment_page_claiming_more_with_a_zero_cursor_raises() -> None:
    payload = fixture("comments_with_replies")
    payload["cursor"] = 0
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(payload)
    assert excinfo.value.path == "cursor"


def test_comment_page_claiming_more_without_a_cursor_raises() -> None:
    payload = without(fixture("comments_with_replies"), "cursor")
    with pytest.raises(UpstreamChanged) as excinfo:
        parser.parse_comments(payload)
    assert excinfo.value.path == "cursor"


# --------------------------------------------------------------------------
# Endpoint table and request building
# --------------------------------------------------------------------------


def test_endpoint_table_covers_the_p0_set() -> None:
    """Every capability the registry declares has a real endpoint behind it."""
    from dtk.worker.registry import P0_CAPABILITIES

    assert {f"tiktok.{capability.value}" for capability in P0_CAPABILITIES} <= set(
        ADAPTER.endpoints.names()
    )


def test_build_content_detail_request() -> None:
    spec = ADAPTER.build_request(endpoints.CONTENT_DETAIL, item_id=NORMAL_ID)

    assert spec["method"] == "GET"
    assert spec["url"] == "https://www.tiktok.com/api/item/detail/"
    assert spec["params"]["itemId"] == NORMAL_ID
    assert spec["params"]["aid"] == "1988"
    assert spec["params"]["app_name"] == "tiktok_web"
    assert spec["headers"]["Referer"] == "https://www.tiktok.com/"
    assert spec["body"] is None
    assert "msToken" not in spec["params"]
    assert "_signature" not in spec["params"]


def test_parameters_are_not_pre_encoded() -> None:
    """V4 stored an already-quoted browser_version and double-encoded it."""
    spec = ADAPTER.build_request(endpoints.CONTENT_DETAIL, item_id=NORMAL_ID)
    assert spec["params"]["browser_version"] == "5.0 (Windows)"
    assert "%" not in spec["params"]["browser_version"]
    assert spec["params"]["root_referer"] == "https://www.tiktok.com/"


def test_author_profile_accepts_either_identifier() -> None:
    by_sec_uid = ADAPTER.build_request(endpoints.AUTHOR_PROFILE, sec_uid=AUTHOR_SEC_UID)
    assert by_sec_uid["params"]["secUid"] == AUTHOR_SEC_UID
    assert by_sec_uid["params"]["uniqueId"] == ""

    by_handle = ADAPTER.build_request(endpoints.AUTHOR_PROFILE, unique_id="synthetic.creator")
    assert by_handle["params"]["uniqueId"] == "synthetic.creator"
    assert by_handle["params"]["secUid"] == ""


def test_author_profile_without_any_identifier_is_rejected() -> None:
    with pytest.raises(DtkError) as excinfo:
        ADAPTER.build_request(endpoints.AUTHOR_PROFILE)
    assert excinfo.value.code is ErrorCode.INVALID_PARAM
    assert excinfo.value.details["missing"] == ["sec_uid", "unique_id"]


def test_build_author_posts_request_round_trips_an_opaque_cursor() -> None:
    page = parser.parse_author_posts(fixture("user_posts_page1"), fetched_at=FETCHED_AT)
    spec = ADAPTER.build_request(endpoints.AUTHOR_POSTS, sec_uid=AUTHOR_SEC_UID, cursor=page.cursor)
    assert spec["params"]["cursor"] == "1716300000000"
    assert spec["params"]["count"] == "30"
    assert spec["params"]["coverFormat"] == "2"


def test_build_comments_request() -> None:
    spec = ADAPTER.build_request(endpoints.COMMENTS, aweme_id=NORMAL_ID)
    assert spec["url"] == "https://www.tiktok.com/api/comment/list/"
    # The comment endpoints keep the aweme naming even on TikTok.
    assert spec["params"]["aweme_id"] == NORMAL_ID
    assert spec["params"]["cursor"] == "0"
    assert spec["params"]["current_region"] == "US"


def test_missing_required_parameter_is_rejected() -> None:
    with pytest.raises(DtkError) as excinfo:
        ADAPTER.build_request(endpoints.COMMENT_REPLIES, item_id=NORMAL_ID, comment_id="")
    assert excinfo.value.code is ErrorCode.INVALID_PARAM
    assert excinfo.value.details["missing"] == ["comment_id"]


def test_client_profile_is_injected_not_hardcoded() -> None:
    from dtk.platforms.base import ClientProfile

    profile = ClientProfile(screen_width=1366, screen_height=768, region="SG", language="zh-Hans")
    spec = ADAPTER.build_request(endpoints.CONTENT_DETAIL, item_id=NORMAL_ID, profile=profile)
    assert spec["params"]["screen_width"] == "1366"
    assert spec["params"]["region"] == "SG"
    assert spec["params"]["priority_region"] == "SG"
    assert spec["params"]["app_language"] == "zh-Hans"


# --------------------------------------------------------------------------
# Follow graph
# --------------------------------------------------------------------------


def test_author_list_reads_the_user_and_stats_pair() -> None:
    """Each entry is the same shape /api/user/detail/ returns for one author."""
    page = parser.parse_author_list(
        {
            "userList": [
                {
                    "user": {
                        "id": "123",
                        "secUid": "MS4wLjABAAAAexample",
                        "uniqueId": "someone",
                        "nickname": "Someone",
                        "avatarThumb": "https://p16.tiktokcdn.com/a.jpeg",
                    },
                    "stats": {"followerCount": 12, "followingCount": 3, "heartCount": 45},
                }
            ],
            "hasMore": True,
            "minCursor": 1788830136,
        }
    )
    assert [author.nickname for author in page.items] == ["Someone"]
    assert page.has_more is True
    assert page.cursor == "1788830136"


def test_an_absent_user_list_is_an_empty_page_not_a_broken_payload() -> None:
    """A hidden following list omits the key rather than sending an empty list.

    Measured 2026-09-08: one account returned 30 followers and, for the
    following side, a 276-byte body with no `userList` key at all. Requiring the
    key turned "this author hides who they follow" into UpstreamChanged.
    """
    page = parser.parse_author_list({"hasMore": False, "minCursor": -1, "statusCode": 0})

    assert page.items == []
    assert page.has_more is False
    assert page.cursor is None


def test_a_present_user_list_is_still_strict() -> None:
    """Tolerating an absent key must not tolerate a malformed present one."""
    with pytest.raises(UpstreamChanged):
        parser.parse_author_list({"userList": [{"user": {}}], "hasMore": False})
