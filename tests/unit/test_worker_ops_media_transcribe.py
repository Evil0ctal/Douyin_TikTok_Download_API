"""Regression tests for the media transcription worker operation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dtk.core.errors import TranscriberDown
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


class PollingClient(FakeClient):
    """A sidecar whose job stays running until told otherwise."""

    def __init__(self, *, missing_after: int | None = None) -> None:
        super().__init__()
        self.reads = 0
        self.missing_after = missing_after
        self.submitted: list[dict[str, Any]] = []

    async def job(self, _job_id: str) -> dict[str, Any]:
        self.reads += 1
        if self.missing_after is not None and self.reads > self.missing_after:
            raise TranscriberUnavailable("transcriber /jobs/x refused the request: no such job")
        return {"state": "running", "stage": "asr"}

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted.append(payload)
        return {"state": "running", "stage": "asr"}


class SettlingSession(FakeSession):
    """A session whose refresh reports what another worker committed."""

    def __init__(self, download: Any, *, settle_to: str | None) -> None:
        super().__init__(download)
        self.settle_to = settle_to

    async def refresh(self, row: Any) -> None:
        self.refreshed = True
        if self.settle_to is not None:
            row.state = self.settle_to


def _row(state: str = "running") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        state=state,
        download_id=uuid.uuid4(),
        normalization_requested=True,
        normalization_failure_policy="fallback",
        model="paraformer-zh-large-onnx-int8",
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch, row: Any, *, database: dict[str, str] | None = None
) -> dict[str, list[Any]]:
    """Fake the service; ``database`` is the committed state, when it differs from ``row``."""
    calls: dict[str, list[Any]] = {"fail": [], "apply_result": []}

    async def get_transcription(_session: Any, _identifier: Any) -> Any:
        return row

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fail(_session: Any, _identifier: Any, reason: str, **_kwargs: Any) -> bool:
        calls["fail"].append(reason)
        # The guarded update matches nothing once another worker has settled it.
        committed = database["state"] if database is not None else row.state
        return committed in ("queued", "running")

    async def apply_result(_session: Any, _identifier: Any, job: Any) -> bool:
        calls["apply_result"].append(job)
        return True

    monkeypatch.setattr(media_transcribe.asyncio, "sleep", no_op)
    monkeypatch.setattr(media_transcribe.transcriptions, "get", get_transcription)
    monkeypatch.setattr(media_transcribe.transcriptions, "mark_running", no_op)
    monkeypatch.setattr(media_transcribe.transcriptions, "fail", fail)
    monkeypatch.setattr(media_transcribe.transcriptions, "apply_result", apply_result)
    monkeypatch.setattr(
        media_transcribe.transcriptions, "relative_path", lambda _row, _download: "post/video.mp4"
    )
    monkeypatch.setattr(
        media_transcribe.transcriptions,
        "as_dict",
        lambda current, **_kwargs: {"state": current.state},
    )
    monkeypatch.setattr(media_transcribe.transcription_progress, "publish", no_op)
    monkeypatch.setattr(media_transcribe.transcription_progress, "clear", no_op)
    return calls


async def test_a_row_another_worker_settled_ends_the_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stale sweep can hand a running transcription to a second worker.

    Whichever finishes first settles the row and acknowledges the sidecar job;
    the other must stop there, not keep polling a job that is about to vanish.
    """
    row = _row()
    calls = _patch(monkeypatch, row)
    session = SettlingSession(
        SimpleNamespace(files_removed_at=None, directory="post"), settle_to="done"
    )
    client = PollingClient()

    result = await media_transcribe.run(
        cast(Any, SimpleNamespace(transcriber=client)),
        cast(Any, session),
        {"transcription_id": str(row.id)},
    )

    assert result == {"state": "done"}
    assert calls["fail"] == []
    assert calls["apply_result"] == []
    # Acknowledged anyway: the orphan sweep settles rows without touching the
    # sidecar, and a second DELETE of a forgotten job is a harmless 404.
    assert client.acknowledged == [row.id.hex]


async def test_a_job_acknowledged_by_another_worker_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 404 after the other worker's DELETE used to overwrite `done` with `failed`.

    The other worker commits before it acknowledges, so by the time this one
    sees the job missing the database already says done - only this worker's
    copy of the row is stale.
    """
    row = _row()
    database = {"state": "running"}
    calls = _patch(monkeypatch, row, database=database)

    class AcknowledgedClient(PollingClient):
        async def job(self, job_id: str) -> dict[str, Any]:
            if self.reads >= 1:
                database["state"] = "done"
            return await super().job(job_id)

    class StaleSession(SettlingSession):
        async def refresh(self, current: Any) -> None:
            # Read just before the other worker's commit landed.
            self.refreshed = True
            if self.refreshes:
                current.state = database["state"]
            self.refreshes += 1

        refreshes = 0

    session = StaleSession(SimpleNamespace(files_removed_at=None, directory="post"), settle_to=None)
    client = AcknowledgedClient(missing_after=1)

    result = await media_transcribe.run(
        cast(Any, SimpleNamespace(transcriber=client)),
        cast(Any, session),
        {"transcription_id": str(row.id)},
    )

    assert result == {"state": "done"}
    assert len(calls["fail"]) == 1  # attempted, and refused by the state guard
    assert database["state"] == "done"


async def test_a_timeout_cancels_the_sidecar_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Giving up must free the sidecar too, or it transcribes for nobody."""
    row = _row()
    calls = _patch(monkeypatch, row)
    session = SettlingSession(
        SimpleNamespace(files_removed_at=None, directory="post"), settle_to=None
    )
    client = PollingClient()
    client.timeout_seconds = 0.0

    with pytest.raises(TranscriberDown, match="timed out"):
        await media_transcribe.run(
            cast(Any, SimpleNamespace(transcriber=client)),
            cast(Any, session),
            {"transcription_id": str(row.id)},
        )

    assert client.acknowledged == [row.id.hex]
    assert calls["fail"] == ["transcription timed out"]


@pytest.mark.parametrize("state", ["failed", "cancelled", "done"])
async def test_a_settled_row_is_never_sent_to_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """A re-queued task for a row that already ended has nothing left to do."""
    row = _row(state)
    calls = _patch(monkeypatch, row)
    client = PollingClient()

    result = await media_transcribe.run(
        cast(Any, SimpleNamespace(transcriber=client)),
        cast(Any, FakeSession(SimpleNamespace(files_removed_at=None, directory="post"))),
        {"transcription_id": str(row.id)},
    )

    assert result == {"state": state}
    assert client.reads == 0 and client.submitted == []
    assert calls["fail"] == []
