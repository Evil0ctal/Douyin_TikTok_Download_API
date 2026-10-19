"""API key administration.

The full key exists in exactly one response, the one that creates it. After
that only the prefix (for display) and the sha256 digest (for verification)
remain, so the service is unable to show it again even to an administrator
(doc 06).

Revocation takes effect on the next request: authentication reads the row every
time, so there is no cache to wait out. The same is true of an edit: a key's
name and scopes can change in place, and its holder sees the new scopes on the
next call without being handed anything new.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.api.deps import Principal, demo_mode_on
from dtk.api.routes.openapi import CREATED_RESPONSES, I18N_KEY
from dtk.api.routes.schemas import ApiKeyCreate, ApiKeyUpdate
from dtk.api.routes.support import audit, iso, manage_pool, ok, read_admin_demo
from dtk.core.crypto import new_api_key
from dtk.core.errors import ForbiddenScope, InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Scope, UserRole
from dtk.db.models import ApiKey, User
from dtk.db.repositories import ApiKeyRepository
from dtk.services import demo

log = get_logger(__name__)

router = APIRouter(prefix="/api-keys")


def _row(key: ApiKey, *, secret: str | None = None, demo_owned: bool = False) -> dict[str, Any]:
    """Everything about a key except the two things that authenticate it.

    ``secret`` is the one exception and it is only ever non-None for the
    published demo key. It is not a permission this function checks: an
    operator's key has no readable plaintext anywhere in this system, so there
    is nothing a caller could be granted. See
    :mod:`dtk.db.migrations.versions.0009_demo_readable_credentials`.
    """
    now = datetime.now(UTC)
    expired = key.expires_at is not None and key.expires_at <= now
    return {
        # Present and non-null only for the demo key. `demo` also drives the
        # console's explanation of why this one is readable and the others are
        # not, so the page never has to infer it from the name.
        "demo": secret is not None,
        "secret": secret,
        # Whether the demo account owns it, switch on or off, live or revoked.
        # `demo` alone goes false the moment the demo is switched off, and the
        # console then offered to edit a key the server refuses to edit.
        "demo_owned": demo_owned,
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


@router.get("", summary="List API keys", openapi_extra={I18N_KEY: "api_keys_list"})
async def list_keys(
    request: Request,
    mine_only: bool = Query(
        default=False, description="Only list keys you created, rather than everyone's."
    ),
    principal: Principal = Depends(read_admin_demo),
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
    # The demo account reaches this page so a visitor can copy the published key
    # and try the API. It sees that key and no other: the rest of this table is
    # the operator's - names, scopes, rate limits, when each was last used -
    # which is inventory of somebody else's instance and none of a visitor's
    # business. Filtered in the query rather than in the renderer, so a row that
    # must not be seen is never loaded rather than merely not drawn.
    if principal.role is UserRole.DEMO:
        stmt = stmt.where(ApiKey.user_id == principal.user_id)
    rows = (await request.state.db.scalars(stmt)).all()

    # The demo key is shown in full while the demo is running. Two conditions,
    # both required: the row has to carry a decryptable plaintext, which only
    # the provisioner ever writes, and the demo has to be on - a key from a
    # demo that was switched off is listed like any other, masked.
    published: str | None = None
    if demo_mode_on(request):
        revealed = await demo.reveal(request.state.db, request.app.state.cipher)
        published = revealed.api_key if revealed else None

    account = await demo.find_user(request.state.db)

    def render(row: ApiKey) -> dict[str, Any]:
        readable = published if (published and row.demo_secret_encrypted) else None
        owned = account is not None and row.user_id == account.id
        return _row(row, secret=readable, demo_owned=owned)

    return ok(request, [render(row) for row in rows])


def _refuse_escalation(principal: Principal, requested: Sequence[Scope]) -> None:
    """A key may not mint scopes its creator does not hold.

    This route is guarded by ``manage_pool``, which admits ``identity:manage``
    as well as ``admin`` - and it did not look at the scopes being minted. So a
    key holding only ``identity:manage``, refused ``GET /admin/users`` and
    refused a write to ``api.public_endpoints``, could create a key with
    ``scopes: ["admin"]`` and use it to do both, one request apart. The guard
    was asking whether the caller may create keys, never whether it may create
    *this* key.

    A console session is bounded by its role rather than by scopes, and the
    guard above already requires at least OPERATOR, so a session is left to
    mint what its role allows. It is a credential minting a credential that has
    to be bounded by what the first one holds.
    """
    if principal.api_key_id is None:
        return
    excess = sorted(scope.value for scope in requested if scope not in principal.scopes)
    if excess:
        raise ForbiddenScope(
            "an API key cannot create a key with scopes it does not hold itself",
            details={"refused": excess, "held": sorted(s.value for s in principal.scopes)},
        )


@router.post(
    "",
    summary="Create an API key",
    openapi_extra={I18N_KEY: "api_keys_create", **CREATED_RESPONSES},
)
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
    _refuse_escalation(principal, body.scopes)
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


async def _is_published_demo_key(session: AsyncSession, key: ApiKey) -> bool:
    """Whether this row is the demo's key, live or from an earlier rotation.

    Two signals because either alone has a gap: only the provisioner writes the
    encrypted copy, but a key minted before that column existed has none, and
    the owner is the one fact every demo key shares.
    """
    if key.demo_secret_encrypted is not None:
        return True
    owner = await session.get(User, key.user_id)
    return owner is not None and owner.role == UserRole.DEMO.value


@router.patch(
    "/{key_id}",
    summary="Rename an API key or change its scopes",
    openapi_extra={I18N_KEY: "api_keys_update"},
)
async def update_key(
    request: Request,
    body: ApiKeyUpdate,
    key_id: uuid.UUID = Path(description="The key to change."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Change a key's name or scopes without changing the key itself.

    Whatever already holds the key keeps using it. That is the reason this
    exists: a release that adds a platform adds its read scope, and without an
    edit the only way to give an existing key that scope was to mint a new one
    and redistribute it to every script that used the old.

    Only the fields you send change. `scopes` replaces the list rather than
    adding to it, and is bounded by your own scopes exactly as creating a key
    is.

    Refused for a revoked key, which nothing can use again, and for the
    published demo key, whose scopes the demo itself decides - widening them
    here would publish more than a stranger is meant to get. Reissue the demo
    pair to bring that key up to date.

    **Parameters**

    - `key_id` - the key to change.
    - `name`, `scopes` - either or both.

    **Returns**

    The key as the list shows it.
    """
    if body.name is None and body.scopes is None:
        raise InvalidParam("nothing to update", details={"fields": ["name", "scopes"]})
    session = request.state.db
    key = await ApiKeyRepository(session).get(key_id)
    if key is None:
        raise NotFound("no such API key")
    if key.revoked_at is not None:
        raise InvalidParam(
            "a revoked key cannot be changed; create a new one",
            details={"field": "key_id", "status": "revoked"},
        )
    if await _is_published_demo_key(session, key):
        raise InvalidParam(
            "the demo key's scopes are set by the demo itself; reissue the demo pair instead",
            details={"field": "key_id", "setting": demo.SETTING_KEY},
        )
    if body.scopes is not None:
        _refuse_escalation(principal, body.scopes)

    before = {"name": key.name, "scopes": list(key.scopes or [])}
    changed: list[str] = []
    if body.name is not None and body.name != key.name:
        key.name = body.name
        changed.append("name")
    if body.scopes is not None:
        # Deduplicated, order kept: a list with a scope twice is a caller's
        # slip, and storing it would show the scope twice on every row after.
        scopes = list(dict.fromkeys(scope.value for scope in body.scopes))
        if sorted(scopes) != sorted(before["scopes"]):
            key.scopes = scopes
            changed.append("scopes")
    await session.flush()

    await audit(
        request,
        principal,
        "api_key.updated",
        target_type="api_key",
        target_id=str(key.id),
        detail={
            "prefix": key.prefix,
            "fields": changed,
            "from": before,
            "to": {"name": key.name, "scopes": list(key.scopes or [])},
        },
    )
    log.info("api_key.updated", key_id=str(key.id), fields=changed)
    return ok(request, _row(key))


@router.delete(
    "/{key_id}", summary="Revoke an API key", openapi_extra={I18N_KEY: "api_keys_revoke"}
)
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
