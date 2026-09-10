"""Reading and rotating the public demo credentials.

The switch itself is a setting, so turning the demo on and off happens in
``PUT /api/v1/admin/settings/demo.enabled`` and not here. What is here is the
part a setting cannot express: showing an administrator what is currently
published, and replacing it when the password has been shared with the wrong
person or has simply been lost.

Both routes are administrators only. The demo account is the one credential on
this instance whose whole purpose is to be given away, which makes deciding who
may mint it a strictly higher bar than deciding who may read the identity pool.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.passwords import hash_password
from dtk.api.routes.support import admin_only, audit, ok
from dtk.core.logging import get_logger
from dtk.services import demo

log = get_logger(__name__)

router = APIRouter(prefix="/demo")


def _state(request: Request, credentials: demo.DemoCredentials | None) -> dict[str, Any]:
    """The demo's state, with whatever secrets the caller is entitled to.

    ``enabled`` and ``provisioned`` are separate because they really can
    disagree: a switch turned on while the database was unreachable leaves the
    first true and the second false, and the console needs to say so rather
    than show an account that is not there.
    """
    enabled = bool(request.app.state.config.get(demo.SETTING_KEY))
    payload: dict[str, Any] = {
        "enabled": enabled,
        "provisioned": credentials is not None,
        "login_path": "/login",
    }
    if credentials is not None:
        payload.update(credentials.as_dict())
    return payload


@router.get("", summary="Show the demo account", openapi_extra={I18N_KEY: "demo_show"})
async def show_demo(request: Request, principal: Principal = Depends(admin_only)) -> Any:
    """What this instance publishes as its demo, without the secrets.

    **Returns**

    Whether demo mode is on, whether an account exists, the account name, the
    API key prefix and the key's scopes. Never the password and never the key:
    neither is stored in a form this endpoint could return, so a lost one is
    rotated rather than looked up.
    """
    return ok(request, _state(request, await demo.describe(request.state.db)))


@router.post(
    "/rotate", summary="Reissue the demo credentials", openapi_extra={I18N_KEY: "demo_rotate"}
)
async def rotate_demo(request: Request, principal: Principal = Depends(admin_only)) -> Any:
    """Generate a new demo password and API key, replacing the current pair.

    The previous key is revoked rather than deleted, so the task and request
    rows that reference it keep meaning something. Every open demo session is
    ended too: the point of rotating is usually that the old password reached
    somebody it should not have, and leaving their session alive would make the
    rotation cosmetic.

    **Returns**

    The new password and the new API key, in the clear and for the only time.
    """
    credentials = await demo.provision(
        request.state.db, hash_password=hash_password, cipher=request.app.state.cipher
    )
    dropped = await demo.end_sessions(request.state.db)
    await audit(
        request,
        principal,
        "demo.rotated",
        target_type="user",
        target_id=credentials.username,
        detail={"api_key_prefix": credentials.api_key_prefix, "sessions_ended": dropped},
    )
    log.info("demo.rotated", prefix=credentials.api_key_prefix, sessions_ended=dropped)
    return ok(request, _state(request, credentials))
