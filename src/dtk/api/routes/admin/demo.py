"""Reading and rotating the public demo credentials.

The switch itself is a setting, so turning the demo on and off happens in
``PUT /api/v1/admin/settings/demo.enabled`` and not here. What is here is the
part a setting cannot express: showing an administrator what is currently
published, and replacing it when the password has been shared with the wrong
person or has simply been lost.

Reissuing is for administrators only. The demo account is the one credential on
this instance whose whole purpose is to be given away, which makes deciding who
may mint it a strictly higher bar than deciding who may read the identity pool.

Reading the state is not. It was administrator-only too, and the console's
demo card - on a page operators and viewers can open - took the refusal for
"off" and showed a demo that was running as switched off. What it returns holds
no secret: the account name is fixed, and the key's prefix and scopes are on
the API keys page, which every one of those roles already reads.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.passwords import hash_password
from dtk.api.routes.support import admin_only, audit, ok, read_admin
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
        "missing_scopes": _missing_scopes(credentials),
    }
    if credentials is not None:
        payload.update(credentials.as_dict())
    return payload


def _missing_scopes(credentials: demo.DemoCredentials | None) -> list[str]:
    """What this release would publish that the live key does not hold.

    A key is minted with the scopes of the release that minted it and an
    upgrade does not touch it, so after 5.1.6 a demo published before it went
    on reading Douyin and TikTok and refusing Bilibili, with nothing on the
    console to say why. Empty when there is no key to compare: "not
    provisioned" is its own state and says so already.
    """
    if credentials is None or not credentials.api_key_prefix:
        return []
    held = set(credentials.scopes)
    return sorted(scope.value for scope in demo.DEMO_KEY_SCOPES if scope.value not in held)


@router.get("", summary="Show the demo account", openapi_extra={I18N_KEY: "demo_show"})
async def show_demo(request: Request, principal: Principal = Depends(read_admin)) -> Any:
    """What this instance publishes as its demo, without the secrets.

    **Returns**

    Whether demo mode is on, whether an account exists, the account name, the
    API key prefix, the key's scopes and any scope this release publishes that
    the key does not hold yet. Never the password and never the key: this
    endpoint does not return them to anybody, so a lost one is rotated rather
    than looked up.
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
