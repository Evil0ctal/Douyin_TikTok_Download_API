"""Backup and restore.

Two properties matter more than the row counts: ciphertext must survive the
round trip byte for byte, and a restore under the wrong ``DTK_SECRET_KEY`` must
stop before it writes anything. The second is what keeps a key mismatch from
producing a database full of credentials nobody can ever decrypt.
"""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Insert, Select

from dtk.core.crypto import Cipher
from dtk.ops import backup

SECRET = "a" * 48
OTHER_SECRET = "b" * 48

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROXY_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
CREATED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def sample_rows() -> dict[str, list[dict[str, Any]]]:
    cipher = Cipher(SECRET)
    return {
        "users": [
            {
                "id": USER_ID,
                "username": "admin",
                "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$deadbeef",
                "role": "admin",
                "created_at": CREATED,
                "last_login_at": None,
            }
        ],
        "api_keys": [
            {
                "id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
                "user_id": USER_ID,
                "name": "console",
                "prefix": "dtk_abc123",
                "key_hash": "0" * 64,
                "scopes": ["douyin:read", "tiktok:read"],
                "rate_limit": None,
                "expires_at": None,
                "revoked_at": None,
                "last_used_at": None,
                "created_at": CREATED,
            }
        ],
        "proxies": [
            {
                "id": PROXY_ID,
                "url_encrypted": cipher.encrypt(
                    "http://user:secret@proxy.example.com:8080", aad=str(PROXY_ID)
                ),
                "label": "residential-1",
                "country": "JP",
                "timezone": "Asia/Tokyo",
                "healthy": True,
                "last_check_at": CREATED,
                "created_at": CREATED,
            }
        ],
        "settings": [
            {
                "key": "pool.target_size",
                "value": 8,
                "updated_at": CREATED,
                "updated_by": USER_ID,
            }
        ],
        "content_snapshots": [
            {
                "ts": CREATED,
                "platform": "douyin",
                "content_type": "video",
                "content_id": "7300000000000000000",
                "play_count": 12345,
                "digg_count": None,
                "comment_count": None,
                "share_count": None,
                "collect_count": None,
                "follower_count": None,
                "raw": {"aweme_id": "7300000000000000000"},
            }
        ],
        "identities": [
            {
                "id": uuid.UUID("44444444-4444-4444-4444-444444444444"),
                "platform": "douyin",
                "cookies_encrypted": cipher.encrypt(
                    "sessionid=abc; ttwid=def",
                    aad="44444444-4444-4444-4444-444444444444",
                ),
                "fingerprint": {"browser_family": "chrome", "browser_major": 149},
                "proxy_id": PROXY_ID,
                "authenticated": True,
                "source": "imported",
                "state": "active",
                "cooldown_until": None,
                "consecutive_fails": 0,
                "minted_at": CREATED,
                "last_used_at": None,
                "retired_at": None,
                "retire_reason": None,
            }
        ],
    }


class _AsyncRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)

    def __aiter__(self) -> _AsyncRows:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)


class _Stream:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _AsyncRows:
        return _AsyncRows(self._rows)


class _Result:
    """As much of a SQLAlchemy Result as the restore's duplicate lookup uses."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeSession:
    """Serves rows for an exported SELECT and records restored INSERTs.

    ON CONFLICT is not modelled - the database enforces that - so a table with a
    primary key accumulates its rows twice here. ``content_snapshots`` has no
    unique index for the database to enforce anything with, which is why the
    lookup that keeps it from duplicating is answered honestly: every row the
    fake holds, ignoring the WHERE, exactly as the real narrowed query returns a
    superset for the caller to match against.
    """

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._rows = rows or {}
        self.inserted: dict[str, list[dict[str, Any]]] = {}
        self.statements: list[str] = []

    async def stream(self, statement: Select[Any]) -> _Stream:
        table = statement.get_final_froms()[0].name  # type: ignore[attr-defined]
        return _Stream(self._rows.get(table, []))

    async def execute(self, statement: Any, params: Any = None) -> Any:
        if isinstance(statement, Insert):
            name = statement.table.name
            self.inserted.setdefault(name, []).extend(params or [])
            return None
        if isinstance(statement, Select):
            table = statement.get_final_froms()[0].name  # type: ignore[attr-defined]
            names = [column.name for column in statement.selected_columns]
            return _Result(
                [tuple(row.get(name) for name in names) for row in self.inserted.get(table, [])]
            )
        self.statements.append(str(statement))
        return None


# --------------------------------------------------------------------------
# manifest and key binding
# --------------------------------------------------------------------------


def test_key_check_identifies_the_key_without_revealing_it() -> None:
    fingerprint = backup.key_check(SECRET)

    assert fingerprint == backup.key_check(SECRET)
    assert fingerprint != backup.key_check(OTHER_SECRET)
    assert SECRET not in fingerprint
    assert len(fingerprint) == 64


def test_manifest_never_carries_the_master_key(tmp_path: Path) -> None:
    manifest = backup.build_manifest(
        contents={"users": 1}, include_identities=False, secret_key=SECRET
    )
    path = backup.write_archive(tmp_path / "b.tar.gz", manifest, {"users": []})

    with tarfile.open(path) as archive:
        handle = archive.extractfile(backup.MANIFEST_NAME)
        assert handle is not None
        raw = handle.read().decode()

    assert SECRET not in raw
    assert json.loads(raw)["key_check"] == backup.key_check(SECRET)


def test_wrong_key_is_reported_as_a_key_mismatch() -> None:
    manifest = backup.build_manifest(contents={}, include_identities=False, secret_key=SECRET)

    backup.verify_secret_key(manifest, SECRET)
    with pytest.raises(backup.SecretKeyMismatch) as raised:
        backup.verify_secret_key(manifest, OTHER_SECRET)

    assert "DTK_SECRET_KEY" in str(raised.value)


def test_unsupported_schema_version_is_refused() -> None:
    manifest = backup.build_manifest(contents={}, include_identities=False, secret_key=SECRET)
    future = backup.Manifest(
        schema_version=backup.BACKUP_SCHEMA_VERSION + 1,
        created_at=manifest.created_at,
        dtk_version="9.0.0",
        include_identities=False,
        key_check=manifest.key_check,
    )

    backup.check_schema_version(manifest)
    with pytest.raises(backup.SchemaVersionUnsupported):
        backup.check_schema_version(future)


def test_a_file_that_is_not_an_archive_is_a_manifest_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"not a tarball")

    with pytest.raises(backup.ManifestInvalid):
        backup.read_manifest(broken)


# --------------------------------------------------------------------------
# contents
# --------------------------------------------------------------------------


def test_identities_are_excluded_by_default() -> None:
    assert "identities" not in backup.tables_for(include_identities=False)
    assert "identities" in backup.tables_for(include_identities=True)


def test_operational_tables_are_never_exported() -> None:
    for name in ("request_log", "identity_events", "tasks", "audit_log"):
        assert name in backup.EXCLUDED_TABLES
        assert name not in backup.tables_for(include_identities=True)


def test_restore_order_puts_dependencies_first() -> None:
    order = backup.RESTORE_ORDER
    assert order.index("users") < order.index("api_keys")
    assert order.index("proxies") < order.index("identities")


# --------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------


async def test_round_trip_through_a_directory(tmp_path: Path) -> None:
    rows = sample_rows()
    source = FakeSession(rows)

    info = await backup.create_backup(
        source, output=tmp_path, secret_key=SECRET, created_at=CREATED
    )

    assert info.path.parent == tmp_path
    assert info.path.name.startswith("dtk-backup-")
    assert info.manifest is not None
    assert info.manifest.contents["users"] == 1
    assert "identities" not in info.manifest.contents

    target = FakeSession()
    report = await backup.restore_backup(target, info.path, secret_key=SECRET)

    assert report.restored["users"] == 1
    assert report.restored["content_snapshots"] == 1
    assert "identities" in report.skipped
    assert target.inserted["users"][0] == rows["users"][0]
    assert target.inserted["settings"][0]["value"] == 8
    assert target.inserted["api_keys"][0]["scopes"] == ["douyin:read", "tiktok:read"]
    # The settings counter is bumped so running processes reload.
    assert any("settings_version" in statement for statement in target.statements)


async def test_ciphertext_survives_the_round_trip_untouched(tmp_path: Path) -> None:
    """Nothing is decrypted on the way out, so nothing can be corrupted."""
    rows = sample_rows()
    original = rows["proxies"][0]["url_encrypted"]
    info = await backup.create_backup(
        FakeSession(rows),
        output=tmp_path / "with-identities.tar.gz",
        secret_key=SECRET,
        include_identities=True,
    )

    target = FakeSession()
    await backup.restore_backup(target, info.path, secret_key=SECRET)

    restored = target.inserted["proxies"][0]["url_encrypted"]
    assert restored == original
    assert isinstance(restored, bytes)
    assert Cipher(SECRET).decrypt(restored, aad=str(PROXY_ID)).startswith("http://")
    assert target.inserted["identities"][0]["fingerprint"]["browser_major"] == 149


async def test_restore_with_the_wrong_key_writes_nothing(tmp_path: Path) -> None:
    info = await backup.create_backup(
        FakeSession(sample_rows()), output=tmp_path, secret_key=SECRET
    )
    target = FakeSession()

    with pytest.raises(backup.SecretKeyMismatch):
        await backup.restore_backup(target, info.path, secret_key=OTHER_SECRET)

    assert target.inserted == {}
    assert target.statements == []


async def test_listing_is_newest_first_and_reports_corrupt_files(tmp_path: Path) -> None:
    older = await backup.create_backup(
        FakeSession(sample_rows()),
        output=tmp_path / "older.tar.gz",
        secret_key=SECRET,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = await backup.create_backup(
        FakeSession(sample_rows()),
        output=tmp_path / "newer.tar.gz",
        secret_key=SECRET,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    (tmp_path / "corrupt.tar.gz").write_bytes(b"junk")

    listing = backup.list_backups(tmp_path)

    assert [item.path for item in listing[:2]] == [newer.path, older.path]
    corrupt = next(item for item in listing if item.path.name == "corrupt.tar.gz")
    assert corrupt.manifest is None
    assert corrupt.error is not None


def test_listing_an_absent_directory_is_empty(tmp_path: Path) -> None:
    assert backup.list_backups(tmp_path / "nope") == []


def test_unknown_columns_in_an_older_archive_are_ignored() -> None:
    from dtk.db.models import User

    decoded = backup.decode_row(
        User.__table__,
        {"id": str(USER_ID), "username": "admin", "column_dropped_since": 1},
    )

    assert decoded == {"id": USER_ID, "username": "admin"}


def test_a_malformed_data_line_names_the_table_and_line(tmp_path: Path) -> None:
    manifest = backup.build_manifest(
        contents={"users": 1}, include_identities=False, secret_key=SECRET
    )
    path = tmp_path / "hand-edited.tar.gz"
    backup.write_archive(path, manifest, {"users": []})
    # Rewrite the member with a broken line.
    with tarfile.open(path, "r:gz") as archive:
        members = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}  # type: ignore[union-attr]
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            content = b"{not json}\n" if name.endswith("users.jsonl") else payload
            info.size = len(content)
            import io

            archive.addfile(info, io.BytesIO(content))

    with pytest.raises(backup.BackupError) as raised:
        backup.read_table(path, "users")
    assert "users.jsonl line 1" in str(raised.value)


async def test_the_settings_counter_bump_survives_a_missing_singleton_row(
    tmp_path: Path,
) -> None:
    """A bare UPDATE touches nothing when the row is absent, and says so.

    The counter is what makes running processes reload restored settings; a
    statement that silently matches no row would leave them on the old
    configuration with nothing to show for it.
    """
    info = await backup.create_backup(
        FakeSession(sample_rows()), output=tmp_path, secret_key=SECRET
    )
    target = FakeSession()

    report = await backup.restore_backup(target, info.path, secret_key=SECRET)

    assert report.restored["settings"] == 1
    bump = next(s for s in target.statements if "settings_version" in s)
    assert "INSERT INTO settings_version" in bump
    assert "ON CONFLICT" in bump


def test_a_hand_edited_key_check_is_a_mismatch_not_a_crash() -> None:
    """compare_digest raises TypeError on a non-ASCII string."""
    manifest = backup.Manifest(
        schema_version=backup.BACKUP_SCHEMA_VERSION,
        created_at=CREATED,
        dtk_version="5.0.0",
        include_identities=False,
        key_check="not-a-hex-digest-\u30ad\u30fc",
    )

    with pytest.raises(backup.SecretKeyMismatch):
        backup.verify_secret_key(manifest, SECRET)


# --------------------------------------------------------------------------
# Damaged archives
#
# The corruption that matters is a truncated gzip, not a file of random bytes.
# gzip signals it with EOFError, which is neither a TarError nor an OSError, so
# it escaped every guard and answered INTERNAL - and the listing endpoint the
# console polls every ten seconds was the thing it broke.
# --------------------------------------------------------------------------


def _archive_bytes(payload: int = 1 << 20) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        body = json.dumps(
            {
                "schema_version": 1,
                "created_at": "2026-03-01T12:00:00+00:00",
                "dtk_version": "5.0.0",
                "include_identities": False,
                "contents": {"users": 1},
                "key_check": "deadbeef",
            }
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
        filler = b"x" * payload
        info = tarfile.TarInfo("data/users.jsonl")
        info.size = len(filler)
        archive.addfile(info, io.BytesIO(filler))
    return buffer.getvalue()


def test_a_truncated_archive_does_not_take_the_listing_down(tmp_path: Path) -> None:
    """A whole page of archives must survive one damaged file."""
    raw = _archive_bytes()
    (tmp_path / "dtk-backup-good.tar.gz").write_bytes(raw)
    (tmp_path / "dtk-backup-cut.tar.gz").write_bytes(raw[: len(raw) // 2])
    (tmp_path / "dtk-backup-garbage.tar.gz").write_bytes(b"not a gzip stream")

    listed = backup.list_backups(tmp_path)

    assert len(listed) == 3, "a damaged archive was dropped rather than reported"
    assert any(info.error for info in listed), "nothing was reported as unreadable"


def test_restoring_a_truncated_archive_is_a_coded_error(tmp_path: Path) -> None:
    """Not an EOFError from inside gzip, which the API renders as INTERNAL."""
    raw = _archive_bytes()
    cut = tmp_path / "dtk-backup-cut.tar.gz"
    cut.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(backup.BackupError):
        backup.read_member(cut, "users")


def test_an_archive_is_never_visible_while_it_is_being_written(tmp_path: Path) -> None:
    """The window that made this a daily failure rather than a rare one.

    create_backup gzips a multi-gigabyte database, which takes minutes, and it
    used to write straight to the final name. For that whole time the directory
    held a truncated archive - so taking a backup broke the page that takes
    backups, for every viewer, every night.
    """
    manifest = backup.build_manifest(
        contents={"users": 0}, include_identities=False, secret_key="k" * 48
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "users.jsonl").write_bytes(b"")
    target = tmp_path / "dtk-backup-atomic.tar.gz"

    backup._pack_archive(target, manifest, staging, ["users"])

    assert target.is_file()
    # Nothing partial survives, and nothing partial ever matched the listing
    # glob in the first place: the temporary name does not end in .tar.gz.
    leftovers = [p.name for p in tmp_path.iterdir() if p.is_file() and p != target]
    assert leftovers == [], f"the pack left files behind: {leftovers}"
    assert [i.path.name for i in backup.list_backups(tmp_path)] == [target.name]


# --------------------------------------------------------------------------
# Staging
#
# /tmp in the shipped container is a 64 MB tmpfs and, with every other path
# read-only, the only place a default TemporaryDirectory can go. A database
# staged there dies of ENOSPC once content_snapshots outgrows RAM, and names a
# filesystem the operator never configured when it does. Staging belongs on the
# volume the archive is headed for anyway.
# --------------------------------------------------------------------------


class _WatchingSession(FakeSession):
    """Records what the backup directory holds while the tables are dumped."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], directory: Path) -> None:
        super().__init__(rows)
        self._directory = directory
        self.entries: set[str] = set()
        self.listed: list[str] = []

    async def stream(self, statement: Select[Any]) -> _Stream:
        self.entries.update(path.name for path in self._directory.iterdir())
        self.listed.extend(info.path.name for info in backup.list_backups(self._directory))
        return await super().stream(statement)


async def test_a_backup_never_stages_into_the_default_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default temp dir that cannot be written stands in for one that is full."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "no-such-tmpdir"))
    session = _WatchingSession(sample_rows(), tmp_path)

    info = await backup.create_backup(session, output=tmp_path, secret_key=SECRET)

    staged = [name for name in session.entries if name.startswith(backup.STAGING_PREFIX)]
    assert len(staged) == 1, f"the tables were staged elsewhere: {sorted(session.entries)}"
    # It sat in the directory the Backup page globs, for as long as the dump
    # took, and the page never saw it as an archive.
    assert session.listed == []
    assert info.path.is_file()
    assert [path.name for path in tmp_path.iterdir()] == [info.path.name]


async def test_staging_a_killed_backup_left_behind_is_swept(tmp_path: Path) -> None:
    """Nothing clears it now that it is not in a tmpfs the container resets."""
    abandoned = tmp_path / f"{backup.STAGING_PREFIX}killed"
    abandoned.mkdir()
    (abandoned / "content_snapshots.jsonl").write_text("{}\n", encoding="utf-8")
    aged = time.time() - backup.STAGING_MAX_AGE_SECONDS - 60
    os.utime(abandoned, (aged, aged))
    running = tmp_path / f"{backup.STAGING_PREFIX}running"
    running.mkdir()

    await backup.create_backup(FakeSession(sample_rows()), output=tmp_path, secret_key=SECRET)

    assert not abandoned.exists()
    assert running.is_dir(), "a backup that is still writing lost its staging directory"


# --------------------------------------------------------------------------
# What the archive is readable by
# --------------------------------------------------------------------------


async def test_the_archive_is_readable_only_by_the_account_that_wrote_it(
    tmp_path: Path,
) -> None:
    """Cookie jars are ciphertext; notify.channels webhook URLs are not."""
    manifest = backup.build_manifest(contents={}, include_identities=False, secret_key=SECRET)
    permissive = os.umask(0o000)
    try:
        info = await backup.create_backup(
            FakeSession(sample_rows()),
            output=tmp_path,
            secret_key=SECRET,
            include_identities=True,
        )
        from_memory = backup.write_archive(tmp_path / "in-memory.tar.gz", manifest, {"users": []})
    finally:
        os.umask(permissive)

    assert stat.S_IMODE(info.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(from_memory.stat().st_mode) == 0o600
    with tarfile.open(info.path) as archive:
        members = archive.getmembers()
    assert {member.name for member in members} >= {"data/identities.jsonl"}
    assert {member.mode for member in members} == {0o600}
    # Nor does it say which account on which host took the backup.
    assert {member.uname for member in members} == {""}
    assert {member.uid for member in members} == {0}


# --------------------------------------------------------------------------
# Restoring twice
#
# content_snapshots is a hypertable with no unique index, so ON CONFLICT DO
# NOTHING has nothing to conflict with and the rows are simply appended again.
# --------------------------------------------------------------------------


async def test_restoring_the_same_archive_twice_does_not_duplicate_snapshots(
    tmp_path: Path,
) -> None:
    info = await backup.create_backup(
        FakeSession(sample_rows()), output=tmp_path, secret_key=SECRET
    )
    target = FakeSession()

    first = await backup.restore_backup(target, info.path, secret_key=SECRET)
    second = await backup.restore_backup(target, info.path, secret_key=SECRET)

    assert len(target.inserted["content_snapshots"]) == 1
    # The per-table counts still say what the archive offered, which is what
    # they have always meant and what the worker reports to the console.
    assert first.restored["content_snapshots"] == 1
    assert second.restored["content_snapshots"] == 1


async def test_an_archive_that_repeats_a_snapshot_restores_it_once(tmp_path: Path) -> None:
    """Two rows with one identifying key are one measurement, however they got there."""
    rows = sample_rows()
    rows["content_snapshots"] = rows["content_snapshots"] * 3

    info = await backup.create_backup(FakeSession(rows), output=tmp_path, secret_key=SECRET)
    target = FakeSession()
    report = await backup.restore_backup(target, info.path, secret_key=SECRET)

    assert report.restored["content_snapshots"] == 3
    assert len(target.inserted["content_snapshots"]) == 1


def test_the_snapshot_key_is_the_one_the_model_declares() -> None:
    """Drift is silent: a wrong key matches nothing and the rows append again."""
    from sqlalchemy import inspect

    from dtk.db.models import ContentSnapshot

    assert backup._NATURAL_KEY["content_snapshots"] == tuple(
        column.name for column in inspect(ContentSnapshot).primary_key
    )
    assert not ContentSnapshot.__table__.primary_key.columns, (
        "the table has a primary key now, and ON CONFLICT DO NOTHING covers it"
    )
