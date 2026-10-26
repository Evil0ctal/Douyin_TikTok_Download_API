"""What the discovery endpoints answer when the thing asked for is not there.

The companion of ``test_absence_matrix.py`` for the reads added beside it -
sounds, hashtags, search, live rooms - each shape captured with a guest
identity on 2026-10-20 and pinned for the reason that file gives: reading an
answer as a refusal cools a healthy identity, and that is the expensive
mistake.
"""

from __future__ import annotations

import json
from typing import Any

from dtk.core.types import Outcome, Platform
from dtk.platforms.registry import get_adapter
from dtk.transport.base import RawResponse
from dtk.transport.classify import Classifier
from tests.replay.support import load


def answer(payload: Any) -> RawResponse:
    return RawResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
        elapsed_ms=200,
    )


def verdict(payload: Any) -> Outcome:
    return Classifier().classify(answer(payload)).outcome


def spec(platform: Platform, endpoint: str) -> Any:
    return get_adapter(platform).endpoints[endpoint]


class TestSounds:
    def test_a_douyin_sound_nobody_has_is_a_null_block_not_a_refusal(self) -> None:
        """493 bytes, `status_code: 0`, `music_info: null`. The transport calls
        it OK; the endpoint's own `absent_when_null` is what makes it a
        not-found rather than a payload missing its one field."""
        payload = load("douyin", "music_detail_missing")
        assert payload["music_info"] is None
        assert verdict(payload) is Outcome.OK
        assert spec(Platform.DOUYIN, "douyin.music_detail").absent_when_null == "music_info"

    def test_a_douyin_sounds_posts_for_an_unknown_sound_are_a_business_error(self) -> None:
        """160 bytes, `status_code: 4` and "the server dozed off, try again"."""
        payload = {"aweme_list": None, "log_pb": {"impr_id": "x"}, "status_code": 4}
        assert verdict(payload) is Outcome.BUSINESS_ERROR

    def test_a_tiktok_sound_nobody_has_is_a_business_error(self) -> None:
        """205 bytes, `statusCode: 10203` beside `status_code: 0`."""
        assert verdict(load("tiktok", "music_detail_missing")) is Outcome.BUSINESS_ERROR

    def test_a_tiktok_sounds_posts_for_an_unknown_sound_are_a_business_error(self) -> None:
        """235 bytes, `statusCode: 100001` and `hasMore: false`."""
        payload = {"cursor": "0", "hasMore": False, "statusCode": 100001, "status_code": 0}
        assert verdict(payload) is Outcome.BUSINESS_ERROR


class TestHashtags:
    def test_a_douyin_hashtag_nobody_has_is_a_null_block(self) -> None:
        """261 bytes, `status_code: 0`, `ch_info: null`."""
        payload = load("douyin", "hashtag_detail_missing")
        assert payload["ch_info"] is None
        assert verdict(payload) is Outcome.OK
        assert spec(Platform.DOUYIN, "douyin.hashtag_detail").absent_when_null == "ch_info"

    def test_a_tiktok_hashtag_nobody_uses_is_a_business_error(self) -> None:
        """205 bytes, `statusCode: 10205`."""
        assert verdict(load("tiktok", "hashtag_detail_missing")) is Outcome.BUSINESS_ERROR

    def test_the_posts_under_a_tiktok_hashtag_nobody_has_are_a_business_error(self) -> None:
        """HTTP 400, 161 bytes: `statusCode: 10201`, "illegal ID given"."""
        response = RawResponse(
            status=400,
            headers={"content-type": "application/json"},
            body=json.dumps(
                {"statusCode": 10201, "statusMsg": "illegal ID given", "status_code": 10201}
            ).encode(),
            elapsed_ms=200,
        )
        assert Classifier().classify(response).outcome is Outcome.BUSINESS_ERROR


class TestLiveRooms:
    def test_a_douyin_room_nobody_has_is_a_business_error(self) -> None:
        """108 bytes, `status_code: 4001038` and "this cannot be viewed"."""
        assert verdict(load("douyin", "live_room_missing")) is Outcome.BUSINESS_ERROR

    def test_a_tiktok_handle_nobody_has_is_a_business_error(self) -> None:
        """114 bytes, `statusCode: 19881007`, `message: user_not_found`."""
        assert verdict(load("tiktok", "live_room_missing")) is Outcome.BUSINESS_ERROR

    def test_a_bilibili_room_nobody_has_is_a_business_error(self) -> None:
        """77 bytes, `code: 19002000` - when signed. Unsigned it is -352."""
        assert verdict(load("bilibili", "live_room_missing")) is Outcome.BUSINESS_ERROR

    def test_a_broadcast_nobody_has_is_a_business_error(self) -> None:
        """128 bytes on Douyin's reflow page and 126 on TikTok's webcast host,
        both `status_code: 10011`, "Request params error" (2026-10-26)."""
        for platform in ("douyin", "tiktok"):
            assert verdict(load(platform, "live_room_by_id_missing")) is Outcome.BUSINESS_ERROR, (
                platform
            )

    def test_a_ranking_for_a_broadcast_nobody_has_is_a_business_error(self) -> None:
        """142 bytes, `status_code: 10011`, "the service is busy, try later"."""
        assert verdict(load("douyin", "live_ranking_missing")) is Outcome.BUSINESS_ERROR

    def test_a_ranking_is_an_answer(self) -> None:
        assert verdict(load("douyin", "live_ranking")) is Outcome.OK

    def test_a_broadcast_that_is_on_is_an_answer(self) -> None:
        for platform in ("douyin", "tiktok"):
            assert verdict(load(platform, "live_room_by_id_on")) is Outcome.OK, platform

    def test_an_ended_broadcast_is_an_answer(self) -> None:
        for platform in ("douyin", "tiktok", "bilibili"):
            assert verdict(load(platform, "live_room_off")) is Outcome.OK, platform
