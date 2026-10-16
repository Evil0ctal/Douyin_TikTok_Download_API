"""Small internal API around an offline FunASR ONNX transcription pipeline."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import subprocess
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

MEDIA_ROOT = Path(os.getenv("DTK_TRANSCRIBER_MEDIA_ROOT", "/var/lib/dtk/media"))
MODEL_PATH = Path(os.getenv("DTK_TRANSCRIBER_MODEL_DIR", "/models/funasr"))
TOKEN = os.getenv("DTK_TRANSCRIBER_TOKEN", "")
WORKERS = max(1, int(os.getenv("DTK_TRANSCRIBER_WORKERS", "1")))
QUEUE_SIZE = max(1, int(os.getenv("DTK_TRANSCRIBER_QUEUE", "8")))
JOB_HISTORY_SIZE = max(
    WORKERS + QUEUE_SIZE,
    1,
    int(os.getenv("DTK_TRANSCRIBER_JOB_HISTORY", "64")),
)
THREADS = max(1, int(os.getenv("DTK_TRANSCRIBER_THREADS", "2")))
PROGRESS_LOG_SECONDS = max(5.0, float(os.getenv("DTK_TRANSCRIBER_PROGRESS_LOG_SECONDS", "30")))
#: The worker gives up on a job after this long, so a decode still running past
#: it has no caller left. It is also what stops one file ffmpeg cannot finish
#: from holding a worker until the container restarts.
DECODE_TIMEOUT_SECONDS = max(60, int(os.getenv("DTK_TRANSCRIBER_TIMEOUT_SECONDS", "3600")))
MODEL_NAME = "paraformer-zh-large-onnx-int8"
NORMALIZER_NAME = "wetext-zh-itn-0.1.8"
VALID_FAILURE_POLICIES = frozenset({"fallback", "fail"})
_policy_value = os.getenv("DTK_TRANSCRIBER_NORMALIZATION_FAILURE_POLICY", "fallback").lower()
POLICY_ERROR = (
    "DTK_TRANSCRIBER_NORMALIZATION_FAILURE_POLICY must be fallback or fail"
    if _policy_value not in VALID_FAILURE_POLICIES
    else ""
)
DEFAULT_FAILURE_POLICY: Literal["fallback", "fail"] = (
    "fail" if _policy_value == "fail" else "fallback"
)
log = logging.getLogger("uvicorn.error.transcriber")
log.setLevel(logging.INFO)


def _safe_error(exc: BaseException) -> str:
    message = str(exc).replace(str(MEDIA_ROOT), "<media-root>")
    return message.replace(str(MODEL_PATH), "<model-root>")[:300]


ProgressCallback = Callable[[float, float], None]
StageCallback = Callable[[str], None]
SegmentCallback = Callable[[str], None]


class NormalizationFailed(RuntimeError):
    pass


class JobCancelled(Exception):
    """Raised from a progress callback to stop a backend whose job was cancelled."""


class Backend(Protocol):
    def transcribe(
        self,
        path: Path,
        normalize: bool,
        normalization_failure_policy: str,
        progress: ProgressCallback | None = None,
        stage: StageCallback | None = None,
        segment: SegmentCallback | None = None,
    ) -> dict[str, Any]: ...


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "preds", "value"):
            if key in value:
                return _first_text(value[key])
    if isinstance(value, list | tuple) and value:
        return _first_text(value[0])
    return ""


def _segments(value: Any) -> list[tuple[int, int]]:
    if isinstance(value, dict):
        for key in ("segments", "value", "timestamps"):
            if key in value:
                return _segments(value[key])
        return []
    if not isinstance(value, list | tuple) or not value:
        return []
    if len(value) == 1 and isinstance(value[0], list | tuple | dict):
        nested = _segments(value[0])
        if nested:
            return nested
    found: list[tuple[int, int]] = []
    for item in value:
        if isinstance(item, list | tuple) and len(item) >= 2:
            try:
                start, end = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if 0 <= start < end:
                found.append((start, end))
    return found


class FunASRBackend:
    def __init__(self) -> None:
        from funasr_onnx import CT_Transformer, Fsmn_vad, Paraformer
        from imageio_ffmpeg import get_ffmpeg_exe
        from wetext import Normalizer

        # Resolve and execute `-version` during backend startup. A wheel missing
        # its platform binary therefore degrades health immediately instead of
        # failing only after a user submits a transcription.
        self._ffmpeg_exe = get_ffmpeg_exe()
        common = {"device_id": -1, "quantize": True, "intra_op_num_threads": THREADS}
        self._asr = Paraformer(str(MODEL_PATH / "asr"), batch_size=1, **common)
        self._vad = Fsmn_vad(str(MODEL_PATH / "vad"), batch_size=1, **common)
        self._punc = CT_Transformer(str(MODEL_PATH / "punc"), **common)
        self._normalizer = Normalizer(
            lang="zh",
            operator="itn",
            enable_0_to_9=False,
            traditional_to_simple=True,
            full_to_half=False,
        )

    def _decode(self, path: Path) -> Any:
        import numpy as np

        try:
            completed = subprocess.run(
                [
                    self._ffmpeg_exe,
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "f32le",
                    "-",
                ],
                check=False,
                capture_output=True,
                timeout=DECODE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffmpeg timed out after {DECODE_TIMEOUT_SECONDS}s decoding the audio track"
            ) from None
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                detail or "ffmpeg could not decode the first audio track"
            )
        return np.frombuffer(completed.stdout, dtype=np.float32).copy()

    def transcribe(
        self,
        path: Path,
        normalize: bool,
        normalization_failure_policy: str,
        progress: ProgressCallback | None = None,
        stage: StageCallback | None = None,
        segment: SegmentCallback | None = None,
    ) -> dict[str, Any]:
        if stage:
            stage("decode")
        audio = self._decode(path)
        duration = len(audio) / 16000.0
        if progress:
            progress(0.0, duration)
        if not len(audio):
            return self._result("", duration, normalize, False, None)

        if stage:
            stage("vad")
        intervals = _segments(self._vad(audio))
        if not intervals:
            return self._result("", duration, normalize, False, None)

        if stage:
            stage("asr")
        parts: list[str] = []
        for start_ms, end_ms in intervals:
            start = max(0, round(start_ms * 16))
            end = min(len(audio), round(end_ms * 16))
            if end <= start:
                continue
            text = _first_text(self._asr(audio[start:end])).strip()
            if text:
                parts.append(text)
                if segment:
                    # This is deliberately the raw, append-only ASR fragment.
                    # Punctuation and ITN run over the whole transcript later,
                    # so callers must treat it as provisional until the job is
                    # done.
                    segment(text)
            if progress:
                progress(min(duration, end_ms / 1000.0), duration)

        raw_text = "".join(parts).strip()
        if raw_text:
            if stage:
                stage("punctuation")
            punctuated = _first_text(self._punc(raw_text)).strip() or raw_text
        else:
            punctuated = ""

        if not normalize or not punctuated:
            return self._result(punctuated, duration, normalize, False, None)
        if stage:
            stage("normalization")
        try:
            normalized = str(self._normalizer.normalize(punctuated)).strip()
        except Exception as exc:
            if normalization_failure_policy == "fail":
                raise NormalizationFailed("NORMALIZATION_FAILED") from exc
            return self._result(
                punctuated, duration, True, False, "NORMALIZATION_FAILED"
            )
        return self._result(normalized, duration, True, True, None)

    @staticmethod
    def _result(
        text: str,
        duration: float,
        requested: bool,
        applied: bool,
        error: str | None,
    ) -> dict[str, Any]:
        return {
            "text": text,
            "duration_ms": round(duration * 1000),
            "normalization_requested": requested,
            "normalization_applied": applied,
            "normalizer": NORMALIZER_NAME if requested else None,
            "normalization_error": error,
        }


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    download_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    path: str = Field(min_length=1, max_length=1024)
    normalize: bool = True
    normalization_failure_policy: Literal["fallback", "fail"] = DEFAULT_FAILURE_POLICY
    model: Literal["paraformer-zh-large-onnx-int8"] = "paraformer-zh-large-onnx-int8"


def _validate_models() -> None:
    for component in ("asr", "vad", "punc"):
        directory = MODEL_PATH / component
        for name in ("model_quant.onnx", "config.yaml"):
            if not (directory / name).is_file():
                raise RuntimeError(f"offline {component} model is missing {name}")


def _media_file(relative: str) -> Path:
    """Resolve a caller's path inside the media root, or refuse it.

    Normalize, then check the prefix. The first version resolved the path and
    called relative_to, which is just as safe, and CodeQL reported it as path
    injection on the PR that added this file: it recognizes this shape as a
    sanitizer and not that one. Symlinks are resolved before the check, so a
    link inside the root that points out of it is still refused.
    """
    if os.path.isabs(relative) or ".." in Path(relative).parts:
        raise HTTPException(
            status_code=400, detail="path must be relative to the media root"
        )
    root = os.path.realpath(MEDIA_ROOT)
    candidate = os.path.realpath(os.path.join(root, relative))
    if not candidate.startswith(root + os.sep):
        raise HTTPException(
            status_code=400, detail="media path is unavailable or escapes root"
        )
    if not os.path.isfile(candidate):
        raise HTTPException(status_code=400, detail="media path is not a file")
    return Path(candidate)


def _loggable(value: object) -> str:
    """One log line per event, whatever a caller put in a field.

    The ids logged here are pattern-checked by the request model, so this
    changes nothing for a valid request; it exists so that no field taken from
    a request can start a forged line, which is what CodeQL asks of log calls.
    """
    return str(value).replace("\r", " ").replace("\n", " ")


def create_app(backend: Backend | None = None) -> FastAPI:
    jobs: dict[str, dict[str, Any]] = {}
    terminal_jobs: OrderedDict[str, None] = OrderedDict()
    queue: asyncio.Queue[tuple[JobRequest, Path, dict[str, Any]]] = asyncio.Queue(
        maxsize=QUEUE_SIZE
    )
    state: dict[str, Any] = {"backend": backend, "detail": POLICY_ERROR}

    def forget_job(job_id: str, expected: dict[str, Any] | None = None) -> None:
        if expected is not None and jobs.get(job_id) is not expected:
            return
        jobs.pop(job_id, None)
        terminal_jobs.pop(job_id, None)

    def remember_terminal(job_id: str, job: dict[str, Any]) -> None:
        # A final result already contains the whole transcript. Keeping its
        # provisional copy doubles the largest field in every retained job.
        job["partial_text"] = ""
        terminal_jobs[job_id] = None
        terminal_jobs.move_to_end(job_id)
        while len(terminal_jobs) > JOB_HISTORY_SIZE:
            expired_id, _ = terminal_jobs.popitem(last=False)
            jobs.pop(expired_id, None)

    async def run_worker() -> None:
        while True:
            request, path, job = await queue.get()
            started = time.monotonic()
            heartbeat: asyncio.Task[None] | None = None

            # The backend runs in a thread that asyncio cannot interrupt, so a
            # cancelled job used to keep its worker until the last segment was
            # recognised - and with one worker, every queued job waited for
            # audio nobody wanted any more. Each callback is a point where the
            # backend can be stopped instead.
            def stop_if_cancelled(target: dict[str, Any]) -> None:
                if target["state"] == "cancelled":
                    raise JobCancelled

            def record_progress(
                processed_seconds: float,
                duration_seconds: float,
                target: dict[str, Any] = job,
            ) -> None:
                stop_if_cancelled(target)
                processed_ms = max(0, round(processed_seconds * 1000))
                duration_ms = max(0, round(duration_seconds * 1000))
                target["processed_ms"] = (
                    min(processed_ms, duration_ms) if duration_ms else processed_ms
                )
                target["media_duration_ms"] = duration_ms or None
                target["progress_percent"] = (
                    round(min(99.9, processed_ms * 100 / duration_ms), 1)
                    if duration_ms
                    else None
                )
                target["progress_revision"] += 1

            def record_stage(
                name: str,
                target: dict[str, Any] = job,
                job_id: str = request.id,
                download_id: str | None = request.download_id,
            ) -> None:
                stop_if_cancelled(target)
                if target.get("stage") == name:
                    return
                target["stage"] = name
                target["progress_revision"] += 1
                log.info(
                    "transcriber.job.stage job_id=%s download_id=%s stage=%s",
                    job_id,
                    download_id or "unknown",
                    name,
                )

            def record_segment(text: str, target: dict[str, Any] = job) -> None:
                stop_if_cancelled(target)
                if not text:
                    return
                target["partial_text"] = f"{target.get('partial_text') or ''}{text}"
                target["progress_revision"] += 1

            async def report_progress(
                target: dict[str, Any] = job,
                job_id: str = request.id,
                download_id: str | None = request.download_id,
                began: float = started,
            ) -> None:
                while True:
                    await asyncio.sleep(PROGRESS_LOG_SECONDS)
                    if target["state"] != "running":
                        return
                    log.info(
                        "transcriber.job.progress job_id=%s download_id=%s stage=%s "
                        "elapsed_ms=%d processed_ms=%s duration_ms=%s progress_percent=%s",
                        job_id,
                        download_id or "unknown",
                        target.get("stage") or "starting",
                        round((time.monotonic() - began) * 1000),
                        target.get("processed_ms") or 0,
                        target.get("media_duration_ms") or "unknown",
                        (
                            target.get("progress_percent")
                            if target.get("progress_percent") is not None
                            else "unknown"
                        ),
                    )

            try:
                if job["state"] == "cancelled":
                    continue
                job["state"] = "running"
                job["started_at"] = datetime.now(UTC).isoformat()
                log.info(
                    "transcriber.job.started job_id=%s download_id=%s normalize=%s "
                    "normalization_failure_policy=%s",
                    request.id,
                    request.download_id or "unknown",
                    request.normalize,
                    request.normalization_failure_policy,
                )
                heartbeat = asyncio.create_task(report_progress())
                result = await asyncio.to_thread(
                    state["backend"].transcribe,
                    path,
                    request.normalize,
                    request.normalization_failure_policy,
                    record_progress,
                    record_stage,
                    record_segment,
                )
                if job["state"] != "cancelled":
                    job.update(result)
                    job["finished_at"] = datetime.now(UTC).isoformat()
                    job["processed_ms"] = result.get("duration_ms")
                    job["media_duration_ms"] = result.get("duration_ms")
                    job["progress_percent"] = 100.0
                    job["state"] = "done"
                    job["progress_revision"] += 1
                    remember_terminal(request.id, job)
                    if result.get("normalization_error"):
                        log.warning(
                            "transcriber.job.normalization_fallback job_id=%s download_id=%s",
                            request.id,
                            request.download_id or "unknown",
                        )
                    log.info(
                        "transcriber.job.done job_id=%s download_id=%s elapsed_ms=%d "
                        "duration_ms=%s normalization_applied=%s",
                        request.id,
                        request.download_id or "unknown",
                        round((time.monotonic() - started) * 1000),
                        result.get("duration_ms"),
                        result.get("normalization_applied"),
                    )
            except Exception as exc:
                if job["state"] != "cancelled":
                    job["state"] = "failed"
                    job["finished_at"] = datetime.now(UTC).isoformat()
                    # Returned to API callers, so it gets the same scrubbing as the
                    # log line: ffmpeg names the file it could not read.
                    job["error"] = _safe_error(exc)
                    job["progress_revision"] += 1
                    if isinstance(exc, NormalizationFailed):
                        job["normalization_error"] = "NORMALIZATION_FAILED"
                    remember_terminal(request.id, job)
                    log.error(
                        "transcriber.job.failed job_id=%s download_id=%s stage=%s elapsed_ms=%d "
                        "error_type=%s detail=%s",
                        request.id,
                        request.download_id or "unknown",
                        job.get("stage") or "starting",
                        round((time.monotonic() - started) * 1000),
                        type(exc).__name__,
                        _safe_error(exc.__cause__ or exc),
                    )
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                if job["state"] == "cancelled":
                    forget_job(request.id, job)
                queue.task_done()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if state["backend"] is None and not state["detail"]:
            try:
                _validate_models()
                log.info(
                    "transcriber.model.loading model=%s threads=%d quantized=true",
                    MODEL_NAME,
                    THREADS,
                )
                state["backend"] = await asyncio.to_thread(FunASRBackend)
                log.info("transcriber.model.ready model=%s", MODEL_NAME)
            except Exception as exc:
                state["detail"] = _safe_error(exc)
                log.error(
                    "transcriber.model.degraded error_type=%s detail=%s",
                    type(exc).__name__,
                    _safe_error(exc),
                )
        workers = [asyncio.create_task(run_worker()) for _ in range(WORKERS)]
        log.info(
            "transcriber.service.ready model_ready=%s workers=%d queue_capacity=%d "
            "normalization_failure_policy=%s",
            state["backend"] is not None and not state["detail"],
            WORKERS,
            QUEUE_SIZE,
            DEFAULT_FAILURE_POLICY,
        )
        try:
            yield
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    app = FastAPI(
        title="DTK Transcriber", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    def authorize(x_transcriber_token: str | None = Header(default=None)) -> None:
        supplied = (x_transcriber_token or "").encode()
        if TOKEN and not hmac.compare_digest(supplied, TOKEN.encode()):
            raise HTTPException(status_code=401, detail="invalid transcriber token")

    @app.get("/health")
    async def health(_: None = Depends(authorize)) -> dict[str, Any]:
        ready = state["backend"] is not None and not state["detail"]
        return {
            "status": "ok" if ready else "degraded",
            "model": MODEL_NAME,
            "normalizer": NORMALIZER_NAME,
            "normalization_failure_policy": DEFAULT_FAILURE_POLICY,
            "workers": WORKERS,
            "queued": queue.qsize(),
            "running": sum(job["state"] == "running" for job in jobs.values()),
            "retained_terminal_jobs": len(terminal_jobs),
            "terminal_job_limit": JOB_HISTORY_SIZE,
            "detail": state["detail"],
        }

    @app.post("/jobs", status_code=202)
    async def submit(
        request: JobRequest, _: None = Depends(authorize)
    ) -> dict[str, Any]:
        if state["backend"] is None or state["detail"]:
            raise HTTPException(
                status_code=503, detail=state["detail"] or "model unavailable"
            )
        existing = jobs.get(request.id)
        if existing is not None:
            return existing
        path = _media_file(request.path)
        job = {
            "id": request.id,
            "download_id": request.download_id,
            "state": "queued",
            "stage": "queued",
            "model": request.model,
            "normalization_requested": request.normalize,
            "normalization_applied": False,
            "normalizer": NORMALIZER_NAME if request.normalize else None,
            "normalization_failure_policy": (
                request.normalization_failure_policy if request.normalize else None
            ),
            "normalization_error": None,
            "text": None,
            "duration_ms": None,
            "processed_ms": 0,
            "media_duration_ms": None,
            "progress_percent": None,
            "partial_text": "",
            "progress_revision": 0,
            "error": None,
        }
        jobs[request.id] = job
        try:
            queue.put_nowait((request, path, job))
        except asyncio.QueueFull:
            jobs.pop(request.id, None)
            raise HTTPException(
                status_code=429, detail="transcription queue is full"
            ) from None
        log.info(
            "transcriber.job.queued job_id=%s download_id=%s queue_depth=%d normalize=%s",
            _loggable(request.id),
            _loggable(request.download_id or "unknown"),
            queue.qsize(),
            _loggable(request.normalize),
        )
        return job

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str, _: None = Depends(authorize)) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job

    @app.delete("/jobs/{job_id}")
    async def cancel(job_id: str, _: None = Depends(authorize)) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        if job["state"] in ("queued", "running"):
            job["state"] = "cancelled"
            job["progress_revision"] += 1
            remember_terminal(job_id, job)
            log.info(
                "transcriber.job.cancelled job_id=%s download_id=%s",
                _loggable(job_id),
                _loggable(job.get("download_id") or "unknown"),
            )
            return job
        # DELETE doubles as an acknowledgement after the durable API has
        # stored a terminal result. This keeps the normal path near-empty,
        # while the bounded cache still supports worker restart recovery.
        response = dict(job)
        forget_job(job_id)
        return response

    return app


app = create_app()
