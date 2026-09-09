"""Task polling and the server-sent event stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.redis import get_redis
from dtk.core.types import TaskState
from dtk.services import tasks as task_service
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    envelope,
    error_code,
    signed_in,
)

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

DOUYIN_VIDEO = "https://www.douyin.com/video/7123456789012345678"


async def submit(client: Any) -> str:
    response = await client.post("/api/v1/parse", json={"url": DOUYIN_VIDEO})
    assert response.status_code == 202
    return str(envelope(response)["data"]["task_id"])


async def test_polling_reports_the_queued_state(client: Any) -> None:
    await signed_in(client)
    task_id = await submit(client)

    response = await client.get(f"/api/v1/tasks/{task_id}")
    data = envelope(response)["data"]
    assert data["state"] == TaskState.QUEUED.value
    assert data["endpoint"] == "parse"
    assert "data" not in data


async def test_polling_returns_the_result_once_it_is_done(client: Any) -> None:
    await signed_in(client)
    task_id = await submit(client)
    async with session_scope() as session:
        await task_service.finish(
            session, uuid.UUID(task_id), result={"data": {"content_id": "7123"}}
        )

    response = await client.get(f"/api/v1/tasks/{task_id}")
    data = envelope(response)["data"]
    assert data["state"] == TaskState.DONE.value
    assert data["data"] == {"content_id": "7123"}
    assert data["finished_at"]


async def test_polling_surfaces_the_error_of_a_failed_task(client: Any) -> None:
    await signed_in(client)
    task_id = await submit(client)
    async with session_scope() as session:
        await task_service.finish(
            session, uuid.UUID(task_id), error={"code": "UPSTREAM_CHANGED", "path": "aweme_detail"}
        )

    data = envelope(await client.get(f"/api/v1/tasks/{task_id}"))["data"]
    assert data["state"] == TaskState.FAILED.value
    assert data["error"]["code"] == "UPSTREAM_CHANGED"


async def test_an_unknown_task_is_a_404_envelope(client: Any) -> None:
    await signed_in(client)
    response = await client.get(f"/api/v1/tasks/{uuid.uuid4()}")
    assert response.status_code == 404
    assert error_code(response) == "TASK_NOT_FOUND"


async def test_an_expired_result_reads_as_task_not_found(client: Any) -> None:
    """The row outlives its payload; a caller must be told to re-submit."""
    await signed_in(client)
    task_id = await submit(client)
    async with session_scope() as session:
        await task_service.finish(session, uuid.UUID(task_id), result={"data": {"x": 1}})
        await task_service.expire_results(session, hours=-1)

    response = await client.get(f"/api/v1/tasks/{task_id}")
    assert error_code(response) == "TASK_NOT_FOUND"


async def test_tasks_require_authentication(client: Any) -> None:
    response = await client.get(f"/api/v1/tasks/{uuid.uuid4()}")
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_the_event_stream_delivers_state_then_result(client: Any) -> None:
    await signed_in(client)
    task_id = await submit(client)

    async def finish_shortly() -> None:
        await asyncio.sleep(0.3)
        async with session_scope() as session:
            await task_service.finish(
                session, uuid.UUID(task_id), result={"data": {"content_id": "7123"}}
            )

    events: list[tuple[str, dict[str, Any]]] = []

    async def read_stream() -> None:
        async with client.stream(
            "GET", f"/api/v1/tasks/{task_id}/events", params={"timeout": 10}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            name: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    name = line.removeprefix("event: ").strip()
                elif line.startswith("data: ") and name:
                    events.append((name, json.loads(line.removeprefix("data: "))))
                    if name == "end":
                        return

    await asyncio.wait_for(asyncio.gather(read_stream(), finish_shortly()), timeout=20)

    names = [name for name, _ in events]
    assert names[0] == "state"
    assert "result" in names
    assert names[-1] == "end"
    result = next(payload for name, payload in events if name == "result")
    assert result["state"] == TaskState.DONE.value
    assert result["data"] == {"content_id": "7123"}


async def test_the_event_stream_rejects_an_unknown_task_before_streaming(client: Any) -> None:
    await signed_in(client)
    response = await client.get(f"/api/v1/tasks/{uuid.uuid4()}/events")
    assert response.status_code == 404
    assert error_code(response) == "TASK_NOT_FOUND"


# --------------------------------------------------------------------------
# Queue backpressure
# --------------------------------------------------------------------------


async def test_the_queue_ceiling_sheds_load_instead_of_growing(client: Any) -> None:
    """sched.queue_max was a setting the console exposed and nothing read.

    RejectReason.QUEUE_FULL was defined and never raised, so the queue grew
    without bound and callers waited rather than being told to come back. Doc 03
    is explicit that rejecting early beats queueing.
    """
    await signed_in(client)
    await client.put("/api/v1/admin/settings/sched.queue_max", json={"value": 2})

    await get_redis().delete(task_service.QUEUE_KEY)
    for _ in range(3):
        await get_redis().rpush(task_service.QUEUE_KEY, str(uuid.uuid4()))

    refused = await client.post(
        "/api/v1/parse",
        json={"url": "https://www.douyin.com/video/7123456789012345678"},
    )
    assert error_code(refused) == "QUEUE_FULL"
    assert refused.status_code == 503
    body = envelope(refused)["error"]
    assert body["details"]["reject_reason"] == "queue_full"
    assert int(refused.headers.get("retry-after", 0)) > 0

    # An operator-triggered job is exempt: a deep queue is exactly when someone
    # runs the self check, and refusing it withholds the tool at the moment it
    # is wanted.
    allowed = await client.post("/api/v1/admin/diagnose", json={"include_smoke_test": False})
    assert allowed.status_code == 202


async def test_a_ceiling_of_zero_disables_the_check(client: Any) -> None:
    """The escape hatch, so an operator can turn backpressure off deliberately."""
    await signed_in(client)
    await client.put("/api/v1/admin/settings/sched.queue_max", json={"value": 0})
    await get_redis().delete(task_service.QUEUE_KEY)
    for _ in range(5):
        await get_redis().rpush(task_service.QUEUE_KEY, str(uuid.uuid4()))

    response = await client.post(
        "/api/v1/parse",
        json={"url": "https://www.douyin.com/video/7123456789012345678"},
    )
    assert response.status_code == 202, envelope(response)


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


async def test_cancelling_a_queued_task_fails_it(client, db_engine, redis_client):
    """The row stays, with its state, the way every finished task does."""
    import uuid as _uuid

    from dtk.core.db import session_scope
    from dtk.services import tasks as task_service
    from tests.integration.test_api_support import envelope, signed_in

    await signed_in(client)
    task_id = await task_service.submit_now("parse", {"url": "https://www.douyin.com/"})

    response = await client.delete(f"/api/v1/tasks/{task_id}")
    assert envelope(response)["data"]["state"] == "failed"

    async with session_scope() as session:
        view = await task_service.get(session, _uuid.UUID(str(task_id)))
        assert view.state.value == "failed"
        assert view.error is not None


async def test_cancelling_a_running_task_reports_it_as_running(client, db_engine, redis_client):
    """Its request is in flight and the identity's quota is already spent;
    recording a failure the worker never had would make the risk rate lie."""
    from dtk.core.db import session_scope
    from dtk.services import tasks as task_service
    from tests.integration.test_api_support import envelope, signed_in

    await signed_in(client)
    task_id = await task_service.submit_now("parse", {"url": "https://www.douyin.com/"})
    async with session_scope() as session:
        await task_service.mark_running(session, task_id)

    response = await client.delete(f"/api/v1/tasks/{task_id}")
    assert envelope(response)["data"]["state"] == "running"


async def test_cancelling_an_unknown_task_is_a_404(client, db_engine, redis_client):
    import uuid as _uuid

    from tests.integration.test_api_support import error_code, signed_in

    await signed_in(client)
    response = await client.delete(f"/api/v1/tasks/{_uuid.uuid4()}")
    assert error_code(response) == "TASK_NOT_FOUND"
