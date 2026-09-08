"""Audit trail, diagnostics, notifications and backups.

Everything but the audit trail and the archive listing is queued rather than
executed inline. A diagnosis dials every proxy, a backup reads whole tables, a
restore writes them, and a notification test makes an outbound request; none of
that belongs on an HTTP handler's thread inside the container that is supposed
to stay responsive (doc 01, doc 15).

The two backup endpoints that do answer inline read the archive directory the
worker writes into - the same directory, by way of a shared volume, because a
listing that looked somewhere else would offer a restore of files that are not
there. Neither of them opens a table, and both are careful about what an archive
is allowed to say about the host: a manifest fingerprints the master key, and
that fingerprint exists to confirm a guess at it, so it never leaves this
process (doc 08).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from dtk.api.deps import Principal
from dtk.api.routes import operations
from dtk.api.routes.operations import Maintenance
from dtk.api.routes.schemas import (
    BackupRequest,
    DiagnoseRequest,
    NotificationTest,
    RestoreRequest,
)
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    admin_only,
    audit,
    iso,
    manage_pool,
    ok,
    read_admin,
)
from dtk.core.config import BootstrapSettings
from dtk.core.errors import InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.db.repositories import AuditRepository
from dtk.ops.backup import (
    ARCHIVE_SUFFIX,
    BackupInfo,
    Manifest,
    SecretKeyMismatch,
    check_schema_version,
    list_backups,
    read_manifest,
    verify_secret_key,
)

log = get_logger(__name__)

router = APIRouter(tags=["admin"])


@router.get("/audit", summary="Read the audit trail")
async def list_audit(
    request: Request,
    limit: int = Query(default=DEFAULT_ADMIN_PAGE_SIZE, ge=1, le=MAX_ADMIN_PAGE_SIZE),
    action: str | None = Query(default=None, max_length=128),
    before: datetime | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(read_admin),
) -> Any:
    """Sensitive operations, newest first.

    Kept apart from ``request_log`` on purpose: this is the record of who
    changed what, and it must not be lost in the volume of ordinary traffic
    (doc 08).
    """
    rows = await AuditRepository(request.state.db).list_recent(
        limit=limit, action=action, before=before, user_id=user_id
    )
    return ok(
        request,
        [
            {
                "id": str(row.id),
                "ts": iso(row.ts),
                "action": row.action,
                "user_id": str(row.user_id) if row.user_id else None,
                "api_key_id": str(row.api_key_id) if row.api_key_id else None,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "detail": row.detail,
                "ip": row.ip,
                "user_agent": row.user_agent,
            }
            for row in rows
        ],
    )


@router.post("/diagnose", summary="Run the six-step self check")
async def diagnose(
    request: Request,
    body: DiagnoseRequest | None = None,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Queue the same diagnosis the setup wizard finishes with (doc 15).

    The report it produces is redacted at the source; proxy passwords, cookies
    and API keys never reach it, because its whole purpose is to be pasted into
    a public issue.
    """
    include_smoke = body.include_smoke_test if body is not None else True
    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.DIAGNOSE.value,
        params={"include_smoke_test": include_smoke},
        coalesce=False,
    )
    await audit(request, principal, "diagnose.requested", detail={"smoke_test": include_smoke})
    return ok(request, {"task_id": str(task_id)}, status_code=202)


@router.post("/notifications/test", summary="Send a test alert")
async def test_notification(
    request: Request,
    body: NotificationTest | None = None,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Prove the channel works before an incident depends on it."""
    channel = body.channel if body is not None else None
    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.NOTIFY_TEST.value,
        params={"channel": channel},
        coalesce=False,
    )
    await audit(
        request,
        principal,
        "notification.test_requested",
        target_type="channel",
        target_id=channel,
    )
    return ok(request, {"task_id": str(task_id)}, status_code=202)


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------


def _backup_dir(request: Request) -> Path:
    """Where this process expects to find archives.

    Taken from the settings the API booted with rather than from
    :func:`dtk.ops.backup.default_backup_dir`, which re-reads the environment on
    every call. The distinction matters for the key check below: an archive is
    verified against ``settings.secret_key``, and the two values have to come
    from one reading of the environment or a restore can be judged against a key
    the process does not actually encrypt with.
    """
    settings: BootstrapSettings = request.app.state.settings
    return Path(settings.backup_dir)


def _archive_path(directory: Path, name: str) -> Path:
    """Resolve one caller-supplied archive name inside the backup directory.

    The name comes from a browser and is about to be joined onto a filesystem
    path, so it must be a bare file name and the result is checked again after
    resolution - a symlink dropped into the backup volume would otherwise make
    this a read of any file the process can open.
    """
    # A NUL reaches the filesystem layer as a ValueError, which would surface as
    # a 500 rather than as the bad parameter it is.
    if "\x00" in name or name != Path(name).name or not name.endswith(ARCHIVE_SUFFIX):
        raise InvalidParam(
            "a backup is addressed by the file name the listing returns",
            details={"field": "path"},
        )
    resolved = (directory / name).resolve()
    if resolved.parent != directory.resolve():
        raise InvalidParam(
            "that name does not address a file in the backup directory",
            details={"field": "path"},
        )
    return resolved


def _key_matches(manifest: Manifest, secret_key: str) -> bool:
    """Whether this instance holds the key the archive was written under.

    One bit, and the only thing the key check may ever produce for a caller: the
    fingerprint itself is an HMAC of a fixed label under the derived key, so
    publishing it would let someone confirm a guess at DTK_SECRET_KEY offline.
    The bit is what the console actually needs - it says whether a restore can
    work before an administrator commits to one.
    """
    try:
        verify_secret_key(manifest, secret_key)
    except SecretKeyMismatch:
        return False
    return True


def _manifest_payload(manifest: Manifest, secret_key: str) -> dict[str, Any]:
    """Everything an archive says about itself except its key fingerprint."""
    return {
        "schema_version": manifest.schema_version,
        "created_at": iso(manifest.created_at),
        "dtk_version": manifest.dtk_version,
        "include_identities": manifest.include_identities,
        "contents": dict(manifest.contents),
        "key_matches": _key_matches(manifest, secret_key),
    }


def _reason(info: BackupInfo, directory: Path) -> str | None:
    """Why an archive could not be read, with the host's paths taken out.

    ``read_manifest`` wraps an OSError, and an OSError renders the absolute name
    it failed on. The file name is the part that identifies the archive to the
    reader; the directory around it describes the server's filesystem to anyone
    who can open the console, and doc 08 keeps that out of every response.
    """
    if info.error is None:
        return None
    named = info.error.replace(str(info.path), info.path.name)
    return named.replace(str(directory), "")


def _entry(info: BackupInfo, directory: Path, secret_key: str) -> dict[str, Any]:
    """One row of the listing, as ``BackupEntry`` in Backup.tsx."""
    return {
        # The file name only. It is what the console displays and what a restore
        # addresses, and it describes nothing outside the backup directory.
        "path": info.path.name,
        "size_bytes": info.size_bytes,
        "manifest": (
            _manifest_payload(info.manifest, secret_key) if info.manifest is not None else None
        ),
        "error": _reason(info, directory),
    }


@router.get("/backup", summary="List backup archives")
async def list_backup_archives(
    request: Request,
    principal: Principal = Depends(read_admin),
) -> Any:
    """Archives the server can see, newest first.

    An archive that cannot be parsed is listed with its reason rather than
    dropped, and one bad file never fails the listing: a corrupt archive is the
    single most important thing this page has to be able to show, because it is
    the reason someone opens it (doc 15).

    Read at the same level as the rest of the administrative surface, one step
    below creating and restoring. Nothing here is a credential - names, sizes,
    row counts and format versions - and an operator who cannot take a backup
    still has to be able to see whether last night's ran.
    """
    directory = _backup_dir(request)
    secret_key: str = request.app.state.settings.secret_key
    # Each archive is opened and its first member decompressed. That is small
    # per file and unbounded in the number of files, and this process is serving
    # other requests on the same loop.
    infos = await asyncio.to_thread(list_backups, directory)
    return ok(request, [_entry(info, directory, secret_key) for info in infos])


@router.post("/backup", summary="Create a backup archive")
async def create_backup(
    request: Request,
    body: BackupRequest | None = None,
    principal: Principal = Depends(admin_only),
) -> Any:
    """Queue an export.

    Identities are excluded unless asked for: they are bound to an egress and a
    fingerprint, so reviving them on another machine means using cookies from a
    new exit address, which is precisely what doc 02 forbids. Credentials are
    exported as ciphertext and the archive never contains the master key, so a
    leaked backup is not a leaked cookie jar (doc 15).
    """
    include_identities = body.include_identities if body is not None else False
    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.BACKUP.value,
        params={"include_identities": include_identities},
        coalesce=False,
    )
    await audit(
        request,
        principal,
        "backup.requested",
        detail={"include_identities": include_identities},
    )
    log.info("backup.requested", include_identities=include_identities)
    return ok(
        request,
        {
            "task_id": str(task_id),
            "include_identities": include_identities,
            "note": (
                "credentials are exported as ciphertext; restoring needs the same DTK_SECRET_KEY"
            ),
        },
        status_code=202,
    )


@router.post("/backup/restore", summary="Restore a backup archive")
async def restore_backup_archive(
    request: Request,
    body: RestoreRequest,
    principal: Principal = Depends(admin_only),
) -> Any:
    """Queue a restore of one archive.

    Administrators only, and confirmed: the archive carries users, API keys and
    settings, so restoring one can hand an account back its access, and nothing
    in the console undoes it. That is the same bar a SENSITIVE setting write
    clears in ``admin/settings.py`` - the role, the scope and an explicit
    ``confirm`` - and it leaves the same audit row behind. Rows that already
    exist are kept, so this fills the gaps in a live instance rather than
    reverting it.

    The archive is vetted here, before anything is queued, because all three
    ways it can be unusable are things the caller has to be told about while
    they are still looking at the dialog: no such file, a format this build
    cannot read, and - the one that surprises people - an archive written under
    a different DTK_SECRET_KEY, whose ciphertext this instance could store but
    never decrypt. The worker checks all three again; between queueing and
    running, a file can change.
    """
    directory = _backup_dir(request)
    settings: BootstrapSettings = request.app.state.settings
    archive = _archive_path(directory, body.path)
    # Refused before the archive is even opened, in the order settings.py uses
    # for a sensitive write: the confirmation is about the act, not about which
    # file it names.
    if not body.confirm:
        raise InvalidParam(
            "a restore writes archived rows into this instance and cannot be "
            "undone from the console; resend with confirm=true",
            details={"field": "confirm", "path": archive.name},
        )
    if not archive.is_file():
        raise NotFound("no such backup archive", details={"path": archive.name})

    manifest = await asyncio.to_thread(read_manifest, archive)
    check_schema_version(manifest)
    verify_secret_key(manifest, settings.secret_key)

    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.BACKUP_RESTORE.value,
        params={"path": archive.name},
        coalesce=False,
    )
    await audit(
        request,
        principal,
        "backup.restore_requested",
        target_type="backup",
        target_id=archive.name,
        detail={
            "created_at": iso(manifest.created_at),
            "dtk_version": manifest.dtk_version,
            "include_identities": manifest.include_identities,
            "rows": manifest.total_rows,
        },
    )
    log.info(
        "backup.restore_requested",
        path=archive.name,
        rows=manifest.total_rows,
        include_identities=manifest.include_identities,
    )
    return ok(
        request,
        {"task_id": str(task_id), "path": archive.name, "rows": manifest.total_rows},
        status_code=202,
    )


__all__ = ["router"]
