"""Runtime configuration.

Doc 10's three-tier fallback - database, then environment, then the code
default - is visible in the response as ``source``, because the classic
confusion on a self-hosted tool is "I edited .env, restarted, and nothing
changed". Once a key is in the table it wins, and the console has to say so.

SENSITIVE keys are the reason this is not a plain key-value editor. The URL
allowlist is the only thing standing between this service and being an open
proxy, so widening it takes an administrator, an explicit ``confirm`` and an
audit row.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from dtk.api.deps import Principal
from dtk.api.routes.schemas import SettingUpdate
from dtk.api.routes.support import audit, has_scope, iso, ok, read_admin
from dtk.core.config import RUNTIME_SETTINGS, SENSITIVE_KEYS, Scope, coerce
from dtk.core.errors import ForbiddenScope, InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import Scope as KeyScope
from dtk.core.types import UserRole
from dtk.db.models import Setting
from dtk.services import settings_store

log = get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["admin"])


def _env_name(key: str) -> str:
    return "DTK_" + key.replace(".", "_").upper()


def _source(key: str, stored: Setting | None) -> str:
    if stored is not None:
        return "database"
    if _env_name(key) in os.environ:
        return "environment"
    return "default"


async def _reload(request: Request) -> None:
    """Swap this process's snapshot immediately after a write.

    The watcher would get there on its own through pub/sub or the version poll,
    but a console that shows a stale value right after saving reads as a bug.
    The snapshot is replaced wholesale, never mutated (doc 10).
    """
    request.app.state.config = await settings_store.load_config()


@router.get("", summary="Read every runtime setting")
async def list_settings(request: Request, principal: Principal = Depends(read_admin)) -> Any:
    rows = {row.key: row for row in (await request.state.db.scalars(select(Setting))).all()}
    config = request.app.state.config
    items = []
    for key, spec in sorted(RUNTIME_SETTINGS.items()):
        stored = rows.get(key)
        items.append(
            {
                "key": key,
                "value": config.get(key),
                "default": spec.default,
                "scope": spec.scope.value,
                "type": spec.type_.__name__,
                "description": spec.description,
                "sensitive": spec.scope is Scope.SENSITIVE,
                "source": _source(key, stored),
                "env_var": _env_name(key),
                "updated_at": iso(stored.updated_at) if stored else None,
                "updated_by": str(stored.updated_by) if stored and stored.updated_by else None,
            }
        )
    return ok(request, {"version": config.version, "settings": items})


@router.put("/{key}", summary="Change one runtime setting")
async def update_setting(
    request: Request,
    key: str,
    body: SettingUpdate,
    principal: Principal = Depends(read_admin),
) -> Any:
    spec = RUNTIME_SETTINGS.get(key)
    if spec is None:
        raise NotFound("no such setting")
    _authorize_write(principal, spec.scope, key, confirmed=body.confirm)

    try:
        value = coerce(key, body.value)
    except (ValueError, TypeError) as exc:
        raise InvalidParam(
            f"invalid value for {key}: {exc}",
            details={"field": "value", "expected": spec.type_.__name__},
        ) from exc

    previous = request.app.state.config.get(key)
    await settings_store.set_value(key, value, updated_by=principal.user_id)
    await _reload(request)

    if spec.scope is Scope.SENSITIVE:
        # Doc 08 lists a SENSITIVE change beside importing an identity and
        # creating a key: the operations that widen the attack surface.
        await audit(
            request,
            principal,
            "settings.updated_sensitive",
            target_type="setting",
            target_id=key,
            detail={"from": previous, "to": value},
        )
    else:
        await audit(
            request,
            principal,
            "settings.updated",
            target_type="setting",
            target_id=key,
            detail={"from": previous, "to": value},
        )
    log.info("settings.updated", key=key, sensitive=spec.scope is Scope.SENSITIVE)
    return ok(
        request,
        {"key": key, "value": value, "version": request.app.state.config.version},
    )


@router.delete("/{key}", summary="Reset one setting to its inherited value")
async def reset_setting(
    request: Request,
    key: str,
    confirm: bool = False,
    principal: Principal = Depends(read_admin),
) -> Any:
    """Delete the override so the key falls back to the environment or default."""
    spec = RUNTIME_SETTINGS.get(key)
    if spec is None:
        raise NotFound("no such setting")
    _authorize_write(principal, spec.scope, key, confirmed=confirm)

    previous = request.app.state.config.get(key)
    await settings_store.reset_value(key)
    await _reload(request)
    await audit(
        request,
        principal,
        "settings.reset",
        target_type="setting",
        target_id=key,
        detail={"from": previous, "to": request.app.state.config.get(key)},
    )
    return ok(
        request,
        {
            "key": key,
            "value": request.app.state.config.get(key),
            "source": _source(key, None),
            "version": request.app.state.config.version,
        },
    )


def _authorize_write(principal: Principal, scope: Scope, key: str, *, confirmed: bool) -> None:
    """Operators may tune, administrators may widen.

    Splitting on the setting's own scope rather than on the endpoint keeps the
    rule in one place: the same PUT is routine for a cache TTL and a security
    decision for the URL allowlist.

    A SENSITIVE key is bounded twice, by role *and* by scope. The endpoint is
    reached with ``identity:manage`` as well as with ``admin``, and the role
    alone would not stop an ``identity:manage`` key belonging to the
    administrator - which is nearly every key on a self-hosted box - from
    widening the URL allowlist, the one defence between this service and being
    an open proxy (doc 08).
    """
    if scope is not Scope.SENSITIVE:
        if principal.role is UserRole.VIEWER:
            raise ForbiddenScope(
                "changing settings requires an operator role",
                details={"required_role": UserRole.OPERATOR.value},
            )
        return
    if not has_scope(principal, (KeyScope.ADMIN,)):
        raise ForbiddenScope(
            "this credential lacks the scope required to change a sensitive setting",
            details={"key": key, "required": [KeyScope.ADMIN.value]},
        )
    if principal.role is not UserRole.ADMIN:
        raise ForbiddenScope(
            "this setting is sensitive and can only be changed by an administrator",
            details={"key": key, "required_role": UserRole.ADMIN.value},
        )
    if not confirmed:
        raise InvalidParam(
            "this setting widens the attack surface; resend with confirm=true",
            details={"field": "confirm", "key": key, "sensitive": True},
        )


__all__ = ["SENSITIVE_KEYS", "router"]
