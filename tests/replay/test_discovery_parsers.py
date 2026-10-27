"""Replay tests for the reads that start beside a post rather than at one.

Related posts, sounds, hashtags, search, an author's own series, live rooms
and the trending board, on every platform that serves each. One file for all
of them because they are asserted the same way across platforms: what the
envelope says about paging, and that the items are the ordinary models the
other endpoints already return.

The envelopes are from captures made with a guest identity on 2026-10-20; the
posts inside them are the repository's own hand-built ones, so a post here
parses to exactly what the detail fixture of the same id does.
"""

from __future__ import annotations

import pytest

from dtk.core.errors import UnsupportedContent, UpstreamChanged
from dtk.core.types import ContentKind, Platform
from dtk.platforms import get_adapter
from dtk.worker import registry
from tests.replay.support import FETCHED_AT, load, without

# --------------------------------------------------------------------------
# Related posts
# --------------------------------------------------------------------------

RELATED_IDS = {
    "douyin": ["7000000000000000001", "7000000000000000002"],
    "tiktok": ["7100000000000000001", "7100000000000000002"],
    "bilibili": ["BV1y7411Q74g", "BV1y7411Q74u"],
}


@pytest.mark.parametrize("name", sorted(RELATED_IDS))
def test_related_posts_are_one_batch_of_ordinary_posts(name: str) -> None:
    page = get_adapter(name).parse_related_posts(load(name, "related_posts"), fetched_at=FETCHED_AT)

    assert [item.content_id for item in page.items] == RELATED_IDS[name]
    assert all(item.platform is Platform(name) for item in page.items)
    # Douyin says has_more: 1 with no cursor, TikTok cursor: -1; neither is a
    # page anything here could ask for, so both are the last page.
    assert page.cursor is None
    assert page.has_more is False


def test_a_related_album_is_still_an_album() -> None:
    page = get_adapter("douyin").parse_related_posts(
        load("douyin", "related_posts"), fetched_at=FETCHED_AT
    )
    assert [item.kind for item in page.items] == [ContentKind.VIDEO, ContentKind.IMAGE_ALBUM]


def test_bilibili_related_reads_the_archive_counters() -> None:
    first = (
        get_adapter("bilibili")
        .parse_related_posts(load("bilibili", "related_posts"), fetched_at=FETCHED_AT)
        .items[0]
    )
    assert first.stats.play_count == 9148981
    assert first.stats.digg_count == 180567
    assert first.duration_ms == 14_000
    assert first.author.uid == "10000001"
    assert first.media.video is None


@pytest.mark.parametrize(
    ("name", "key"), [("douyin", "aweme_list"), ("tiktok", "itemList"), ("bilibili", "data")]
)
def test_related_posts_without_their_list_is_a_schema_change(name: str, key: str) -> None:
    with pytest.raises(UpstreamChanged) as caught:
        get_adapter(name).parse_related_posts(
            without(load(name, "related_posts"), key), fetched_at=FETCHED_AT
        )
    assert caught.value.details["path"] == key


@pytest.mark.parametrize("name", sorted(RELATED_IDS))
def test_related_posts_resolve_through_the_registry(name: str) -> None:
    definition = registry.definition_for(f"{name}.related_posts")
    assert definition.required == (registry.CONTENT_ID,)
    assert definition.cache_ttl_key == "cache.list_ttl"
    page = definition.parse(load(name, "related_posts"), fetched_at=FETCHED_AT)
    assert [item.content_id for item in page.items] == RELATED_IDS[name]


# --------------------------------------------------------------------------
# Sounds
# --------------------------------------------------------------------------

MUSIC_PLATFORMS = ("douyin", "tiktok")


def test_a_douyin_sound_and_its_publisher() -> None:
    sound = get_adapter("douyin").parse_music_detail(load("douyin", "music_detail"))

    assert sound.platform is Platform.DOUYIN
    assert sound.music_id == "6800000000000000001"
    assert sound.title == "original sound - Synthetic Creator"
    assert sound.author == "Synthetic Creator"
    assert sound.duration_ms == 19_000
    assert sound.play_url == "https://sf6-cdn-tos.example-cdn.invalid/obj/ies-music/synthetic.mp3"
    assert sound.cover is not None
    assert sound.web_url == "https://www.douyin.com/music/6800000000000000001"
    assert sound.content_count == 103176
    # `is_original_sound`, not `is_original`: the capture said true and false.
    assert sound.is_original is True
    assert sound.owner is not None
    assert sound.owner.uid == sound.owner.sec_uid == "MS4wLjABAAAA_SYNTHETIC_DOUYIN_AUTHOR_0001"
    assert sound.owner.nickname == "Synthetic Creator"
    assert sound.owner.unique_id == "88881111"
    assert sound.raw is not None


def test_a_douyin_library_track_has_no_owner() -> None:
    payload = load("douyin", "music_detail")
    for key in ("sec_uid", "owner_nickname", "owner_handle", "owner_id"):
        payload["music_info"].pop(key)
    assert get_adapter("douyin").parse_music_detail(payload).owner is None


def test_a_tiktok_sound_and_its_publisher() -> None:
    sound = get_adapter("tiktok").parse_music_detail(load("tiktok", "music_detail"))

    assert sound.platform is Platform.TIKTOK
    assert sound.music_id == "6900000000000000101"
    assert sound.title == "original sound"
    assert sound.author == "Synthetic Creator"
    assert sound.duration_ms == 13_000
    assert sound.play_url == "https://v16.example-cdn.invalid/music/synthetic.mp3"
    assert sound.web_url == "https://www.tiktok.com/music/original-sound-6900000000000000101"
    assert sound.content_count == 189400
    assert sound.is_original is True
    assert sound.owner is not None
    assert sound.owner.sec_uid == "MS4wLjABAAAA_SYNTHETIC_TIKTOK_AUTHOR_0001"
    assert sound.owner.unique_id == "synthetic.creator"


def test_a_tiktok_sound_without_a_title_still_has_a_link() -> None:
    payload = load("tiktok", "music_detail")
    payload["musicInfo"]["music"]["title"] = ""
    payload["musicInfo"].pop("author")
    sound = get_adapter("tiktok").parse_music_detail(payload)
    assert sound.web_url == "https://www.tiktok.com/music/-6900000000000000101"
    assert sound.owner is None


@pytest.mark.parametrize("name", MUSIC_PLATFORMS)
def test_both_platforms_produce_the_same_sound_shape(name: str) -> None:
    sound = get_adapter(name).parse_music_detail(load(name, "music_detail"))
    other = get_adapter("tiktok" if name == "douyin" else "douyin").parse_music_detail(
        load("tiktok" if name == "douyin" else "douyin", "music_detail")
    )
    assert set(sound.model_dump()) == set(other.model_dump())


@pytest.mark.parametrize(
    ("name", "path"),
    [("douyin", "music_info"), ("tiktok", "musicInfo"), ("tiktok", "musicInfo.music")],
)
def test_a_sound_without_its_block_is_a_schema_change(name: str, path: str) -> None:
    with pytest.raises(UpstreamChanged) as caught:
        get_adapter(name).parse_music_detail(without(load(name, "music_detail"), path))
    assert caught.value.details["path"] == path


@pytest.mark.parametrize(("name", "cursor"), [("douyin", "13"), ("tiktok", "30")])
def test_the_posts_of_a_sound_page_by_cursor(name: str, cursor: str) -> None:
    page = get_adapter(name).parse_music_posts(
        load(name, "music_posts_page1"), fetched_at=FETCHED_AT
    )
    assert [item.content_id for item in page.items] == RELATED_IDS[name]
    assert page.cursor == cursor
    assert page.has_more is True


@pytest.mark.parametrize("name", MUSIC_PLATFORMS)
def test_sounds_resolve_through_the_registry(name: str) -> None:
    detail = registry.definition_for(f"{name}.music_detail")
    posts = registry.definition_for(f"{name}.music_posts")
    assert detail.required == posts.required == (registry.MUSIC_ID,)
    assert detail.cache_ttl_key == "cache.author_ttl"
    sound = detail.parse(load(name, "music_detail"))
    assert sound.content_count is not None


def test_bilibili_has_no_sounds_to_ask_about() -> None:
    from dtk.core.errors import UnsupportedContent

    assert "bilibili.music_detail" not in registry.ENDPOINTS
    with pytest.raises(UnsupportedContent):
        get_adapter("bilibili").parse_music_detail({})


# --------------------------------------------------------------------------
# Hashtags
# --------------------------------------------------------------------------


def test_a_douyin_hashtag() -> None:
    tag = get_adapter("douyin").parse_hashtag_detail(load("douyin", "hashtag_detail"))

    assert tag.platform is Platform.DOUYIN
    assert tag.hashtag_id == "1600000000000000001"
    assert tag.name == "sunrise"
    assert tag.description == "Synthetic hashtag description."
    assert tag.cover is not None
    assert tag.cover.url == "https://p11-sign.example-cdn.invalid/obj/image-cut-tos/synthetic.jpg"
    assert tag.web_url == "https://www.douyin.com/hashtag/1600000000000000001"
    assert tag.content_count == 1016265
    assert tag.view_count == 9266189037
    assert tag.raw is not None


def test_a_tiktok_hashtag_reads_the_exact_counters() -> None:
    tag = get_adapter("tiktok").parse_hashtag_detail(load("tiktok", "hashtag_detail"))

    assert tag.hashtag_id == "1600000000000000101"
    assert tag.name == "sunrise"
    assert tag.web_url == "https://www.tiktok.com/tag/sunrise"
    assert tag.cover is not None
    # statsV2, not stats: the capture said 0 posts and a rounded view count
    # beside the real figures.
    assert tag.content_count == 194388
    assert tag.view_count == 5205317187


def test_a_tiktok_hashtag_without_statsv2_falls_back_to_stats() -> None:
    payload = without(load("tiktok", "hashtag_detail"), "challengeInfo.statsV2")
    tag = get_adapter("tiktok").parse_hashtag_detail(payload)
    assert tag.content_count == 0
    assert tag.view_count == 5200000000


def test_an_empty_description_is_absent() -> None:
    payload = load("douyin", "hashtag_detail")
    payload["ch_info"]["desc"] = ""
    assert get_adapter("douyin").parse_hashtag_detail(payload).description is None


def test_both_platforms_produce_the_same_hashtag_shape() -> None:
    douyin = get_adapter("douyin").parse_hashtag_detail(load("douyin", "hashtag_detail"))
    tiktok = get_adapter("tiktok").parse_hashtag_detail(load("tiktok", "hashtag_detail"))
    assert set(douyin.model_dump()) == set(tiktok.model_dump())


@pytest.mark.parametrize(
    ("name", "path"),
    [("douyin", "ch_info"), ("tiktok", "challengeInfo"), ("tiktok", "challengeInfo.challenge")],
)
def test_a_hashtag_without_its_block_is_a_schema_change(name: str, path: str) -> None:
    with pytest.raises(UpstreamChanged) as caught:
        get_adapter(name).parse_hashtag_detail(without(load(name, "hashtag_detail"), path))
    assert caught.value.details["path"] == path


def test_the_posts_under_a_tiktok_hashtag_page_by_cursor() -> None:
    page = get_adapter("tiktok").parse_hashtag_posts(
        load("tiktok", "hashtag_posts_page1"), fetched_at=FETCHED_AT
    )
    assert [item.content_id for item in page.items] == RELATED_IDS["tiktok"]
    assert page.cursor == "30"
    assert page.has_more is True


def test_douyin_does_not_offer_the_posts_under_a_hashtag() -> None:
    from dtk.core.errors import UnsupportedContent

    assert "douyin.hashtag_detail" in registry.ENDPOINTS
    assert "douyin.hashtag_posts" not in registry.ENDPOINTS
    with pytest.raises(UnsupportedContent):
        get_adapter("douyin").parse_hashtag_posts({}, fetched_at=FETCHED_AT)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def test_a_tiktok_post_search_is_ordinary_posts_paged_by_offset() -> None:
    page = get_adapter("tiktok").parse_search_posts(
        load("tiktok", "search_posts_page1"), fetched_at=FETCHED_AT
    )
    assert [item.content_id for item in page.items] == RELATED_IDS["tiktok"]
    assert page.cursor == "12"
    assert page.has_more is True


def test_a_tiktok_search_page_with_nothing_left_has_no_list() -> None:
    """What a guest's second page is: no item_list key, has_more 0."""
    payload = load("tiktok", "search_posts_end")
    assert "item_list" not in payload
    page = get_adapter("tiktok").parse_search_posts(payload, fetched_at=FETCHED_AT)
    assert page.items == []
    assert page.cursor is None
    assert page.has_more is False


def test_an_empty_tiktok_page_claiming_more_is_still_the_end() -> None:
    payload = load("tiktok", "search_posts_end")
    payload["has_more"] = 1
    page = get_adapter("tiktok").parse_search_posts(payload, fetched_at=FETCHED_AT)
    assert page.has_more is False


def test_tiktok_accounts_carry_the_id_the_author_endpoints_take() -> None:
    page = get_adapter("tiktok").parse_search_authors(load("tiktok", "search_authors_page1"))

    first = page.items[0]
    assert first.platform is Platform.TIKTOK
    assert first.uid == "6900000000000000001"
    assert first.sec_uid == "MS4wLjABAAAA_SYNTHETIC_TIKTOK_SEARCHER_0001"
    assert first.unique_id == "synthetic.searcher.1"
    assert first.nickname == "Synthetic Searcher 1"
    assert first.web_url == "https://www.tiktok.com/@synthetic.searcher.1"
    assert first.verified is True
    assert first.stats is not None
    assert first.stats.follower_count == 128400
    assert first.stats.total_digg == 3894512
    assert page.items[1].verified is False
    assert page.cursor == "10"
    assert page.has_more is True


def test_a_bilibili_hit_loses_its_highlighting() -> None:
    page = get_adapter("bilibili").parse_search_posts(
        load("bilibili", "search_posts_page1"), fetched_at=FETCHED_AT
    )

    first = page.items[0]
    assert first.content_id == "BV1y7411Q74g"
    assert first.title == "Synthetic video title & more"
    assert first.web_url == "https://www.bilibili.com/video/BV1y7411Q74g"
    assert first.duration_ms == 70_000
    assert first.author.uid == first.author.sec_uid == "10000001"
    assert first.author.nickname == "Synthetic Creator"
    assert first.stats.play_count == 2898
    assert first.stats.digg_count == 322
    assert first.stats.comment_count == 23
    assert first.stats.collect_count == 25
    assert first.tags == ["synthetic", "fixture"]
    assert first.media.covers[0].url == (
        "https://i2.example-cdn.invalid/bfs/archive/synthetic-0.jpg"
    )
    assert first.media.video is None
    assert page.cursor == "2"
    assert page.has_more is True


def test_bilibili_search_stops_at_the_last_page_it_names() -> None:
    payload = load("bilibili", "search_posts_page1")
    payload["data"]["page"] = 50
    page = get_adapter("bilibili").parse_search_posts(payload, fetched_at=FETCHED_AT)
    assert page.has_more is False
    assert page.cursor is None


def test_a_bilibili_hit_that_is_not_a_video_is_left_out() -> None:
    payload = load("bilibili", "search_posts_page1")
    payload["data"]["result"][1]["bvid"] = ""
    page = get_adapter("bilibili").parse_search_posts(payload, fetched_at=FETCHED_AT)
    assert [item.content_id for item in page.items] == ["BV1y7411Q74g"]


def test_bilibili_uploaders_from_search() -> None:
    page = get_adapter("bilibili").parse_search_authors(load("bilibili", "search_authors_page1"))

    first = page.items[0]
    assert first.uid == first.sec_uid == "10000021"
    assert first.nickname == "Synthetic Uploader 1"
    assert first.signature == "Fixture account for tests. Not a real person."
    assert first.avatar is not None
    assert first.avatar.url == "https://i2.example-cdn.invalid/bfs/face/synthetic-0.jpg"
    assert first.web_url == "https://space.bilibili.com/10000021"
    assert first.verified is True
    assert first.stats is not None
    assert (first.stats.follower_count, first.stats.content_count) == (1921477, 238)
    assert page.cursor == "2"


def test_an_unverified_bilibili_uploader_is_not_verified() -> None:
    """Search spells "not verified" 127, not the card's -1 (measured)."""
    payload = load("bilibili", "search_authors_page1")
    payload["data"]["result"][0]["official_verify"] = {"type": 127, "desc": ""}
    page = get_adapter("bilibili").parse_search_authors(payload)
    assert page.items[0].verified is False
    assert page.items[1].verified is True


def test_a_tiktok_hashtag_link_is_quoted() -> None:
    from dtk.platforms.tiktok.parser import hashtag_web_url

    assert hashtag_web_url("a b#c") == "https://www.tiktok.com/tag/a%20b%23c"


def test_a_bilibili_search_with_no_hits_is_an_empty_last_page() -> None:
    payload = load("bilibili", "search_authors_none")
    assert "result" not in payload["data"]
    page = get_adapter("bilibili").parse_search_authors(payload)
    assert page.items == []
    assert page.has_more is False


def test_search_results_share_the_shapes_the_other_lists_use() -> None:
    tiktok = get_adapter("tiktok").parse_search_authors(load("tiktok", "search_authors_page1"))
    bilibili = get_adapter("bilibili").parse_search_authors(
        load("bilibili", "search_authors_page1")
    )
    assert set(tiktok.items[0].model_dump()) == set(bilibili.items[0].model_dump())


@pytest.mark.parametrize("name", ["tiktok", "bilibili"])
def test_searches_resolve_through_the_registry(name: str) -> None:
    for capability in ("search_posts", "search_authors"):
        definition = registry.definition_for(f"{name}.{capability}")
        assert definition.required == (registry.KEYWORD,)
        assert definition.cache_ttl_key == "cache.list_ttl"


def test_douyin_does_not_offer_search() -> None:
    from dtk.core.errors import UnsupportedContent

    assert "douyin.search_posts" not in registry.ENDPOINTS
    assert "douyin.search_authors" not in registry.ENDPOINTS
    with pytest.raises(UnsupportedContent):
        get_adapter("douyin").parse_search_authors({})


# --------------------------------------------------------------------------
# An author's own series
# --------------------------------------------------------------------------


def test_douyin_mixes_count_their_posts_and_plays() -> None:
    page = get_adapter("douyin").parse_author_mixes(load("douyin", "author_mixes"))

    first = page.items[0]
    assert first.platform is Platform.DOUYIN
    assert first.mix_id == "7300000000000000001"
    assert first.name == "Synthetic Mix 1"
    assert first.description == "Synthetic mix description."
    assert first.cover is not None
    assert first.item_count == 8
    assert first.play_count == 119548
    assert first.web_url == "https://www.douyin.com/collection/7300000000000000001"
    assert first.owner_id == "MS4wLjABAAAA_SYNTHETIC_DOUYIN_AUTHOR_0001"
    assert first.owner_name == "Synthetic Creator"
    assert first.created_at is not None
    assert first.updated_at is not None
    assert page.items[1].description is None
    assert page.has_more is False
    assert page.cursor is None


def test_tiktok_playlists_link_to_the_profile_they_are_on() -> None:
    page = get_adapter("tiktok").parse_author_mixes(load("tiktok", "author_mixes"))

    first, second = page.items
    assert first.mix_id == "7400000000000000001"
    assert first.name == "Synthetic Playlist 1"
    assert first.item_count == 7
    assert first.play_count is None
    assert first.owner_id == "MS4wLjABAAAA_SYNTHETIC_TIKTOK_AUTHOR_0001"
    assert first.owner_name == "synthetic.creator"
    assert first.web_url == (
        "https://www.tiktok.com/@synthetic.creator/playlist/Synthetic-Playlist-1-7400000000000000001"
    )
    assert second.item_count == 13
    assert page.has_more is False


def test_a_tiktok_author_without_playlists_is_an_empty_page() -> None:
    payload = load("tiktok", "author_mixes_none")
    assert "playList" not in payload
    page = get_adapter("tiktok").parse_author_mixes(payload)
    assert page.items == []
    assert page.has_more is False


def test_bilibili_lists_the_seasons_and_leaves_the_series_out() -> None:
    payload = load("bilibili", "author_mixes")
    assert payload["data"]["items_lists"]["series_list"]
    page = get_adapter("bilibili").parse_author_mixes(payload)

    assert [mix.mix_id for mix in page.items] == ["5000001", "5000002"]
    first = page.items[0]
    # `title`, not `name`, which carries a Chinese "collection" prefix.
    assert first.name == "Synthetic Season 1"
    assert first.item_count == 2
    assert first.owner_id == "10000001"
    assert first.web_url == "https://space.bilibili.com/10000001/lists/5000001?type=season"
    assert first.updated_at is not None
    # Page 2 of 20 with 32 counted: the last page.
    assert page.has_more is False


def test_bilibili_mixes_page_by_number_over_seasons_and_series_both() -> None:
    payload = load("bilibili", "author_mixes")
    payload["data"]["items_lists"]["page"]["page_num"] = 1
    page = get_adapter("bilibili").parse_author_mixes(payload)
    assert page.has_more is True
    assert page.cursor == "2"


def test_a_bilibili_season_is_every_video_in_one_page() -> None:
    page = get_adapter("bilibili").parse_mix_posts(
        load("bilibili", "mix_posts_page1"), fetched_at=FETCHED_AT
    )

    assert [item.content_id for item in page.items] == ["BV1y7411Q74W", "BV1y7411Q74p"]
    first = page.items[0]
    assert first.author.uid == "10000001"
    assert first.author.nickname == "Synthetic Creator"
    assert first.duration_ms == 1_538_000
    assert first.stats.play_count == 8982816
    assert first.stats.collect_count == 135642
    assert page.has_more is False


@pytest.mark.parametrize("name", ["douyin", "tiktok"])
def test_douyin_and_tiktok_mix_posts_still_read_the_author_list_envelope(name: str) -> None:
    page = get_adapter(name).parse_mix_posts(load(name, "user_posts_page1"), fetched_at=FETCHED_AT)
    assert page.items


@pytest.mark.parametrize("name", ["douyin", "tiktok", "bilibili"])
def test_mixes_resolve_through_the_registry(name: str) -> None:
    definition = registry.definition_for(f"{name}.author_mixes")
    assert definition.required == (registry.AUTHOR_ID,)
    page = definition.parse(load(name, "author_mixes"))
    assert page.items
    assert registry.definition_for(f"{name}.mix_posts").required == (registry.MIX_ID,)


def test_every_platform_produces_the_same_mix_shape() -> None:
    shapes = {
        name: set(
            get_adapter(name).parse_author_mixes(load(name, "author_mixes")).items[0].model_dump()
        )
        for name in ("douyin", "tiktok", "bilibili")
    }
    assert shapes["douyin"] == shapes["tiktok"] == shapes["bilibili"]


# --------------------------------------------------------------------------
# Live rooms
# --------------------------------------------------------------------------


def bilibili_live_on() -> dict:
    """The room answer as the fetch layer hands it over: with its pull URLs."""
    payload = load("bilibili", "live_room_on")
    follow_up = get_adapter("bilibili").follow_up(
        "bilibili.live_room", payload, {"room_id": "30000001"}
    )
    assert follow_up is not None
    return follow_up.merge(payload, load("bilibili", "live_streams"))


def test_a_douyin_room_that_is_live() -> None:
    room = get_adapter("douyin").parse_live_room(
        load("douyin", "live_room_on"), room_id="615171448842"
    )

    assert room.platform is Platform.DOUYIN
    # The web_rid that asked, not the room id the answer carries.
    assert room.room_id == "615171448842"
    assert room.web_url == "https://live.douyin.com/615171448842"
    assert room.is_live is True
    assert room.title == "Synthetic live title"
    assert room.cover is not None
    # room_view_stats.display_value, not the rounded user_count_str.
    assert room.viewer_count == 4474
    assert room.like_count == 15223
    assert room.author is not None
    assert room.author.sec_uid == "MS4wLjABAAAA_SYNTHETIC_DOUYIN_AUTHOR_0001"
    assert {(s.format, s.quality) for s in room.streams} == {
        ("flv", "FULL_HD1"),
        ("flv", "HD1"),
        ("hls", "FULL_HD1"),
        ("hls", "HD1"),
    }


def test_a_douyin_room_that_has_ended_has_no_streams() -> None:
    room = get_adapter("douyin").parse_live_room(load("douyin", "live_room_off"), room_id="1")
    assert room.is_live is False
    assert room.streams == []
    assert room.viewer_count is None
    assert room.title == "Synthetic ended broadcast"


def test_a_tiktok_room_reads_the_streams_out_of_their_json_string() -> None:
    room = get_adapter("tiktok").parse_live_room(load("tiktok", "live_room_on"))

    assert room.room_id == "synthetic.creator"
    assert room.web_url == "https://www.tiktok.com/@synthetic.creator/live"
    assert room.is_live is True
    assert room.viewer_count == 314
    assert room.started_at is not None
    assert room.author is not None
    assert room.author.sec_uid == "MS4wLjABAAAA_SYNTHETIC_TIKTOK_AUTHOR_0001"
    pairs = {(s.format, s.quality) for s in room.streams}
    assert ("flv", "origin") in pairs
    assert ("hls", "hd") in pairs
    # The audio-only rendition has no hls, and an empty URL is not a stream.
    assert ("hls", "ao") not in pairs
    assert all(s.url for s in room.streams)


def test_a_tiktok_room_with_a_stream_string_that_will_not_decode_has_no_streams() -> None:
    payload = load("tiktok", "live_room_on")
    payload["data"]["liveRoom"]["streamData"]["pull_data"]["stream_data"] = "{not json"
    room = get_adapter("tiktok").parse_live_room(payload)
    assert room.is_live is True
    assert room.streams == []


def test_a_tiktok_room_that_has_ended() -> None:
    room = get_adapter("tiktok").parse_live_room(load("tiktok", "live_room_off"))
    assert room.is_live is False
    assert room.streams == []
    assert room.started_at is None


def test_a_room_that_is_on_says_which_broadcast_it_is() -> None:
    """``live_id`` is what ``/live?live_id=`` and the other live reads take,
    so the answer that found a room has to hand it back."""
    douyin = get_adapter("douyin").parse_live_room(
        load("douyin", "live_room_on"), room_id="615171448842"
    )
    tiktok = get_adapter("tiktok").parse_live_room(load("tiktok", "live_room_on"))
    assert douyin.live_id == "7300000000000000101"
    # The user's roomId: nothing under liveRoom carries it.
    assert tiktok.live_id == "7400000000000000101"


def test_a_room_that_has_ended_has_no_broadcast_to_name() -> None:
    for platform in ("douyin", "tiktok"):
        room = get_adapter(platform).parse_live_room(load(platform, "live_room_off"), room_id="1")
        assert room.live_id is None, platform
    assert get_adapter("bilibili").parse_live_room(bilibili_live_on()).live_id is None


def test_a_douyin_room_by_its_broadcast_is_named_by_its_web_rid() -> None:
    """The reflow answer carries the room's web_rid as ``owner.web_rid``,
    which is what the room is asked for by again once this broadcast ends."""
    room = get_adapter("douyin").parse_live_room_by_id(load("douyin", "live_room_by_id_on"))

    assert room.platform is Platform.DOUYIN
    assert room.room_id == "615171448842"
    assert room.web_url == "https://live.douyin.com/615171448842"
    assert room.live_id == "7300000000000000101"
    assert room.is_live is True
    assert room.title == "Synthetic live title"
    # user_count: room_view_stats here counts who has watched, 348,959.
    assert room.viewer_count == 16109
    assert room.like_count == 101481
    # The broadcaster is room.owner; data.user is the guest that asked.
    assert room.author is not None
    assert room.author.sec_uid == "MS4wLjABAAAA_SYNTHETIC_DOUYIN_AUTHOR_0001"
    assert room.author.unique_id == "synthetic.creator"
    assert {(s.format, s.quality) for s in room.streams} == {
        (fmt, quality) for fmt in ("flv", "hls") for quality in ("FULL_HD1", "HD1", "SD1", "SD2")
    }


def test_a_douyin_broadcast_without_its_web_rid_is_named_by_itself() -> None:
    payload = load("douyin", "live_room_by_id_on")
    del payload["data"]["room"]["owner"]["web_rid"]
    room = get_adapter("douyin").parse_live_room_by_id(payload)
    assert room.room_id == "7300000000000000101"
    assert room.web_url is None


def test_a_tiktok_room_by_its_broadcast_is_named_by_its_owner() -> None:
    room = get_adapter("tiktok").parse_live_room_by_id(load("tiktok", "live_room_by_id_on"))

    assert room.platform is Platform.TIKTOK
    assert room.room_id == "synthetic.creator"
    assert room.web_url == "https://www.tiktok.com/@synthetic.creator/live"
    assert room.live_id == "7400000000000000101"
    assert room.is_live is True
    assert room.viewer_count == 249
    # create_time: start_time beside it is 0, and create_time is what the
    # handle answer calls startTime.
    assert room.started_at is not None
    assert int(room.started_at.timestamp()) == 1792537200
    assert room.like_count is None
    assert room.author is not None
    assert room.author.uid == "1000000000000000001"
    assert room.author.sec_uid == "MS4wLjABAAAA_SYNTHETIC_TIKTOK_AUTHOR_0001"
    assert room.author.unique_id == "synthetic.creator"
    # The same JSON-in-a-string as the handle answer, under another key.
    pairs = {(s.format, s.quality) for s in room.streams}
    assert pairs == {("flv", "hd"), ("hls", "hd"), ("flv", "ao")}


def test_a_broadcast_that_has_ended_has_no_streams() -> None:
    douyin = load("douyin", "live_room_by_id_on")
    douyin["data"]["room"]["status"] = 4
    tiktok = load("tiktok", "live_room_by_id_on")
    tiktok["data"]["status"] = 4
    for platform, payload in (("douyin", douyin), ("tiktok", tiktok)):
        room = get_adapter(platform).parse_live_room_by_id(payload)
        assert room.is_live is False, platform
        assert room.live_id is None, platform
        assert room.streams == [], platform
        assert room.viewer_count is None, platform


def test_douyin_and_tiktok_answer_for_each_broadcast_in_the_order_asked() -> None:
    douyin = get_adapter("douyin").parse_live_status(load("douyin", "live_status"))
    assert [(s.live_id, s.is_live) for s in douyin.items] == [
        ("7300000000000000101", True),
        ("7300000000000000102", False),
        ("7300000000000000103", False),
    ]
    assert all(s.platform is Platform.DOUYIN for s in douyin.items)
    assert douyin.has_more is False
    assert douyin.cursor is None

    tiktok = get_adapter("tiktok").parse_live_status(load("tiktok", "live_status"))
    assert [(s.live_id, s.is_live) for s in tiktok.items] == [
        ("7400000000000000101", True),
        ("7400000000000000102", True),
        ("7400000000000000103", False),
    ]
    assert all(s.platform is Platform.TIKTOK for s in tiktok.items)
    assert tiktok.has_more is False
    assert tiktok.cursor is None


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
def test_an_id_left_out_of_the_answer_is_off_air_in_its_own_place(platform: str) -> None:
    # Douyin leaves out an id that was never a broadcast. Before it was put
    # back the page came up one short, and a caller pairing it with its list
    # by position gave every room after the gap its neighbour's answer.
    adapter = get_adapter(platform)
    payload = load(platform, "live_status")
    full = [(s.live_id, s.is_live) for s in adapter.parse_live_status(payload).items]
    asked = tuple(live_id for live_id, _ in full)
    del payload["data"][0]  # on air in both fixtures, so a shift would show

    page = adapter.parse_live_status(payload, live_ids=asked)

    assert [(s.live_id, s.is_live) for s in page.items] == [(asked[0], False), *full[1:]]


def test_douyin_answers_an_ask_of_only_nonexistent_broadcasts_off_air() -> None:
    payload = load("douyin", "live_status")
    payload["data"] = []
    asked = ("7300000000000000901", "7300000000000000902")
    page = get_adapter("douyin").parse_live_status(payload, live_ids=asked)
    assert [(s.live_id, s.is_live) for s in page.items] == [(a, False) for a in asked]
    assert page.has_more is False
    assert page.cursor is None


def test_a_status_task_is_answered_for_its_list_as_the_request_sent_it() -> None:
    payload = load("douyin", "live_status")
    del payload["data"][0]
    page = registry.definition_for("douyin.live_status").parse(
        payload,
        params={"live_ids": " 7300000000000000101, 7300000000000000102,,7300000000000000101"},
    )
    assert [(s.live_id, s.is_live) for s in page.items] == [
        ("7300000000000000101", False),
        ("7300000000000000102", False),
    ]


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
def test_a_status_without_its_flag_is_a_changed_answer_not_an_ended_room(platform: str) -> None:
    payload = load(platform, "live_status")
    del payload["data"][0]["alive"]
    with pytest.raises(UpstreamChanged):
        get_adapter(platform).parse_live_status(payload)


def test_bilibili_has_no_way_to_check_many_rooms() -> None:
    with pytest.raises(UnsupportedContent):
        get_adapter("bilibili").parse_live_status({})


def test_a_douyin_ranking_is_its_places_in_order() -> None:
    page = get_adapter("douyin").parse_live_ranking(load("douyin", "live_ranking"))

    assert [viewer.rank for viewer in page.items] == [1, 2, 3, 4]
    first = page.items[0].author
    assert first is not None
    assert first.sec_uid == "MS4wLjABAAAA_SYNTHETIC_DOUYIN_VIEWER_0001"
    assert first.unique_id == "synthetic.viewer.a"
    assert first.web_url == (
        "https://www.douyin.com/user/MS4wLjABAAAA_SYNTHETIC_DOUYIN_VIEWER_0001"
    )
    assert page.has_more is False


def test_a_viewer_who_chose_not_to_be_listed_is_a_place_without_a_name() -> None:
    """The answer still carries their account; it is not passed on."""
    payload = load("douyin", "live_ranking")
    assert payload["data"]["ranks"][1]["user"]["sec_uid"]
    page = get_adapter("douyin").parse_live_ranking(payload)
    assert page.items[1].rank == 2
    assert page.items[1].author is None
    dumped = page.model_dump_json()
    assert "VIEWER_0002" not in dumped
    assert "synthetic.viewer.b" not in dumped


def test_a_viewer_named_only_by_a_mask_is_nobody_to_ask_about() -> None:
    page = get_adapter("douyin").parse_live_ranking(load("douyin", "live_ranking"))
    assert page.items[3].rank == 4
    assert page.items[3].author is None


def test_a_ranking_place_that_is_not_one_is_a_changed_answer() -> None:
    payload = load("douyin", "live_ranking")
    payload["data"]["ranks"][0]["rank"] = 0
    with pytest.raises(UpstreamChanged):
        get_adapter("douyin").parse_live_ranking(payload)


def test_an_empty_room_is_an_empty_ranking() -> None:
    payload = load("douyin", "live_ranking")
    payload["data"]["ranks"] = []
    assert get_adapter("douyin").parse_live_ranking(payload).items == []


@pytest.mark.parametrize("platform", ["tiktok", "bilibili"])
def test_only_douyin_ranks_a_room(platform: str) -> None:
    with pytest.raises(UnsupportedContent):
        get_adapter(platform).parse_live_ranking({})


def test_bilibili_has_no_broadcast_id_to_ask_by() -> None:
    with pytest.raises(UnsupportedContent):
        get_adapter("bilibili").parse_live_room_by_id({})


def test_a_bilibili_room_that_is_live_is_followed_by_its_pull_urls() -> None:
    room = get_adapter("bilibili").parse_live_room(bilibili_live_on())

    assert room.room_id == "30000001"
    assert room.web_url == "https://live.bilibili.com/30000001"
    assert room.is_live is True
    assert room.viewer_count == 2517
    assert room.author is not None
    assert room.author.uid == "10000001"
    assert room.author.nickname == "Synthetic Creator"
    formats = [s.format for s in room.streams]
    assert formats.count("flv") == 1
    assert formats.count("hls") == 2
    first = room.streams[0]
    assert first.url == (
        "https://d1--ov-gotcha07.example-cdn.invalid/live-bvc/000000/live_synthetic.flv?"
        "expires=0&qn=10000&sign=synthetic"
    )
    assert first.quality == "10000"


def test_a_bilibili_room_without_its_pull_urls_is_still_the_room() -> None:
    room = get_adapter("bilibili").parse_live_room(load("bilibili", "live_room_on"))
    assert room.is_live is True
    assert room.streams == []


def test_a_bilibili_room_that_is_off_asks_for_no_pull_urls() -> None:
    payload = load("bilibili", "live_room_off")
    assert get_adapter("bilibili").follow_up("bilibili.live_room", payload, {}) is None
    room = get_adapter("bilibili").parse_live_room(payload)
    assert room.is_live is False
    assert room.viewer_count is None


def test_every_platform_produces_the_same_live_room_shape() -> None:
    shapes = [
        set(
            get_adapter("douyin")
            .parse_live_room(load("douyin", "live_room_on"), room_id="1")
            .model_dump()
        ),
        set(get_adapter("tiktok").parse_live_room(load("tiktok", "live_room_on")).model_dump()),
        set(get_adapter("bilibili").parse_live_room(bilibili_live_on()).model_dump()),
    ]
    assert shapes[0] == shapes[1] == shapes[2]


@pytest.mark.parametrize(
    ("name", "room"),
    [("douyin", "615171448842"), ("tiktok", "synthetic.creator"), ("bilibili", "30000001")],
)
def test_live_rooms_resolve_through_the_registry(name: str, room: str) -> None:
    definition = registry.definition_for(f"{name}.live_room")
    assert definition.required == (registry.ROOM_ID,)
    parsed = definition.parse(load(name, "live_room_on"), params={"room_id": room})
    assert parsed.room_id == room


# --------------------------------------------------------------------------
# Trending boards
# --------------------------------------------------------------------------


def test_the_douyin_board_opens_with_a_pinned_line() -> None:
    page = get_adapter("douyin").parse_trending(load("douyin", "trending_hot"))

    pinned, first, second = page.items
    assert pinned.platform is Platform.DOUYIN
    assert pinned.rank is None
    # A pinned line says hot_value 0; it is unranked, not cold.
    assert pinned.hot_value is None
    assert (first.rank, first.hot_value, first.content_count) == (1, 12124851, 6)
    assert first.word == "Synthetic trending phrase 1"
    assert first.web_url == "https://www.douyin.com/hot/2700001"
    assert first.cover is not None
    assert second.rank == 2
    assert page.has_more is False


def test_bilibili_hot_search_is_ranked_in_order() -> None:
    page = get_adapter("bilibili").parse_trending(load("bilibili", "trending"))

    assert [line.rank for line in page.items] == [1, 2, 3]
    first = page.items[0]
    assert first.word == "Synthetic hot keyword 0"
    assert first.hot_value == 1396338
    assert first.content_count is None
    assert first.web_url == ("https://search.bilibili.com/all?keyword=Synthetic+hot+keyword+0")


def test_bilibili_puts_pinned_lines_first_and_unranked() -> None:
    payload = load("bilibili", "trending")
    trending = payload["data"]["trending"]
    trending["top_list"] = [dict(trending["list"][0], keyword="Pinned", show_name="Pinned")]
    page = get_adapter("bilibili").parse_trending(payload)
    assert [(line.word, line.rank) for line in page.items][:2] == [
        ("Pinned", None),
        ("Synthetic hot keyword 0", 1),
    ]


def test_both_boards_produce_the_same_shape() -> None:
    douyin = get_adapter("douyin").parse_trending(load("douyin", "trending_hot")).items[0]
    bilibili = get_adapter("bilibili").parse_trending(load("bilibili", "trending")).items[0]
    assert set(douyin.model_dump()) == set(bilibili.model_dump())


def test_tiktok_has_no_board() -> None:
    from dtk.core.errors import UnsupportedContent

    assert "tiktok.trending" not in registry.ENDPOINTS
    with pytest.raises(UnsupportedContent):
        get_adapter("tiktok").parse_trending({})
