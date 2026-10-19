"""User and role administration.

Doc 15 defines three roles and this is where they are handed out, so every
route here is administrator-only. Two guards exist because a self-hosted
instance has no support desk to undo a mistake:

* the last administrator cannot be demoted or deleted - an instance with no
  administrator can only be repaired from a shell inside the container;
* deleting or re-keying an account revokes its sessions, so a removed user does
  not keep a working console tab.

A third covers the one account that is not an administrator's to shape: the
demo account belongs to ``demo.enabled``, and nothing here changes it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy import func, select

from dtk.api.deps import Principal
from dtk.api.routes import sessions
from dtk.api.routes.openapi import CREATED_RESPONSES, I18N_KEY
from dtk.api.routes.passwords import hash_password
from dtk.api.routes.schemas import UserCreate, UserUpdate
from dtk.api.routes.support import admin_only, audit, iso, ok
from dtk.core.errors import InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import UserRole
from dtk.db.models import User
from dtk.db.repositories import UserRepository
from dtk.services import demo

log = get_logger(__name__)

router = APIRouter(prefix="/users")


def _row(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }


async def _admin_count(request: Request, *, excluding: uuid.UUID | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(User.role == UserRole.ADMIN.value)
    if excluding is not None:
        stmt = stmt.where(User.id != excluding)
    return int((await request.state.db.execute(stmt)).scalar_one())


@router.get("", summary="List console accounts", openapi_extra={I18N_KEY: "users_list"})
async def list_users(request: Request, principal: Principal = Depends(admin_only)) -> Any:
    """Every console account.

    Password hashes are never returned.

    **Returns**

    Each account's id, username, role and when it was created.
    """
    users = await UserRepository(request.state.db).list_all()
    return ok(request, [_row(user) for user in users])


def _refuse_hand_made_demo(role: UserRole | None) -> None:
    """The demo role is provisioned, never assigned.

    `demo.enabled` mints exactly one demo account with a generated password and
    a key scoped to the two platform reads, and the console shows that pair
    once. An administrator hand-making a second one would get an account with a
    password of their choosing and no key, break the "there is one of each"
    invariant that `dtk.services.demo.find_user` relies on, and produce a login
    that looks like the published demo and is not.
    """
    if role is not None and role is UserRole.DEMO:
        raise InvalidParam(
            "the demo account is created by turning on demo.enabled, not here",
            details={"field": "role", "setting": "demo.enabled"},
        )


def _refuse_demo_account_change(user: User, body: UserUpdate) -> None:
    """The demo account's role and password belong to its switch.

    Only *assigning* the demo role was refused, so the reverse went through: the
    Users page saves a role the moment the picker changes, and one stray click
    made the account whose password the login page prints for strangers an
    administrator. Its password is no better a target. The login page and the
    API keys page show a stored copy of it, which a reset here would leave
    behind - the published password would stop working and the page would go on
    printing it. Reissuing the pair keeps both copies in step.
    """
    if user.role != UserRole.DEMO.value:
        return
    if body.role is not None:
        raise InvalidParam(
            "the demo account's role is fixed; it is managed by demo.enabled",
            details={"field": "role", "setting": demo.SETTING_KEY},
        )
    if body.password is not None:
        raise InvalidParam(
            "the demo password is published; reissue the demo pair instead of resetting it",
            details={"field": "password", "setting": demo.SETTING_KEY},
        )


def _refuse_demo_account_delete(user: User) -> None:
    """Turning the switch off withdraws the demo; deleting it only breaks it.

    With the row gone the switch would still read on, every lookup of "the
    demo account" would find nothing, and the next rotation would mint a new
    one - so deleting it achieves less than switching it off and loses the
    audit trail of which key made which call.
    """
    if user.role == UserRole.DEMO.value:
        raise InvalidParam(
            "the demo account is withdrawn by turning demo.enabled off, not deleted",
            details={"field": "user_id", "setting": demo.SETTING_KEY},
        )


@router.post(
    "",
    summary="Create a console account",
    openapi_extra={I18N_KEY: "users_create", **CREATED_RESPONSES},
)
async def create_user(
    request: Request,
    body: UserCreate,
    principal: Principal = Depends(admin_only),
) -> Any:
    """Create a console account.

    The password is checked against the instance's strength policy and stored
    hashed. Usernames are unique.

    **Parameters**

    - `username` - the new account name.
    - `password` - its password.
    - `role` - what the account may do.

    **Returns**

    The created account, without its password.
    """
    _refuse_hand_made_demo(body.role)
    users = UserRepository(request.state.db)
    if await users.get_by_username(body.username) is not None:
        raise InvalidParam(
            "that username is already taken",
            details={"field": "username"},
        )
    user = await users.create(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    await audit(
        request,
        principal,
        "user.created",
        target_type="user",
        target_id=str(user.id),
        detail={"username": user.username, "role": user.role},
    )
    log.info("user.created", user_id=str(user.id), role=user.role)
    return ok(request, _row(user), status_code=201)


@router.put(
    "/{user_id}",
    summary="Change a role or reset a password",
    openapi_extra={I18N_KEY: "users_update"},
)
async def update_user(
    request: Request,
    body: UserUpdate,
    user_id: uuid.UUID = Path(description="The account to change."),
    principal: Principal = Depends(admin_only),
) -> Any:
    """The password reset an administrator performs for someone else.

    Changing your own password goes through ``/api/v1/auth/password``, which
    demands the current one; that check is meaningless here and would only
    stop an administrator from helping a user who forgot theirs.

    The demo account is refused both: its role is fixed and its password is
    reissued with the demo pair, never set by hand.
    """
    _refuse_hand_made_demo(body.role)
    session = request.state.db
    users = UserRepository(session)
    user = await users.get(user_id)
    if user is None:
        raise NotFound("no such user")
    if body.role is None and body.password is None:
        raise InvalidParam("nothing to update", details={"fields": ["role", "password"]})
    _refuse_demo_account_change(user, body)

    changed: list[str] = []
    if body.role is not None and body.role.value != user.role:
        if (
            user.role == UserRole.ADMIN.value
            and body.role is not UserRole.ADMIN
            and await _admin_count(request, excluding=user.id) == 0
        ):
            raise InvalidParam(
                "this is the last administrator; promote another account first",
                details={"field": "role"},
            )
        await users.set_role(user_id, body.role)
        changed.append("role")

    revoked = 0
    if body.password is not None:
        await users.set_password(user_id, hash_password(body.password))
        # A reset password means the old one is no longer trusted; the sessions
        # it opened should not outlive it.
        revoked = await sessions.revoke_others(user_id, keep=None)
        changed.append("password")

    await audit(
        request,
        principal,
        "user.updated",
        target_type="user",
        target_id=str(user_id),
        detail={"fields": changed, "revoked_sessions": revoked},
    )
    refreshed = await users.get(user_id)
    return ok(request, {**_row(refreshed or user), "revoked_sessions": revoked})


@router.delete(
    "/{user_id}", summary="Delete a console account", openapi_extra={I18N_KEY: "users_delete"}
)
async def delete_user(
    request: Request,
    user_id: uuid.UUID = Path(description="The account to delete."),
    principal: Principal = Depends(admin_only),
) -> Any:
    """Delete a console account and revoke its sessions.

    You cannot delete the account you are signed in with, the last remaining
    administrator, or the demo account - turn `demo.enabled` off to withdraw
    that one.

    **Parameters**

    - `user_id` - the account to delete.
    """
    if user_id == principal.user_id:
        raise InvalidParam(
            "you cannot delete the account you are signed in with",
            details={"field": "user_id"},
        )
    users = UserRepository(request.state.db)
    user = await users.get(user_id)
    if user is None:
        raise NotFound("no such user")
    _refuse_demo_account_delete(user)
    if user.role == UserRole.ADMIN.value and await _admin_count(request, excluding=user_id) == 0:
        raise InvalidParam(
            "this is the last administrator; an instance without one can only be "
            "repaired from a shell",
            details={"field": "user_id"},
        )

    revoked = await sessions.revoke_others(user_id, keep=None)
    await users.delete(user_id)
    await audit(
        request,
        principal,
        "user.deleted",
        target_type="user",
        target_id=str(user_id),
        detail={"username": user.username, "revoked_sessions": revoked},
    )
    log.info("user.deleted", user_id=str(user_id), revoked_sessions=revoked)
    return ok(request, {"id": str(user_id), "deleted": True, "revoked_sessions": revoked})


__all__ = ["router"]
