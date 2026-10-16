"""Transcription console listing, cancellation, and record deletion."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from dtk.core.db import session_scope
from dtk.core.types import Scope
from dtk.db.models import MediaDownload, Transcription
from dtk.services import transcription_progress
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    anonymous_client,
    envelope,
    error_code,
    make_api_key,
    make_user,
)

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

CONTENT_ID = "7408915107113127220"


async def transcription_key(*scopes: Scope) -> str:
    user_id = await make_user()
    return await make_api_key(
        user_id,
        scopes=scopes or (Scope.TRANSCRIPTION_READ, Scope.TRANSCRIPTION_WRITE),
    )


async def add_download() -> uuid.UUID:
    identifier = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=identifier,
                platform="douyin",
                content_id=CONTENT_ID,
                author_uid="author",
                state="done",
                directory=f"douyin/author/{CONTENT_ID}",
                bytes_total=100,
                file_count=1,
                files=[
                    {
                        "name": "video.mp4",
                        "kind": "video",
                        "state": "done",
                        "bytes": 100,
                        "sha256": "a" * 64,
                    }
                ],
            )
        )
    return identifier


async def add_transcription(
    download_id: uuid.UUID, *, state: str, text: str | None = None
) -> uuid.UUID:
    identifier = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            Transcription(
                id=identifier,
                download_id=download_id,
                platform="douyin",
                content_id=CONTENT_ID,
                state=state,
                text=text,
                model="test-model",
                normalization_requested=True,
                normalization_applied=state == "done",
                source_name="video.mp4",
                source_sha256="a" * 64,
                source_bytes=100,
            )
        )
    return identifier


async def test_list_filters_and_omits_full_text(api_app, db_engine, redis_client):
    download_id = await add_download()
    live_id = await add_transcription(download_id, state="running")
    await add_transcription(download_id, state="done", text="complete durable transcript")
    await transcription_progress.publish(
        live_id,
        {
            "state": "running",
            "stage": "recognizing",
            "progress_percent": 25,
            "partial_text": "provisional words",
        },
    )
    key = await transcription_key(Scope.TRANSCRIPTION_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get(
            "/api/v1/transcriptions?platform=douyin&state=running",
            headers={"X-API-Key": key},
        )

    assert response.status_code == 200
    data = envelope(response)["data"]
    assert data["total"] == 1
    row = data["items"][0]
    assert row["transcription_id"] == str(live_id)
    assert "text" not in row
    assert row["progress"]["progress_percent"] == 25
    # Grows with the job and the list never shows it; only the detail read does.
    assert "partial_text" not in row["progress"]

    async with anonymous_client(api_app) as caller:
        detail = await caller.get(f"/api/v1/transcriptions/{live_id}", headers={"X-API-Key": key})
    assert envelope(detail)["data"]["progress"]["partial_text"] == "provisional words"


async def test_read_scope_cannot_delete_records(api_app, db_engine, redis_client):
    download_id = await add_download()
    transcription_id = await add_transcription(download_id, state="done", text="kept")
    key = await transcription_key(Scope.TRANSCRIPTION_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/transcriptions/delete",
            json={"ids": [str(transcription_id)]},
            headers={"X-API-Key": key},
        )

    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_delete_is_atomic_and_never_deletes_source_media(api_app, db_engine, redis_client):
    download_id = await add_download()
    done_id = await add_transcription(download_id, state="done", text="finished")
    live_id = await add_transcription(download_id, state="running")
    key = await transcription_key()

    async with anonymous_client(api_app) as caller:
        refused = await caller.post(
            "/api/v1/transcriptions/delete",
            json={"ids": [str(done_id), str(live_id)]},
            headers={"X-API-Key": key},
        )
        assert error_code(refused) == "INVALID_PARAM"

        cancelled = await caller.delete(
            f"/api/v1/transcriptions/{live_id}", headers={"X-API-Key": key}
        )
        assert envelope(cancelled)["data"]["state"] == "cancelled"

        removed = await caller.post(
            "/api/v1/transcriptions/delete",
            json={"ids": [str(done_id), str(live_id)]},
            headers={"X-API-Key": key},
        )
        assert envelope(removed)["data"]["deleted"] == 2

    async with session_scope() as session:
        count = await session.scalar(select(func.count()).select_from(Transcription))
        download = await session.get(MediaDownload, download_id)
    assert count == 0
    assert download is not None
    assert download.files and download.files[0]["name"] == "video.mp4"
