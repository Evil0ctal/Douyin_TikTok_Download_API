"""Storing media on the operator's disk: scopes, refusals, and the eviction sweep.

No downloader container runs in these tests, and mostly that is the point: the
API's own contract is that it validates, records and queues, and that an absent
sidecar is a clear 501 rather than a 500. The one place a sidecar is needed -
the eviction sweep - gets a stub, because what is being tested there is which
directories the policy chooses, not whether Go can delete a folder.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.types import Scope
from dtk.db.models import ArchivedContent, MediaDownload
from dtk.services import downloads
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
AUTHOR_UID = "MS4wLjABAAAAexample"
VIDEO = "https://v9-v2-mps-cdn.douyinvod.com/x/video/tos/cn/a/?mime_type=video_mp4"
COVER = "https://p3-pc-sign.douyinpic.com/aweme/cover.jpeg"


async def archive_a_post(
    *, content_id: str = CONTENT_ID, media: dict[str, Any] | None = None
) -> None:
    now = datetime.now(UTC)
    async with session_scope() as session:
        session.add(
            ArchivedContent(
                platform="douyin",
                content_id=content_id,
                kind="video",
                web_url=f"https://www.douyin.com/video/{content_id}",
                title="a post",
                description="a post",
                author_uid=AUTHOR_UID,
                author_nickname="someone",
                tags=[],
                media=media
                if media is not None
                else {"video": {"url": VIDEO}, "covers": [{"url": COVER}]},
                first_seen_at=now,
                last_seen_at=now,
            )
        )


async def media_key(user_id: uuid.UUID, *scopes: Scope) -> str:
    return await make_api_key(user_id, scopes=scopes or (Scope.MEDIA_READ, Scope.MEDIA_WRITE))


# --------------------------------------------------------------------------
# Scopes
# --------------------------------------------------------------------------


async def test_a_read_key_cannot_start_a_download(api_app, db_engine, redis_client):
    """Starting one spends an identity and fills a disk, so no read scope implies it."""
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)
    await archive_a_post()
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_a_platform_read_key_cannot_see_stored_media(api_app, db_engine, redis_client):
    # media:read is separate from douyin:read on purpose: what is on the
    # operator's filesystem is a different question from what a platform said.
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/downloads", headers={"X-API-Key": key})
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_downloads_need_a_key_at_all(api_app, db_engine, redis_client):
    async with anonymous_client(api_app) as caller:
        assert (await caller.get("/api/v1/downloads")).status_code == 401


# --------------------------------------------------------------------------
# Starting one
# --------------------------------------------------------------------------


async def test_a_post_that_was_never_archived_is_refused(api_app, db_engine, redis_client):
    """The caller supplies a content key, and this instance decides if it knows it.

    That is the whole SSRF story for this endpoint: there is no URL to validate
    because there is no URL to send.
    """
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": "404404404"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "NOT_FOUND"


async def test_a_post_with_no_fetchable_mirror_is_refused_with_the_reason(
    api_app, db_engine, redis_client
):
    await archive_a_post(
        content_id="7000000000000000001",
        media={"video": {"url": "https://evil.example/v.mp4"}},
    )
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": "7000000000000000001"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "INVALID_PARAM"
    body = envelope(response)
    assert "allowlist" in body["error"]["message"] or body["error"]["details"]["skipped"]


async def test_starting_one_records_the_plan_and_queues_a_task(api_app, db_engine, redis_client):
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )
    assert response.status_code == 202, response.text
    data = envelope(response)["data"]
    assert [item["name"] for item in data["planned"]] == ["video.mp4", "cover.jpeg"]
    assert data["directory"] == f"douyin/{AUTHOR_UID}/{CONTENT_ID}"

    async with session_scope() as session:
        row = await downloads.get(session, uuid.UUID(data["download_id"]))
        assert row is not None
        # The row exists before anything is fetched, so a worker that dies
        # between accepting and submitting leaves something to reconcile.
        assert row.state == "queued"
        assert str(row.task_id) == data["task_id"]


async def test_two_requests_for_the_same_post_are_not_coalesced(api_app, db_engine, redis_client):
    """The second is usually "the first did not work"; joining them would answer
    it with the failure it was retrying."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        first = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )
        second = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )
    assert envelope(first)["data"]["task_id"] != envelope(second)["data"]["task_id"]


# --------------------------------------------------------------------------
# Reading back
# --------------------------------------------------------------------------


async def test_an_evicted_row_is_still_listed_and_says_so(api_app, db_engine, redis_client):
    """ "Collected and later cleaned up" is a different fact from "never fetched",
    and only the first can be undone by asking again."""
    download_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=download_id,
                platform="douyin",
                content_id=CONTENT_ID,
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/{CONTENT_ID}",
                bytes_total=0,
                file_count=2,
                files=[{"name": "video.mp4", "state": "done", "bytes": 100}],
                files_removed_at=datetime.now(UTC),
            )
        )
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)
    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/downloads", headers={"X-API-Key": key})
    row = envelope(response)["data"]["items"][0]
    assert row["on_disk"] is False
    assert row["files_removed_at"]
    # The file list survives the files: the row still says what was collected.
    assert row["files"]


async def test_storage_reports_the_ceiling_and_an_absent_downloader(
    api_app, db_engine, redis_client
):
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)
    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/downloads/storage", headers={"X-API-Key": key})
    data = envelope(response)["data"]
    assert data["max_bytes"] == 2 * 1024**3
    # No sidecar in these tests, and that is a deployment fact rather than a
    # failure: the endpoint answers with it instead of raising.
    assert data["downloader"]["available"] is False


async def test_pinning_a_download_that_does_not_exist_is_a_404(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            f"/api/v1/downloads/{uuid.uuid4()}/pin",
            json={"pinned": True},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "NOT_FOUND"


# --------------------------------------------------------------------------
# The eviction sweep
# --------------------------------------------------------------------------


class StubDownloader:
    """Enough of the sidecar for the policy to be exercised."""

    configured = True

    def __init__(self, total_bytes: int) -> None:
        self._total = total_bytes
        self.deleted: list[str] = []

    async def files(self) -> dict[str, Any]:
        return {"total_bytes": self._total, "entries": []}

    async def delete(self, paths: list[str]) -> dict[str, Any]:
        self.deleted.extend(paths)
        return {"freed_bytes": 100, "removed": paths}


async def _download(*, pinned: bool, finished_minutes_ago: int, name: str) -> uuid.UUID:
    download_id = uuid.uuid4()
    finished = datetime.now(UTC) - timedelta(minutes=finished_minutes_ago)
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=download_id,
                platform="douyin",
                content_id=name,
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/{name}",
                bytes_total=100,
                file_count=1,
                pinned=pinned,
                finished_at=finished,
            )
        )
    return download_id


async def test_the_sweep_takes_the_oldest_unpinned_and_keeps_the_record(
    api_app, db_engine, redis_client
):
    from dtk.core.config import Config
    from dtk.core.crypto import Cipher
    from dtk.worker.maintenance import Maintenance, MaintenanceReport

    oldest = await _download(pinned=False, finished_minutes_ago=90, name="7000000000000000010")
    await _download(pinned=True, finished_minutes_ago=120, name="7000000000000000011")
    newest = await _download(pinned=False, finished_minutes_ago=1, name="7000000000000000012")

    # Three rows of 100 against a ceiling of 250: over by 50, so exactly one
    # eviction is enough.
    config = Config({**Config.defaults().as_dict(), "media.max_bytes": 250})
    stub = StubDownloader(total_bytes=300)
    maintenance = Maintenance(
        cipher=Cipher("x" * 40),
        config=lambda: config,
        downloader=stub,  # type: ignore[arg-type]
    )
    report = MaintenanceReport()
    await maintenance.enforce_media_ceiling(report)

    # Only as much as it takes, oldest first, and never the pinned one - which
    # is older than either of them.
    assert stub.deleted == [f"douyin/{AUTHOR_UID}/7000000000000000010"]
    assert report.media_evicted == 1

    async with session_scope() as session:
        gone = await downloads.get(session, oldest)
        kept = await downloads.get(session, newest)
        assert gone is not None and gone.files_removed_at is not None
        # The record survives; only the bytes are gone.
        assert gone.state == "done" and gone.bytes_total == 0 and gone.file_count == 1
        assert kept is not None and kept.files_removed_at is None


async def test_a_stale_download_stops_claiming_to_be_in_flight(api_app, db_engine, redis_client):
    """A task that exhausts its attempts would otherwise leave `running` forever."""
    from dtk.core.config import Config
    from dtk.core.crypto import Cipher
    from dtk.worker.maintenance import Maintenance, MaintenanceConfig, MaintenanceReport

    download_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=download_id,
                platform="douyin",
                content_id="7000000000000000013",
                author_uid=AUTHOR_UID,
                state="running",
                directory=f"douyin/{AUTHOR_UID}/7000000000000000013",
                created_at=datetime.now(UTC) - timedelta(hours=6),
            )
        )

    maintenance = Maintenance(
        cipher=Cipher("x" * 40),
        config=lambda: Config.defaults(),
        options=MaintenanceConfig(stale_download_seconds=3600),
    )
    report = MaintenanceReport()
    await maintenance.fail_stale_downloads(report)

    assert report.stale_downloads_failed == 1
    async with session_scope() as session:
        row = await downloads.get(session, download_id)
        assert row is not None and row.state == "failed" and row.error
