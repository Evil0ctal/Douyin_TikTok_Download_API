"""Regression tests for the media transcription worker operation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dtk.transcriber import TranscriberUnavailable
from dtk.worker.ops import media_transcribe


class FakeClient:
    configured = True
    timeout_seconds = 10.0

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.acknowledged: list[str] = []

    async def job(self, _job_id: str) -> dict[str, Any]:
        raise TranscriberUnavailable("no such job")

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payload = payload
        return {
            "state": "done",
            "text": "hello",
            "duration_ms": 1234,
            "normalization_applied": True,
            "normalization_error": None,
        }

    async def cancel(self, job_id: str) -> bool:
        self.acknowledged.append(job_id)
        return True


class FakeSession:
    def __init__(self, download: Any) -> None:
        self.download = download
        self.refreshed = False

    async def get(self, _model: Any, _identifier: Any) -> Any:
        return self.download

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def refresh(self, row: Any) -> None:
        self.refreshed = True
        row.state = "done"


async def test_completed_result_is_refreshed_before_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcription_id = uuid.uuid4()
    download_id = uuid.uuid4()
    row = SimpleNamespace(
        id=transcription_id,
        state="queued",
        download_id=download_id,
        normalization_requested=True,
        normalization_failure_policy="fallback",
        model="paraformer-zh-large-onnx-int8",
    )
    download = SimpleNamespace(files_removed_at=None, directory="douyin/author/post")
    session = FakeSession(download)
    client = FakeClient()

    async def get_transcription(_session: Any, _identifier: Any) -> Any:
        return row

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(media_transcribe.transcriptions, "get", get_transcription)
    monkeypatch.setattr(media_transcribe.transcriptions, "mark_running", no_op)
    monkeypatch.setattr(media_transcribe.transcriptions, "apply_result", no_op)
    monkeypatch.setattr(
        media_transcribe.transcriptions,
        "relative_path",
        lambda _row, _download: "douyin/author/post/video.mp4",
    )
    monkeypatch.setattr(
        media_transcribe.transcriptions,
        "as_dict",
        lambda current, **_kwargs: {"state": current.state},
    )

    result = await media_transcribe.run(
        cast(Any, SimpleNamespace(transcriber=client)),
        cast(Any, session),
        {"transcription_id": str(transcription_id)},
    )

    assert session.refreshed is True
    assert result == {"state": "done"}
    assert client.payload is not None
    assert client.payload["download_id"] == str(download_id)
    assert client.payload["normalize"] is True
    assert client.payload["normalization_failure_policy"] == "fallback"
    assert client.acknowledged == [transcription_id.hex]


async def test_existing_sidecar_result_is_reused_without_resubmission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcription_id = uuid.uuid4()
    download_id = uuid.uuid4()
    row = SimpleNamespace(
        id=transcription_id,
        state="running",
        download_id=download_id,
        normalization_requested=False,
        normalization_failure_policy=None,
        model="paraformer-zh-large-onnx-int8",
    )
    download = SimpleNamespace(files_removed_at=None, directory="douyin/author/post")
    session = FakeSession(download)

    class ExistingClient(FakeClient):
        async def job(self, _job_id: str) -> dict[str, Any]:
            return {
                "state": "done",
                "text": "already computed",
                "duration_ms": 1234,
            }

        async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError(f"existing job was resubmitted: {payload}")

    async def get_transcription(_session: Any, _identifier: Any) -> Any:
        return row

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(media_transcribe.transcriptions, "get", get_transcription)
    monkeypatch.setattr(media_transcribe.transcriptions, "mark_running", no_op)
    monkeypatch.setattr(media_transcribe.transcriptions, "apply_result", no_op)
    monkeypatch.setattr(
        media_transcribe.transcriptions,
        "relative_path",
        lambda _row, _download: "douyin/author/post/video.mp4",
    )
    monkeypatch.setattr(
        media_transcribe.transcriptions,
        "as_dict",
        lambda current, **_kwargs: {"state": current.state},
    )

    result = await media_transcribe.run(
        cast(Any, SimpleNamespace(transcriber=(client := ExistingClient()))),
        cast(Any, session),
        {"transcription_id": str(transcription_id)},
    )

    assert session.refreshed is True
    assert result == {"state": "done"}
    assert client.acknowledged == [transcription_id.hex]


async def test_running_progress_is_published_then_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcription_id = uuid.uuid4()
    download_id = uuid.uuid4()
    row = SimpleNamespace(
        id=transcription_id,
        state="queued",
        download_id=download_id,
        normalization_requested=True,
        normalization_failure_policy="fallback",
        model="paraformer-zh-large-onnx-int8",
    )
    session = FakeSession(SimpleNamespace(files_removed_at=None, directory="post"))

    class RunningClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.submitted = False

        async def job(self, _job_id: str) -> dict[str, Any]:
            if not self.submitted:
                raise TranscriberUnavailable("no such job")
            return {
                "state": "done",
                "text": "Final text.",
                "duration_ms": 1000,
                "normalization_applied": True,
            }

        async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.submitted = True
            return {
                "state": "running",
                "stage": "asr",
                "progress_revision": 2,
                "processed_ms": 500,
                "media_duration_ms": 1000,
                "progress_percent": 50.0,
                "partial_text": "Temporary text",
            }

    published: list[dict[str, Any]] = []
    cleared: list[uuid.UUID] = []

    async def get_transcription(_session: Any, _identifier: Any) -> Any:
        return row

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def publish(_identifier: Any, job: dict[str, Any]) -> None:
        published.append(dict(job))

    async def clear(identifier: uuid.UUID) -> None:
        cleared.append(identifier)

    monkeypatch.setattr(media_transcribe.asyncio, "sleep", no_op)
    monkeypatch.setattr(media_transcribe.transcriptions, "get", get_transcription)
    monkeypatch.setattr(media_transcribe.transcriptions, "mark_running", no_op)
    monkeypatch.setattr(media_transcribe.transcriptions, "apply_result", no_op)
    monkeypatch.setattr(
        media_transcribe.transcriptions, "relative_path", lambda _row, _download: "post/video.mp4"
    )
    monkeypatch.setattr(
        media_transcribe.transcriptions,
        "as_dict",
        lambda current, **_kwargs: {"state": current.state},
    )
    monkeypatch.setattr(media_transcribe.transcription_progress, "publish", publish)
    monkeypatch.setattr(media_transcribe.transcription_progress, "clear", clear)

    result = await media_transcribe.run(
        cast(Any, SimpleNamespace(transcriber=RunningClient())),
        cast(Any, session),
        {"transcription_id": str(transcription_id)},
    )

    assert result == {"state": "done"}
    assert published == [
        {
            "state": "running",
            "stage": "asr",
            "progress_revision": 2,
            "processed_ms": 500,
            "media_duration_ms": 1000,
            "progress_percent": 50.0,
            "partial_text": "Temporary text",
        }
    ]
    assert cleared == [transcription_id]
