"""API key administration.

The full key exists in exactly one response, the one that creates it. After
that only the prefix (for display) and the sha256 digest (for verification)
remain, so the service is unable to show it again even to an administrator
(doc 06).

Revocation takes effect on the next request: authentication reads the row every
time, so there is no cache to wait out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from dtk.api.deps import Principal
from dtk.api.routes.schemas import ApiKeyCreate
from dtk.api.routes.support import audit, iso, manage_pool, ok, read_admin
from dtk.core.crypto import new_api_key
from dtk.core.errors import InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.db.models import ApiKey
from dtk.db.repositories import ApiKeyRepository

log = get_logger(__name__)

router = APIRouter(prefix="/api-keys", tags=["admin"])


def _row(key: ApiKey) -> dict[str, Any]:
    """Everything about a key except the two things that authenticate it."""
    now = datetime.now(UTC)
    expired = key.expires_at is not None and key.expires_at <= now
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.prefix,
        "user_id": str(key.user_id),
        "scopes": list(key.scopes or []),
        "rate_limit": key.rate_limit,
        "expires_at": iso(key.expires_at),
        "revoked_at": iso(key.revoked_at),
        "last_used_at": iso(key.last_used_at),
        "created_at": iso(key.created_at),
        "status": "revoked" if key.revoked_at else ("expired" if expired else "active"),
    }


@router.get("", summary="List API keys")
async def list_keys(
    request: Request,
    mine_only: bool = Query(
        default=False, description="Only list keys you created, rather than everyone's."
    ),
    principal: Principal = Depends(read_admin),
) -> Any:
    """Every API key on this instance, newest first.

    Secrets are never returned - a key's value is shown once, when it is
    created, and cannot be read back afterwards.

    **Parameters**

    - `mine_only` - list only the keys you created.

    **Returns**

    Each key's name, scopes, owner, creation and last-used times, and whether
    it is still active.
    """
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
    if mine_only:
        stmt = stmt.where(ApiKey.user_id == principal.user_id)
    rows = (await request.state.db.scalars(stmt)).all()
    return ok(request, [_row(row) for row in rows])


@router.post("", summary="Create an API key")
async def create_key(
    request: Request,
    body: ApiKeyCreate,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Mint a key and show it once.

    The response carries ``key`` and a warning that it will not be shown again;
    the console has to put that in front of the user, because the only recovery
    from losing it is creating another one.
    """
    if body.expires_at is not None and body.expires_at <= datetime.now(UTC):
        raise InvalidParam(
            "expires_at must be in the future",
            details={"field": "expires_at"},
        )
    full, prefix, digest = new_api_key()
    key = await ApiKeyRepository(request.state.db).create(
        user_id=principal.user_id,
        name=body.name,
        prefix=prefix,
        key_hash=digest,
        scopes=[s.value for s in body.scopes],
        rate_limit=body.rate_limit,
        expires_at=body.expires_at,
    )
    await audit(
        request,
        principal,
        "api_key.created",
        target_type="api_key",
        target_id=str(key.id),
        detail={
            "name": body.name,
            "prefix": prefix,
            "scopes": [s.value for s in body.scopes],
            "rate_limit": body.rate_limit,
        },
    )
    log.info("api_key.created", key_id=str(key.id), prefix=prefix)
    return ok(
        request,
        {
            **_row(key),
            "key": full,
            "warning": "this is the only time the full key is shown; store it now",
        },
        status_code=201,
    )


@router.delete("/{key_id}", summary="Revoke an API key")
async def revoke_key(
    request: Request,
    key_id: uuid.UUID = Path(description="The key to revoke."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Revoke one API key.

    Takes effect immediately and cannot be undone; issue a new key instead of
    trying to restore this one.

    **Parameters**

    - `key_id` - the key to revoke.
    """
    keys = ApiKeyRepository(request.state.db)
    key = await keys.get(key_id)
    if key is None:
        raise NotFound("no such API key")
    revoked = await keys.revoke(key_id)
    await audit(
        request,
        principal,
        "api_key.revoked",
        target_type="api_key",
        target_id=str(key_id),
        detail={"prefix": key.prefix, "already_revoked": not revoked},
    )
    log.info("api_key.revoked", key_id=str(key_id), changed=revoked)
    return ok(request, {"id": str(key_id), "revoked": True})


__all__ = ["router"]
