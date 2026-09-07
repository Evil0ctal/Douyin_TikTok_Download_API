"""Task polling and the server-sent event stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from dtk.core.db import session_scope
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
