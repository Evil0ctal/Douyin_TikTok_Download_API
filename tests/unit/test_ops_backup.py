"""Backup and restore.

Two properties matter more than the row counts: ciphertext must survive the
round trip byte for byte, and a restore under the wrong ``DTK_SECRET_KEY`` must
stop before it writes anything. The second is what keeps a key mismatch from
producing a database full of credentials nobody can ever decrypt.
"""

from __future__ import annotations

import json
import tarfile
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


class FakeSession:
    """Serves rows for an exported SELECT and records restored INSERTs."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._rows = rows or {}
        self.inserted: dict[str, list[dict[str, Any]]] = {}
        self.statements: list[str] = []

    async def stream(self, statement: Select[Any]) -> _Stream:
        table = statement.get_final_froms()[0].name  # type: ignore[attr-defined]
        return _Stream(self._rows.get(table, []))

    async def execute(self, statement: Any, params: Any = None) -> None:
        if isinstance(statement, Insert):
            name = statement.table.name
            self.inserted.setdefault(name, []).extend(params or [])
            return None
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
