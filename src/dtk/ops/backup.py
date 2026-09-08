"""Backup, restore and listing.

``content_snapshots`` is months of accumulated user data and logged-in cookies
are expensive to obtain; without a backup, moving to another machine means
starting from zero (docs/design/15-operations.md).

Two rules shape everything here:

**Credentials are exported as ciphertext and the archive never holds the master
key.** Proxy URLs and cookie jars are copied byte for byte out of ``bytea``
columns; nothing is decrypted on the way out and nothing is re-encrypted on the
way in. Restoring therefore needs the same ``DTK_SECRET_KEY``. The manifest
carries an HMAC of the derived key - not the key, and not reversible into it -
so a mismatch is reported as a mismatch instead of quietly producing rows whose
credentials will never decrypt.

**Identities are excluded unless asked for.** An identity is a cookie jar bound
to one proxy and one egress IP (docs/design/02-identity-pool.md). After a move
the egress has changed, so those identities should not be reused anyway.
Excluding them by default is the correct semantics, not a shortcut.

The archive is a gzipped tar holding ``manifest.json`` and one JSON-lines file
per table under ``data/``. Restore reads members by exact name and never
extracts to disk, so a hand-edited archive cannot write outside its own bytes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import tarfile
import tempfile
import uuid
import zlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import DateTime, LargeBinary, Table, Uuid, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dtk import __version__
from dtk.core.crypto import derive_key
from dtk.core.errors import DtkError, ErrorCode
from dtk.core.logging import get_logger
from dtk.db.models import ApiKey, ContentSnapshot, Identity, Proxy, Setting, User

log = get_logger(__name__)

#: Format version of the archive itself, independent of the database schema.
#: Bumped when the layout changes in a way an older reader cannot handle.
BACKUP_SCHEMA_VERSION: Final[int] = 1

#: Versions this build can restore.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})

MANIFEST_NAME: Final[str] = "manifest.json"
DATA_DIR: Final[str] = "data"
ARCHIVE_SUFFIX: Final[str] = ".tar.gz"
FILENAME_PREFIX: Final[str] = "dtk-backup-"


#: Where archives go when no path is given. Relative, so a container that binds
#: a volume at ./backups gets them on the host without extra configuration.
#: It lives here rather than beside one caller because every writer and every
#: reader has to agree on it: an archive the CLI's ``backup list`` cannot find
#: is not a backup.
def default_backup_dir() -> Path:
    """Where archives live, from DTK_BACKUP_DIR.

    Read per call rather than frozen at import: the container sets it to a
    mounted volume because the image itself is read-only, and a module-level
    constant would bake in whatever the environment looked like when the first
    import happened - which, for a test that patches the environment, is the
    wrong answer.
    """
    from dtk.core.config import BootstrapSettings

    return Path(BootstrapSettings().backup_dir)


#: The checkout default. Callers that must honour DTK_BACKUP_DIR use
#: :func:`default_backup_dir` instead.
DEFAULT_BACKUP_DIR: Final[Path] = Path("backups")

#: Domain-separated label for the key check, so the value in a manifest can
#: never be replayed as any other HMAC this project computes.
_KEY_CHECK_LABEL: Final[bytes] = b"dtk.backup.key-check.v1"

#: Rows are inserted in this order so foreign keys resolve: a key needs its
#: user, an identity needs its proxy.
RESTORE_ORDER: Final[tuple[str, ...]] = (
    "users",
    "api_keys",
    "proxies",
    "identities",
    "settings",
    "content_snapshots",
)

#: Tables every backup carries (doc 15). ``settings_version`` is absent on
#: purpose: it is a change counter for the running processes, not user data.
DEFAULT_TABLES: Final[tuple[str, ...]] = (
    "users",
    "api_keys",
    "proxies",
    "settings",
    "content_snapshots",
)

#: Only exported with ``--include-identities``.
OPTIONAL_TABLES: Final[tuple[str, ...]] = ("identities",)

#: Never exported. ``request_log`` and ``identity_events`` are operational
#: history that means nothing on another machine; ``tasks`` are in-flight work;
#: ``audit_log`` records actions taken against the instance being replaced.
EXCLUDED_TABLES: Final[tuple[str, ...]] = (
    "request_log",
    "identity_events",
    "tasks",
    "audit_log",
    "settings_version",
)

_TABLES: Final[Mapping[str, Table]] = {
    "users": cast(Table, User.__table__),
    "api_keys": cast(Table, ApiKey.__table__),
    "proxies": cast(Table, Proxy.__table__),
    "identities": cast(Table, Identity.__table__),
    "settings": cast(Table, Setting.__table__),
    "content_snapshots": cast(Table, ContentSnapshot.__table__),
}

#: Rows written per INSERT during a restore.
INSERT_BATCH = 500

#: Marker wrapping base64 for a ``bytea`` column, so ciphertext survives JSON
#: without being mistaken for text.
BYTES_MARKER: Final[str] = "__bytes__"


class BackupError(DtkError):
    """The archive cannot be used. Always a caller-facing, actionable fault."""

    code = ErrorCode.INVALID_PARAM


class ManifestInvalid(BackupError):
    """No manifest, or one that is not a manifest."""


class SchemaVersionUnsupported(BackupError):
    """The archive was written by a version this build cannot read."""


class SecretKeyMismatch(BackupError):
    """DTK_SECRET_KEY differs from the one the backup was taken with."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """What the archive says about itself."""

    schema_version: int
    created_at: datetime
    dtk_version: str
    include_identities: bool
    key_check: str
    contents: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
            "dtk_version": self.dtk_version,
            "include_identities": self.include_identities,
            "key_check": self.key_check,
            "contents": dict(self.contents),
        }

    @property
    def total_rows(self) -> int:
        return sum(self.contents.values())


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """One archive on disk, as ``dtk backup list`` shows it."""

    path: Path
    size_bytes: int
    manifest: Manifest | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "manifest": self.manifest.as_dict() if self.manifest else None,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RestoreReport:
    """What a restore actually put back."""

    manifest: Manifest
    restored: dict[str, int]
    skipped: tuple[str, ...] = ()

    @property
    def total_rows(self) -> int:
        return sum(self.restored.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.as_dict(),
            "restored": dict(self.restored),
            "skipped": list(self.skipped),
            "total_rows": self.total_rows,
        }


# ---------------------------------------------------------------------------
# Key binding
# ---------------------------------------------------------------------------


def key_check(secret_key: str) -> str:
    """Fingerprint of the master key, safe to store in a plaintext manifest.

    An HMAC of a fixed label under the derived key. It proves two keys are the
    same without carrying any material that helps decrypt the ciphertext beside
    it, which is what makes a leaked archive useless on its own.
    """
    return hmac.new(derive_key(secret_key), _KEY_CHECK_LABEL, hashlib.sha256).hexdigest()


def verify_secret_key(manifest: Manifest, secret_key: str) -> None:
    """Raise :class:`SecretKeyMismatch` unless the key matches the archive."""
    # Compared as bytes: ``compare_digest`` raises TypeError on a non-ASCII
    # string, and ``key_check`` comes out of a manifest a user may have edited.
    # A hand-mangled archive has to be a reported mismatch, not a crash.
    if not hmac.compare_digest(
        manifest.key_check.encode("utf-8", "surrogatepass"),
        key_check(secret_key).encode("ascii"),
    ):
        raise SecretKeyMismatch(
            "this backup was created with a different DTK_SECRET_KEY; restoring "
            "with the current key would leave every proxy URL and cookie jar "
            "undecryptable. Restore with the original key, or start fresh.",
            details={"created_at": manifest.created_at.isoformat()},
        )


# ---------------------------------------------------------------------------
# Row encoding
# ---------------------------------------------------------------------------


def _encode_value(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return {BYTES_MARKER: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def encode_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe representation of one database row."""
    return {key: _encode_value(value) for key, value in row.items()}


def decode_row(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    """Turn one decoded JSON object back into column values.

    Typing is driven by the table definition rather than by guessing from the
    JSON, because asyncpg refuses a string where a ``uuid`` or ``timestamptz``
    is declared and would fail mid-restore instead of at the first row.
    """
    decoded: dict[str, Any] = {}
    for name, value in row.items():
        column = table.columns.get(name)
        if column is None:
            # A column dropped since the archive was written. Ignoring it keeps
            # an older backup restorable; the alternative is a hard failure the
            # user cannot act on.
            continue
        decoded[name] = _decode_value(column.type, value)
    return decoded


def _decode_value(column_type: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping) and BYTES_MARKER in value:
        return base64.b64decode(str(value[BYTES_MARKER]))
    if isinstance(column_type, LargeBinary) and isinstance(value, str):
        return base64.b64decode(value)
    if isinstance(column_type, Uuid) and isinstance(value, str):
        return uuid.UUID(value)
    if isinstance(column_type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


# ---------------------------------------------------------------------------
# Archive IO
# ---------------------------------------------------------------------------


def default_filename(created_at: datetime | None = None) -> str:
    stamp = (created_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{FILENAME_PREFIX}{stamp}{ARCHIVE_SUFFIX}"


def build_manifest(
    *,
    contents: Mapping[str, int],
    include_identities: bool,
    secret_key: str,
    created_at: datetime | None = None,
) -> Manifest:
    return Manifest(
        schema_version=BACKUP_SCHEMA_VERSION,
        created_at=created_at or datetime.now(UTC),
        dtk_version=__version__,
        include_identities=include_identities,
        key_check=key_check(secret_key),
        contents=dict(contents),
    )


def write_archive(
    path: str | Path,
    manifest: Manifest,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Path:
    """Write a complete archive from rows already in memory.

    Used by the tests and by any caller that already holds the data;
    :func:`create_backup` streams from the database instead so a large
    ``content_snapshots`` never has to fit in memory.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        _add_bytes(archive, MANIFEST_NAME, _manifest_bytes(manifest))
        for name, rows in tables.items():
            payload = "".join(
                json.dumps(encode_row(row), ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            )
            _add_bytes(archive, f"{DATA_DIR}/{name}.jsonl", payload.encode("utf-8"))
    return target


def read_manifest(path: str | Path) -> Manifest:
    """Read and validate the manifest of an archive."""
    try:
        raw = _first_member_bytes(Path(path), MANIFEST_NAME)
    except _UNREADABLE as exc:
        # EOFError and zlib.error are the shapes a truncated gzip takes, and
        # neither is a TarError or an OSError. Left uncaught they reached the
        # generic exception handler and answered INTERNAL, so an archive the
        # page exists to warn about was the one thing it could not describe.
        raise ManifestInvalid(f"not a readable backup archive: {exc}") from exc
    if raw is None:
        raise ManifestInvalid(f"archive has no {MANIFEST_NAME}")
    return parse_manifest(raw)


#: Everything a damaged or truncated archive can raise on the way to its first
#: member. gzip signals truncation with EOFError and corruption with
#: zlib.error; neither inherits from TarError or OSError.
_UNREADABLE: Final[tuple[type[BaseException], ...]] = (
    tarfile.TarError,
    OSError,
    EOFError,
    zlib.error,
)


def _first_member_bytes(path: Path, name: str) -> bytes | None:
    """Read one named member without decompressing the whole archive.

    ``tarfile.open(..., "r:gz")`` supports seeking, and gzip does not: seeking
    forward decompresses and discards, so reading a 220-byte manifest cost a
    full pass over the archive - about a fifth of a second per uncompressed
    gigabyte, per file, on every ten-second poll of the Backup page. The stream
    mode ("r|gz") reads forward only and stops at the member, which is the first
    one written.
    """
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if member.name != name:
                continue
            handle = archive.extractfile(member)
            return handle.read() if handle is not None else None
    return None


def parse_manifest(raw: bytes) -> Manifest:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestInvalid(f"{MANIFEST_NAME} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ManifestInvalid(f"{MANIFEST_NAME} must hold a JSON object")

    try:
        schema_version = int(document["schema_version"])
        created_at = datetime.fromisoformat(str(document["created_at"]))
        key_value = str(document["key_check"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestInvalid(f"{MANIFEST_NAME} is missing required fields: {exc}") from exc

    contents = document.get("contents") or {}
    if not isinstance(contents, dict):
        raise ManifestInvalid("manifest 'contents' must be an object")

    return Manifest(
        schema_version=schema_version,
        created_at=created_at,
        dtk_version=str(document.get("dtk_version") or "unknown"),
        include_identities=bool(document.get("include_identities", False)),
        key_check=key_value,
        contents={str(k): int(v) for k, v in contents.items()},
    )


def check_schema_version(manifest: Manifest) -> None:
    if manifest.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SchemaVersionUnsupported(
            f"backup format version {manifest.schema_version} cannot be read by "
            f"dtk {__version__}; supported versions are "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            details={"schema_version": manifest.schema_version},
        )


def read_table(path: str | Path, table: str) -> list[dict[str, Any]]:
    """Every row an archive holds for one table."""
    return list(iter_table(path, table))


def read_member(path: str | Path, table: str) -> bytes | None:
    """Raw JSON-lines bytes an archive holds for one table, or None.

    Random access, unlike the manifest read: the tables are pulled in
    RESTORE_ORDER, which is not the order they were written, so a forward-only
    stream cannot serve it.

    A damaged archive raises here rather than escaping as an EOFError from
    deep inside gzip. Restore validates the manifest first, and this is the
    point where the *rest* of the file turns out to be unreadable - the caller
    needs a coded error it can report, not a 500 halfway through a restore.
    """
    try:
        with tarfile.open(Path(path), "r:gz") as archive:
            return _member_bytes(archive, f"{DATA_DIR}/{table}.jsonl")
    except _UNREADABLE as exc:
        raise ManifestInvalid(
            f"the archive is damaged and {table} could not be read: {exc}"
        ) from exc


def parse_rows(raw: bytes | None, table: str) -> Iterator[dict[str, Any]]:
    """Decode JSON-lines bytes into rows, naming the line that is malformed."""
    if raw is None:
        return
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BackupError(f"{table}.jsonl line {number} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise BackupError(f"{table}.jsonl line {number} is not an object")
        yield row


def iter_table(path: str | Path, table: str) -> Iterator[dict[str, Any]]:
    """Stream the rows an archive holds for one table."""
    yield from parse_rows(read_member(path, table), table)


def list_backups(directory: str | Path) -> list[BackupInfo]:
    """Archives in a directory, newest first. Unreadable ones are listed too."""
    root = Path(directory)
    if not root.is_dir():
        return []
    found: list[BackupInfo] = []
    for path in sorted(root.glob(f"*{ARCHIVE_SUFFIX}")):
        size = path.stat().st_size
        try:
            found.append(BackupInfo(path=path, size_bytes=size, manifest=read_manifest(path)))
        except BackupError as exc:
            # A corrupt archive is exactly what the user needs to be told
            # about, so it is listed with its reason rather than hidden.
            found.append(BackupInfo(path=path, size_bytes=size, error=str(exc)))
    found.sort(
        key=lambda info: (
            info.manifest.created_at if info.manifest else datetime.min.replace(tzinfo=UTC)
        ),
        reverse=True,
    )
    return found


# ---------------------------------------------------------------------------
# Database side
# ---------------------------------------------------------------------------


def tables_for(include_identities: bool) -> tuple[str, ...]:
    """Which tables a backup with these options carries."""
    names = list(DEFAULT_TABLES)
    if include_identities:
        names.extend(OPTIONAL_TABLES)
    return tuple(name for name in RESTORE_ORDER if name in names)


async def create_backup(
    session: AsyncSession,
    *,
    output: str | Path,
    secret_key: str,
    include_identities: bool = False,
    created_at: datetime | None = None,
) -> BackupInfo:
    """Export the database into a new archive.

    ``output`` may be a directory - a timestamped filename is generated inside
    it - or the full path of the archive to write.
    """
    target = Path(output)
    if target.is_dir() or not target.name.endswith(ARCHIVE_SUFFIX):
        target = target / default_filename(created_at)
    target.parent.mkdir(parents=True, exist_ok=True)

    selected = tables_for(include_identities)
    contents: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix="dtk-backup-") as scratch:
        staging = Path(scratch)
        for name in selected:
            contents[name] = await _dump_table(session, name, staging / f"{name}.jsonl")

        manifest = build_manifest(
            contents=contents,
            include_identities=include_identities,
            secret_key=secret_key,
            created_at=created_at,
        )
        # gzip over a multi-gigabyte content_snapshots is seconds of pure CPU.
        # On the event loop that stalls every request the process is serving,
        # so the packing runs on a worker thread.
        await asyncio.to_thread(_pack_archive, target, manifest, staging, selected)

    log.info(
        "ops.backup.created",
        path=str(target),
        rows=manifest.total_rows,
        include_identities=include_identities,
    )
    return BackupInfo(path=target, size_bytes=target.stat().st_size, manifest=manifest)


def _pack_archive(target: Path, manifest: Manifest, staging: Path, selected: Sequence[str]) -> None:
    """Write the finished archive from the staged JSON-lines files.

    Written beside the target and renamed into place. Gzipping a multi-gigabyte
    database takes minutes, and writing straight to the final name left a
    truncated .tar.gz sitting in the directory for that whole time - which the
    console's Backup page polls every ten seconds, and which raises EOFError
    rather than a tar error, so taking a backup broke the page that takes
    backups. A rename within one directory is atomic, so a reader sees the file
    either absent or complete.
    """
    partial = target.with_name(f".{target.name}.partial")
    try:
        with tarfile.open(partial, "w:gz") as archive:
            _add_bytes(archive, MANIFEST_NAME, _manifest_bytes(manifest))
            for name in selected:
                archive.add(staging / f"{name}.jsonl", arcname=f"{DATA_DIR}/{name}.jsonl")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


async def _dump_table(session: AsyncSession, name: str, destination: Path) -> int:
    table = _TABLES[name]
    written = 0
    with destination.open("w", encoding="utf-8") as handle:
        result = await session.stream(select(table))
        async for row in result.mappings():
            handle.write(json.dumps(encode_row(dict(row)), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            written += 1
    return written


async def restore_backup(
    session: AsyncSession,
    path: str | Path,
    *,
    secret_key: str,
) -> RestoreReport:
    """Load an archive back into the database.

    Validated before a single row is written: the format version must be one
    this build understands and the master key must be the one the archive was
    taken with. Existing rows win - a restore adds what is missing rather than
    overwriting an instance that is already in use.
    """
    archive_path = Path(path)
    manifest = await asyncio.to_thread(read_manifest, archive_path)
    check_schema_version(manifest)
    verify_secret_key(manifest, secret_key)

    restored: dict[str, int] = {}
    skipped: list[str] = []
    for name in RESTORE_ORDER:
        if name not in manifest.contents:
            skipped.append(name)
            continue
        restored[name] = await _load_table(session, archive_path, name)

    if restored.get("settings"):
        # Every process polls this counter to notice a configuration change;
        # without a bump the restored settings would sit unread until restart.
        # Upserted rather than updated: the singleton row is seeded by the first
        # migration, and a bare UPDATE against a database where it is missing
        # touches nothing and reports success, which is the one failure mode
        # this statement exists to prevent.
        await session.execute(
            text(
                "INSERT INTO settings_version (id, version) VALUES (1, 1) "
                "ON CONFLICT (id) DO UPDATE SET version = settings_version.version + 1"
            )
        )

    log.info(
        "ops.backup.restored",
        path=str(archive_path),
        rows=sum(restored.values()),
        tables=len(restored),
    )
    return RestoreReport(manifest=manifest, restored=restored, skipped=tuple(skipped))


async def _load_table(session: AsyncSession, path: Path, name: str) -> int:
    table = _TABLES[name]
    batch: list[dict[str, Any]] = []
    written = 0
    # Same reason as the pack side: gunzip is CPU, and it does not belong on
    # the event loop of a process that is still serving requests.
    raw = await asyncio.to_thread(read_member, path, name)
    for row in parse_rows(raw, name):
        batch.append(decode_row(table, row))
        if len(batch) >= INSERT_BATCH:
            written += await _insert(session, table, batch)
            batch = []
    if batch:
        written += await _insert(session, table, batch)
    return written


async def _insert(session: AsyncSession, table: Table, rows: list[dict[str, Any]]) -> int:
    # ON CONFLICT DO NOTHING without a target is valid for a table with no
    # unique index at all, which is what the append-only hypertables are.
    await session.execute(pg_insert(table).on_conflict_do_nothing(), rows)
    return len(rows)


# ---------------------------------------------------------------------------
# tar helpers
# ---------------------------------------------------------------------------


def _manifest_bytes(manifest: Manifest) -> bytes:
    return json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2).encode("utf-8")


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = int(datetime.now(UTC).timestamp())
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(payload))


def _member_bytes(archive: tarfile.TarFile, name: str) -> bytes | None:
    """Read one member by exact name.

    Names are matched, never joined onto a filesystem path, so an archive
    carrying ``../../etc/passwd`` is simply a member nobody asks for.
    """
    try:
        member = archive.getmember(name)
    except KeyError:
        return None
    if not member.isfile():
        return None
    handle = archive.extractfile(member)
    if handle is None:
        return None
    with handle:
        return handle.read()


__all__ = [
    "ARCHIVE_SUFFIX",
    "BACKUP_SCHEMA_VERSION",
    "DEFAULT_BACKUP_DIR",
    "DEFAULT_TABLES",
    "EXCLUDED_TABLES",
    "MANIFEST_NAME",
    "OPTIONAL_TABLES",
    "RESTORE_ORDER",
    "SUPPORTED_SCHEMA_VERSIONS",
    "BackupError",
    "BackupInfo",
    "Manifest",
    "ManifestInvalid",
    "RestoreReport",
    "SchemaVersionUnsupported",
    "SecretKeyMismatch",
    "build_manifest",
    "check_schema_version",
    "create_backup",
    "decode_row",
    "default_backup_dir",
    "default_filename",
    "encode_row",
    "iter_table",
    "key_check",
    "list_backups",
    "parse_manifest",
    "parse_rows",
    "read_manifest",
    "read_member",
    "read_table",
    "restore_backup",
    "tables_for",
    "verify_secret_key",
    "write_archive",
]
