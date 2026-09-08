"""Request-scoped dependencies: authentication, scopes and rate limiting.

Two callers, two mechanisms. The console uses a server-side session cookie; a
script or an agent uses an API key. Both resolve to the same principal object so
routes never branch on how the caller authenticated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.api import public_endpoints
from dtk.core.crypto import hash_api_key
from dtk.core.errors import ForbiddenScope, RateLimited, Unauthenticated
from dtk.core.redis import get_redis
from dtk.core.types import Scope, UserRole
from dtk.db.models import ApiKey, User

SESSION_COOKIE = "dtk_session"

#: The header a program authenticates with. Named because the OpenAPI document
#: advertises it to Swagger UI, and a document that names a different header
#: than the code reads is worse than one that names none.
API_KEY_HEADER = "X-API-Key"
SESSION_KEY = "session:{token}"
RATE_KEY = "ratelimit:{subject}:{window}"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    role: UserRole
    scopes: frozenset[Scope]
    api_key_id: uuid.UUID | None
    rate_limit_per_min: int | None

    @property
    def subject(self) -> str:
        return f"key:{self.api_key_id}" if self.api_key_id else f"user:{self.user_id}"

    def require(self, *needed: Scope) -> None:
        if self.role is UserRole.ADMIN or Scope.ADMIN in self.scopes:
            return
        if not set(needed) & self.scopes:
            raise ForbiddenScope(
                "this API key lacks the scope required for this endpoint",
                details={"required": sorted(s.value for s in needed)},
            )


async def _from_api_key(session: AsyncSession, raw_key: str) -> Principal | None:
    row = (
        await session.execute(
            select(ApiKey, User)
            .join(User, User.id == ApiKey.user_id)
            .where(ApiKey.key_hash == hash_api_key(raw_key))
        )
    ).first()
    if row is None:
        return None
    key, user = row
    now = datetime.now(UTC)
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        return None

    key.last_used_at = now
    scopes: set[Scope] = set()
    for value in key.scopes or []:
        try:
            scopes.add(Scope(value))
        except ValueError:
            continue
    return Principal(
        user_id=user.id,
        role=UserRole(user.role),
        scopes=frozenset(scopes),
        api_key_id=key.id,
        rate_limit_per_min=key.rate_limit,
    )


async def _from_session(session: AsyncSession, token: str) -> Principal | None:
    raw = await get_redis().get(SESSION_KEY.format(token=token))
    if not raw:
        return None
    try:
        user_id = uuid.UUID(raw)
    except (ValueError, AttributeError):
        return None
    user = await session.get(User, user_id)
    if user is None:
        return None
    # A console session carries the user's full role rather than a scope subset.
    return Principal(
        user_id=user.id,
        role=UserRole(user.role),
        scopes=frozenset(Scope),
        api_key_id=None,
        rate_limit_per_min=None,
    )


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get(API_KEY_HEADER.lower())


async def current_principal(request: Request) -> Principal:
    """Resolve the caller, or reject.

    Attached to ``request.state`` as well so middleware can attribute the
    request without resolving it a second time.
    """
    session: AsyncSession = request.state.db
    principal: Principal | None = None

    raw_key = _bearer(request)
    if raw_key:
        principal = await _from_api_key(session, raw_key)
    if principal is None:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            principal = await _from_session(session, cookie)

    if principal is None:
        principal = _anonymous_if_opened(request)
    if principal is None:
        raise Unauthenticated("valid credentials are required")

    request.state.principal = principal
    return principal


def _anonymous_if_opened(request: Request) -> Principal | None:
    """The principal for an endpoint an operator has deliberately opened.

    None means "still requires credentials", which is every endpoint until
    somebody edits `api.public_endpoints`, and every admin, auth and setup
    endpoint permanently - `dtk.api.public_endpoints` refuses those whatever the
    setting says.

    Bad credentials are NOT the same as none. A caller that sent a key we
    rejected is told so even on an open endpoint: silently downgrading them to
    anonymous would turn "your key expired" into "your key works but sees less",
    which is the harder failure to diagnose of the two.
    """
    if _bearer(request) or request.cookies.get(SESSION_COOKIE):
        return None
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if not path:
        return None
    try:
        configured = request.app.state.config.get(public_endpoints.SETTING_KEY)
    except Exception:
        return None
    if not public_endpoints.is_public(configured, request.method, path):
        return None
    return cast("Principal", public_endpoints.anonymous())


async def enforce_rate_limit(
    request: Request, principal: Principal = Depends(current_principal)
) -> Principal:
    """Fixed-window abuse protection.

    Not metering and not billing: the only purpose is stopping one runaway
    script from draining the identity pool.
    """
    limit = principal.rate_limit_per_min or request.app.state.config.get(
        "api.default_rate_limit_per_min"
    )
    subject = principal.subject
    if principal.api_key_id is None and principal.user_id == public_endpoints.ANONYMOUS_USER_ID:
        # Every anonymous caller shares one user id, so metering by subject
        # alone would make the whole internet a single bucket: one script would
        # lock everybody else out, and one open endpoint would otherwise be an
        # unmetered drain on the identity pool.
        #
        # The peer address is read directly rather than through
        # `support.client_ip` - that module imports this one. It is the same
        # value and carries the same caveat: behind Docker's userland proxy
        # every caller looks like the bridge gateway, so this degrades to one
        # shared bucket rather than to no limit at all. Degrading toward
        # stricter is the right direction for a counter whose job is abuse.
        peer = request.client.host if request.client else None
        subject = f"anon:{peer or 'unknown'}"
    if not limit or limit <= 0:
        return principal

    now = int(datetime.now(UTC).timestamp())
    window = now // 60
    key = RATE_KEY.format(subject=subject, window=window)

    redis = get_redis()
    used = await redis.incr(key)
    if used == 1:
        await redis.expire(key, 120)

    reset_at = (window + 1) * 60
    request.state.rate_limit = (limit, max(0, limit - used), reset_at)

    if used > limit:
        raise RateLimited(
            "request rate limit exceeded",
            retry_after=max(1, reset_at - now),
            details={"limit": limit},
        )
    return principal


def require_scopes(*scopes: Scope):
    async def dependency(principal: Principal = Depends(enforce_rate_limit)) -> Principal:
        principal.require(*scopes)
        return principal

    return dependency


def require_role(*roles: UserRole):
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in roles and principal.role is not UserRole.ADMIN:
            raise ForbiddenScope(
                "this operation requires a higher role",
                details={"required": sorted(r.value for r in roles)},
            )
        return principal

    return dependency


__all__ = [
    "RATE_KEY",
    "SESSION_COOKIE",
    "SESSION_KEY",
    "Principal",
    "current_principal",
    "enforce_rate_limit",
    "require_role",
    "require_scopes",
]
