"""Durable transcription presentation contracts."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.dialects import postgresql

from dtk.db.models import Transcription
from dtk.services import transcriptions


class RecordingSession:
    statement: Any = None

    async def execute(self, statement: Any) -> Any:
        self.statement = statement
        return SimpleNamespace(rowcount=1)


def test_listing_never_embeds_the_complete_transcript() -> None:
    row = Transcription(
        platform="douyin",
        content_id="7408915107113127220",
        state="done",
        text="word " * 500,
        model="test-model",
        normalization_requested=True,
        normalization_applied=True,
        source_name="video.mp4",
        source_sha256="a" * 64,
        source_bytes=100,
        created_at=datetime.now(UTC),
    )

    result = transcriptions.as_list_dict(row)

    assert "text" not in result
    assert result["text_preview"] == ("word " * 500)[:160]


async def test_apply_result_persists_sidecar_lifecycle_timestamps() -> None:
    started_at = datetime(2026, 10, 15, 9, 0, 0, tzinfo=UTC)
    finished_at = datetime(2026, 10, 15, 9, 0, 12, tzinfo=UTC)
    session = RecordingSession()

    await transcriptions.apply_result(
        cast(Any, session),
        uuid.uuid4(),
        {
            "text": "finished",
            "duration_ms": 12_000,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        },
    )

    assert session.statement is not None
    params = session.statement.compile(dialect=postgresql.dialect()).params
    assert params["started_at"] == started_at
    assert params["finished_at"] == finished_at


async def test_apply_result_reports_a_row_that_had_already_settled() -> None:
    class SettledSession(RecordingSession):
        async def execute(self, statement: Any) -> Any:
            self.statement = statement
            return SimpleNamespace(rowcount=0)

    session = SettledSession()
    applied = await transcriptions.apply_result(cast(Any, session), uuid.uuid4(), {"text": "late"})

    assert applied is False
    where = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "transcriptions.state IN" in where
