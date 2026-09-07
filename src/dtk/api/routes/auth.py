"""Console authentication: login, logout, profile, password, sessions.

Password login is the console's only entry point; scripts and agents use API
keys and never touch these routes. Two protections are specific to this file:

* failed logins are counted per username *and* per source address. Per username
  alone lets one attacker spray a thousand accounts from one host; per address
  alone lets a botnet grind one account. Both counters are abuse protection,
  not authorization - the address is unreliable under Docker (doc 06).
* changing a password logs the account out everywhere else, because the usual
  reason to change one is that it may have leaked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from dtk.api.deps import Principal
from dtk.api.routes import sessions
from dtk.api.routes.passwords import hash_password, needs_rehash, verify_password
from dtk.api.routes.schemas import LoginRequest, PasswordChange
from dtk.api.routes.support import (
    audit,
    authenticated,
    client_ip,
    iso,
    ok,
    user_agent,
)
from dtk.core.errors import InvalidParam, RateLimited, Unauthenticated
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.db.repositories import UserRepository

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

LOGIN_FAIL_USER_KEY = "login:fail:user:{username}"
LOGIN_FAIL_IP_KEY = "login:fail:ip:{ip}"

#: How long a failure counter lives, and therefore how long a lockout lasts.
LOGIN_LOCKOUT_SECONDS = 900

#: One account: a person mistypes a password a few times, a script does not.
MAX_FAILURES_PER_USERNAME = 5
#: One address: high enough to cover a shared NAT, low enough to stop spraying.
MAX_FAILURES_PER_IP = 20


def _user_payload(user: Any, principal: Principal | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }
    if principal is not None:
        payload["scopes"] = sorted(s.value for s in principal.scopes)
        payload["via"] = "api_key" if principal.api_key_id else "session"
    return payload


async def _check_lockout(username: str, ip: str | None) -> None:
    """Refuse before verifying anything when either counter is exhausted."""
    redis = get_redis()
    keys = [(LOGIN_FAIL_USER_KEY.format(username=username.lower()), MAX_FAILURES_PER_USERNAME)]
    if ip:
        keys.append((LOGIN_FAIL_IP_KEY.format(ip=ip), MAX_FAILURES_PER_IP))
    for key, ceiling in keys:
        raw = await redis.get(key)
        if raw is not None and int(raw) >= ceiling:
            ttl = await redis.ttl(key)
            log.warning("auth.login_locked", key_kind=key.split(":")[2], ttl=ttl)
            raise RateLimited(
                "too many failed login attempts; try again later",
                retry_after=max(1, ttl if ttl and ttl > 0 else LOGIN_LOCKOUT_SECONDS),
            )


async def _record_failure(username: str, ip: str | None) -> None:
    redis = get_redis()
    for key in (
        LOGIN_FAIL_USER_KEY.format(username=username.lower()),
        LOGIN_FAIL_IP_KEY.format(ip=ip) if ip else None,
    ):
        if key is None:
            continue
        used = await redis.incr(key)
        if used == 1:
            await redis.expire(key, LOGIN_LOCKOUT_SECONDS)


async def _clear_failures(username: str, ip: str | None) -> None:
    redis = get_redis()
    await redis.delete(LOGIN_FAIL_USER_KEY.format(username=username.lower()))
    if ip:
        await redis.delete(LOGIN_FAIL_IP_KEY.format(ip=ip))


@router.post("/login", summary="Exchange a password for a session cookie")
async def login(request: Request, body: LoginRequest) -> Any:
    session = request.state.db
    ip = client_ip(request)
    await _check_lockout(body.username, ip)

    users = UserRepository(session)
    user = await users.get_by_username(body.username)
    # Verify even when the account does not exist; the dummy digest keeps the
    # two paths indistinguishable in time.
    if not verify_password(user.password_hash if user else None, body.password):
        await _record_failure(body.username, ip)
        log.warning("auth.login_failed", username=body.username[:64], ip=ip)
        raise Unauthenticated("incorrect username or password")

    if user is None:  # unreachable: a missing account never verifies
        raise Unauthenticated("incorrect username or password")
    await _clear_failures(body.username, ip)
    if needs_rehash(user.password_hash):
        # Parameters moved on since this digest was written; upgrade it now
        # that the plaintext is briefly in hand.
        await users.set_password(user.id, hash_password(body.password))
    await users.touch_login(user.id)

    token = await sessions.create(
        user.id, ip=ip, user_agent=user_agent(request), ttl=sessions.SESSION_TTL_SECONDS
    )
    user.last_login_at = datetime.now(UTC)
    log.info("auth.login", user_id=str(user.id), role=user.role)

    response = ok(
        request,
        {"user": _user_payload(user), "expires_in": sessions.SESSION_TTL_SECONDS},
    )
    sessions.attach_cookie(response, request, token)
    return response


@router.post("/logout", summary="Revoke the current session")
async def logout(request: Request) -> Any:
    """Idempotent on purpose: logging out twice is not an error."""
    token = sessions.cookie_token(request)
    if token:
        await sessions.revoke(token)
    response = ok(request, {"logged_out": True})
    sessions.clear_cookie(response)
    return response


@router.get("/me", summary="The authenticated principal")
async def me(request: Request, principal: Principal = Depends(authenticated)) -> Any:
    user = await UserRepository(request.state.db).get(principal.user_id)
    if user is None:
        # The session outlived the account it names.
        raise Unauthenticated("the account for this credential no longer exists")
    token = sessions.cookie_token(request)
    if token:
        await sessions.touch(token)
    return ok(
        request,
        {
            "user": _user_payload(user, principal),
            "rate_limit_per_min": principal.rate_limit_per_min,
            "api_key_id": str(principal.api_key_id) if principal.api_key_id else None,
        },
    )


@router.post("/password", summary="Change your own password")
async def change_password(
    request: Request,
    body: PasswordChange,
    principal: Principal = Depends(authenticated),
) -> Any:
    """Requires the current password even inside an authenticated session.

    A stolen session should not be enough to take the account over, and an API
    key must not be able to change the owner's password at all.
    """
    if principal.api_key_id is not None:
        raise InvalidParam(
            "a password can only be changed from a console session, not with an API key",
            details={"field": "credential"},
        )
    session = request.state.db
    users = UserRepository(session)
    user = await users.get(principal.user_id)
    if user is None:
        raise Unauthenticated("the account for this session no longer exists")

    if not verify_password(user.password_hash, body.current_password):
        log.warning("auth.password_change_rejected", user_id=str(user.id))
        raise Unauthenticated("the current password is incorrect")
    if body.new_password == body.current_password:
        raise InvalidParam(
            "the new password must differ from the current one",
            details={"field": "new_password"},
        )

    await users.set_password(user.id, hash_password(body.new_password))
    current = sessions.cookie_token(request)
    revoked = await sessions.revoke_others(user.id, keep=current)
    await audit(
        request,
        principal,
        "user.password_changed",
        target_type="user",
        target_id=str(user.id),
        detail={"revoked_sessions": revoked},
    )
    log.info("auth.password_changed", user_id=str(user.id), revoked_sessions=revoked)
    return ok(request, {"changed": True, "revoked_sessions": revoked})


@router.get("/sessions", summary="List your live sessions")
async def list_sessions(request: Request, principal: Principal = Depends(authenticated)) -> Any:
    current = sessions.cookie_token(request)
    live = await sessions.list_for(principal.user_id, current=current)
    return ok(request, [s.as_dict() for s in live])


@router.delete("/sessions", summary="Log out every other device")
async def revoke_other_sessions(
    request: Request, principal: Principal = Depends(authenticated)
) -> Any:
    current = sessions.cookie_token(request)
    revoked = await sessions.revoke_others(principal.user_id, keep=current)
    await audit(
        request,
        principal,
        "user.sessions_revoked",
        target_type="user",
        target_id=str(principal.user_id),
        detail={"revoked": revoked, "kept_current": current is not None},
    )
    return ok(request, {"revoked": revoked})


@router.delete("/sessions/{session_ref}", summary="Revoke one of your sessions")
async def revoke_one_session(
    request: Request, session_ref: str, principal: Principal = Depends(authenticated)
) -> Any:
    """``session_ref`` is the digest from the list, never the token itself."""
    revoked = await sessions.revoke_by_id(principal.user_id, session_ref)
    if not revoked:
        raise InvalidParam(
            "no such session for this account",
            details={"field": "session_ref"},
        )
    return ok(request, {"revoked": 1})


__all__ = [
    "LOGIN_FAIL_IP_KEY",
    "LOGIN_FAIL_USER_KEY",
    "LOGIN_LOCKOUT_SECONDS",
    "MAX_FAILURES_PER_IP",
    "MAX_FAILURES_PER_USERNAME",
    "router",
]
