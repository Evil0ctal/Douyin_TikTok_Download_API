"""Console authentication: login, logout, profile, password, sessions.

Password login is the console's only entry point; scripts and agents use API
keys and never touch these routes. Two protections are specific to this file:

* failed logins are counted per username *and* per source address. Per username
  alone lets one attacker spray a thousand accounts from one host; per address
  alone lets a botnet grind one account. Both counters are abuse protection,
  not authorization - the address is unreliable under Docker (doc 06), so the
  address counter refuses a login only where the deployment has made the
  address mean something and is otherwise an alert. A counter that a stranger
  can aim at the operator is a denial of service wearing a lockout's clothes.
* changing a password logs the account out everywhere else, because the usual
  reason to change one is that it may have leaked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Request

from dtk.api.deps import Principal, demo_mode_on
from dtk.api.routes import sessions
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.passwords import hash_password, needs_rehash, verify_password
from dtk.api.routes.schemas import LoginRequest, PasswordChange
from dtk.api.routes.support import (
    audit,
    authenticated,
    client_ip,
    client_ip_is_trustworthy,
    demo_read,
    iso,
    ok,
    user_agent,
)
from dtk.core.errors import InvalidParam, NotFound, RateLimited, Unauthenticated
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import UserRole
from dtk.db.repositories import UserRepository
from dtk.services import demo

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

LOGIN_FAIL_USER_KEY = "login:fail:user:{username}"
LOGIN_FAIL_IP_KEY = "login:fail:ip:{ip}"

#: How long a failure counter lives, and therefore how long a lockout lasts.
LOGIN_LOCKOUT_SECONDS = 900

#: One account: a person mistypes a password a few times, a script does not.
MAX_FAILURES_PER_USERNAME = 5
#: One address: high enough to cover a shared NAT, low enough to stop spraying.
#: Only ever enforced against an address that names one caller; see
#: :func:`_check_lockout`.
MAX_FAILURES_PER_IP = 20

#: Failures across every account, in a short window. Not a lockout: refusing
#: globally is the same denial of service the per-address counter was softened
#: to avoid, just wearing a different hat. It buys a delay instead.
LOGIN_SPRAY_KEY = "login:fail:all"
LOGIN_SPRAY_WINDOW_SECONDS = 60
#: Above this many failures in a window, every further attempt waits before its
#: password is verified.
SPRAY_THRESHOLD = 30
#: How long that wait grows to. Chosen against what it costs each side: a person
#: who mistyped waits once and gets in; an attacker's throughput from one
#: address drops by two orders of magnitude, and the wait is bounded so a flood
#: cannot pin every worker on a sleep.
SPRAY_DELAY_SECONDS = 2.0

#: Password verifications that may run at once. argon2id at these parameters is
#: ~30ms of CPU each by design, so an unauthenticated endpoint that starts one
#: per request is a CPU-exhaustion channel. Queueing costs a legitimate login
#: nothing - there is one operator - and costs a flood everything.
_VERIFY_SLOTS = asyncio.Semaphore(4)


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


async def _refuse_if_exhausted(key: str, ceiling: int) -> None:
    redis = get_redis()
    raw = await redis.get(key)
    if raw is None or int(raw) < ceiling:
        return
    ttl = await redis.ttl(key)
    log.warning("auth.login_locked", key_kind=key.split(":")[2], ttl=ttl)
    raise RateLimited(
        "too many failed login attempts; try again later",
        retry_after=max(1, ttl if ttl and ttl > 0 else LOGIN_LOCKOUT_SECONDS),
    )


async def _check_lockout(username: str, ip: str | None) -> None:
    """Refuse before verifying anything when a counter that binds is exhausted.

    The username counter always binds: it costs an attacker the account they
    are guessing at, and nothing else.

    The address counter binds only when the address identifies one caller. In
    the default topology it does not - behind a TLS terminator, or behind
    Docker's published-port userland proxy, every login on earth shares one
    peer address - and refusing there means twenty junk attempts from a
    stranger shut the only administrator out of their own console for fifteen
    minutes, repeatably. Where the address is that coarse the counter still
    runs, but as an alert: it says an attack is in progress, and the
    per-username ceiling is what actually stops the guessing.
    """
    await _refuse_if_exhausted(
        LOGIN_FAIL_USER_KEY.format(username=username.lower()), MAX_FAILURES_PER_USERNAME
    )
    if not ip:
        return
    key = LOGIN_FAIL_IP_KEY.format(ip=ip)
    if client_ip_is_trustworthy():
        await _refuse_if_exhausted(key, MAX_FAILURES_PER_IP)
        return
    raw = await get_redis().get(key)
    if raw is not None and int(raw) >= MAX_FAILURES_PER_IP:
        # Loud rather than silent: this is the operator's only sign that
        # someone is spraying, and the hint is what turns it back into a block.
        log.warning(
            "auth.login_spray_suspected",
            failures=int(raw),
            ip=ip,
            enforced=False,
            hint="set DTK_FORWARDED_ALLOW_IPS behind a reverse proxy to enforce per-address limits",
        )


async def _slow_down_a_spray() -> None:
    """Wait, when failures across all accounts say someone is guessing.

    A delay rather than a refusal, and global rather than per-address, because
    the two counters above cannot cover this case: the username counter binds
    one account at a time, and the address counter does not bind at all in the
    default topology. Without something here, an unauthenticated caller can try
    an unbounded number of passwords across usernames, each one costing a real
    argon2 verification.

    Refusing instead would hand a stranger the operator's console back, which is
    the failure this whole path was rewritten to remove.
    """
    try:
        failures = int(await get_redis().get(LOGIN_SPRAY_KEY) or 0)
    except Exception as exc:  # Redis is down; a login must still be possible.
        log.warning("auth.spray_check_failed", error=f"{type(exc).__name__}: {exc}")
        return
    if failures < SPRAY_THRESHOLD:
        return
    log.warning("auth.login_spray_throttled", failures=failures)
    await asyncio.sleep(SPRAY_DELAY_SECONDS)


async def _record_failure(username: str, ip: str | None) -> None:
    redis = get_redis()
    # The global window is incremented separately: it has its own, much shorter
    # expiry, because it measures a rate rather than remembering a lockout.
    try:
        spray = await redis.incr(LOGIN_SPRAY_KEY)
        if spray == 1:
            await redis.expire(LOGIN_SPRAY_KEY, LOGIN_SPRAY_WINDOW_SECONDS)
    except Exception as exc:
        log.warning("auth.spray_record_failed", error=f"{type(exc).__name__}: {exc}")
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


@router.post(
    "/login",
    summary="Exchange a password for a session cookie",
    openapi_extra={I18N_KEY: "auth_login"},
)
async def login(request: Request, body: LoginRequest) -> Any:
    """Sign in to the console and receive a session cookie.

    For programs, prefer an API key over this endpoint: a key carries scopes
    and can be revoked on its own. Repeated failures are throttled, and too
    many will lock the account out for a while.

    **Parameters**

    - `username` - the console account name.
    - `password` - that account's password.

    **Returns**

    The signed-in principal, plus a `Set-Cookie` holding the session.
    """
    session = request.state.db
    ip = client_ip(request)
    await _check_lockout(body.username, ip)

    await _slow_down_a_spray()

    users = UserRepository(session)
    user = await users.get_by_username(body.username)
    # Verify even when the account does not exist; the dummy digest keeps the
    # two paths indistinguishable in time. Behind a semaphore, because the
    # verification is deliberately expensive and this endpoint is open.
    async with _VERIFY_SLOTS:
        verified = await asyncio.to_thread(
            verify_password, user.password_hash if user else None, body.password
        )
    if not verified:
        await _record_failure(body.username, ip)
        log.warning("auth.login_failed", username=body.username[:64], ip=ip)
        raise Unauthenticated("incorrect username or password")

    if user is None:  # unreachable: a missing account never verifies
        raise Unauthenticated("incorrect username or password")

    # The demo account exists whether or not the demo is running, so the switch
    # is what decides. Answered as a plain credential failure rather than
    # "demo mode is off": the password is published in a README, so a truthful
    # message here would tell anyone who read it that the account is real and
    # waiting - which is a thing to say on the login page, not to a caller
    # holding a working password.
    if UserRole(user.role) is UserRole.DEMO and not demo_mode_on(request):
        log.info("auth.login_refused_demo_off", username=body.username[:64], ip=ip)
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


@router.get(
    "/demo",
    summary="The demo credentials, for the login page",
    openapi_extra={I18N_KEY: "auth_demo"},
)
async def demo_credentials(request: Request) -> Any:
    """What to prefill the login form with, when this instance runs a demo.

    Unauthenticated, because the login page is: a visitor who has to sign in to
    learn how to sign in has not been given a demo. That is safe only because of
    what it can return, and the boundaries are worth stating plainly.

    It returns exactly one account's password - the one whose whole purpose is
    to be published - and only while ``demo.enabled`` is on. With the demo off
    it answers ``enabled: false`` and nothing else, so an instance that has
    never run a demo, or has stopped, leaks nothing and looks the same as one
    that was never built with the feature.

    There is no other account this endpoint can name. It resolves the demo user
    by role, and the role cannot be assigned by hand
    (:func:`dtk.api.routes.admin.users._refuse_hand_made_demo`), so there is no
    way to make it print somebody else's password by creating a user.

    **Returns**

    ``enabled``, and when it is true the username and password to prefill.
    """
    if not demo_mode_on(request):
        return ok(request, {"enabled": False})
    credentials = await demo.reveal(request.state.db, request.app.state.cipher)
    if credentials is None or not credentials.password:
        # Enabled but not provisioned, or provisioned before a key rotation.
        # Reported as "on, nothing to prefill" so the console can still show the
        # demo banner and let somebody type credentials they were given.
        return ok(request, {"enabled": True, "username": None, "password": None})
    return ok(
        request,
        {
            "enabled": True,
            "username": credentials.username,
            "password": credentials.password,
        },
    )


@router.post(
    "/logout", summary="Revoke the current session", openapi_extra={I18N_KEY: "auth_logout"}
)
async def logout(request: Request) -> Any:
    """Idempotent on purpose: logging out twice is not an error."""
    token = sessions.cookie_token(request)
    if token:
        await sessions.revoke(token)
    response = ok(request, {"logged_out": True})
    sessions.clear_cookie(response)
    return response


@router.get("/me", summary="The authenticated principal", openapi_extra={I18N_KEY: "auth_me"})
async def me(request: Request, principal: Principal = Depends(demo_read)) -> Any:
    """Who the current credential belongs to.

    Works with either a session cookie or an API key, so it doubles as a way to
    check that a key is live and to see what it is allowed to do.

    Open to the demo role, unlike everything else behind :data:`authenticated`.
    This endpoint is how the console learns which role it is running as, and the
    console trims itself with that answer - the sidebar a demo visitor gets, and
    the controls a page offers. Refusing it did not hide anything: the demo
    account and its scopes are published in plaintext by ``/auth/demo`` while the
    switch is on. What it did was leave the console unable to tell it was a demo,
    so it rendered the full navigation and every link in it answered 403.

    **Returns**

    The account name, role and the scopes this credential carries.
    """
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


@router.post(
    "/password", summary="Change your own password", openapi_extra={I18N_KEY: "auth_password"}
)
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


@router.get(
    "/sessions", summary="List your live sessions", openapi_extra={I18N_KEY: "auth_sessions"}
)
async def list_sessions(request: Request, principal: Principal = Depends(authenticated)) -> Any:
    """Every device currently signed in as you.

    **Returns**

    One entry per live session with where and when it signed in, and a flag
    marking the one making this request.
    """
    current = sessions.cookie_token(request)
    live = await sessions.list_for(principal.user_id, current=current)
    return ok(request, [s.as_dict() for s in live])


@router.delete(
    "/sessions",
    summary="Log out every other device",
    openapi_extra={I18N_KEY: "auth_sessions_revoke_all"},
)
async def revoke_other_sessions(
    request: Request, principal: Principal = Depends(authenticated)
) -> Any:
    """Sign out everywhere except here.

    The session making the request is kept, so you are not logged out by your
    own call. Use this after changing a password, or if a device was lost.

    **Returns**

    How many sessions were revoked.
    """
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


@router.delete(
    "/sessions/{session_ref}",
    summary="Revoke one of your sessions",
    openapi_extra={I18N_KEY: "auth_session_revoke"},
)
async def revoke_one_session(
    request: Request,
    session_ref: str = Path(description="The session digest from the list endpoint."),
    principal: Principal = Depends(authenticated),
) -> Any:
    """``session_ref`` is the digest from the list, never the token itself."""
    revoked = await sessions.revoke_by_id(principal.user_id, session_ref)
    if not revoked:
        # NotFound, not InvalidParam. Every other resource in this API tells a
        # caller whether their id was malformed or simply unknown; this was the
        # one route that answered 400 to both, so a client could not tell a typo
        # from a session that was already gone. There is nothing to protect by
        # blurring them - `revoke_by_id` only ever scans the caller's own index.
        raise NotFound(
            "no such session for this account",
            details={"session_ref": session_ref[:64]},
        )
    return ok(request, {"revoked": 1})


__all__ = [
    "LOGIN_FAIL_IP_KEY",
    "LOGIN_FAIL_USER_KEY",
    "LOGIN_LOCKOUT_SECONDS",
    "LOGIN_SPRAY_KEY",
    "MAX_FAILURES_PER_IP",
    "MAX_FAILURES_PER_USERNAME",
    "router",
]
