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


async def test_a_tiktok_tag_link_queues_its_name(client: Any) -> None:
    await signed_in(client)
    row = await queued(
        await client.get(
            "/api/v1/tiktok/hashtag", params={"url": "https://www.tiktok.com/tag/hibachi"}
        )
    )
    assert row.params["hashtag_id"] == "hibachi"


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


async def test_the_posts_under_a_tag_link_ask_for_the_id_first(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/tiktok/hashtag/posts", params={"url": "https://www.tiktok.com/tag/hibachi"}
    )
    assert error_code(response) == "INVALID_PARAM"
    details = envelope(response)["error"]["details"]
    assert details["field"] == "url"
    assert details["name"] == "hibachi"


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
