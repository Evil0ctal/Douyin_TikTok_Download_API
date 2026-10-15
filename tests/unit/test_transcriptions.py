"""Durable transcription presentation contracts."""

from datetime import UTC, datetime

from dtk.db.models import Transcription
from dtk.services import transcriptions


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
