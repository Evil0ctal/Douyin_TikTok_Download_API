"""Public data endpoints: SSRF rejection, scopes, async submission and ?wait=.

No worker runs in these tests, which is the point: the API layer's contract is
that it validates, queues and answers 202, and that a finished task is rendered
through the same envelope. Completion is simulated by finishing the task row
directly, exactly as a worker would.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.types import Scope, TaskState
from dtk.db.models import Task
from dtk.services import tasks as task_service
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    envelope,
    error_code,
    make_api_key,
    make_user,
    signed_in,
)

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

DOUYIN_VIDEO = "https://www.douyin.com/video/7123456789012345678"
DOUYIN_USER = "https://www.douyin.com/user/MS4wLjABAAAAexample"
TIKTOK_VIDEO = "https://www.tiktok.com/@someone/video/7123456789012345678"
SHORT_LINK = "https://v.douyin.com/abc123/"
#: What the Douyin app actually puts on the clipboard: a numeric code, the
#: caption and the link, all on one line.
SHARE_TEXT = "7.61 gTa:/ a caption https://v.douyin.com/abc123/ copy this link"

#: Addresses an SSRF attempt reaches for: cloud metadata, loopback, the docker
#: bridge gateway and a lookalike domain.
BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/api/v1/admin/settings",
    "http://172.17.0.1:6379/",
    "https://douyin.com.evil.example/video/1",
    "https://evil.example/video/1",
    "file:///etc/passwd",
]


async def read_key(user_id: uuid.UUID) -> str:
    return await make_api_key(user_id, scopes=(Scope.DOUYIN_READ, Scope.TIKTOK_READ))


async def finish_task(task_id: str, payload: dict[str, Any]) -> None:
    """Stand in for the worker: store a result and wake any waiter."""
    async with session_scope() as session:
        await task_service.finish(session, uuid.UUID(task_id), result=payload)


# --------------------------------------------------------------------------
# SSRF chokepoint
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", BLOCKED_URLS)
async def test_parse_rejects_urls_off_the_allowlist(client: Any, url: str) -> None:
    user_id = await make_user()
    key = await read_key(user_id)

    response = await client.post(
        "/api/v1/parse", json={"url": url}, headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 400
    body = envelope(response)
    assert body["error"]["code"] == "INVALID_URL"
    assert body["error"]["details"]["reason"] == "host_not_allowed"


async def test_private_address_is_rejected_before_any_task_is_created(client: Any) -> None:
    user_id = await make_user()
    key = await read_key(user_id)

    await client.post(
        "/api/v1/parse",
        json={"url": "http://127.0.0.1/video/1"},
        headers={"Authorization": f"Bearer {key}"},
    )
    async with session_scope() as session:
        assert await session.get(Task, uuid.uuid4()) is None
        rows = (await session.execute(Task.__table__.select())).all()
    assert rows == []


async def test_platform_endpoint_rejects_a_url_from_the_other_platform(client: Any) -> None:
    user_id = await make_user()
    key = await read_key(user_id)

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": TIKTOK_VIDEO},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert error_code(response) == "INVALID_URL"
    assert envelope(response)["error"]["details"]["reason"] == "platform_mismatch"


async def test_callback_url_is_refused_while_webhooks_are_off(client: Any) -> None:
    user_id = await make_user()
    key = await read_key(user_id)

    response = await client.post(
        "/api/v1/parse",
        json={"url": DOUYIN_VIDEO, "callback_url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert error_code(response) == "INVALID_PARAM"


# --------------------------------------------------------------------------
# Authentication and scopes
# --------------------------------------------------------------------------


async def test_data_endpoints_require_a_credential(client: Any) -> None:
    response = await client.get("/api/v1/douyin/video", params={"aweme_id": "7123"})
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_a_tiktok_key_cannot_read_douyin(client: Any) -> None:
    """Scope is enforced on the key even though its owner is an administrator.

    Almost every key on a self-hosted instance belongs to the admin account, so
    a scope check that softened for administrators would enforce nothing.
    """
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.TIKTOK_READ,))

    response = await client.get(
        "/api/v1/douyin/video",
        params={"aweme_id": "7123456789012345678"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 403
    body = envelope(response)
    assert body["error"]["code"] == "FORBIDDEN_SCOPE"
    assert body["error"]["details"]["required"] == ["douyin:read"]


async def test_a_read_key_cannot_reach_the_admin_surface(client: Any) -> None:
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))

    response = await client.get(
        "/api/v1/admin/identities", headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_rate_limit_headers_are_present_and_count_down(client: Any) -> None:
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,), rate_limit=5)
    headers = {"Authorization": f"Bearer {key}"}

    first = await client.get("/api/v1/douyin/video", params={"aweme_id": "7123"}, headers=headers)
    second = await client.get("/api/v1/douyin/video", params={"aweme_id": "7124"}, headers=headers)
    assert first.headers["x-ratelimit-limit"] == "5"
    assert int(first.headers["x-ratelimit-remaining"]) == 4
    assert int(second.headers["x-ratelimit-remaining"]) == 3


async def test_exhausting_the_rate_limit_answers_429_with_retry_after(client: Any) -> None:
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,), rate_limit=2)
    headers = {"Authorization": f"Bearer {key}"}

    for index in range(2):
        assert (
            await client.get(
                "/api/v1/douyin/video", params={"aweme_id": f"712{index}"}, headers=headers
            )
        ).status_code == 202

    limited = await client.get("/api/v1/douyin/video", params={"aweme_id": "7129"}, headers=headers)
    assert limited.status_code == 429
    body = envelope(limited)
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["retry_after"] >= 1
    assert limited.headers["retry-after"]


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------


async def test_submitting_returns_202_and_a_queued_task(client: Any) -> None:
    await signed_in(client)

    response = await client.post("/api/v1/parse", json={"url": DOUYIN_VIDEO})
    assert response.status_code == 202
    data = envelope(response)["data"]
    assert data["state"] == TaskState.QUEUED.value

    async with session_scope() as session:
        view = await task_service.get(session, uuid.UUID(data["task_id"]))
    assert view.endpoint == "parse"
    assert view.state is TaskState.QUEUED


async def test_the_task_row_is_committed_so_a_worker_can_see_it(client: Any) -> None:
    await signed_in(client)
    response = await client.post("/api/v1/parse", json={"url": DOUYIN_VIDEO})
    task_id = envelope(response)["data"]["task_id"]

    # A separate session stands in for the worker process.
    async with session_scope() as session:
        row = await session.get(Task, uuid.UUID(task_id))
    assert row is not None
    assert row.params["url"] == DOUYIN_VIDEO


async def test_url_endpoints_extract_the_id_so_callers_coalesce(client: Any) -> None:
    await signed_in(client)

    by_url = await client.get("/api/v1/douyin/video", params={"url": DOUYIN_VIDEO})
    by_id = await client.get("/api/v1/douyin/video", params={"aweme_id": "7123456789012345678"})
    assert by_url.status_code == 202
    assert by_id.status_code == 202
    # Same post reached two ways: one upstream request, one task.
    assert envelope(by_url)["data"]["task_id"] == envelope(by_id)["data"]["task_id"]


async def test_a_short_link_is_queued_for_expansion_rather_than_followed(client: Any) -> None:
    await signed_in(client)
    response = await client.post("/api/v1/parse", json={"url": SHORT_LINK})
    assert response.status_code == 202

    async with session_scope() as session:
        row = await session.get(Task, uuid.UUID(envelope(response)["data"]["task_id"]))
    assert row is not None
    assert row.params["url"] == "https://v.douyin.com/abc123"


async def test_parse_accepts_the_share_text_around_a_link(client: Any) -> None:
    """The clipboard, not a bare URL, is what an iOS Shortcut sends.

    The CLI and the MCP tools already pull the link out of pasted share text;
    the HTTP surface documents the same thing and has to do it too.
    """
    await signed_in(client)
    response = await client.post("/api/v1/parse", json={"url": SHARE_TEXT})
    assert response.status_code == 202, response.text

    async with session_scope() as session:
        row = await session.get(Task, uuid.UUID(envelope(response)["data"]["task_id"]))
    assert row is not None
    assert row.params["url"] == "https://v.douyin.com/abc123"


async def test_share_text_around_a_blocked_link_is_still_rejected(client: Any) -> None:
    """Extraction widens what is accepted, never what escapes the allowlist."""
    await signed_in(client)
    response = await client.post(
        "/api/v1/parse",
        json={"url": "look at this http://169.254.169.254/latest/meta-data/ now"},
    )
    assert response.status_code == 400
    assert envelope(response)["error"]["details"]["reason"] == "host_not_allowed"


async def test_parse_url_identifies_a_link_inside_share_text(client: Any) -> None:
    """The dry-run tool has to accept what the endpoint it previews accepts.

    /tools/parse-url documents "a share link, or the whole clipboard text with
    a link in it", and answered `allowed: false` for the second one - so the
    tool told operators their paste was unsupported while /parse fetched it.
    """
    await signed_in(client)
    response = await client.get("/api/v1/tools/parse-url", params={"url": SHARE_TEXT})
    assert response.status_code == 200, response.text
    data = envelope(response)["data"]
    assert data["allowed"] is True
    assert data["platform"] == "douyin"
    assert data["needs_expansion"] is True


async def test_parse_url_still_refuses_a_blocked_link_inside_share_text(client: Any) -> None:
    """Extraction widens what is recognized, never what the allowlist permits."""
    await signed_in(client)
    response = await client.get(
        "/api/v1/tools/parse-url",
        params={"url": "look at this http://169.254.169.254/latest/meta-data/ now"},
    )
    assert response.status_code == 200
    assert envelope(response)["data"]["allowed"] is False


async def test_wait_returns_the_result_once_the_task_is_done(client: Any) -> None:
    await signed_in(client)
    submitted = await client.get("/api/v1/douyin/user", params={"url": DOUYIN_USER})
    task_id = envelope(submitted)["data"]["task_id"]
    await finish_task(
        task_id,
        {"data": {"nickname": "example"}, "meta": {"cached": False, "duration_ms": 412}},
    )

    # The identical request joins the finished task instead of starting a new
    # one, so the wait returns immediately.
    response = await client.get("/api/v1/douyin/user", params={"url": DOUYIN_USER, "wait": 5})
    assert response.status_code == 200
    body = envelope(response)
    assert body["data"] == {"nickname": "example"}
    assert body["meta"]["cached"] is False
    assert body["meta"]["duration_ms"] == 412
    assert body["meta"]["task_id"] == task_id


async def test_wait_falls_back_to_202_when_the_task_is_still_running(client: Any) -> None:
    await signed_in(client)
    response = await client.post("/api/v1/parse", params={"wait": 1}, json={"url": DOUYIN_VIDEO})
    assert response.status_code == 202
    assert envelope(response)["data"]["task_id"]


async def test_wait_above_the_ceiling_is_rejected(client: Any) -> None:
    await signed_in(client)
    ceiling = 30
    response = await client.post(
        "/api/v1/parse", params={"wait": ceiling + 1}, json={"url": DOUYIN_VIDEO}
    )
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["max"] == ceiling


async def test_a_failed_task_renders_as_the_matching_error_envelope(client: Any) -> None:
    """A task that fails while a caller is waiting comes back as its error code.

    The waiter is woken by the completion signal rather than by a poll tick,
    which is the whole reason the signal exists.
    """
    await signed_in(client)
    submitted = await client.get("/api/v1/douyin/video", params={"aweme_id": "7123"})
    task_id = envelope(submitted)["data"]["task_id"]

    async def fail_shortly() -> None:
        await asyncio.sleep(0.2)
        async with session_scope() as session:
            await task_service.finish(
                session,
                uuid.UUID(task_id),
                error={"code": "IDENTITY_POOL_EXHAUSTED", "retry_after": 45},
            )

    response, _ = await asyncio.gather(
        client.get("/api/v1/douyin/video", params={"aweme_id": "7123", "wait": 5}),
        fail_shortly(),
    )
    assert response.status_code == 503
    body = envelope(response)
    assert body["error"]["code"] == "IDENTITY_POOL_EXHAUSTED"
    assert body["error"]["retry_after"] == 45
    assert response.headers["retry-after"] == "45"


async def test_a_failed_task_is_not_replayed_to_the_next_caller(client: Any) -> None:
    """A failure must not be pinned to the digest for the rest of the TTL.

    Joining an in-flight task is a saving; joining a finished failure would
    turn one bad request into a minute of them.
    """
    await signed_in(client)
    first = await client.get("/api/v1/douyin/video", params={"aweme_id": "7126"})
    first_id = envelope(first)["data"]["task_id"]
    async with session_scope() as session:
        await task_service.finish(
            session, uuid.UUID(first_id), error={"code": "UPSTREAM_RISK_CONTROL"}
        )

    second = await client.get("/api/v1/douyin/video", params={"aweme_id": "7126"})
    assert second.status_code == 202
    assert envelope(second)["data"]["task_id"] != first_id


async def test_missing_identifier_is_an_invalid_param(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/douyin/video")
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["fields"] == ["url", "aweme_id"]


async def test_an_unknown_platform_is_a_validation_error(client: Any) -> None:
    await signed_in(client)
    response = await client.get("/api/v1/bilibili/video", params={"aweme_id": "1"})
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PARAM"


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------


async def test_batch_reports_each_item_independently(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/tasks/batch",
        json={
            "items": [
                {"url": DOUYIN_VIDEO},
                {"url": TIKTOK_VIDEO},
                {"url": "https://evil.example/video/1"},
            ]
        },
    )
    assert response.status_code == 202
    data = envelope(response)["data"]
    assert data["submitted"] == 2
    assert data["rejected"] == 1
    assert data["items"][2]["task_id"] is None
    assert data["items"][2]["error"]["code"] == "INVALID_URL"
    assert all(item["task_id"] for item in data["items"][:2])


async def test_batch_rejects_an_oversized_request(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/tasks/batch",
        json={"items": [{"url": DOUYIN_VIDEO}] * 51},
    )
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PARAM"
