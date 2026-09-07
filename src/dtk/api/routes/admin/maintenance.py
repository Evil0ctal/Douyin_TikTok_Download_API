"""Audit trail, diagnostics, notifications and backups.

The last three are queued rather than executed inline. A diagnosis dials every
proxy, a backup reads whole tables, and a notification test makes an outbound
request; none of that belongs on an HTTP handler's thread inside the container
that is supposed to stay responsive (doc 01, doc 15).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from dtk.api.deps import Principal
from dtk.api.routes import operations
from dtk.api.routes.operations import Maintenance
from dtk.api.routes.schemas import BackupRequest, DiagnoseRequest, NotificationTest
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
from dtk.core.logging import get_logger
from dtk.db.repositories import AuditRepository

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


__all__ = ["router"]
