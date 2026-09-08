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
credentials will never decrypt. ``settings`` is the exception that has to be
made to fit: an alert channel keeps its bot token, signing secret and SMTP
password as plain JSON in the database, so those fields are encrypted here on
the way out and decrypted on the way back in (:func:`seal_setting`). Without
that, the sentence above would be false for the one table an operator is most
likely to hand to someone else.

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
import os
import shutil
import tarfile
import tempfile
import time
import uuid
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from cryptography.exceptions import InvalidTag
from sqlalchemy import DateTime, LargeBinary, Table, Uuid, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dtk import __version__
from dtk.core.crypto import Cipher, derive_key
from dtk.core.errors import DtkError, ErrorCode
from dtk.core.logging import get_logger
from dtk.db.models import ApiKey, ContentSnapshot, Identity, Proxy, Setting, User
from dtk.ops.masking import is_credential_field

log = get_logger(__name__)

#: Format version of the archive itself, independent of the database schema.
#: Bumped when the layout changes in a way an older reader cannot handle.
#:
#: 2 encrypts the credential fields of a setting's value. A build that predates
#: it would restore the ciphertext markers as if they were the credentials and
#: leave every alert channel quietly misconfigured, so it must refuse instead.
BACKUP_SCHEMA_VERSION: Final[int] = 2

#: Versions this build can restore. A version 1 archive holds those fields in
#: clear and is read exactly as it was written.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1, 2})

MANIFEST_NAME: Final[str] = "manifest.json"
DATA_DIR: Final[str] = "data"
ARCHIVE_SUFFIX: Final[str] = ".tar.gz"
FILENAME_PREFIX: Final[str] = "dtk-backup-"

#: Names the staging directory a backup dumps its tables into. The leading dot
#: and the absent ``.tar.gz`` keep one out of :func:`list_backups`, which is the
#: only reason the shape matters: the directory sits in the backup directory
#: itself, next to the archives it is about to become.
STAGING_PREFIX: Final[str] = ".dtk-staging-"

#: How long a staging directory may sit before it is taken for abandoned.
#: Generous by design - deleting one out from under a backup that is still
#: writing it fails that backup - and no backup runs for a day.
STAGING_MAX_AGE_SECONDS: Final[float] = 24 * 60 * 60

#: Mode of the archive and of every member inside it. The archive carries cookie
#: jars and proxy URLs as ciphertext, but ``settings`` holds alert-channel
#: webhooks in the clear, so neither the file nor a hand-extracted copy of it may
#: be readable by other accounts on the host.
ARCHIVE_MODE: Final[int] = 0o600


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

#: Identifying columns of a table the database cannot deduplicate for us.
#: ``content_snapshots`` is a hypertable and carries no unique index - one would
#: have to include the partitioning column and would cost the ingest path a write
#: on every parse - so ``ON CONFLICT DO NOTHING`` matches nothing there and
#: restoring the same archive twice appended every row a second time, which the
#: history chart plots twice and then truncates against its row limit. These are
#: the columns the ORM declares as the mapper-level primary key
#: (:class:`dtk.db.models.ContentSnapshot`); the first is the partitioning
#: column, which is what makes the lookup cheap.
_NATURAL_KEY: Final[Mapping[str, tuple[str, ...]]] = {
    "content_snapshots": ("ts", "platform", "content_type", "content_id"),
}

#: Marker wrapping base64 for a ``bytea`` column, so ciphertext survives JSON
#: without being mistaken for text.
BYTES_MARKER: Final[str] = "__bytes__"

#: The same, for a credential encrypted inside a setting's JSON value. Shaped
#: like BYTES_MARKER so the two read alike in an archive somebody opens by hand.
SECRET_MARKER: Final[str] = "__secret__"


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


def seal_setting(cipher: Cipher, key: str, value: Any) -> Any:
    """Encrypt every credential field of one setting's value.

    The field names are the ones the console and the CLI already mask on
    (:mod:`dtk.ops.masking`), so a value hidden on screen is a value encrypted
    in the archive. The setting's key is bound in as additional data, which
    stops a ciphertext being moved to another setting, and the result is still
    JSON, so the archive stays readable by everything that reads archives.
    """

    def convert(item: Any) -> Any:
        if not isinstance(item, str):
            return _walk_credentials(item, convert)
        blob = cipher.encrypt(item, aad=_setting_aad(key))
        return {SECRET_MARKER: base64.b64encode(blob).decode("ascii")}

    return _walk_credentials(value, convert)


def open_setting(cipher: Cipher, key: str, value: Any) -> Any:
    """Undo :func:`seal_setting`, leaving a version 1 archive as it was.

    Only a marker is decrypted, so a plaintext value from an older archive
    passes through untouched and the restore needs no version test of its own.
    """

    def convert(item: Any) -> Any:
        if not (isinstance(item, Mapping) and SECRET_MARKER in item):
            return _walk_credentials(item, convert)
        try:
            payload = base64.b64decode(str(item[SECRET_MARKER]), validate=True)
            return cipher.decrypt(payload, aad=_setting_aad(key))
        except (InvalidTag, ValueError, TypeError) as exc:
            # The key was verified against the manifest before any of this ran,
            # so a failure here is the archive having been altered, not the
            # operator having the wrong key - and it must not be reported as
            # the latter.
            raise BackupError(
                f"a credential in setting '{key}' could not be decrypted; the "
                "archive has been altered since it was written"
            ) from exc

    return _walk_credentials(value, convert)


def _setting_aad(key: str) -> str:
    return f"setting:{key}"


def _walk_credentials(value: Any, convert: Callable[[Any], Any]) -> Any:
    """Apply ``convert`` to every credential-named field, at any depth."""
    if isinstance(value, Mapping):
        return {
            field: convert(item)
            if is_credential_field(str(field))
            else _walk_credentials(item, convert)
            for field, item in value.items()
        }
    if isinstance(value, list):
        return [_walk_credentials(item, convert) for item in value]
    return value


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
    with _new_archive(target) as archive:
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

    _sweep_stale_staging(target.parent)
    # Staged beside the archive rather than in the default temp dir. In the
    # shipped container every filesystem but /tmp and the backup volume is
    # read-only, and /tmp is a 64 MB tmpfs - RAM - so a content_snapshots of any
    # age filled it and the backup died on ENOSPC blaming a filesystem the
    # operator had never configured. The backup directory is writable, is sized
    # for the archive it is about to hold, and is where the .partial goes too.
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=STAGING_PREFIX) as scratch:
        staging = Path(scratch)
        for name in selected:
            contents[name] = await _dump_table(
                session, name, staging / f"{name}.jsonl", secret_key=secret_key
            )

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
        with _new_archive(partial) as archive:
            _add_bytes(archive, MANIFEST_NAME, _manifest_bytes(manifest))
            for name in selected:
                archive.add(
                    staging / f"{name}.jsonl",
                    arcname=f"{DATA_DIR}/{name}.jsonl",
                    filter=_staged_member,
                )
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def _sweep_stale_staging(directory: Path) -> None:
    """Delete staging directories a killed backup left in the backup directory.

    Staging used to land in /tmp, where the tmpfs cleared it on the next
    container start. Here nothing clears it, and an abandoned one holds a full
    copy of every exported table on the volume the archives need - so the
    cheapest housekeeping happens at the one moment we know a backup is starting.
    Failures are logged and not raised: this is not what the caller asked for.
    """
    cutoff = time.time() - STAGING_MAX_AGE_SECONDS
    for path in directory.glob(f"{STAGING_PREFIX}*"):
        try:
            if path.is_symlink() or not path.is_dir() or path.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(path)
        except OSError as exc:
            log.warning("ops.backup.staging_sweep_failed", error=f"{type(exc).__name__}: {exc}")
            continue
        log.info("ops.backup.staging_removed", name=path.name)


async def _dump_table(
    session: AsyncSession, name: str, destination: Path, *, secret_key: str
) -> int:
    table = _TABLES[name]
    # The only table whose credentials are plaintext in the database, and so the
    # only one with anything to encrypt on the way out.
    cipher = Cipher(secret_key) if name == "settings" else None
    written = 0
    with destination.open("w", encoding="utf-8") as handle:
        result = await session.stream(select(table))
        async for row in result.mappings():
            record = dict(row)
            if cipher is not None:
                key = str(record.get("key", ""))
                record["value"] = seal_setting(cipher, key, record.get("value"))
            handle.write(json.dumps(encode_row(record), ensure_ascii=False, sort_keys=True))
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
        restored[name] = await _load_table(session, archive_path, name, secret_key=secret_key)

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


async def _load_table(session: AsyncSession, path: Path, name: str, *, secret_key: str) -> int:
    table = _TABLES[name]
    cipher = Cipher(secret_key) if name == "settings" else None
    batch: list[dict[str, Any]] = []
    written = 0
    # Same reason as the pack side: gunzip is CPU, and it does not belong on
    # the event loop of a process that is still serving requests.
    raw = await asyncio.to_thread(read_member, path, name)
    for row in parse_rows(raw, name):
        if cipher is not None:
            key = str(row.get("key", ""))
            row = {**row, "value": open_setting(cipher, key, row.get("value"))}
        batch.append(decode_row(table, row))
        if len(batch) >= INSERT_BATCH:
            written += await _insert(session, table, batch)
            batch = []
    if batch:
        written += await _insert(session, table, batch)
    return written


async def _insert(session: AsyncSession, table: Table, rows: list[dict[str, Any]]) -> int:
    """Write one batch, keeping whatever the database already holds.

    Returns what the archive offered rather than what was new. For the tables
    with a primary key the database does not report the difference, and a count
    that means "read" for five tables and "written" for the sixth would be worse
    than one that means the same thing everywhere (dtk.worker.ops.restore).
    """
    offered = len(rows)
    natural_key = _NATURAL_KEY.get(table.name)
    if natural_key is not None:
        rows = await _without_duplicates(session, table, natural_key, rows)
    if rows:
        # DO NOTHING without a target covers every unique index the table has,
        # which for the tables that reach this line is the primary key.
        await session.execute(pg_insert(table).on_conflict_do_nothing(), rows)
    return offered


async def _without_duplicates(
    session: AsyncSession,
    table: Table,
    natural_key: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop the rows of a batch the table already holds, and its own repeats.

    Both halves happen: the table holds them when the same archive is restored
    twice, and the batch repeats itself when the archive does. Rows written by
    an earlier batch of this restore are visible to the lookup - the whole
    restore is one transaction - so a repeat that straddles two batches is
    caught by the first half rather than the second.
    """
    columns = [table.columns[name] for name in natural_key]
    # Narrowed on the partitioning column alone, and matched in full here. A
    # duplicate necessarily shares that column, so this returns a superset of
    # what has to go; a plain IN over one column is also the shape TimescaleDB
    # excludes whole chunks on, which a row-wise comparison is not.
    partition = natural_key[0]
    probe = select(*columns).where(columns[0].in_({row.get(partition) for row in rows}))
    seen = {tuple(found) for found in (await session.execute(probe)).all()}

    keep: list[dict[str, Any]] = []
    for row in rows:
        # .get, not [..]: a column an older archive never carried is missing
        # here, and a row that cannot state its key cannot match one either.
        key = tuple(row.get(name) for name in natural_key)
        if key in seen:
            continue
        seen.add(key)
        keep.append(row)
    return keep


# ---------------------------------------------------------------------------
# tar helpers
# ---------------------------------------------------------------------------


def _manifest_bytes(manifest: Manifest) -> bytes:
    return json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2).encode("utf-8")


@contextmanager
def _new_archive(path: Path) -> Iterator[tarfile.TarFile]:
    """Open a new archive at ``path``, unreadable to other accounts from byte one.

    The mode is set as the file is created rather than chmod'ed afterwards
    because gzipping a large database takes minutes, and for all of them the
    file would otherwise be sitting there at whatever the umask allowed - 0644
    on a stock host, for a file holding cookie jars and webhook URLs.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, ARCHIVE_MODE)
    with os.fdopen(descriptor, "wb") as raw:
        # O_CREAT's mode applies only to a file it creates, and the .partial a
        # killed backup left behind is exactly the file this reopens.
        os.fchmod(raw.fileno(), ARCHIVE_MODE)
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            yield archive


def _staged_member(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Header for a staged file, with the host taken back out of it.

    ``TarFile.add`` copies the mode the umask happened to give the staged file
    and the account that ran the backup. The mode puts every exported cookie jar
    at 0644 the moment someone unpacks the archive by hand, and the owner names
    describe the machine to whoever reads the file next.
    """
    info.mode = ARCHIVE_MODE
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = int(datetime.now(UTC).timestamp())
    info.mode = ARCHIVE_MODE
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
    "SECRET_MARKER",
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
    "open_setting",
    "parse_manifest",
    "parse_rows",
    "read_manifest",
    "read_member",
    "read_table",
    "restore_backup",
    "seal_setting",
    "tables_for",
    "verify_secret_key",
    "write_archive",
]
