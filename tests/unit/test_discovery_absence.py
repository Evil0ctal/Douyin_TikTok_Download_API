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
