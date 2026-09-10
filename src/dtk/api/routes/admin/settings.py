"""Runtime configuration.

Doc 10's three-tier fallback - database, then environment, then the code
default - is visible in the response as ``source``, because the classic
confusion on a self-hosted tool is "I edited .env, restarted, and nothing
changed". Once a key is in the table it wins, and the console has to say so.

SENSITIVE keys are the reason this is not a plain key-value editor. The URL
allowlist is the only thing standing between this service and being an open
proxy, so widening it takes an administrator, an explicit ``confirm`` and an
audit row.

A setting can also *hold* a credential - ``notify.channels`` carries bot tokens,
signing secrets and an SMTP password - and this endpoint is reachable by a
viewer session and by any ``identity:manage`` key. So every value leaving here
is masked, on the response and in the audit row alike, and a masked value coming
back in means "keep the stored one" (:mod:`dtk.ops.masking`).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.schemas import SettingUpdate
from dtk.api.routes.support import audit, has_scope, iso, ok, read_admin
from dtk.core.config import RUNTIME_SETTINGS, SENSITIVE_KEYS, Scope, coerce
from dtk.core.errors import ForbiddenScope, InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, UserRole
from dtk.core.types import Scope as KeyScope
from dtk.db.models import Setting
from dtk.i18n.catalog import has as catalog_has
from dtk.i18n.catalog import t as translate
from dtk.ops.masking import redact_setting, unredact_setting
from dtk.services import settings_store

log = get_logger(__name__)

router = APIRouter(prefix="/settings")


def _env_name(key: str) -> str:
    return "DTK_" + key.replace(".", "_").upper()


def _source(key: str, stored: Setting | None) -> str:
    if stored is not None:
        return "database"
    if _env_name(key) in os.environ:
        return "environment"
    return "default"


def _description(key: str, spec: Any, language: Any) -> str:
    """The setting's help text in the caller's language.

    The registry's own ``description`` is a note for whoever reads config.py; the
    text a user reads lives in the catalogue with everything else the server
    renders. Falling back to the registry rather than to the humanized key means
    a setting added without a catalogue entry still explains itself in English
    instead of showing "Max wait seconds" back to the reader.
    """
    catalog_key = f"settings.description.{key}"
    if catalog_has(catalog_key, DEFAULT_LANGUAGE):
        return translate(catalog_key, language)
    return spec.description


async def _reload(request: Request) -> None:
    """Swap this process's snapshot immediately after a write.

    The watcher would get there on its own through pub/sub or the version poll,
    but a console that shows a stale value right after saving reads as a bug.
    The snapshot is replaced wholesale, never mutated (doc 10).
    """
    request.app.state.config = await settings_store.load_config()


@router.get("", summary="Read every runtime setting", openapi_extra={I18N_KEY: "settings_list"})
async def list_settings(request: Request, principal: Principal = Depends(read_admin)) -> Any:
    """Every runtime setting, with its current value and where that came from.

    Secrets are redacted. A value can come from the database, the environment
    or the built-in default, and the response says which - so a setting that
    refuses to change is explained rather than mysterious.

    **Returns**

    Each setting's key, current value, source, type, allowed choices where it
    is constrained, and a description in the requested language.
    """
    rows = {row.key: row for row in (await request.state.db.scalars(select(Setting))).all()}
    config = request.app.state.config
    items = []
    for key, spec in sorted(RUNTIME_SETTINGS.items()):
        stored = rows.get(key)
        value = config.get(key)
        redacted = redact_setting(value, spec.key)
        items.append(
            {
                "key": key,
                "value": redacted,
                # The console has to be able to say "this field is shown masked;
                # leave it alone and it keeps its value", because that is the
                # only way to tell it apart from a field the user must fill in.
                "masked": redacted != value,
                "default": spec.default,
                "scope": spec.scope.value,
                "type": spec.type_.__name__,
                # Present only for a setting with a fixed set of values, so the
                # console renders a choice instead of a free-text box that can
                # be saved with a typo the server will then reject.
                "choices": list(spec.choices) if spec.choices else None,
                "description": _description(key, spec, request.state.language),
                "sensitive": spec.scope is Scope.SENSITIVE,
                "source": _source(key, stored),
                "env_var": _env_name(key),
                "updated_at": iso(stored.updated_at) if stored else None,
                "updated_by": str(stored.updated_by) if stored and stored.updated_by else None,
            }
        )
    return ok(request, {"version": config.version, "settings": items})


@router.put(
    "/{key}", summary="Change one runtime setting", openapi_extra={I18N_KEY: "settings_update"}
)
async def update_setting(
    request: Request,
    body: SettingUpdate,
    key: str = Path(description="The setting to change, as shown by the list endpoint."),
    principal: Principal = Depends(read_admin),
) -> Any:
    """Change one runtime setting; it takes effect without a restart.

    The value is validated against the setting's type and, where it has one,
    its list of allowed choices. Settings that can disrupt a running instance
    require `confirm`.

    **Parameters**

    - `key` - the setting to change.
    - `value` - the new value, in the setting's own type.
    - `confirm` - required for settings flagged as disruptive.

    **Returns**

    The setting, with its new value and source.
    """
    spec = RUNTIME_SETTINGS.get(key)
    if spec is None:
        raise NotFound("no such setting")
    _authorize_write(principal, spec.scope, key, confirmed=body.confirm)

    previous = request.app.state.config.get(key)
    try:
        # Unmasked before coercion, so what is validated is what will be stored:
        # a value that arrived as a mask must not reach the table unchecked.
        value = coerce(key, unredact_setting(body.value, previous, key))
    except (ValueError, TypeError) as exc:
        # The message is replaced by a translated one for the caller's language,
        # so anything the operator needs in order to fix the value has to travel
        # in the details. For a constrained setting that means the valid values
        # themselves: "invalid" plus the Python type name would leave someone
        # guessing at the spelling of a mode they can see in a picker.
        raise InvalidParam(
            f"invalid value for {key}: {exc}",
            details={
                "field": "value",
                "expected": spec.type_.__name__,
                **({"choices": list(spec.choices)} if spec.choices else {}),
            },
        ) from exc

    await settings_store.set_value(key, value, updated_by=principal.user_id)
    await _reload(request)

    # Turning the demo switch has to do more than store a boolean: on, it mints
    # the account and the key and hands them back once; off, it drops the
    # sessions that are already open. Done after the reload so the new value is
    # what everything downstream reads, and returned in `demo` on this response
    # because there is no second chance to see the password.
    demo_payload = await _apply_demo_switch(request, key, value, principal)

    # AuditRepository.record: never a credential in detail. The audit trail is
    # read by humans, returned verbatim by GET /admin/audit and never trimmed by
    # retention, so a bot token written here outlives the channel it belongs to.
    change = {"from": redact_setting(previous, key), "to": redact_setting(value, key)}
    if spec.scope is Scope.SENSITIVE:
        # Doc 08 lists a SENSITIVE change beside importing an identity and
        # creating a key: the operations that widen the attack surface.
        await audit(
            request,
            principal,
            "settings.updated_sensitive",
            target_type="setting",
            target_id=key,
            detail=change,
        )
    else:
        await audit(
            request,
            principal,
            "settings.updated",
            target_type="setting",
            target_id=key,
            detail=change,
        )
    log.info("settings.updated", key=key, sensitive=spec.scope is Scope.SENSITIVE)
    payload: dict[str, Any] = {
        "key": key,
        "value": redact_setting(value, key),
        "version": request.app.state.config.version,
    }
    if demo_payload is not None:
        payload["demo"] = demo_payload
    return ok(request, payload)


async def _apply_demo_switch(
    request: Request, key: str, value: Any, principal: Principal
) -> dict[str, Any] | None:
    """Provision or withdraw the demo account when its switch moves.

    Returns the credentials on the transition to on, and None otherwise -
    including when the setting written was some other key, and including when
    the demo was already on and is being written on again, because reprovisioning
    on every save would invalidate a password the operator had already published.

    Nothing here raises into the caller's response. The setting is already
    stored and reloaded by the time this runs, so a failure to mint leaves the
    switch on with no account behind it - which the console shows as "on, no
    credentials" and an administrator fixes by rotating. Raising instead would
    report the whole update as failed while having applied half of it.
    """
    from dtk.api.routes.passwords import hash_password
    from dtk.services import demo

    if key != demo.SETTING_KEY:
        return None

    try:
        if bool(value):
            existing = await demo.describe(request.state.db)
            if existing is not None and existing.api_key_prefix:
                return existing.as_dict()
            credentials = await demo.provision(
                request.state.db, hash_password=hash_password, cipher=request.app.state.cipher
            )
            await audit(
                request,
                principal,
                "demo.provisioned",
                target_type="user",
                target_id=credentials.username,
                detail={"api_key_prefix": credentials.api_key_prefix},
            )
            return credentials.as_dict()

        dropped = await demo.end_sessions(request.state.db)
        await audit(
            request,
            principal,
            "demo.disabled",
            target_type="user",
            target_id=demo.DEMO_USERNAME,
            detail={"sessions_ended": dropped},
        )
        return None
    except Exception as exc:
        log.error("demo.switch_failed", error=f"{type(exc).__name__}: {exc}", enabled=bool(value))
        return None


@router.delete(
    "/{key}",
    summary="Reset one setting to its inherited value",
    openapi_extra={I18N_KEY: "settings_reset"},
)
async def reset_setting(
    request: Request,
    key: str = Path(description="The setting to reset, as shown by the list endpoint."),
    confirm: bool = Query(
        default=False, description="Required for settings flagged as disruptive."
    ),
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
        detail={
            "from": redact_setting(previous, key),
            "to": redact_setting(request.app.state.config.get(key), key),
        },
    )
    return ok(
        request,
        {
            "key": key,
            "value": redact_setting(request.app.state.config.get(key), key),
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
