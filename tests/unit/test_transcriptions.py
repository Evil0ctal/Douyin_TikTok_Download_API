"""Durable transcription presentation contracts."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.dialects import postgresql

from dtk.api.deps import Principal
from dtk.core.types import Scope, UserRole
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


class ListingSession:
    """Answers a search with no rows, and keeps every statement it was sent."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.statements: list[Any] = []
        self.rows = rows or []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        rows = self.rows
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: rows),
            scalar_one=lambda: len(rows),
            all=lambda: rows,
        )


async def test_search_matches_the_transcript_the_title_and_the_id() -> None:
    session = ListingSession()
    await transcriptions.search(
        cast(Any, session), transcriptions.TranscriptionFilter(query="50%_off")
    )

    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "transcriptions.text ILIKE" in sql
    assert "transcriptions.content_id ILIKE" in sql
    # The title is the archive's, read the way the listing shows it.
    assert "EXISTS" in sql
    assert "archived_contents.title ILIKE" in sql
    # Typed text, not a pattern.
    assert "%50\\%\\_off%" in compiled.params.values()
    # The count is filtered the same way, or `total` would count the lot.
    assert "ILIKE" in str(session.statements[1].compile(dialect=postgresql.dialect()))


async def test_the_newest_openable_transcription_wins() -> None:
    """A refresh still running is preferred over the text it is replacing, and
    one row per download however many there are."""
    download = uuid.uuid4()
    other = uuid.uuid4()
    newest, older, theirs = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = ListingSession(
        rows=[(download, newest, "running"), (download, older, "done"), (other, theirs, "done")]
    )

    found = await transcriptions.latest_for_downloads(cast(Any, session), [download, other])

    assert found == {
        download: {"transcription_id": str(newest), "state": "running"},
        other: {"transcription_id": str(theirs), "state": "done"},
    }
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    # A failed one is not worth opening: asking again is what the reader wants.
    assert "transcriptions.state IN" in sql
    assert set(transcriptions.OPENABLE_STATES) == {"queued", "running", "done"}


async def test_no_downloads_asks_nothing() -> None:
    session = ListingSession()
    assert await transcriptions.latest_for_downloads(cast(Any, session), []) == {}
    assert session.statements == []


def _caller(role: UserRole, *scopes: Scope, key: bool = False) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        role=role,
        scopes=frozenset(scopes),
        api_key_id=uuid.uuid4() if key else None,
        rate_limit_per_min=None,
    )


@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        # The demo account may list downloads and may not open /transcriptions;
        # `permits` alone answers yes to every session whatever its role.
        (_caller(UserRole.DEMO), False),
        (_caller(UserRole.VIEWER), True),
        (_caller(UserRole.ADMIN), True),
        (_caller(UserRole.ADMIN, Scope.MEDIA_READ, key=True), False),
        (_caller(UserRole.ADMIN, Scope.MEDIA_READ, Scope.TRANSCRIPTION_READ, key=True), True),
    ],
)
def test_listings_link_a_transcript_only_for_a_caller_who_can_open_it(
    caller: Principal, expected: bool
) -> None:
    from dtk.api.routes import transcriptions as routes

    assert routes.may_read(caller) is expected
