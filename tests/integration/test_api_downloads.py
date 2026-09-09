"""Storing media on the operator's disk: scopes, refusals, and the eviction sweep.

No downloader container runs in these tests, and mostly that is the point: the
API's own contract is that it validates, records and queues, and that an absent
sidecar is a clear 501 rather than a 500. The one place a sidecar is needed -
the eviction sweep - gets a stub, because what is being tested there is which
directories the policy chooses, not whether Go can delete a folder.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from dtk.core.db import session_scope
from dtk.core.types import Scope
from dtk.db.models import ArchivedContent, MediaDownload
from dtk.ops import capacity
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


async def test_a_post_that_was_never_archived_is_fetched_first(api_app, db_engine, redis_client):
    """A downloader that can only save what you already parsed is two steps.

    This used to be a 404 telling the caller to parse it first. The worker
    fetches it now - through the same pool, scheduler and request log as any
    other read - so the request is accepted and the archive lookup happens
    where the fetching happens.
    """
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )
    assert response.status_code == 202
    body = envelope(response)["data"]
    assert body["archived"] is False
    # Nothing to plan from yet, and the directory is a placeholder until the
    # fetch says who the author is.
    assert body["planned"] == []
    assert downloads.PENDING_AUTHOR in body["directory"]


async def test_a_download_can_be_started_from_a_link(api_app, db_engine, redis_client):
    """The link is not a URL anything fetches: identify() yields the post id."""
    user_id = await make_user()
    key = await media_key(user_id)
    await archive_a_post()
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"url": f"https://www.douyin.com/video/{CONTENT_ID}"},
            headers={"X-API-Key": key},
        )
    assert response.status_code == 202
    assert envelope(response)["data"]["archived"] is True


async def test_share_text_around_a_link_still_works(api_app, db_engine, redis_client):
    """What the app actually puts on the clipboard: a caption, a code and a link."""
    user_id = await make_user()
    key = await media_key(user_id)
    await archive_a_post()
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"url": f"7.61 gTa:/ look at this https://www.douyin.com/video/{CONTENT_ID} copy"},
            headers={"X-API-Key": key},
        )
    assert response.status_code == 202


async def test_a_host_nobody_allowlisted_is_refused(api_app, db_engine, redis_client):
    """The SSRF story, restated for the field that now exists."""
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"url": "http://169.254.169.254/latest/meta-data/"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "INVALID_PARAM"


async def test_a_short_link_says_to_expand_it(api_app, db_engine, redis_client):
    """Resolving one means following it, and this endpoint makes no requests."""
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"url": "https://v.douyin.com/iRNBho6G/"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["needs_expansion"] is True


async def test_a_profile_link_is_not_a_post(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"url": "https://www.douyin.com/user/MS4wLjABAAAAexample"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "INVALID_PARAM"


async def test_a_link_and_a_platform_that_disagree_are_refused(api_app, db_engine, redis_client):
    """Guessing is how you download the wrong post and call it a feature."""
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"url": f"https://www.douyin.com/video/{CONTENT_ID}", "platform": "tiktok"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "INVALID_PARAM"


async def test_a_malformed_typed_id_never_reaches_the_pool(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": "not-an-id"},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "INVALID_PARAM"


async def test_neither_a_link_nor_a_key_is_refused(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post("/api/v1/downloads", json={}, headers={"X-API-Key": key})
    assert error_code(response) == "INVALID_PARAM"


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


async def test_a_settled_attempt_is_retried_rather_than_coalesced(api_app, db_engine, redis_client):
    """The second request is usually "the first did not work".

    Joining them onto one task would answer it with the failure it was
    retrying, so a SETTLED attempt never blocks a new one - which is what this
    always said. What changed is that a still-running attempt is now joined
    instead: retrying that one cannot help and races it on the same directory.
    """
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        first = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )
        # Settle it, the way the worker would.
        async with session_scope() as session:
            await session.execute(
                update(MediaDownload)
                .where(MediaDownload.id == uuid.UUID(envelope(first)["data"]["download_id"]))
                .values(state="failed")
            )
            await session.commit()

        second = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )

    assert second.status_code == 202
    assert envelope(second)["data"]["reused"] is None
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


async def test_storage_counts_a_shared_directory_once(api_app, db_engine, redis_client):
    """Three rows, one directory, one set of bytes on the volume.

    The media layout has nothing per-download in it, so every download of one
    post lands in the same directory and every row records the whole of it.
    Summing the rows reported 3x what the disk actually held.
    """
    directory = f"douyin/{AUTHOR_UID}/{CONTENT_ID}"
    async with session_scope() as session:
        for _ in range(3):
            session.add(
                MediaDownload(
                    id=uuid.uuid4(),
                    platform="douyin",
                    content_id=CONTENT_ID,
                    author_uid=AUTHOR_UID,
                    state="done",
                    directory=directory,
                    bytes_total=1000,
                    file_count=2,
                )
            )
        # A second directory does add its own bytes, and an evicted row adds
        # none: its files are gone from the volume the number describes.
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id="7000000000000000001",
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/7000000000000000001",
                bytes_total=250,
                file_count=1,
            )
        )
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id="7000000000000000002",
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/7000000000000000002",
                bytes_total=9999,
                file_count=1,
                files_removed_at=datetime.now(UTC),
            )
        )
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)
    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/downloads/storage", headers={"X-API-Key": key})
    data = envelope(response)["data"]
    assert data["downloads"] == 5
    assert data["bytes_total"] == 1250
    assert data["evicted"] == 1


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


# --------------------------------------------------------------------------
# Serving a stored file
#
# The API is the only component in this system with authentication, scopes,
# rate limiting and an audit trail, which is why it serves the bytes and the
# downloader - whose own header insists it must never become a relay - does
# not. What that buys is only as good as the path handling below.
# --------------------------------------------------------------------------


async def stored_download(tmp_root, *, name: str = "video.mp4", state: str = "done") -> uuid.UUID:
    """A record whose directory really exists under the media root."""
    download_id = uuid.uuid4()
    directory = f"douyin/{AUTHOR_UID}/{CONTENT_ID}"
    (tmp_root / directory).mkdir(parents=True, exist_ok=True)
    (tmp_root / directory / name).write_bytes(b"not really an mp4, but bytes are bytes")
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=download_id,
                platform="douyin",
                content_id=CONTENT_ID,
                author_uid=AUTHOR_UID,
                state="done",
                directory=directory,
                bytes_total=38,
                file_count=1,
                files=[
                    {
                        "name": name,
                        "kind": "video",
                        "state": state,
                        "bytes": 38,
                        "content_type": "video/mp4",
                    }
                ],
            )
        )
    return download_id


async def test_a_stored_file_is_served_with_its_recorded_type(
    api_app, db_engine, redis_client, tmp_path, monkeypatch
):
    monkeypatch.setattr(capacity, "MEDIA_PATH", str(tmp_path))
    download_id = await stored_download(tmp_path)
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get(
            f"/api/v1/downloads/{download_id}/files/video.mp4", headers={"X-API-Key": key}
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert "video.mp4" in response.headers.get("content-disposition", "")
    assert response.content == b"not really an mp4, but bytes are bytes"


async def test_a_name_not_in_the_record_is_refused(
    api_app, db_engine, redis_client, tmp_path, monkeypatch
):
    """Existing on the volume is not the test; being in the record is.

    A file the downloader wrote beside this one, for a different download, is
    reachable on the filesystem and must not be reachable through this record.
    """
    monkeypatch.setattr(capacity, "MEDIA_PATH", str(tmp_path))
    download_id = await stored_download(tmp_path)
    neighbour = tmp_path / f"douyin/{AUTHOR_UID}/{CONTENT_ID}" / "someone-elses.mp4"
    neighbour.write_bytes(b"not yours")
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get(
            f"/api/v1/downloads/{download_id}/files/someone-elses.mp4",
            headers={"X-API-Key": key},
        )

    assert error_code(response) == "NOT_FOUND"
    assert neighbour.exists(), "the probe must not have removed the neighbour"


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "....//....//etc/passwd",
        "/etc/passwd",
    ],
)
async def test_a_traversing_name_never_leaves_the_media_root(
    api_app, db_engine, redis_client, tmp_path, monkeypatch, name
):
    monkeypatch.setattr(capacity, "MEDIA_PATH", str(tmp_path))
    download_id = await stored_download(tmp_path)
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get(
            f"/api/v1/downloads/{download_id}/files/{name}", headers={"X-API-Key": key}
        )

    # Either the router never matches it or the record never lists it. What must
    # not happen is a 200 carrying something from outside the root.
    assert response.status_code in (404, 405)
    if response.status_code == 404 and response.headers.get("content-type", "").startswith(
        "application/json"
    ):
        assert error_code(response) == "NOT_FOUND"


async def test_an_evicted_download_says_so_rather_than_refetching(
    api_app, db_engine, redis_client, tmp_path, monkeypatch
):
    monkeypatch.setattr(capacity, "MEDIA_PATH", str(tmp_path))
    download_id = await stored_download(tmp_path)
    async with session_scope() as session:
        row = await session.get(MediaDownload, download_id)
        assert row is not None
        row.files_removed_at = datetime.now(UTC)
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get(
            f"/api/v1/downloads/{download_id}/files/video.mp4", headers={"X-API-Key": key}
        )

    assert error_code(response) == "NOT_FOUND"
    assert envelope(response)["error"]["details"]["reason"] == "evicted"


async def test_serving_a_file_needs_media_read(
    api_app, db_engine, redis_client, tmp_path, monkeypatch
):
    monkeypatch.setattr(capacity, "MEDIA_PATH", str(tmp_path))
    download_id = await stored_download(tmp_path)
    user_id = await make_user()
    key = await media_key(user_id, Scope.DOUYIN_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get(
            f"/api/v1/downloads/{download_id}/files/video.mp4", headers={"X-API-Key": key}
        )

    assert error_code(response) == "FORBIDDEN_SCOPE"


# --------------------------------------------------------------------------
# What the library can see
# --------------------------------------------------------------------------


async def test_the_archive_says_which_posts_are_already_stored(api_app, db_engine, redis_client):
    """The gap this closes: saving a post produced a toast and then a page that
    looked exactly as it had a second earlier, which reads as nothing having
    happened. The library had no way to ask what was already on the disk."""
    async with session_scope() as session:
        session.add(
            ArchivedContent(
                platform="douyin",
                content_id=CONTENT_ID,
                kind="video",
                web_url=f"https://www.douyin.com/video/{CONTENT_ID}",
                title="a post",
                author_uid=AUTHOR_UID,
                media={},
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id=CONTENT_ID,
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/{CONTENT_ID}",
                bytes_total=4096,
                file_count=2,
                files=[
                    {"name": "video.mp4", "kind": "video", "state": "done", "bytes": 4000},
                    {"name": "cover.jpeg", "kind": "cover", "state": "done", "bytes": 96},
                ],
            )
        )
    user_id = await make_user()
    key = await media_key(user_id, Scope.ARCHIVE_READ, Scope.MEDIA_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/archive", headers={"X-API-Key": key})

    row = envelope(response)["data"]["items"][0]
    assert row["stored"]["video"] == "video.mp4"
    assert row["stored"]["cover"] == "cover.jpeg"
    assert row["stored"]["bytes_total"] == 4096


async def test_an_unstored_post_says_so_rather_than_omitting_the_field(
    api_app, db_engine, redis_client
):
    """null, not missing: a card that has to branch on undefined reads the same
    as one whose request failed."""
    async with session_scope() as session:
        session.add(
            ArchivedContent(
                platform="douyin",
                content_id="7000000000000000009",
                kind="video",
                web_url="https://www.douyin.com/video/7000000000000000009",
                title="never fetched",
                author_uid=AUTHOR_UID,
                media={},
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
    user_id = await make_user()
    key = await media_key(user_id, Scope.ARCHIVE_READ, Scope.MEDIA_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/archive", headers={"X-API-Key": key})

    row = envelope(response)["data"]["items"][0]
    assert "stored" in row
    assert row["stored"] is None


async def test_a_caller_without_media_read_is_told_nothing_about_the_disk(
    api_app, db_engine, redis_client
):
    """`archive:read` is deliberately separate from `media:read`. A key that can
    read the archive must not learn what is on the operator's volume."""
    async with session_scope() as session:
        session.add(
            ArchivedContent(
                platform="douyin",
                content_id=CONTENT_ID,
                kind="video",
                web_url=f"https://www.douyin.com/video/{CONTENT_ID}",
                title="a post",
                author_uid=AUTHOR_UID,
                media={},
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id=CONTENT_ID,
                author_uid=AUTHOR_UID,
                state="done",
                directory=f"douyin/{AUTHOR_UID}/{CONTENT_ID}",
                bytes_total=4096,
                file_count=1,
                files=[{"name": "video.mp4", "kind": "video", "state": "done", "bytes": 4000}],
            )
        )
    user_id = await make_user()
    key = await media_key(user_id, Scope.ARCHIVE_READ)

    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/archive", headers={"X-API-Key": key})

    assert envelope(response)["data"]["items"][0]["stored"] is None


# --------------------------------------------------------------------------
# Keeping one copy
# --------------------------------------------------------------------------


async def _record(
    content_id: str,
    *,
    directory: str,
    created: datetime,
    state: str = "done",
    evicted: bool = False,
    bytes_total: int = 1024,
) -> uuid.UUID:
    download_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=download_id,
                platform="douyin",
                content_id=content_id,
                author_uid=AUTHOR_UID,
                state=state,
                directory=directory,
                bytes_total=0 if evicted else bytes_total,
                file_count=1,
                created_at=created,
                files_removed_at=created if evicted else None,
            )
        )
    return download_id


async def test_one_download_is_not_a_duplicate(api_app, db_engine, redis_client):
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    await _record(CONTENT_ID, directory="d/a/1", created=datetime.now(UTC))

    async with anonymous_client(api_app) as caller:
        body = envelope(
            await caller.post("/api/v1/downloads/deduplicate", headers={"X-API-Key": key})
        )["data"]

    assert body["duplicates"] == 0
    assert body["posts"] == []


async def test_the_newest_copy_is_kept(api_app, db_engine, redis_client):
    """Newest because it came from the freshest mirrors."""
    user_id = await make_user()
    key = await media_key(user_id)
    now = datetime.now(UTC)
    old = await _record(CONTENT_ID, directory="d/a/1", created=now - timedelta(hours=2))
    new = await _record(CONTENT_ID, directory="d/a/1", created=now)

    async with anonymous_client(api_app) as caller:
        body = envelope(
            await caller.post("/api/v1/downloads/deduplicate", headers={"X-API-Key": key})
        )["data"]

    assert body["removed"] == 1
    async with session_scope() as session:
        assert await session.get(MediaDownload, new) is not None
        assert await session.get(MediaDownload, old) is None


async def test_the_shared_directory_is_not_deleted(api_app, db_engine, redis_client):
    """Two downloads of one post land in the same directory.

    Deleting the older row's path by id would delete the file the newer one
    just wrote - which is the whole reason this is not a loop over ids.
    """
    user_id = await make_user()
    key = await media_key(user_id)
    now = datetime.now(UTC)
    await _record(CONTENT_ID, directory="d/a/1", created=now - timedelta(hours=2))
    await _record(CONTENT_ID, directory="d/a/1", created=now)

    async with anonymous_client(api_app) as caller:
        body = envelope(
            await caller.post("/api/v1/downloads/deduplicate", headers={"X-API-Key": key})
        )["data"]

    # No paths to remove, so no downloader is needed and none was called - the
    # rows go, the one real copy on disk stays.
    assert body["posts"][0]["paths"] == []
    assert body["removed"] == 1
    assert body["freed_bytes"] == 0


async def test_an_evicted_copy_is_never_preferred_over_a_present_one(
    api_app, db_engine, redis_client
):
    """Keeping the evicted row would delete the only copy there is."""
    user_id = await make_user()
    key = await media_key(user_id)
    now = datetime.now(UTC)
    present = await _record(CONTENT_ID, directory="d/a/1", created=now - timedelta(hours=2))
    await _record(CONTENT_ID, directory="d/a/1", created=now, evicted=True)

    async with anonymous_client(api_app) as caller:
        await caller.post("/api/v1/downloads/deduplicate", headers={"X-API-Key": key})

    async with session_scope() as session:
        assert await session.get(MediaDownload, present) is not None


async def test_a_dry_run_changes_nothing(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    now = datetime.now(UTC)
    await _record(CONTENT_ID, directory="d/a/1", created=now - timedelta(hours=2))
    await _record(CONTENT_ID, directory="d/a/1", created=now)

    async with anonymous_client(api_app) as caller:
        body = envelope(
            await caller.post(
                "/api/v1/downloads/deduplicate",
                json={"dry_run": True},
                headers={"X-API-Key": key},
            )
        )["data"]

    assert body["duplicates"] == 1
    assert body["removed"] == 0
    async with session_scope() as session:
        assert len((await session.scalars(select(MediaDownload))).all()) == 2


async def test_posts_downloaded_once_each_are_left_alone(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    now = datetime.now(UTC)
    await _record("7000000000000000001", directory="d/a/1", created=now)
    await _record("7000000000000000002", directory="d/a/2", created=now)

    async with anonymous_client(api_app) as caller:
        body = envelope(
            await caller.post("/api/v1/downloads/deduplicate", headers={"X-API-Key": key})
        )["data"]

    assert body["duplicates"] == 0
    async with session_scope() as session:
        assert len((await session.scalars(select(MediaDownload))).all()) == 2


async def test_a_read_key_cannot_deduplicate(api_app, db_engine, redis_client):
    """It deletes files and records, so no read scope implies it."""
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)
    async with anonymous_client(api_app) as caller:
        response = await caller.post("/api/v1/downloads/deduplicate", headers={"X-API-Key": key})
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_a_download_that_cannot_be_fetched_ends_as_failed(api_app, db_engine, redis_client):
    """It used to sit at `queued` forever, reading as still waiting.

    `fail` wrote the terminal state into the session and the worker raised
    immediately afterwards to fail the task, so the rollback took the write with
    it. Only visible once downloading a post nobody had archived became an
    ordinary thing to do - before that, an unfetchable post was refused by the
    API and never reached the worker at all.
    """
    from dtk.services import downloads as service

    await archive_a_post()
    async with session_scope() as session:
        row = (await session.scalars(select(ArchivedContent))).one()
        download = await service.create(session, row)
        download_id = download.id
        await session.commit()

    async with session_scope() as session:
        await service.fail(session, download_id, "nothing to fetch")
        # The rollback a raising caller would trigger.
        await session.rollback()

    async with session_scope() as session:
        settled = await session.get(MediaDownload, download_id)
        assert settled is not None
        assert settled.state == "failed"
        assert settled.error == "nothing to fetch"


# --------------------------------------------------------------------------
# Not downloading the same post twice at once, or twice at all
# --------------------------------------------------------------------------


async def test_a_download_already_in_flight_is_joined(api_app, db_engine, redis_client):
    """Two of them write into one directory, and one loses the rename.

    Measured on a live instance: three requests for one post finished within
    10ms of each other and one came back `partial` with "rename cover.jpeg.part:
    no such file or directory". Joining is not a preference, so no flag turns it
    off - and it is what the caller wanted anyway, since the media they are
    waiting for is already on its way.
    """
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

    assert first.status_code == 202
    assert second.status_code == 200
    assert envelope(second)["data"]["reused"] == "in_flight"
    assert envelope(second)["data"]["download_id"] == envelope(first)["data"]["download_id"]
    async with session_scope() as session:
        assert len((await session.scalars(select(MediaDownload))).all()) == 1


async def test_skip_existing_hands_back_what_is_already_stored(api_app, db_engine, redis_client):
    """What re-running a feed wants: three new posts, forty already here."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    stored = await _record(CONTENT_ID, directory="d/a/1", created=datetime.now(UTC))

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID, "skip_existing": True},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 200
    assert envelope(response)["data"]["reused"] == "already_stored"
    assert envelope(response)["data"]["download_id"] == str(stored)


async def test_without_the_flag_a_stored_post_is_downloaded_again(api_app, db_engine, redis_client):
    """Re-downloading is how you refresh a post, so it stays the default."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    await _record(CONTENT_ID, directory="d/a/1", created=datetime.now(UTC))

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 202
    assert envelope(response)["data"]["reused"] is None


async def test_skip_existing_ignores_an_evicted_copy(api_app, db_engine, redis_client):
    """The record is kept on purpose; the bytes are gone. There is nothing to skip."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    await _record(CONTENT_ID, directory="d/a/1", created=datetime.now(UTC), evicted=True)

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID, "skip_existing": True},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 202


async def test_a_finished_failure_can_still_be_retried(api_app, db_engine, redis_client):
    """ "The first one did not work" is the usual reason for a second request."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    await _record(
        CONTENT_ID, directory="d/a/1", created=datetime.now(UTC), state="failed", bytes_total=0
    )

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID, "skip_existing": True},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 202


async def test_simultaneous_requests_cannot_both_start(api_app, db_engine, redis_client):
    """The case the pre-check cannot catch.

    Three requests arriving together all read "none in flight" before any of
    them writes, so the check passes for all three. `ux_media_downloads_in_flight`
    is the only place that sees all three, and the loser gets the same answer
    the check would have given.

    This is not hypothetical: three requests for one post on a live instance
    finished within 10ms of each other and one came back `partial` with
    "rename cover.jpeg.part: no such file or directory".
    """
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)

    async with anonymous_client(api_app) as caller:
        responses = await asyncio.gather(
            *(
                caller.post(
                    "/api/v1/downloads",
                    json={"platform": "douyin", "content_id": CONTENT_ID},
                    headers={"X-API-Key": key},
                )
                for _ in range(3)
            )
        )

    started = [r for r in responses if r.status_code == 202]
    joined = [r for r in responses if r.status_code == 200]
    assert len(started) == 1
    assert len(joined) == 2
    assert all(envelope(r)["data"]["reused"] == "in_flight" for r in joined)
    # One download, and everyone was told about the same one.
    ids = {envelope(r)["data"]["download_id"] for r in responses}
    assert len(ids) == 1
    async with session_scope() as session:
        assert len((await session.scalars(select(MediaDownload))).all()) == 1


async def test_a_settled_download_does_not_block_the_next_one(api_app, db_engine, redis_client):
    """The index is partial, so only in-flight rows collide."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    now = datetime.now(UTC)
    await _record(CONTENT_ID, directory="d/a/1", created=now, state="failed", bytes_total=0)
    await _record(CONTENT_ID, directory="d/a/1", created=now, state="cancelled", bytes_total=0)

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": CONTENT_ID},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 202


async def test_two_different_posts_are_unaffected(api_app, db_engine, redis_client):
    await archive_a_post(content_id="7000000000000000001")
    await archive_a_post(content_id="7000000000000000002")
    user_id = await make_user()
    key = await media_key(user_id)

    async with anonymous_client(api_app) as caller:
        first = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": "7000000000000000001"},
            headers={"X-API-Key": key},
        )
        second = await caller.post(
            "/api/v1/downloads",
            json={"platform": "douyin", "content_id": "7000000000000000002"},
            headers={"X-API-Key": key},
        )

    assert first.status_code == second.status_code == 202


# --------------------------------------------------------------------------
# Trying again
# --------------------------------------------------------------------------


async def test_a_failed_download_can_be_retried(api_app, db_engine, redis_client):
    """Ask again, which is the only thing that works.

    There is no resume: the sidecar truncates its `.part` on every attempt, and
    it is built that way because media URLs are signed and expire - bytes
    fetched an hour ago cannot be continued against a link that now answers 403.
    """
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    failed = await _record(
        CONTENT_ID, directory="d/a/1", created=datetime.now(UTC), state="failed", bytes_total=0
    )

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads/retry",
            json={"download_id": str(failed)},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 202
    assert envelope(response)["data"]["download_id"] != str(failed)


async def test_a_partial_download_can_be_retried(api_app, db_engine, redis_client):
    """The state the cover race left behind, and the reason this button exists."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    partial = await _record(
        CONTENT_ID, directory="d/a/1", created=datetime.now(UTC), state="partial"
    )

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads/retry",
            json={"download_id": str(partial)},
            headers={"X-API-Key": key},
        )

    assert response.status_code == 202


async def test_a_running_download_is_not_retried(api_app, db_engine, redis_client):
    """A second attempt would race the first into the same directory."""
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    running = await _record(
        CONTENT_ID, directory="d/a/1", created=datetime.now(UTC), state="running"
    )

    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads/retry",
            json={"download_id": str(running)},
            headers={"X-API-Key": key},
        )

    assert error_code(response) == "INVALID_PARAM"


async def test_retrying_something_that_is_not_there_is_a_404(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads/retry",
            json={"download_id": str(uuid.uuid4())},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "NOT_FOUND"


async def test_a_read_key_cannot_retry(api_app, db_engine, redis_client):
    user_id = await make_user()
    key = await media_key(user_id, Scope.MEDIA_READ)
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/downloads/retry",
            json={"download_id": str(uuid.uuid4())},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_the_listing_carries_a_progress_slot(api_app, db_engine, redis_client):
    """Present on every row, null unless the sidecar had something to say.

    A settled download's progress is its result, so only live rows are ever
    asked about - and with no sidecar configured, nothing is.
    """
    await archive_a_post()
    user_id = await make_user()
    key = await media_key(user_id)
    await _record(CONTENT_ID, directory="d/a/1", created=datetime.now(UTC))

    async with anonymous_client(api_app) as caller:
        body = envelope(await caller.get("/api/v1/downloads", headers={"X-API-Key": key}))["data"]

    rows = body["items"] if isinstance(body, dict) else body
    assert rows
    assert all("progress" in row for row in rows)
    assert rows[0]["progress"] is None
