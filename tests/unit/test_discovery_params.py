"""Request building for the discovery endpoints.

Each builder here encodes a measured fact about the platform - a spelling, a
parameter the page sends, one it must not - and this pins them, so a later
tidy-up that looks harmless cannot quietly undo one.
"""

from __future__ import annotations

import pytest

from dtk.core.errors import InvalidParam
from dtk.platforms import get_adapter
from dtk.worker import registry
from tests.replay.support import FETCHED_AT


def query(endpoint: str, **params: object) -> dict[str, str]:
    platform = endpoint.split(".", 1)[0]
    return get_adapter(platform).build_request(endpoint, **params)["params"]


class TestRelatedPosts:
    def test_douyin_asks_for_the_first_batch(self) -> None:
        built = query("douyin.related_posts", aweme_id="7123456789012345678", count=10)
        assert built["aweme_id"] == "7123456789012345678"
        assert built["refresh_index"] == "1"
        assert built["filterGids"] == ""

    def test_tiktok_spells_the_post_item_id(self) -> None:
        built = query("tiktok.related_posts", item_id="7123456789012345678", count=16)
        assert built["itemID"] == "7123456789012345678"
        assert built["count"] == "16"

    def test_bilibili_sends_the_bvid_and_no_size(self) -> None:
        built = query("bilibili.related_posts", bvid="av170001", count=10)
        assert built == {"bvid": "BV17x411w7KC"}


class TestSounds:
    def test_the_two_tiktok_paths_spell_the_id_differently(self) -> None:
        assert query("tiktok.music_detail", music_id="1")["musicId"] == "1"
        assert query("tiktok.music_posts", music_id="1")["musicID"] == "1"

    def test_douyin_pages_a_sound_by_cursor(self) -> None:
        built = query("douyin.music_posts", music_id="1", cursor="13", count=12)
        assert (built["music_id"], built["cursor"], built["count"]) == ("1", "13", "12")


class TestHashtags:
    def test_tiktok_looks_a_hashtag_up_by_id(self) -> None:
        built = query("tiktok.hashtag_detail", challenge_id="6929")
        assert built["challengeId"] == "6929"
        assert "challengeName" not in built

    def test_an_all_digit_name_is_still_a_name(self) -> None:
        """#2024 is a hashtag; challengeId=2024 is a different one."""
        built = query("tiktok.hashtag_detail", challenge_name="2024")
        assert built["challengeName"] == "2024"
        assert "challengeId" not in built

    def test_an_id_that_is_not_a_number_is_refused_not_guessed(self) -> None:
        with pytest.raises(InvalidParam):
            query("tiktok.hashtag_detail", challenge_id="hibachi")

    def test_an_id_and_a_name_together_are_refused(self) -> None:
        with pytest.raises(InvalidParam):
            query("tiktok.hashtag_detail", challenge_id="6929", challenge_name="hibachi")

    def test_tiktok_drops_the_hash_from_a_name(self) -> None:
        assert query("tiktok.hashtag_detail", challenge_name="#hibachi")["challengeName"] == (
            "hibachi"
        )

    def test_tiktok_needs_one_or_the_other(self) -> None:
        with pytest.raises(InvalidParam):
            query("tiktok.hashtag_detail")

    def test_the_posts_under_a_tiktok_hashtag_refuse_a_name(self) -> None:
        """Sent as challengeID it is HTTP 400 "illegal ID given" - the answer an
        id nobody has gets - so the caller would hear the hashtag is gone."""
        with pytest.raises(InvalidParam) as caught:
            query("tiktok.hashtag_posts", challenge_id="hibachi")
        assert "/hashtag" in str(caught.value)

    def test_douyin_calls_it_a_challenge(self) -> None:
        assert query("douyin.hashtag_detail", ch_id="1592544802272436")["ch_id"] == (
            "1592544802272436"
        )

    def test_a_canonical_name_reaches_the_tiktok_builder(self) -> None:
        definition = registry.definition_for("tiktok.hashtag_detail")
        assert definition.platform_params({"name": "hibachi"}) == {"challenge_name": "hibachi"}

    def test_douyin_has_no_name_to_be_given(self) -> None:
        definition = registry.definition_for("douyin.hashtag_detail")
        with pytest.raises(InvalidParam):
            definition.platform_params({"name": "hibachi"})


def test_the_fixed_clock_is_shared() -> None:
    """The replay helpers are imported here, so they must stay importable from unit."""
    assert FETCHED_AT.tzinfo is not None


class TestSearch:
    def test_tiktok_pages_posts_by_offset_and_accounts_by_cursor(self) -> None:
        posts = query("tiktok.search_posts", keyword="cat", cursor="12", count=12)
        users = query("tiktok.search_authors", keyword="cat", cursor="10", count=10)
        assert (posts["offset"], "cursor" in posts) == ("12", False)
        assert (users["cursor"], "offset" in users) == ("10", False)
        assert posts["search_source"] == users["search_source"] == "normal_search"

    def test_bilibili_names_the_tab_it_searches(self) -> None:
        posts = query("bilibili.search_posts", keyword="cat", cursor="2", count=20)
        users = query("bilibili.search_authors", keyword="cat")
        assert posts == {"search_type": "video", "keyword": "cat", "page": "2", "page_size": "20"}
        assert users["search_type"] == "bili_user"
        assert users["page"] == "1"

    @pytest.mark.parametrize("endpoint", ["tiktok.search_posts", "bilibili.search_authors"])
    def test_a_blank_keyword_is_refused_before_it_is_sent(self, endpoint: str) -> None:
        with pytest.raises(InvalidParam):
            query(endpoint, keyword="   ")

    def test_tiktok_says_what_a_guest_loses(self) -> None:
        from dtk.core.types import LoginNeed

        posts = get_adapter("tiktok").endpoints["tiktok.search_posts"]
        users = get_adapter("tiktok").endpoints["tiktok.search_authors"]
        assert posts.login is LoginNeed.PREFERRED
        assert posts.login_for({"cursor": "12"}) is LoginNeed.REQUIRED
        assert users.login is LoginNeed.NONE
        assert users.login_for({"cursor": "10"}) is LoginNeed.REQUIRED


class TestMixes:
    def test_douyin_asks_for_an_authors_mixes(self) -> None:
        built = query("douyin.author_mixes", sec_user_id="MS4wLjABAAAAx", cursor="12", count=12)
        assert (built["sec_user_id"], built["cursor"], built["count"]) == (
            "MS4wLjABAAAAx",
            "12",
            "12",
        )
        assert built["req_from"] == "channel_pc_web"

    def test_tiktok_asks_for_an_authors_playlists(self) -> None:
        built = query("tiktok.author_mixes", sec_uid="MS4wLjABAAAAx", count=20)
        assert built["secUid"] == "MS4wLjABAAAAx"
        assert built["cursor"] == "0"

    def test_bilibili_pages_the_space_by_number(self) -> None:
        built = query("bilibili.author_mixes", mid="946974", cursor="2", count=20)
        assert built == {"mid": "946974", "page_num": "2", "page_size": "20"}

    def test_a_bilibili_season_is_asked_for_whole(self) -> None:
        """pn and ps change nothing (measured), so the cursor is not sent on."""
        built = query("bilibili.mix_posts", season_id="7039840", cursor="3", count=5)
        assert built == {"season_id": "7039840", "pn": "1", "ps": "20"}

    def test_a_bilibili_season_id_is_a_number(self) -> None:
        with pytest.raises(InvalidParam):
            query("bilibili.mix_posts", season_id="abc")


class TestLiveRooms:
    def test_a_douyin_room_is_a_number(self) -> None:
        with pytest.raises(InvalidParam):
            query("douyin.live_room", web_rid="abc")

    def test_douyin_asks_the_live_site_by_web_rid(self) -> None:
        request = get_adapter("douyin").build_request("douyin.live_room", web_rid="615171448842")
        assert request["url"].startswith("https://live.douyin.com/")
        assert request["params"]["web_rid"] == "615171448842"
        assert request["params"]["app_name"] == "douyin_web"
        assert "device_platform" in request["params"]
        assert request["headers"]["Referer"] == "https://live.douyin.com/"

    def test_tiktok_asks_by_the_owners_handle(self) -> None:
        built = query("tiktok.live_room", unique_id="@ventisfit")
        assert built["uniqueId"] == "ventisfit"
        assert built["sourceType"] == "54"

    def test_douyin_asks_the_reflow_page_by_the_broadcast(self) -> None:
        """The share page's own short list: no browser fields, app 1128."""
        request = get_adapter("douyin").build_request(
            "douyin.live_room_by_id", room_id="7300000000000000101"
        )
        assert request["url"] == "https://webcast.amemv.com/webcast/room/reflow/info/"
        assert request["params"] == {
            "type_id": "0",
            "live_id": "1",
            "room_id": "7300000000000000101",
            "app_id": "1128",
        }
        assert request["headers"]["Referer"] == "https://live.douyin.com/"

    def test_tiktok_asks_the_webcast_host_by_the_broadcast(self) -> None:
        """www.tiktok.com answers /webcast/room/info/ with a 302."""
        request = get_adapter("tiktok").build_request(
            "tiktok.live_room_by_id", room_id="7400000000000000101"
        )
        assert request["url"] == "https://webcast.tiktok.com/webcast/room/info/"
        assert request["params"]["room_id"] == "7400000000000000101"
        assert request["params"]["aid"] == "1988"

    def test_douyin_asks_the_webcast_host_about_many_broadcasts(self) -> None:
        """live.douyin.com answers /webcast/room/check_alive/ with a 404."""
        request = get_adapter("douyin").build_request(
            "douyin.live_status", room_ids=" 7300000000000000101, 7300000000000000102 "
        )
        assert request["url"] == "https://webcast.amemv.com/webcast/room/check_alive/"
        assert request["params"]["room_ids"] == "7300000000000000101,7300000000000000102"
        assert request["params"]["app_name"] == "douyin_web"
        assert request["headers"]["Referer"] == "https://live.douyin.com/"

    def test_tiktok_asks_the_webcast_host_about_many_broadcasts(self) -> None:
        request = get_adapter("tiktok").build_request(
            "tiktok.live_status", room_ids=" 7400000000000000101, 7400000000000000102 "
        )
        assert request["url"] == "https://webcast.tiktok.com/webcast/room/check_alive/"
        assert request["params"]["room_ids"] == "7400000000000000101,7400000000000000102"

    @pytest.mark.parametrize("endpoint", ["douyin.live_status", "tiktok.live_status"])
    def test_a_broadcast_named_twice_is_asked_about_once(self, endpoint: str) -> None:
        built = query(endpoint, room_ids="7400000000000000101,7400000000000000101")
        assert built["room_ids"] == "7400000000000000101"

    @pytest.mark.parametrize("endpoint", ["douyin.live_status", "tiktok.live_status"])
    @pytest.mark.parametrize(
        "room_ids",
        [
            "",
            " , ",
            "7400000000000000101,ventisfit",
            ",".join(str(7400000000000000000 + n) for n in range(101)),
        ],
    )
    def test_a_list_that_cannot_be_asked_about_is_refused(
        self, endpoint: str, room_ids: str
    ) -> None:
        with pytest.raises(InvalidParam):
            query(endpoint, room_ids=room_ids)

    @pytest.mark.parametrize("endpoint", ["douyin.live_status", "tiktok.live_status"])
    def test_a_hundred_broadcasts_is_one_call(self, endpoint: str) -> None:
        many = ",".join(str(7400000000000000000 + n) for n in range(100))
        assert query(endpoint, room_ids=many)["room_ids"] == many

    def test_douyin_asks_the_live_site_for_the_audience_list(self) -> None:
        request = get_adapter("douyin").build_request(
            "douyin.live_ranking", room_id="7300000000000000101"
        )
        assert request["url"] == "https://live.douyin.com/webcast/ranklist/audience/"
        built = request["params"]
        assert built["room_id"] == "7300000000000000101"
        assert built["rank_type"] == "30"
        # Without it the list stops at 100.
        assert built["webcast_sdk_version"] == "2450"
        assert built["app_name"] == "douyin_web"
        assert "web_rid" not in built
        assert request["headers"]["Referer"] == "https://live.douyin.com/"

    def test_a_ranking_is_asked_for_by_a_number(self) -> None:
        with pytest.raises(InvalidParam):
            query("douyin.live_ranking", room_id="abc")

    @pytest.mark.parametrize("endpoint", ["douyin.live_room_by_id", "tiktok.live_room_by_id"])
    def test_a_broadcast_is_a_number(self, endpoint: str) -> None:
        with pytest.raises(InvalidParam) as caught:
            query(endpoint, room_id="ventisfit")
        assert caught.value.details["field"] == "live_id"

    def test_bilibili_follows_a_live_room_with_its_play_info(self) -> None:
        request = get_adapter("bilibili").build_request("bilibili.live_streams", room_id="173551")
        assert request["params"]["room_id"] == "173551"
        assert request["params"]["qn"] == "10000"
        assert request["headers"]["Referer"] == "https://live.bilibili.com/"


class TestTrending:
    @pytest.mark.parametrize(
        ("board", "board_type", "sub_type"),
        [
            ("hot", "0", ""),
            ("entertainment", "2", "2"),
            ("society", "2", "4"),
            ("challenge", "2", "hotspot_challenge"),
        ],
    )
    def test_douyin_names_each_board_the_way_its_page_does(
        self, board: str, board_type: str, sub_type: str
    ) -> None:
        built = query("douyin.trending", board=board)
        assert (built["board_type"], built["board_sub_type"]) == (board_type, sub_type)

    def test_bilibili_has_one_board(self) -> None:
        assert query("bilibili.trending", board="hot")["limit"] == "10"
        with pytest.raises(InvalidParam):
            query("bilibili.trending", board="society")

    def test_every_board_name_is_one_the_route_offers(self) -> None:
        from dtk.core.types import TrendingBoard

        for board in TrendingBoard:
            query("douyin.trending", board=board.value)
