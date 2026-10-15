"""Ephemeral transcription progress stays bounded and parseable."""

from __future__ import annotations

from typing import Any

from dtk.services import transcription_progress


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expiries[key] = ex

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


async def test_progress_round_trips_through_redis_with_a_short_ttl(monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(transcription_progress, "get_redis", lambda: redis)
    transcription_id = "7afe9635-9deb-4590-b76a-46a18c38bf7b"
    job: dict[str, Any] = {
        "state": "running",
        "stage": "asr",
        "progress_revision": 4,
        "processed_ms": 500,
        "media_duration_ms": 1000,
        "progress_percent": 50.0,
        "partial_text": "temporary text",
        # The final text and errors are deliberately outside the snapshot.
        "text": "must not be copied",
        "error": "must not be copied",
    }

    await transcription_progress.publish(transcription_id, job)
    snapshot = await transcription_progress.read(transcription_id)

    assert snapshot is not None
    assert snapshot.stage == "asr"
    assert snapshot.revision == 4
    assert snapshot.partial_text == "temporary text"
    key = transcription_progress.KEY.format(transcription_id=transcription_id)
    assert redis.expiries[key] == transcription_progress.TTL_SECONDS
    assert "must not be copied" not in redis.values[key]

    await transcription_progress.clear(transcription_id)
    assert await transcription_progress.read(transcription_id) is None


async def test_malformed_progress_is_ignored(monkeypatch) -> None:
    redis = FakeRedis()
    transcription_id = "7afe9635-9deb-4590-b76a-46a18c38bf7b"
    redis.values[transcription_progress.KEY.format(transcription_id=transcription_id)] = "not-json"
    monkeypatch.setattr(transcription_progress, "get_redis", lambda: redis)

    assert await transcription_progress.read(transcription_id) is None
