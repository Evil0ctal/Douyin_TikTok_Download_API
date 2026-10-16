"""The transcriber sidecar stays offline, bounded, idempotent and path-safe."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from docker.transcriber import app as sidecar
from fastapi.testclient import TestClient


class FakeBackend:
    def __init__(self) -> None:
        self.normalize: bool | None = None
        self.failure_policy: str | None = None

    def transcribe(
        self,
        path: Path,
        normalize: bool,
        failure_policy: str,
        progress=None,
        stage=None,
        segment=None,
    ):
        self.normalize = normalize
        self.failure_policy = failure_policy
        if stage is not None:
            stage("asr")
        if progress is not None:
            progress(0.5, 1.234)
        if segment is not None:
            segment("text from ")
            segment(path.name)
        return {
            "text": f"text from {path.name}",
            "duration_ms": 1234,
            "normalization_requested": normalize,
            "normalization_applied": normalize,
            "normalizer": sidecar.NORMALIZER_NAME if normalize else None,
            "normalization_error": None,
        }


def test_runtime_dependencies_pin_portable_onnx_pipeline():
    requirements = (
        Path(__file__).parents[2] / "docker" / "transcriber" / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert "funasr-onnx==0.4.3" in requirements
    assert "imageio-ffmpeg==0.6.0" in requirements
    assert "onnxruntime==1.23.2" in requirements
    assert "wetext==0.1.8" in requirements


def test_runtime_image_uses_the_bundled_ffmpeg_wheel():
    dockerfile = (Path(__file__).parents[2] / "docker" / "Dockerfile.transcriber").read_text(
        encoding="utf-8"
    )
    assert "--only-binary=imageio-ffmpeg" in dockerfile
    assert "apt-get install --no-install-recommends -y ffmpeg" not in dockerfile


def test_compose_healthcheck_rejects_a_degraded_model_backend():
    compose = (Path(__file__).parents[2] / "docker" / "compose.yml").read_text(encoding="utf-8")
    transcriber = compose.split("\n  transcriber:\n", 1)[1].split("\nnetworks:\n", 1)[0]

    assert 'health.get("status") != "ok"' in transcriber
    assert "raise SystemExit(1)" in transcriber


def test_decode_invokes_the_resolved_ffmpeg_binary(monkeypatch, tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video")
    backend = sidecar.FunASRBackend.__new__(sidecar.FunASRBackend)
    backend._ffmpeg_exe = "/opt/imageio/ffmpeg"
    calls = []
    decoded = [0.25, -0.25]
    fake_numpy = SimpleNamespace(
        float32="float32",
        frombuffer=lambda payload, dtype: SimpleNamespace(copy=lambda: decoded),
    )
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b"pcm",
            stderr=b"",
        )

    monkeypatch.setattr(sidecar.subprocess, "run", run)

    audio = backend._decode(media)

    assert calls[0][0][0] == "/opt/imageio/ffmpeg"
    assert calls[0][0][1:] == [
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(media),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "f32le",
        "-",
    ]
    assert calls[0][1] == {
        "check": False,
        "capture_output": True,
        "timeout": sidecar.DECODE_TIMEOUT_SECONDS,
    }
    assert audio == decoded


def _done(client: TestClient, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["state"] not in ("queued", "running"):
            return body
        time.sleep(0.01)
    raise AssertionError("fake transcription did not settle")


def test_normalization_defaults_to_enabled_with_fallback():
    base = {"id": "a" * 32, "path": "post/video.mp4"}
    request = sidecar.JobRequest(**base)
    assert request.normalize is True
    assert request.normalization_failure_policy == "fallback"


def test_health_reports_degraded_when_the_models_are_unavailable(monkeypatch):
    def missing_models():
        raise RuntimeError("offline asr model is missing model_quant.onnx")

    monkeypatch.setattr(sidecar, "_validate_models", missing_models)
    app = sidecar.create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert "missing model_quant.onnx" in response.json()["detail"]


def test_invalid_normalization_failure_policy_is_rejected():
    with pytest.raises(ValueError):
        sidecar.JobRequest(
            id="a" * 32,
            path="post/video.mp4",
            normalization_failure_policy="ignore",
        )


def test_unsupported_model_is_rejected():
    with pytest.raises(ValueError):
        sidecar.JobRequest(id="a" * 32, path="post/video.mp4", model="unsupported-model")


def test_fun_asr_pipeline_normalizes_after_punctuation():
    backend = sidecar.FunASRBackend.__new__(sidecar.FunASRBackend)
    backend._decode = lambda _path: [0.0] * 16000
    backend._vad = lambda _audio: [[[0, 1000]]]
    backend._asr = lambda _audio: ["\u9a8c\u8bc1\u7801\u4e94\u516d\u4e03\u516b"]
    backend._punc = lambda text: [f"{text}\u3002"]
    backend._normalizer = SimpleNamespace(
        normalize=lambda text: text.replace("\u4e94\u516d\u4e03\u516b", "5678")
    )

    result = backend.transcribe(Path("video.mp4"), True, "fallback")

    assert result["text"] == "\u9a8c\u8bc1\u78015678\u3002"
    assert result["normalization_applied"] is True


@pytest.mark.parametrize(
    ("policy", "state", "text"),
    [
        ("fallback", "done", "\u9a8c\u8bc1\u7801\u4e94\u516d\u4e03\u516b\u3002"),
        ("fail", "failed", None),
    ],
)
def test_normalization_failure_policy_controls_job_result(
    tmp_path, monkeypatch, policy, state, text
):
    class PolicyBackend(FakeBackend):
        def transcribe(
            self,
            path: Path,
            normalize: bool,
            failure_policy: str,
            progress=None,
            stage=None,
            segment=None,
        ):
            if failure_policy == "fail":
                raise sidecar.NormalizationFailed("NORMALIZATION_FAILED")
            return {
                "text": "\u9a8c\u8bc1\u7801\u4e94\u516d\u4e03\u516b\u3002",
                "duration_ms": 1000,
                "normalization_requested": True,
                "normalization_applied": False,
                "normalizer": sidecar.NORMALIZER_NAME,
                "normalization_error": "NORMALIZATION_FAILED",
            }

    media = tmp_path / "post"
    media.mkdir()
    (media / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    app = sidecar.create_app(PolicyBackend())
    with TestClient(app) as client:
        response = client.post(
            "/jobs",
            json={
                "id": ("c" if policy == "fallback" else "d") * 32,
                "path": "post/video.mp4",
                "normalization_failure_policy": policy,
            },
        )
        result = _done(client, response.json()["id"])
    assert result["state"] == state
    assert result["text"] == text
    assert result["normalization_error"] == "NORMALIZATION_FAILED"


def test_submit_is_idempotent_and_returns_whole_text(tmp_path, monkeypatch, caplog):
    media = tmp_path / "douyin" / "author" / "post"
    media.mkdir(parents=True)
    (media / "video-transcription.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    backend = FakeBackend()
    app = sidecar.create_app(backend)
    job_id = "a" * 32
    payload = {
        "id": job_id,
        "download_id": "8afe9635-9deb-4590-b76a-46a18c38bf7b",
        "path": "douyin/author/post/video-transcription.mp4",
        "normalize": True,
        "model": sidecar.MODEL_NAME,
    }
    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error.transcriber"),
        TestClient(app) as client,
    ):
        assert client.post("/jobs", json=payload).status_code == 202
        assert client.post("/jobs", json=payload).status_code == 202
        result = _done(client, job_id)
    assert result["text"] == "text from video-transcription.mp4"
    assert result["duration_ms"] == 1234
    assert result["normalization_applied"] is True
    assert result["partial_text"] == ""
    assert backend.normalize is True
    assert backend.failure_policy == "fallback"
    assert "segments" not in result
    assert "transcriber.job.queued" in caplog.text
    assert "transcriber.job.done" in caplog.text
    assert job_id in caplog.text
    assert "download_id=8afe9635-9deb-4590-b76a-46a18c38bf7b" in caplog.text
    assert "video-transcription.mp4" not in caplog.text
    assert "text from" not in caplog.text


def test_running_job_logs_elapsed_and_media_progress(tmp_path, monkeypatch, caplog):
    class SlowBackend(FakeBackend):
        def transcribe(
            self,
            path: Path,
            normalize: bool,
            failure_policy: str,
            progress=None,
            stage=None,
            segment=None,
        ):
            if progress is not None:
                progress(0.5, 1.0)
            time.sleep(0.08)
            return super().transcribe(path, normalize, failure_policy, progress, stage, segment)

    media = tmp_path / "post"
    media.mkdir()
    (media / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(sidecar, "PROGRESS_LOG_SECONDS", 0.02)
    app = sidecar.create_app(SlowBackend())
    job_id = "b" * 32
    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error.transcriber"),
        TestClient(app) as client,
    ):
        assert (
            client.post(
                "/jobs",
                json={
                    "id": job_id,
                    "download_id": "8afe9635-9deb-4590-b76a-46a18c38bf7b",
                    "path": "post/video.mp4",
                    "model": sidecar.MODEL_NAME,
                },
            ).status_code
            == 202
        )
        result = _done(client, job_id)
    assert result["state"] == "done"
    started_at = datetime.fromisoformat(result["started_at"])
    finished_at = datetime.fromisoformat(result["finished_at"])
    assert started_at.tzinfo == UTC
    assert finished_at.tzinfo == UTC
    assert (finished_at - started_at).total_seconds() >= 0.07
    assert "transcriber.job.progress" in caplog.text
    assert "processed_ms=500" in caplog.text
    assert "progress_percent=50.0" in caplog.text
    assert "download_id=8afe9635-9deb-4590-b76a-46a18c38bf7b" in caplog.text
    assert "video.mp4" not in caplog.text


def test_running_job_exposes_provisional_text_without_logging_it(tmp_path, monkeypatch, caplog):
    class PartialBackend(FakeBackend):
        def transcribe(
            self,
            path: Path,
            normalize: bool,
            failure_policy: str,
            progress=None,
            stage=None,
            segment=None,
        ):
            if stage is not None:
                stage("asr")
            if segment is not None:
                segment("temporary words")
            if progress is not None:
                progress(0.5, 1.0)
            time.sleep(0.08)
            return {
                "text": "Final words.",
                "duration_ms": 1000,
                "normalization_requested": normalize,
                "normalization_applied": normalize,
                "normalizer": sidecar.NORMALIZER_NAME if normalize else None,
                "normalization_error": None,
            }

    media = tmp_path / "post"
    media.mkdir()
    (media / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    app = sidecar.create_app(PartialBackend())
    job_id = "e" * 32
    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error.transcriber"),
        TestClient(app) as client,
    ):
        response = client.post(
            "/jobs",
            json={"id": job_id, "path": "post/video.mp4", "model": sidecar.MODEL_NAME},
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 1
        running = None
        while time.monotonic() < deadline:
            candidate = client.get(f"/jobs/{job_id}").json()
            if candidate["state"] == "running" and candidate["partial_text"]:
                running = candidate
                break
            time.sleep(0.005)
        assert running is not None
        assert running["partial_text"] == "temporary words"
        assert running["progress_revision"] > 0
        result = _done(client, job_id)

    assert result["text"] == "Final words."
    assert result["partial_text"] == ""
    assert "temporary words" not in caplog.text


def test_terminal_job_history_is_bounded(tmp_path, monkeypatch):
    class FirstFailsBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def transcribe(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("expected failure")
            return super().transcribe(*args, **kwargs)

    media = tmp_path / "post"
    media.mkdir()
    (media / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(sidecar, "JOB_HISTORY_SIZE", 2)
    app = sidecar.create_app(FirstFailsBackend())

    job_ids = [f"{index:032x}" for index in range(1, 4)]
    with TestClient(app) as client:
        states = []
        for job_id in job_ids:
            response = client.post(
                "/jobs",
                json={"id": job_id, "path": "post/video.mp4", "model": sidecar.MODEL_NAME},
            )
            assert response.status_code == 202
            states.append(_done(client, job_id)["state"])

        assert states == ["failed", "done", "done"]
        assert client.get(f"/jobs/{job_ids[0]}").status_code == 404
        assert client.get(f"/jobs/{job_ids[1]}").status_code == 200
        assert client.get(f"/jobs/{job_ids[2]}").status_code == 200
        health = client.get("/health").json()
        assert health["retained_terminal_jobs"] == 2
        assert health["terminal_job_limit"] == 2


def test_terminal_delete_acknowledges_and_discards_result(tmp_path, monkeypatch):
    media = tmp_path / "post"
    media.mkdir()
    (media / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    app = sidecar.create_app(FakeBackend())
    job_id = "f" * 32

    with TestClient(app) as client:
        assert (
            client.post(
                "/jobs",
                json={"id": job_id, "path": "post/video.mp4", "model": sidecar.MODEL_NAME},
            ).status_code
            == 202
        )
        assert _done(client, job_id)["state"] == "done"
        assert client.delete(f"/jobs/{job_id}").status_code == 200
        assert client.get(f"/jobs/{job_id}").status_code == 404


def test_rejects_absolute_parent_and_symlink_escape(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside-video.mp4"
    outside.write_bytes(b"video")
    (tmp_path / "escape.mp4").symlink_to(outside)
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)
    app = sidecar.create_app(FakeBackend())
    with TestClient(app) as client:
        for index, path in enumerate((str(outside), "../outside-video.mp4", "escape.mp4")):
            response = client.post(
                "/jobs",
                json={"id": f"{index + 1:032x}", "path": path, "model": sidecar.MODEL_NAME},
            )
            assert response.status_code == 400


def _media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "post"
    media.mkdir()
    (media / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(sidecar, "MEDIA_ROOT", tmp_path)


def test_cancelling_a_running_job_stops_the_backend_at_its_next_segment(tmp_path, monkeypatch):
    """A cancelled job gives its worker back instead of finishing the audio first.

    `asyncio.to_thread` cannot interrupt the backend, so the progress callbacks
    are the only place a cancellation can land. With one worker - the default -
    a cancel that only flipped a flag left every queued job waiting behind an
    hour of audio nobody wanted any more.
    """
    started = threading.Event()
    release = threading.Event()
    processed: list[int] = []

    class LongBackend(FakeBackend):
        def transcribe(
            self, path, normalize, failure_policy, progress=None, stage=None, segment=None
        ):
            started.set()
            release.wait(2)
            for index in range(100):
                progress(index / 100, 1.0)
                processed.append(index)
            return super().transcribe(path, normalize, failure_policy)

    _media(tmp_path, monkeypatch)
    job_id = "c" * 32
    with TestClient(sidecar.create_app(LongBackend())) as client:
        body = {"id": job_id, "path": "post/video.mp4", "model": sidecar.MODEL_NAME}
        assert client.post("/jobs", json=body).status_code == 202
        assert started.wait(2)
        assert client.delete(f"/jobs/{job_id}").json()["state"] == "cancelled"
        release.set()
        deadline = time.monotonic() + 2
        while client.get(f"/jobs/{job_id}").status_code != 404:
            assert time.monotonic() < deadline, "the cancelled job was never released"
            time.sleep(0.01)

    assert processed == []


def test_a_failed_job_does_not_report_the_container_media_path(tmp_path, monkeypatch):
    """ffmpeg names the file it could not read, and that text reaches API callers."""

    class CorruptBackend(FakeBackend):
        def transcribe(self, path, *args, **kwargs):
            raise RuntimeError(f"{path}: Invalid data found when processing input")

    _media(tmp_path, monkeypatch)
    job_id = "d" * 32
    with TestClient(sidecar.create_app(CorruptBackend())) as client:
        body = {"id": job_id, "path": "post/video.mp4", "model": sidecar.MODEL_NAME}
        assert client.post("/jobs", json=body).status_code == 202
        result = _done(client, job_id)

    assert result["state"] == "failed"
    assert str(tmp_path) not in result["error"]
    assert result["error"].startswith("<media-root>/post/video.mp4")


def test_a_decode_that_hangs_is_bounded(monkeypatch, tmp_path):
    """One wedged ffmpeg would otherwise hold the only worker until a restart."""
    backend = sidecar.FunASRBackend.__new__(sidecar.FunASRBackend)
    backend._ffmpeg_exe = "/opt/imageio/ffmpeg"
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(sidecar.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="timed out"):
        backend._decode(tmp_path / "video.mp4")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"X-Transcriber-Token": "wrong"}, 401),
        # Not ASCII: hmac.compare_digest raises TypeError on such a str, which
        # would be a 500 rather than the refusal it is. Bytes, because the test
        # client will not encode a non-ASCII str header itself.
        ({"X-Transcriber-Token": b"s\xe9cret"}, 401),
        ({"X-Transcriber-Token": "secret"}, 200),
    ],
)
def test_a_configured_token_is_required(monkeypatch, headers, status):
    monkeypatch.setattr(sidecar, "TOKEN", "secret")
    with TestClient(sidecar.create_app(FakeBackend())) as client:
        assert client.get("/health", headers=headers).status_code == status
