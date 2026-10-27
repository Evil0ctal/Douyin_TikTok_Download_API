"""The discovery routes: related posts, sounds, hashtags, search, live rooms.

As in ``test_api_content``, no worker runs: what is under test is that each
route validates, refuses what a platform does not serve, and queues the task
the worker would run, under the endpoint name and with the parameters the
registry expects.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.db.models import Task
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

DOUYIN_ID = "7123456789012345678"
TIKTOK_ID = "7123456789012345679"
BILIBILI_ID = "BV1xx411c7mD"


async def queued(response: Any) -> Task:
    assert response.status_code == 202, response.text
    async with session_scope() as session:
        row = await session.get(Task, uuid.UUID(envelope(response)["data"]["task_id"]))
    assert row is not None
    return row


# --------------------------------------------------------------------------
# Related posts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "post"),
    [("douyin", DOUYIN_ID), ("tiktok", TIKTOK_ID), ("bilibili", BILIBILI_ID)],
)
async def test_related_posts_queue_under_each_platforms_endpoint(
    client: Any, platform: str, post: str
) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(f"/api/v1/{platform}/video/related", params={"aweme_id": post})
    )
    assert row.endpoint == f"{platform}.related_posts"
    assert row.params["aweme_id"] == post


async def test_related_posts_read_the_post_out_of_a_link(client: Any) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(
            "/api/v1/douyin/video/related",
            params={"url": f"https://www.douyin.com/video/{DOUYIN_ID}", "count": 10},
        )
    )
    assert row.params["aweme_id"] == DOUYIN_ID
    assert row.params["count"] == 10


async def test_related_posts_need_a_post(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/tiktok/video/related")
    assert error_code(response) == "INVALID_PARAM"


async def test_related_posts_refuse_tiktoks_page_ceiling(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/tiktok/video/related", params={"aweme_id": TIKTOK_ID, "count": 36}
    )
    assert error_code(response) == "INVALID_PARAM"


# --------------------------------------------------------------------------
# Sounds
# --------------------------------------------------------------------------

MUSIC_ID = "7661456186985761599"


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
@pytest.mark.parametrize(
    ("path", "endpoint"), [("music", "music_detail"), ("music/posts", "music_posts")]
)
async def test_a_sound_queues_under_each_platforms_endpoint(
    client: Any, platform: str, path: str, endpoint: str
) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(f"/api/v1/{platform}/{path}", params={"music_id": MUSIC_ID})
    )
    assert row.endpoint == f"{platform}.{endpoint}"
    assert row.params["music_id"] == MUSIC_ID


@pytest.mark.parametrize(
    ("link", "platform"),
    [
        (f"https://www.douyin.com/music/{MUSIC_ID}", "douyin"),
        (f"https://www.tiktok.com/music/original-sound-{MUSIC_ID}", "tiktok"),
    ],
)
async def test_a_sound_is_read_out_of_its_link(client: Any, link: str, platform: str) -> None:
    await signed_in(client)
    row = await queued(await client.get(f"/api/v1/{platform}/music", params={"url": link}))
    assert row.params["music_id"] == MUSIC_ID
    assert row.params.get("url") is None


async def test_the_posts_of_a_sound_carry_the_page(client: Any) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(
            "/api/v1/douyin/music/posts",
            params={"music_id": MUSIC_ID, "cursor": "13", "count": 12},
        )
    )
    assert row.params["cursor"] == "13"
    assert row.params["count"] == 12


async def test_bilibili_has_no_sounds(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/bilibili/music", params={"music_id": MUSIC_ID})
    assert error_code(response) == "UNSUPPORTED_CONTENT"
    assert envelope(response)["error"]["details"]["supported"] == ["douyin", "tiktok"]


async def test_a_sound_id_is_a_number(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/douyin/music", params={"music_id": "abc"})
    assert error_code(response) == "INVALID_PARAM"


async def test_a_sound_link_from_the_other_platform_is_refused(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/douyin/music",
        params={"url": f"https://www.tiktok.com/music/x-{MUSIC_ID}"},
    )
    assert error_code(response) == "INVALID_URL"


# --------------------------------------------------------------------------
# Hashtags
# --------------------------------------------------------------------------

HASHTAG_ID = "1592544802272436"


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_a_hashtag_queues_by_id(client: Any, platform: str) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(f"/api/v1/{platform}/hashtag", params={"hashtag_id": HASHTAG_ID})
    )
    assert row.endpoint == f"{platform}.hashtag_detail"
    assert row.params["hashtag_id"] == HASHTAG_ID


async def test_a_tiktok_hashtag_queues_by_name(client: Any) -> None:
    await signed_in(client)
    row = await queued(await client.get("/api/v1/tiktok/hashtag", params={"name": "#hibachi"}))
    assert row.params["name"] == "hibachi"


@pytest.mark.parametrize("tag", ["hibachi", "2024"])
async def test_a_tiktok_tag_link_queues_its_name(client: Any, tag: str) -> None:
    """Even when the name is all digits: #2024 is not hashtag number 2024."""
    await signed_in(client)
    row = await queued(
        await client.get(
            "/api/v1/tiktok/hashtag", params={"url": f"https://www.tiktok.com/tag/{tag}"}
        )
    )
    assert row.params["name"] == tag
    assert "hashtag_id" not in row.params


async def test_a_bare_hash_is_not_a_name(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/tiktok/hashtag", params={"name": "#"})
    assert error_code(response) == "INVALID_PARAM"


async def test_douyin_refuses_a_hashtag_name(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/douyin/hashtag", params={"name": "sunrise"})
    assert error_code(response) == "INVALID_PARAM"


async def test_a_name_and_an_id_together_are_refused(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/tiktok/hashtag", params={"name": "hibachi", "hashtag_id": "6929"}
    )
    assert error_code(response) == "INVALID_PARAM"


async def test_the_posts_under_a_tiktok_hashtag_queue(client: Any) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(
            "/api/v1/tiktok/hashtag/posts", params={"hashtag_id": "6929", "cursor": "30"}
        )
    )
    assert row.endpoint == "tiktok.hashtag_posts"
    assert row.params["cursor"] == "30"


@pytest.mark.parametrize("tag", ["hibachi", "2024"])
async def test_the_posts_under_a_tag_link_ask_for_the_id_first(client: Any, tag: str) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/tiktok/hashtag/posts", params={"url": f"https://www.tiktok.com/tag/{tag}"}
    )
    assert error_code(response) == "INVALID_PARAM"
    details = envelope(response)["error"]["details"]
    assert details["field"] == "url"
    assert details["name"] == tag


async def test_douyin_does_not_offer_the_posts_under_a_hashtag(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/douyin/hashtag/posts", params={"hashtag_id": HASHTAG_ID})
    assert error_code(response) == "UNSUPPORTED_CONTENT"
    assert envelope(response)["error"]["details"]["supported"] == ["tiktok"]


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["tiktok", "bilibili"])
@pytest.mark.parametrize(
    ("path", "endpoint"), [("search/posts", "search_posts"), ("search/users", "search_authors")]
)
async def test_a_search_queues_its_keyword(
    client: Any, platform: str, path: str, endpoint: str
) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(f"/api/v1/{platform}/{path}", params={"keyword": "  cat  ", "cursor": "2"})
    )
    assert row.endpoint == f"{platform}.{endpoint}"
    assert row.params["keyword"] == "cat"
    assert row.params["cursor"] == "2"


async def test_a_search_needs_a_keyword(client: Any) -> None:
    await signed_in(client)
    assert error_code(await client.get("/api/v1/tiktok/search/posts")) == "INVALID_PARAM"
    response = await client.get("/api/v1/tiktok/search/posts", params={"keyword": "   "})
    assert error_code(response) == "INVALID_PARAM"


async def test_douyin_does_not_offer_search(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/douyin/search/posts", params={"keyword": "cat"})
    assert error_code(response) == "UNSUPPORTED_CONTENT"
    assert envelope(response)["error"]["details"]["supported"] == ["bilibili", "tiktok"]


async def test_bilibili_search_refuses_its_page_ceiling(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/bilibili/search/users", params={"keyword": "cat", "count": 21}
    )
    assert error_code(response) == "INVALID_PARAM"


# --------------------------------------------------------------------------
# An author's own series
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "author"),
    [
        ("douyin", "MS4wLjABAAAAexample"),
        ("tiktok", "MS4wLjABAAAAexample"),
        ("bilibili", "946974"),
    ],
)
async def test_an_authors_mixes_queue(client: Any, platform: str, author: str) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(f"/api/v1/{platform}/user/mixes", params={"sec_user_id": author})
    )
    assert row.endpoint == f"{platform}.author_mixes"
    assert row.params["sec_user_id"] == author


async def test_an_authors_mixes_are_read_out_of_a_profile_link(client: Any) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(
            "/api/v1/bilibili/user/mixes", params={"url": "https://space.bilibili.com/946974"}
        )
    )
    assert row.params["sec_user_id"] == "946974"


async def test_a_bilibili_season_is_a_mix(client: Any) -> None:
    await signed_in(client)
    row = await queued(await client.get("/api/v1/bilibili/mix/posts", params={"mix_id": "7039840"}))
    assert row.endpoint == "bilibili.mix_posts"


# --------------------------------------------------------------------------
# Live rooms
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "link", "room"),
    [
        ("douyin", "https://live.douyin.com/615171448842?show_type=live_cover", "615171448842"),
        ("tiktok", "https://www.tiktok.com/@ventisfit/live", "ventisfit"),
        ("bilibili", "https://live.bilibili.com/173551", "173551"),
    ],
)
async def test_a_live_room_is_read_out_of_its_link(
    client: Any, platform: str, link: str, room: str
) -> None:
    await signed_in(client)
    row = await queued(await client.get(f"/api/v1/{platform}/live", params={"url": link}))
    assert row.endpoint == f"{platform}.live_room"
    assert row.params["room_id"] == room


async def test_a_tiktok_room_takes_the_handle_with_or_without_the_at(client: Any) -> None:
    await signed_in(client)
    row = await queued(await client.get("/api/v1/tiktok/live", params={"room_id": "@ventisfit"}))
    assert row.params["room_id"] == "ventisfit"


async def test_a_live_room_needs_a_room(client: Any) -> None:
    await signed_in(client)
    assert error_code(await client.get("/api/v1/douyin/live")) == "INVALID_PARAM"


@pytest.mark.parametrize(
    ("platform", "room"), [("douyin", "abc"), ("bilibili", "room"), ("tiktok", "@")]
)
async def test_a_room_that_cannot_be_one_is_refused_before_it_queues(
    client: Any, platform: str, room: str
) -> None:
    await signed_in(client)
    response = await client.get(f"/api/v1/{platform}/live", params={"room_id": room})
    assert error_code(response) == "INVALID_PARAM"


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_a_broadcast_is_asked_for_by_its_own_id(client: Any, platform: str) -> None:
    await signed_in(client)
    response = await client.get(
        f"/api/v1/{platform}/live", params={"live_id": "7300000000000000101"}
    )
    row = await queued(response)
    assert row.endpoint == f"{platform}.live_room_by_id"
    assert row.params["live_id"] == "7300000000000000101"


async def test_a_douyin_reflow_link_goes_to_the_broadcast_without_a_hop(client: Any) -> None:
    """What an app share link expands to. The id is in the link, so the route
    reads it rather than queueing the link for the worker to follow."""
    await signed_in(client)
    link = "https://webcast.amemv.com/douyin/webcast/reflow/7318296342189919011?u_code=x"
    row = await queued(await client.get("/api/v1/douyin/live", params={"url": link}))
    assert row.endpoint == "douyin.live_room_by_id"
    assert row.params["live_id"] == "7318296342189919011"
    assert row.params.get("url") is None


async def test_an_id_wins_over_a_link(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/douyin/live",
        params={"url": "https://live.douyin.com/615171448842", "live_id": "7300000000000000101"},
    )
    assert (await queued(response)).endpoint == "douyin.live_room_by_id"


async def test_a_broadcast_id_is_a_number(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/tiktok/live", params={"live_id": "ventisfit"})
    assert error_code(response) == "INVALID_PARAM"


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_many_broadcasts_are_one_task(client: Any, platform: str) -> None:
    await signed_in(client)
    response = await client.get(
        f"/api/v1/{platform}/live/status",
        params={"live_ids": "7400000000000000101, 7400000000000000102,7400000000000000101"},
    )
    row = await queued(response)
    assert row.endpoint == f"{platform}.live_status"
    # Spaces and the repeat gone, so two spellings of one list share a task.
    assert row.params["live_ids"] == "7400000000000000101,7400000000000000102"


@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
@pytest.mark.parametrize("live_ids", ["ventisfit", "7400000000000000101,,x"])
async def test_a_status_list_that_cannot_be_one_is_refused(
    client: Any, platform: str, live_ids: str
) -> None:
    await signed_in(client)
    response = await client.get(f"/api/v1/{platform}/live/status", params={"live_ids": live_ids})
    assert error_code(response) == "INVALID_PARAM"


async def test_bilibili_has_no_way_to_check_many_rooms(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/bilibili/live/status", params={"live_ids": "7300000000000000101"}
    )
    assert error_code(response) == "UNSUPPORTED_CONTENT"


async def test_a_douyin_ranking_queues_by_its_broadcast(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/douyin/live/ranking", params={"live_id": "7300000000000000101"}
    )
    row = await queued(response)
    assert row.endpoint == "douyin.live_ranking"
    assert row.params["live_id"] == "7300000000000000101"


async def test_a_ranking_needs_a_broadcast_that_is_a_number(client: Any) -> None:
    await signed_in(client)
    assert error_code(await client.get("/api/v1/douyin/live/ranking")) == "INVALID_PARAM"
    response = await client.get("/api/v1/douyin/live/ranking", params={"live_id": "615x"})
    assert error_code(response) == "INVALID_PARAM"


@pytest.mark.parametrize("platform", ["tiktok", "bilibili"])
async def test_only_douyin_ranks_a_room(client: Any, platform: str) -> None:
    await signed_in(client)
    response = await client.get(
        f"/api/v1/{platform}/live/ranking", params={"live_id": "7400000000000000101"}
    )
    assert error_code(response) == "UNSUPPORTED_CONTENT"


async def test_bilibili_has_no_broadcast_id(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/bilibili/live", params={"live_id": "173551"})
    assert error_code(response) == "UNSUPPORTED_CONTENT"


# --------------------------------------------------------------------------
# Trending boards
# --------------------------------------------------------------------------


@pytest.mark.parametrize("board", ["hot", "entertainment", "society", "challenge"])
async def test_each_douyin_board_queues(client: Any, board: str) -> None:
    await signed_in(client)
    row = await queued(await client.get("/api/v1/douyin/trending", params={"board": board}))
    assert row.endpoint == "douyin.trending"
    assert row.params["board"] == board


async def test_the_board_defaults_to_hot(client: Any) -> None:
    await signed_in(client)
    row = await queued(await client.get("/api/v1/bilibili/trending"))
    assert row.params["board"] == "hot"


async def test_a_board_that_does_not_exist_is_refused(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/douyin/trending", params={"board": "weekly"})
    assert error_code(response) == "INVALID_PARAM"


async def test_bilibili_refuses_a_board_it_does_not_have(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/bilibili/trending", params={"board": "society"})
    assert error_code(response) == "INVALID_PARAM"


async def test_tiktok_has_no_trending_board(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/tiktok/trending")
    assert error_code(response) == "UNSUPPORTED_CONTENT"
    assert envelope(response)["error"]["details"]["supported"] == ["bilibili", "douyin"]
