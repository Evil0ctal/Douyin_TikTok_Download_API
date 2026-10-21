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
