"""What the console's content pages need to link to one another, and to find a row.

Downloads, the library and transcriptions describe the same post three ways,
and each page used to know only its own: a download did not say it had a
transcript, so the Transcribe button was a request whose only outcome was
being handed back a record the page could have linked to; a post opened from a
link said it had never been saved while its video was on the disk. These pin
the fields that carry those links, the searches that find a row past the first
page, and the export of a hand-picked selection.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.types import Scope
from dtk.db.models import ArchivedContent, MediaDownload, Transcription
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    anonymous_client,
    envelope,
    error_code,
    make_api_key,
    make_user,
    signed_in,
)

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

AUTHOR_UID = "MS4wLjABAAAAexample"


async def archive_post(content_id: str, *, title: str, nickname: str = "someone") -> None:
    now = datetime.now(UTC)
    async with session_scope() as session:
        session.add(
            ArchivedContent(
                platform="douyin",
                content_id=content_id,
                kind="video",
                web_url=f"https://www.douyin.com/video/{content_id}",
                title=title,
                description="",
                author_uid=AUTHOR_UID,
                author_nickname=nickname,
                tags=[],
                media={},
                first_seen_at=now,
                last_seen_at=now,
            )
        )


async def store(content_id: str, *, finished_at: datetime | None = None) -> uuid.UUID:
    download_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=download_id,
                platform="douyin",
                content_id=content_id,
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/{content_id}",
                bytes_total=4096,
                file_count=1,
                files=[
                    {
                        "name": "video.mp4",
                        "kind": "video",
                        "state": "done",
                        "bytes": 4096,
                        "sha256": "a" * 64,
                    }
                ],
                finished_at=finished_at,
            )
        )
    return download_id


async def transcribe(
    download_id: uuid.UUID, content_id: str, *, state: str, text: str = ""
) -> uuid.UUID:
    transcription_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            Transcription(
                id=transcription_id,
                download_id=download_id,
                platform="douyin",
                content_id=content_id,
                state=state,
                text=text or None,
                model="test-model",
                normalization_requested=True,
                normalization_applied=state == "done",
                source_name="video.mp4",
                source_sha256="a" * 64,
                source_bytes=4096,
            )
        )
    return transcription_id


# --------------------------------------------------------------------------
# Finding a row past the first page
# --------------------------------------------------------------------------


async def test_downloads_are_searched_by_the_archived_title_author_and_id(client: Any) -> None:
    await signed_in(client)
    await archive_post("7408915107113127220", title="cats on a piano", nickname="pianist")
    await archive_post("7408915107113127221", title="a quiet morning")
    await store("7408915107113127220")
    await store("7408915107113127221")

    async def found(term: str) -> list[str]:
        response = await client.get("/api/v1/downloads", params={"q": term})
        data = envelope(response)["data"]
        assert data["total"] == len(data["items"])
        return [row["content_id"] for row in data["items"]]

    assert await found("PIANO") == ["7408915107113127220"]
    assert await found("pianist") == ["7408915107113127220"]
    assert await found("221") == ["7408915107113127221"]
    # Typed text, not a pattern: `%` would otherwise match every row.
    assert await found("%") == []


async def test_transcriptions_are_searched_by_text_title_and_id(client: Any) -> None:
    await signed_in(client)
    await archive_post("7408915107113127220", title="cats on a piano")
    first = await store("7408915107113127220")
    second = await store("7408915107113127221")
    await transcribe(first, "7408915107113127220", state="done", text="hello there")
    await transcribe(second, "7408915107113127221", state="done", text="general kenobi")

    async def found(term: str) -> list[str]:
        response = await client.get("/api/v1/transcriptions", params={"q": term})
        data = envelope(response)["data"]
        assert data["total"] == len(data["items"])
        return [row["content_id"] for row in data["items"]]

    assert await found("kenobi") == ["7408915107113127221"]
    assert await found("piano") == ["7408915107113127220"]
    assert await found("7408915107113127221") == ["7408915107113127221"]


# --------------------------------------------------------------------------
# A download that already has a transcript says so
# --------------------------------------------------------------------------


async def test_a_download_names_its_transcript(client: Any) -> None:
    await signed_in(client)
    download_id = await store("7408915107113127220")
    transcription_id = await transcribe(download_id, "7408915107113127220", state="done")

    listed = envelope(await client.get("/api/v1/downloads"))["data"]["items"][0]
    assert listed["transcription"] == {
        "transcription_id": str(transcription_id),
        "state": "done",
    }

    # The single read too: a drawer opened from /downloads?id= has no listing.
    single = envelope(await client.get(f"/api/v1/downloads/{download_id}"))["data"]
    assert single["transcription"]["transcription_id"] == str(transcription_id)
    assert "post" in single
    assert "progress" in single


async def test_a_failed_transcription_is_not_offered_as_one(client: Any) -> None:
    """Asking again is what the reader wants there, not a link to the failure."""
    await signed_in(client)
    download_id = await store("7408915107113127220")
    await transcribe(download_id, "7408915107113127220", state="failed")

    listed = envelope(await client.get("/api/v1/downloads"))["data"]["items"][0]
    assert listed["transcription"] is None


async def test_a_key_that_cannot_read_transcripts_is_not_told(api_app: Any) -> None:
    download_id = await store("7408915107113127220")
    await transcribe(download_id, "7408915107113127220", state="done")
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.MEDIA_READ,))

    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/downloads", headers={"X-API-Key": key})

    assert envelope(response)["data"]["items"][0]["transcription"] is None


# --------------------------------------------------------------------------
# The library's view of the same post
# --------------------------------------------------------------------------


async def test_a_stored_post_says_when_it_was_saved_and_what_became_of_it(client: Any) -> None:
    """`saved_at` is what grouping by the day it was saved reads. It used to
    read `last_seen_at`, which a recheck moves."""
    await signed_in(client)
    await archive_post("7408915107113127220", title="cats on a piano")
    saved = datetime.now(UTC) - timedelta(days=30)
    download_id = await store("7408915107113127220", finished_at=saved)
    transcription_id = await transcribe(download_id, "7408915107113127220", state="running")

    row = envelope(await client.get("/api/v1/archive"))["data"]["items"][0]

    assert datetime.fromisoformat(row["stored"]["saved_at"]) == saved
    assert row["stored"]["transcription"] == {
        "transcription_id": str(transcription_id),
        "state": "running",
    }


async def test_one_archived_post_says_what_is_on_the_disk(client: Any) -> None:
    """The single read answered `stored: null` for everything, so a post opened
    from /library?platform=&content_id= offered to save what was already saved."""
    await signed_in(client)
    await archive_post("7408915107113127220", title="cats on a piano")
    download_id = await store("7408915107113127220")

    response = await client.get("/api/v1/archive/douyin/7408915107113127220")
    data = envelope(response)["data"]

    assert data["stored"]["download_id"] == str(download_id)
    assert data["collections"] == []


# --------------------------------------------------------------------------
# Exporting what was picked
# --------------------------------------------------------------------------


def lines(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


async def test_a_selection_exports_exactly_those_posts(client: Any) -> None:
    await signed_in(client)
    for content_id in ("7001", "7002", "7003"):
        await archive_post(content_id, title=f"post {content_id}")

    response = await client.post(
        "/api/v1/archive/export",
        json={
            "items": [
                {"platform": "douyin", "content_id": "7001"},
                {"platform": "douyin", "content_id": "7003"},
                # Not archived: skipped rather than refusing the other two.
                {"platform": "douyin", "content_id": "7999"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert sorted(row["content_id"] for row in lines(response)) == ["7001", "7003"]


async def test_exporting_a_selection_needs_the_export_scope(api_app: Any) -> None:
    await archive_post("7001", title="post")
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.ARCHIVE_READ,))

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/archive/export",
            json={"items": [{"platform": "douyin", "content_id": "7001"}]},
            headers={"X-API-Key": key},
        )

    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_the_filtered_export_honours_the_collection(client: Any) -> None:
    """The console sent `collection` and the export ignored it, so exporting
    one collection exported the whole archive."""
    await signed_in(client)
    for content_id in ("7001", "7002"):
        await archive_post(content_id, title=f"post {content_id}")
    created = await client.post("/api/v1/archive/collections", json={"name": "keep"})
    collection_id = envelope(created)["data"]["id"]
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items",
        json={"items": [{"platform": "douyin", "content_id": "7002"}]},
    )

    response = await client.get("/api/v1/archive/export", params={"collection": collection_id})

    assert [row["content_id"] for row in lines(response)] == ["7002"]


# --------------------------------------------------------------------------
# Naming a watched target
# --------------------------------------------------------------------------


async def test_a_watched_target_can_be_renamed_and_handed_back(client: Any) -> None:
    await signed_in(client)
    created = await client.post(
        "/api/v1/admin/watchlist",
        json={
            "platform": "douyin",
            "kind": "author",
            "target_id": AUTHOR_UID,
            "interval_seconds": 3600,
            "label": "the cooking one",
        },
    )
    entry = envelope(created)["data"]
    assert entry["label"] == "the cooking one"

    renamed = await client.patch(
        f"/api/v1/admin/watchlist/{entry['id']}", json={"label": "the baking one", "pages": 2}
    )
    assert envelope(renamed)["data"]["label"] == "the baking one"
    assert envelope(renamed)["data"]["pages"] == 2

    # Leaving it out keeps it; null hands it back to the next run.
    paused = await client.patch(f"/api/v1/admin/watchlist/{entry['id']}", json={"enabled": False})
    assert envelope(paused)["data"]["label"] == "the baking one"
    cleared = await client.patch(f"/api/v1/admin/watchlist/{entry['id']}", json={"label": None})
    assert envelope(cleared)["data"]["label"] is None
