"""The calls that hand over or act as a cookie jar, asked by a viewer's session.

A console session carries no scopes and passes every scope check: it is bounded
by its role instead. Two places asked the scope question alone, so a viewer -
documented as read-only - got past both:

- polling an operator's explained task returned the jar the explanation
  stored, although asking for the explanation required the operator role;
- `POST /api/v1/tools/identity` drove the browser and handed back a new jar,
  where minting into the pool requires the operator role.

Review of that fix found the same gap one step over: reading any task back
checked its scope alone. A pinned task's result is one logged-in account's
private view, and a maintenance task's is an operator's - a proxy's exit
address, a backup. The request log hands a viewer the ids.

Each case is checked through the real stack, both ways round.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.types import UserRole
from dtk.services import tasks as task_service
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

DOUYIN_VIDEO = "https://www.douyin.com/video/7123456789012345678"
JAR = "sessionid=not-a-real-value"


async def explained_task(client: Any) -> str:
    """A finished task carrying what an operator's `?explain=true` stores."""
    await signed_in(client, username="operator", role=UserRole.OPERATOR)
    response = await client.post("/api/v1/parse", json={"url": DOUYIN_VIDEO})
    assert response.status_code == 202, response.text
    task_id = str(envelope(response)["data"]["task_id"])
    async with session_scope() as session:
        await task_service.finish(
            session,
            uuid.UUID(task_id),
            result={
                "data": {"content_id": "7123456789012345678"},
                "meta": {"explain": {"cookie_header": JAR, "url": "https://www.douyin.com/"}},
            },
        )
    return task_id


async def test_a_viewer_reading_an_explained_task_gets_no_jar(client: Any) -> None:
    task_id = await explained_task(client)
    await signed_in(client, username="viewer", role=UserRole.VIEWER)

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 200, response.text
    data = envelope(response)["data"]
    assert data["data"] == {"content_id": "7123456789012345678"}, "the result itself is shared"
    assert "explain" not in data.get("result_meta", {})
    assert JAR not in response.text


async def test_an_operator_reading_it_still_gets_the_explanation(client: Any) -> None:
    task_id = await explained_task(client)

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert envelope(response)["data"]["result_meta"]["explain"]["cookie_header"] == JAR


async def test_a_viewer_cannot_mint_an_identity(client: Any) -> None:
    await signed_in(client, username="viewer", role=UserRole.VIEWER)

    response = await client.post("/api/v1/tools/identity", json={"platform": "douyin"})

    assert response.status_code == 403, response.text
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_an_operator_passes_the_mint_gate(client: Any) -> None:
    """Past the gate, this instance has no browser to mint with - which is the
    answer that proves the gate let the operator through."""
    await signed_in(client, username="operator", role=UserRole.OPERATOR)

    response = await client.post("/api/v1/tools/identity", json={"platform": "douyin"})

    assert response.status_code != 403, response.text
    assert error_code(response) != "FORBIDDEN_SCOPE"


async def stored_task(endpoint: str, params: dict[str, Any], result: dict[str, Any]) -> str:
    async with session_scope() as session:
        task_id = await task_service.create(session, endpoint, params)
    async with session_scope() as session:
        await task_service.finish(session, task_id, result=result)
    return str(task_id)


async def pinned_bookmarks() -> str:
    return await stored_task(
        "douyin.author_bookmarks",
        {"count": 10, "identity": str(uuid.uuid4())},
        {"data": {"items": [{"content_id": "7123456789012345678"}]}},
    )


async def test_a_viewer_cannot_read_a_pinned_task(client: Any) -> None:
    """Somebody's bookmarks, read through an operator's pinned request."""
    task_id = await pinned_bookmarks()
    await signed_in(client, username="viewer", role=UserRole.VIEWER)

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 403, response.text
    assert error_code(response) == "FORBIDDEN_SCOPE"
    assert "7123456789012345678" not in response.text


async def test_nor_stream_it(client: Any) -> None:
    task_id = await pinned_bookmarks()
    await signed_in(client, username="viewer", role=UserRole.VIEWER)

    response = await client.get(f"/api/v1/tasks/{task_id}/events")

    assert response.status_code == 403, response.text


async def test_an_operator_reads_a_pinned_task(client: Any) -> None:
    task_id = await pinned_bookmarks()
    await signed_in(client, username="operator", role=UserRole.OPERATOR)

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert envelope(response)["data"]["data"]["items"][0]["content_id"] == "7123456789012345678"


async def test_a_viewer_still_reads_an_unpinned_task(client: Any) -> None:
    """Results are shared between the people on an instance; only a pinned one
    belongs to a session."""
    task_id = await stored_task(
        "douyin.content_detail", {"aweme_id": "7123"}, {"data": {"content_id": "7123"}}
    )
    await signed_in(client, username="viewer", role=UserRole.VIEWER)

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert envelope(response)["data"]["data"] == {"content_id": "7123"}


async def test_a_viewer_cannot_read_a_proxy_probe(client: Any) -> None:
    task_id = await stored_task(
        "proxy.test", {"proxy_id": str(uuid.uuid4())}, {"data": {"exit_ip": "203.0.113.7"}}
    )
    await signed_in(client, username="viewer", role=UserRole.VIEWER)

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 403, response.text
    assert "203.0.113.7" not in response.text


async def test_an_operator_cannot_cancel_a_restore(client: Any) -> None:
    """A restore is an admin's: so is reading or cancelling it."""
    async with session_scope() as session:
        task_id = await task_service.create(session, "backup.restore", {"file": "x"})
    await signed_in(client, username="operator", role=UserRole.OPERATOR)

    response = await client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 403, response.text
    async with session_scope() as session:
        assert (await task_service.get(session, task_id)).state.value == "queued"
