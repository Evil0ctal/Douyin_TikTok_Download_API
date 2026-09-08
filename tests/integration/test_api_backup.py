"""The console's Backup page: listing archives and asking for a restore.

Three properties are worth more than the happy paths, and each one is a rule
this endpoint pair could quietly break:

* **The key check never leaves the server.** A manifest fingerprints
  DTK_SECRET_KEY so a restore can refuse an archive it cannot decrypt. Handing
  that fingerprint to a browser hands out an oracle for offline guesses at the
  key it fingerprints, so the listing answers with a verdict instead (doc 08).
* **No response describes the host's filesystem.** Archives are named by file
  name, and the reason a corrupt one could not be read is an OSError message
  that arrives with an absolute path inside it.
* **A name from a browser cannot address a file outside the backup
  directory.** It is joined onto a path and handed to a tar reader; a traversal
  or a planted symlink here reads anything the process can open.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from dtk.core.db import session_scope
from dtk.core.types import Scope, UserRole
from dtk.db.models import AuditLog, Task
from dtk.ops.backup import (
    ARCHIVE_SUFFIX,
    BACKUP_SCHEMA_VERSION,
    Manifest,
    build_manifest,
    key_check,
    write_archive,
)
from dtk.worker import ops as worker_ops
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    TEST_SECRET_KEY,
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

#: A key that is not this instance's, for the archive nobody here can restore.
OTHER_SECRET_KEY = "another-master-key-entirely-0123456789abcdef"

CREATED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

USER_ROW: dict[str, Any] = {
    "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "username": "restored-admin",
    "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$deadbeef",
    "role": "admin",
    "created_at": CREATED,
    "last_login_at": None,
}


@pytest.fixture
def backup_dir(api_app: Any, tmp_path: Path) -> Path:
    """Point the API at a scratch directory instead of the checkout's own.

    Replaced rather than mutated: BootstrapSettings is read once at startup and
    every reader of it has to see one consistent object.
    """
    directory = tmp_path / "backups"
    directory.mkdir()
    api_app.state.settings = api_app.state.settings.model_copy(
        update={"backup_dir": str(directory)}
    )
    return directory


def make_archive(
    directory: Path,
    name: str = "dtk-backup-20260301T120000Z.tar.gz",
    *,
    secret_key: str = TEST_SECRET_KEY,
    schema_version: int = BACKUP_SCHEMA_VERSION,
    include_identities: bool = False,
    created_at: datetime = CREATED,
) -> Path:
    """Write a real archive, the way ``dtk backup create`` would."""
    manifest = build_manifest(
        contents={"users": 1},
        include_identities=include_identities,
        secret_key=secret_key,
        created_at=created_at,
    )
    if schema_version != manifest.schema_version:
        manifest = Manifest(
            schema_version=schema_version,
            created_at=manifest.created_at,
            dtk_version=manifest.dtk_version,
            include_identities=manifest.include_identities,
            key_check=manifest.key_check,
            contents=dict(manifest.contents),
        )
    return write_archive(directory / name, manifest, {"users": [USER_ROW]})


async def queued_tasks() -> list[Task]:
    async with session_scope() as session:
        return list((await session.scalars(select(Task))).all())


async def audit_actions() -> list[str]:
    async with session_scope() as session:
        rows = (await session.scalars(select(AuditLog).order_by(AuditLog.ts))).all()
        return [row.action for row in rows]


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


async def test_listing_answers_with_an_array_the_page_can_render(
    client: Any, backup_dir: Path
) -> None:
    await signed_in(client)
    make_archive(backup_dir)

    response = await client.get("/api/v1/admin/backup")
    rows = envelope(response)["data"]

    # web/src/pages/Backup.tsx accepts a bare array or an object wrapping one;
    # the bare array is what it lists first and what every other administrative
    # listing already answers with.
    assert isinstance(rows, list)
    assert rows[0]["path"] == "dtk-backup-20260301T120000Z.tar.gz"
    assert rows[0]["size_bytes"] > 0
    assert rows[0]["error"] is None
    manifest = rows[0]["manifest"]
    assert manifest["schema_version"] == BACKUP_SCHEMA_VERSION
    assert manifest["include_identities"] is False
    assert manifest["contents"] == {"users": 1}


async def test_an_empty_or_absent_directory_is_an_empty_listing(
    client: Any, backup_dir: Path
) -> None:
    await signed_in(client)
    empty = await client.get("/api/v1/admin/backup")
    assert envelope(empty)["data"] == []

    # Nothing has taken a backup yet on a fresh install, so the directory the
    # worker will create does not exist. That is a listing, not a failure.
    backup_dir.rmdir()
    missing = await client.get("/api/v1/admin/backup")
    assert envelope(missing)["data"] == []


async def test_listing_never_returns_the_key_or_its_check(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir)

    response = await client.get("/api/v1/admin/backup")

    assert TEST_SECRET_KEY not in response.text
    # The check confirms two keys are the same, which is exactly what an
    # attacker guessing the key needs in order to know they guessed right.
    assert key_check(TEST_SECRET_KEY) not in response.text
    assert "key_check" not in response.text
    # What the page actually needs from it: can this instance restore this file.
    assert envelope(response)["data"][0]["manifest"]["key_matches"] is True


async def test_an_archive_written_under_another_key_says_so(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir, "dtk-backup-20260228T090000Z.tar.gz", secret_key=OTHER_SECRET_KEY)

    response = await client.get("/api/v1/admin/backup")
    row = envelope(response)["data"][0]

    assert row["manifest"]["key_matches"] is False
    assert key_check(OTHER_SECRET_KEY) not in response.text


async def test_a_corrupt_archive_is_listed_with_its_reason(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir)
    (backup_dir / f"dtk-backup-truncated{ARCHIVE_SUFFIX}").write_bytes(b"not a gzip stream")

    response = await client.get("/api/v1/admin/backup")
    rows = {row["path"]: row for row in envelope(response)["data"]}

    # The whole listing must survive one bad file: a corrupt archive is the
    # reason someone opens this page.
    assert set(rows) == {
        "dtk-backup-20260301T120000Z.tar.gz",
        f"dtk-backup-truncated{ARCHIVE_SUFFIX}",
    }
    broken = rows[f"dtk-backup-truncated{ARCHIVE_SUFFIX}"]
    assert broken["manifest"] is None
    assert broken["error"]
    assert rows["dtk-backup-20260301T120000Z.tar.gz"]["manifest"] is not None


async def test_no_listing_describes_the_filesystem(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir)
    # An unreadable archive is reported through an OSError, and an OSError
    # renders the absolute name it failed on.
    unreadable = backup_dir / f"dtk-backup-unreadable{ARCHIVE_SUFFIX}"
    unreadable.write_bytes(b"")
    unreadable.chmod(0o000)
    try:
        response = await client.get("/api/v1/admin/backup")
    finally:
        unreadable.chmod(0o600)

    assert str(backup_dir) not in response.text
    for row in envelope(response)["data"]:
        assert row["path"] == Path(row["path"]).name
        assert "/" not in row["path"]


async def test_listing_is_the_administrative_read_surface(
    api_app: Any, client: Any, backup_dir: Path
) -> None:
    make_archive(backup_dir)

    async with anonymous_client(api_app) as stranger:
        assert error_code(await stranger.get("/api/v1/admin/backup")) == "UNAUTHENTICATED"

    # A read key minted for content is not an administrative credential.
    user_id = await make_user()
    raw = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    async with anonymous_client(api_app) as keyed:
        response = await keyed.get("/api/v1/admin/backup", headers={"X-API-Key": raw})
        assert error_code(response) == "FORBIDDEN_SCOPE"

    # A console session that cannot take a backup can still see whether last
    # night's ran; the listing carries no credential.
    await signed_in(client, username="watcher", role=UserRole.VIEWER)
    assert envelope(await client.get("/api/v1/admin/backup"))["data"]


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


async def test_restore_queues_the_job_the_worker_answers_for(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir)

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": "dtk-backup-20260301T120000Z.tar.gz", "confirm": True},
    )
    assert response.status_code == 202
    body = envelope(response)["data"]
    assert uuid.UUID(body["task_id"])

    tasks = await queued_tasks()
    assert [task.endpoint for task in tasks] == ["backup.restore"]
    # The queued parameter is the file name, so the job cannot be pointed at
    # anything outside the backup directory by the row it reads.
    assert tasks[0].params == {"path": "dtk-backup-20260301T120000Z.tar.gz"}
    # The other half of the contract: something on the worker side claims it.
    assert worker_ops.handles(tasks[0].endpoint)

    assert "backup.restore_requested" in await audit_actions()


async def test_restore_audits_what_was_asked_for_and_no_key_material(
    client: Any, backup_dir: Path
) -> None:
    await signed_in(client)
    make_archive(backup_dir, include_identities=True)

    await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": "dtk-backup-20260301T120000Z.tar.gz", "confirm": True},
    )

    async with session_scope() as session:
        row = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "backup.restore_requested")
            )
        ).one()
    assert row.target_id == "dtk-backup-20260301T120000Z.tar.gz"
    detail = row.detail or {}
    assert detail["include_identities"] is True
    assert detail["rows"] == 1
    # The audit trail is read by humans and pasted into bug reports.
    assert key_check(TEST_SECRET_KEY) not in str(detail)
    assert str(backup_dir) not in str(detail)


async def test_restore_demands_an_explicit_confirmation(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir)

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": "dtk-backup-20260301T120000Z.tar.gz"},
    )

    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["field"] == "confirm"
    assert await queued_tasks() == []


async def test_restore_refuses_an_archive_from_another_key(client: Any, backup_dir: Path) -> None:
    await signed_in(client)
    make_archive(backup_dir, secret_key=OTHER_SECRET_KEY)

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": "dtk-backup-20260301T120000Z.tar.gz", "confirm": True},
    )

    # A refusal with a code, not a 500: restoring ciphertext this instance
    # cannot decrypt would fill the tables with rows nothing can ever read.
    assert error_code(response) == "INVALID_PARAM"
    # Backup.tsx tells this apart from an ordinary bad request by the archive's
    # creation time in the details, and shows the key-mismatch explanation.
    assert isinstance(envelope(response)["error"]["details"]["created_at"], str)
    assert await queued_tasks() == []


async def test_restore_refuses_a_format_this_build_cannot_read(
    client: Any, backup_dir: Path
) -> None:
    await signed_in(client)
    make_archive(backup_dir, schema_version=BACKUP_SCHEMA_VERSION + 99)

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": "dtk-backup-20260301T120000Z.tar.gz", "confirm": True},
    )

    assert error_code(response) == "INVALID_PARAM"
    assert await queued_tasks() == []


async def test_restore_reports_a_missing_archive_as_not_found(
    client: Any, backup_dir: Path
) -> None:
    await signed_in(client)

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": f"dtk-backup-never-written{ARCHIVE_SUFFIX}", "confirm": True},
    )

    assert error_code(response) == "NOT_FOUND"
    assert await queued_tasks() == []


@pytest.mark.parametrize(
    "path",
    [
        "../outside.tar.gz",
        "../../etc/passwd",
        "/etc/passwd",
        "subdir/dtk-backup.tar.gz",
        "..",
        "",
        "dtk-backup.txt",
        "dtk-backup\x00.tar.gz",
    ],
)
async def test_restore_refuses_a_name_that_is_not_a_file_in_the_backup_directory(
    client: Any, backup_dir: Path, tmp_path: Path, path: str
) -> None:
    await signed_in(client)
    # A real, restorable archive one directory up: if traversal worked, this is
    # the file the worker would be pointed at.
    make_archive(tmp_path, "outside.tar.gz")

    response = await client.post(
        "/api/v1/admin/backup/restore", json={"path": path, "confirm": True}
    )

    assert error_code(response) in {"INVALID_PARAM", "NOT_FOUND"}
    assert await queued_tasks() == []


async def test_restore_refuses_a_symlink_out_of_the_backup_directory(
    client: Any, backup_dir: Path, tmp_path: Path
) -> None:
    await signed_in(client)
    make_archive(tmp_path, "outside.tar.gz")
    # A bare file name that resolves elsewhere. Rejecting only on the spelling
    # of the name would let this one through and read a file the caller was
    # never allowed to name.
    (backup_dir / f"dtk-backup-link{ARCHIVE_SUFFIX}").symlink_to(tmp_path / "outside.tar.gz")

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": f"dtk-backup-link{ARCHIVE_SUFFIX}", "confirm": True},
    )

    assert error_code(response) == "INVALID_PARAM"
    assert await queued_tasks() == []


async def test_restore_is_administrators_only(client: Any, backup_dir: Path) -> None:
    make_archive(backup_dir)
    # An operator maintains the pool; a restore hands archived users and API
    # keys back their access, which is a different decision.
    await signed_in(client, username="operator", role=UserRole.OPERATOR)

    response = await client.post(
        "/api/v1/admin/backup/restore",
        json={"path": "dtk-backup-20260301T120000Z.tar.gz", "confirm": True},
    )

    assert error_code(response) == "FORBIDDEN_SCOPE"
    assert await queued_tasks() == []
