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

from dtk.core.errors import UpstreamChanged
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
