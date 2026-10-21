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
