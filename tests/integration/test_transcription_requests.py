"""The one start path REST and MCP share, against a real database and queue."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.errors import QueueFull
from dtk.core.redis import get_redis
from dtk.db.models import MediaDownload, Task, Transcription
from dtk.services import tasks, transcription_requests, transcriptions
from tests.integration import test_api_support as support

#: For its table clearing and its own Redis database, not for HTTP.
api_app = support.api_app

pytestmark = pytest.mark.integration

CONTENT_ID = "7408915107113127221"


class Settings:
    def __init__(self, **values: Any) -> None:
        self._values = {"sched.queue_max": 0, "sched.max_wait_seconds": 5, **values}

    def get(self, key: str) -> Any:
        return self._values[key]


async def _download(media_root: Path) -> uuid.UUID:
    directory = f"douyin/author/{CONTENT_ID}"
    (media_root / directory).mkdir(parents=True)
    (media_root / directory / "video.mp4").write_bytes(b"video")
    identifier = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=identifier,
                platform="douyin",
                content_id=CONTENT_ID,
                author_uid="author",
                state="done",
                directory=directory,
                bytes_total=5,
                file_count=1,
                files=[
                    {
                        "name": "video.mp4",
                        "kind": "video",
                        "state": "done",
                        "bytes": 5,
                        "sha256": "b" * 64,
                    }
                ],
            )
        )
    return identifier


async def _start(
    download_id: uuid.UUID, config: Settings
) -> transcription_requests.TranscriptionStart:
    async with session_scope() as session:
        return await transcription_requests.start(
            session,
            config=config,
            normalization_failure_policy="fallback",
            download_id=download_id,
            normalize=True,
            refresh=False,
        )


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(transcriptions, "MEDIA_PATH", str(tmp_path))
    return tmp_path


async def test_a_new_transcription_is_committed_with_its_task(api_app, media_root):
    """The row must never be readable without the task that will settle it."""
    download_id = await _download(media_root)

    started = await _start(download_id, Settings())

    assert started.reused is None
    async with session_scope() as session:
        row = await session.get(Transcription, started.row.id)
        assert row is not None and row.task_id is not None
        task = await session.get(Task, row.task_id)
        assert task is not None and task.endpoint == "media.transcribe"
    assert str(row.task_id) in await get_redis().lrange(tasks.QUEUE_KEY, 0, -1)


async def test_a_second_request_joins_the_live_one(api_app, media_root):
    download_id = await _download(media_root)

    first = await _start(download_id, Settings())
    second = await _start(download_id, Settings())

    assert second.reused == "in_flight"
    assert second.row.id == first.row.id


async def test_the_queue_ceiling_applies_to_transcriptions(api_app, media_root):
    """MCP used to create the task itself and skip the ceiling REST applied."""
    download_id = await _download(media_root)
    await get_redis().rpush(tasks.QUEUE_KEY, str(uuid.uuid4()))

    with pytest.raises(QueueFull):
        await _start(download_id, Settings(**{"sched.queue_max": 1}))

    async with session_scope() as session:
        assert await transcriptions.search(session, transcriptions.TranscriptionFilter()) == ([], 0)


async def test_a_failed_publish_settles_both_rows(
    api_app, media_root, monkeypatch: pytest.MonkeyPatch
):
    """A live row with no queued task would hold the in-flight index for good.

    Redis is down for the task's finish as well, since that is what failing the
    publish means: finishing a task ends by waking its waiters through Redis,
    and when that ran first it raised and took the row's write down with it.
    """
    download_id = await _download(media_root)

    async def unreachable(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("redis went away")

    monkeypatch.setattr(tasks, "enqueue", unreachable)
    monkeypatch.setattr(tasks, "_signal", unreachable)
    with pytest.raises(ConnectionError):
        await _start(download_id, Settings())

    async with session_scope() as session:
        rows, total = await transcriptions.search(session, transcriptions.TranscriptionFilter())
        assert total == 1
        assert rows[0].state == "failed"
        task = await session.get(Task, rows[0].task_id)
        assert task is not None and task.state == "failed"
