"""Bilibili's parsers against the shapes captured on 2026-10-18.

The fixtures are real responses from a guest identity with every value
substituted - see ``tests/fixtures/README.md`` - so the structure is the
platform's and the numbers and names are ours. Counters the parity test pins
(followers, posts, the comment cursor) were set to the shared values on
purpose; everything else is as captured.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from dtk.core.errors import UpstreamChanged, UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.platforms import get_adapter
from dtk.platforms.bilibili import parser
from dtk.platforms.bilibili.endpoints import CONTENT_DETAIL, CONTENT_STREAMS
from dtk.platforms.bilibili.ids import bv_to_av
from tests.replay.support import FETCHED_AT, load, without

ADAPTER = get_adapter("bilibili")


def bili(name: str) -> dict[str, Any]:
    return load("bilibili", name)


def with_streams(detail: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = detail or bili("video_normal")
    follow_up = ADAPTER.follow_up(CONTENT_DETAIL, payload)
    assert follow_up is not None
    return follow_up.merge(payload, bili("content_streams"))


class TestContent:
    def test_the_detail_alone_is_a_video_with_no_stream(self) -> None:
        """A real answer without its playurl: parsed, not refused."""
        content = ADAPTER.parse_content(bili("video_normal"), fetched_at=FETCHED_AT)

        assert content.platform is Platform.BILIBILI
        assert content.kind is ContentKind.VIDEO
        assert content.content_id.startswith("BV1")
        assert content.web_url == f"https://www.bilibili.com/video/{content.content_id}"
        assert content.media.video is None
        assert content.media.covers and content.media.covers[0].url.startswith("https://")

    def test_its_counters_land_where_the_other_platforms_put_them(self) -> None:
        stat = bili("video_normal")["data"]["stat"]
        stats = ADAPTER.parse_content(bili("video_normal"), fetched_at=FETCHED_AT).stats

        assert stats.play_count == stat["view"]
        assert stats.digg_count == stat["like"]
        assert stats.comment_count == stat["reply"]
        assert stats.share_count == stat["share"]
        assert stats.collect_count == stat["favorite"]

    def test_the_author_is_keyed_by_mid_in_both_id_fields(self) -> None:
        author = ADAPTER.parse_content(bili("video_normal"), fetched_at=FETCHED_AT).author
        mid = str(bili("video_normal")["data"]["owner"]["mid"])

        assert author.uid == author.sec_uid == mid
        assert author.unique_id is None
        assert author.web_url == f"https://space.bilibili.com/{mid}"

    def test_the_followed_up_stream_becomes_the_video(self) -> None:
        streams = bili("content_streams")["data"]
        video = ADAPTER.parse_content(with_streams(), fetched_at=FETCHED_AT).media.video

        assert video is not None
        assert video.format == "mp4"
        assert video.url == streams["durl"][0]["url"]
        assert video.size_bytes == streams["durl"][0]["size"]
        assert (video.width, video.height) == (3840, 2160)

    def test_a_portrait_upload_reports_its_real_orientation(self) -> None:
        payload = bili("video_normal")
        payload["data"]["dimension"] = {"width": 1920, "height": 1080, "rotate": 1}
        video = ADAPTER.parse_content(with_streams(payload), fetched_at=FETCHED_AT).media.video
        assert video is not None
        assert (video.width, video.height) == (1080, 1920)

    def test_a_file_cut_into_segments_is_not_offered_as_the_video(self) -> None:
        """The first of three segments, stored under the video's name, would be
        a third of the video that says it is the whole of it."""
        streams = bili("content_streams")
        segment = streams["data"]["durl"][0]
        streams["data"]["durl"] = [segment, copy.deepcopy(segment)]
        merged = parser.merge_streams(bili("video_normal"), streams)
        assert ADAPTER.parse_content(merged, fetched_at=FETCHED_AT).media.video is None

    def test_a_flv_answer_is_not_offered_either(self) -> None:
        streams = bili("content_streams")
        streams["data"]["format"] = "flv480"
        merged = parser.merge_streams(bili("video_normal"), streams)
        assert ADAPTER.parse_content(merged, fetched_at=FETCHED_AT).media.video is None

    def test_a_multipart_video_is_one_post(self) -> None:
        payload = bili("video_multipart")
        assert len(payload["data"]["pages"]) > 1
        content = ADAPTER.parse_content(payload, fetched_at=FETCHED_AT)
        assert content.content_id == payload["data"]["bvid"]

    def test_a_long_description_is_kept_whole(self) -> None:
        payload = bili("video_long_desc")
        content = ADAPTER.parse_content(payload, fetched_at=FETCHED_AT)
        assert content.description == payload["data"]["desc"]
        assert content.title == payload["data"]["title"]

    def test_a_negative_state_is_a_restricted_video(self) -> None:
        payload = bili("video_normal")
        payload["data"]["state"] = -1
        content = ADAPTER.parse_content(payload, fetched_at=FETCHED_AT)
        assert (content.is_deleted, content.is_private) == (False, True)

    @pytest.mark.parametrize(
        "path", ["data", "data.bvid", "data.owner", "data.owner.mid", "data.stat"]
    )
    def test_a_missing_field_names_its_path(self, path: str) -> None:
        with pytest.raises(UpstreamChanged) as caught:
            ADAPTER.parse_content(without(bili("video_normal"), path), fetched_at=FETCHED_AT)
        assert path in str(caught.value.details)


class TestFollowUp:
    def test_a_detail_asks_for_its_first_part_s_stream(self) -> None:
        payload = bili("video_normal")
        follow_up = ADAPTER.follow_up(CONTENT_DETAIL, payload)

        assert follow_up is not None
        assert follow_up.endpoint == CONTENT_STREAMS
        assert dict(follow_up.params) == {
            "bvid": payload["data"]["bvid"],
            "cid": str(payload["data"]["cid"]),
        }

    def test_nothing_else_asks_for_one(self) -> None:
        assert ADAPTER.follow_up("bilibili.author_profile", bili("user_profile")) is None

    def test_a_detail_without_a_part_asks_for_nothing(self) -> None:
        payload = bili("video_normal")
        del payload["data"]["cid"]
        assert ADAPTER.follow_up(CONTENT_DETAIL, payload) is None

    def test_the_merge_leaves_the_detail_it_was_given_alone(self) -> None:
        detail = bili("video_normal")
        before = copy.deepcopy(detail)
        parser.merge_streams(detail, bili("content_streams"))
        assert detail == before


class TestAuthors:
    def test_the_card_carries_the_profile_and_its_counters(self) -> None:
        payload = bili("user_profile")
        author = ADAPTER.parse_author(payload)

        assert author.uid == author.sec_uid == payload["data"]["card"]["mid"]
        assert author.stats is not None
        assert author.stats.follower_count == 128400
        assert author.stats.content_count == 213
        assert author.stats.following_count == payload["data"]["card"]["attention"]
        assert author.stats.total_digg == payload["data"]["like_num"]
        assert author.verified is True

    def test_an_unverified_account_is_not_called_verified(self) -> None:
        payload = bili("user_profile")
        payload["data"]["card"]["Official"]["type"] = -1
        assert ADAPTER.parse_author(payload).verified is False


class TestAuthorPosts:
    def test_a_page_and_where_the_next_one_starts(self) -> None:
        page = ADAPTER.parse_author_posts(bili("user_posts_page1"), fetched_at=FETCHED_AT)

        assert len(page.items) == 3
        assert (page.has_more, page.cursor) == (True, "2")
        first = page.items[0]
        assert first.content_id.startswith("BV1")
        assert first.author.avatar is None, "the space list carries no avatar"
        assert first.duration_ms == 384_000

    def test_the_last_page_has_no_cursor(self) -> None:
        payload = bili("user_posts_page1")
        payload["data"]["page"] = {"pn": 71, "ps": 3, "count": 213}
        page = ADAPTER.parse_author_posts(payload, fetched_at=FETCHED_AT)
        assert (page.has_more, page.cursor) == (False, None)

    def test_an_author_nobody_has_is_an_empty_page(self) -> None:
        page = ADAPTER.parse_author_posts(bili("user_posts_empty"), fetched_at=FETCHED_AT)
        assert (page.items, page.has_more, page.cursor) == ([], False, None)

    @pytest.mark.parametrize(
        ("length", "ms"), [("06:24", 384_000), ("1:02:03", 3_723_000), ("", None), ("x", None)]
    )
    def test_the_list_s_clock_durations(self, length: str, ms: int | None) -> None:
        assert parser._length_ms(length) == ms


class TestComments:
    def test_pinned_comments_come_first(self) -> None:
        page = ADAPTER.parse_comments(bili("comments_with_replies"))

        assert len(page.items) == 2
        assert page.items[0].is_pinned is True
        assert page.items[0].reply_count == 2
        assert page.items[1].is_pinned is False
        assert all(comment.parent_id is None for comment in page.items)

    def test_the_offset_is_handed_back_as_the_cursor(self) -> None:
        page = ADAPTER.parse_comments(bili("comments_with_replies"))
        assert (page.has_more, page.cursor) == (True, "20")

    def test_the_content_id_is_the_bvid_whatever_the_caller_sent(self) -> None:
        payload = bili("comments_with_replies")
        oid = payload["data"]["replies"][0]["oid"]
        page = ADAPTER.parse_comments(payload, content_id=f"av{oid}")
        content_id = page.items[0].content_id
        assert content_id.startswith("BV1")
        assert bv_to_av(content_id) == oid

    def test_a_guest_page_is_an_honest_last_page(self) -> None:
        """Three comments and is_end: what a guest is given, not a refusal."""
        page = ADAPTER.parse_comments(bili("comments_guest_first_page"))
        assert len(page.items) == 3
        assert (page.has_more, page.cursor) == (False, None)

    def test_a_pinned_comment_also_in_the_page_is_listed_once(self) -> None:
        payload = bili("comments_with_replies")
        payload["data"]["replies"].append(copy.deepcopy(payload["data"]["top_replies"][0]))
        page = ADAPTER.parse_comments(payload)
        ids = [comment.comment_id for comment in page.items]
        assert len(ids) == len(set(ids))


class TestReplies:
    def test_replies_name_the_thread_they_belong_to(self) -> None:
        payload = bili("comment_replies_page1")
        page = ADAPTER.parse_comment_replies(payload)
        root = str(payload["data"]["root"]["rpid"])

        assert len(page.items) == 3
        assert {reply.parent_id for reply in page.items} == {root}
        assert (page.has_more, page.cursor) == (True, "2")

    def test_the_caller_s_parent_wins(self) -> None:
        page = ADAPTER.parse_comment_replies(bili("comment_replies_page1"), parent_id="300000099")
        assert {reply.parent_id for reply in page.items} == {"300000099"}


class TestFolders:
    def test_public_folders(self) -> None:
        payload = bili("user_collections")
        page = ADAPTER.parse_author_collections(payload)

        assert len(page.items) == len(payload["data"]["list"])
        assert all(folder.is_public for folder in page.items)
        assert page.items[0].item_count == payload["data"]["list"][0]["media_count"]
        assert (page.has_more, page.cursor) == (False, None)

    def test_the_private_bit(self) -> None:
        payload = bili("user_collections")
        payload["data"]["list"][0]["attr"] = 21
        assert ADAPTER.parse_author_collections(payload).items[0].is_public is False

    def test_an_author_with_none_answers_null(self) -> None:
        page = ADAPTER.parse_author_collections(bili("user_collections_none"))
        assert page.items == []

    def test_folder_posts_page_from_the_cursor_that_asked(self) -> None:
        """The answer says only that there is more; which page it was is ours."""
        first = ADAPTER.parse_collection_posts(
            bili("collection_posts_page1"), fetched_at=FETCHED_AT
        )
        third = ADAPTER.parse_collection_posts(
            bili("collection_posts_page1"), fetched_at=FETCHED_AT, cursor="3"
        )
        assert (first.cursor, third.cursor) == ("2", "4")
        assert len(first.items) == 3

    def test_an_entry_that_is_not_a_video_is_not_a_post(self) -> None:
        payload = bili("collection_posts_page1")
        payload["data"]["medias"][0]["bvid"] = ""
        page = ADAPTER.parse_collection_posts(payload, fetched_at=FETCHED_AT)
        assert len(page.items) == 2


class TestRiskControl:
    def test_the_voucher_answer_is_a_refusal(self) -> None:
        assert ADAPTER.detect_risk_control(bili("risk_control_voucher")) == "code:-352"
        with pytest.raises(UpstreamRiskControl):
            ADAPTER.parse_content(bili("risk_control_voucher"), fetched_at=FETCHED_AT)

    def test_a_space_answer_marked_risky_is_a_refusal(self) -> None:
        payload = bili("user_posts_page1")
        payload["data"]["is_risk"] = True
        assert ADAPTER.detect_risk_control(payload) == "data.is_risk"

    def test_an_ordinary_answer_is_not(self) -> None:
        for name in ("video_normal", "user_profile", "comments_with_replies", "user_posts_page1"):
            assert ADAPTER.detect_risk_control(bili(name)) is None, name
