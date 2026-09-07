"""Shared plumbing for the HTTP routes.

Everything here exists so a route handler reads as a description of one
endpoint. Three of these helpers carry design decisions rather than
convenience:

* :func:`guard` enforces scope and role together. A console session is bounded
  by its role, an API key by the scopes minted into it - and the scope check
  never softens because the key happens to belong to an administrator, which is
  what doc 06 means by "identity:manage cannot be reached by an ordinary read
  key" (almost every key on a self-hosted instance belongs to the admin user).
* :func:`client_ip` is documented as unreliable. Under Docker's userland proxy
  every request appears to come from the bridge gateway, so the address is a
  coarse abuse bucket and never an authorization signal (doc 06).
* :func:`mask_proxy_url` is the only way a stored proxy is allowed to reach a
  response. No endpoint returns a credential in clear text (doc 08).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from dtk.api import envelope
from dtk.api.deps import Principal, enforce_rate_limit
from dtk.core.errors import ForbiddenScope, InvalidParam, InvalidUrl
from dtk.core.logging import get_logger
from dtk.core.types import Language, Scope, UserRole
from dtk.db.repositories import AuditRepository
from dtk.urls import is_private_host

log = get_logger(__name__)

#: Roles are a ladder, not a set: an administrator can do anything an operator
#: can. Comparing ranks keeps every endpoint from listing three roles.
ROLE_RANK: dict[UserRole, int] = {
    UserRole.VIEWER: 0,
    UserRole.OPERATOR: 1,
    UserRole.ADMIN: 2,
}

#: Page size ceiling for every list endpoint. A caller that wants more pages
#: through; one that wants everything at once is a caller to slow down.
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20

#: Ceiling for administrative listings, which are read from the local database
#: rather than from a platform and are therefore cheaper.
MAX_ADMIN_PAGE_SIZE = 500
DEFAULT_ADMIN_PAGE_SIZE = 100


# --------------------------------------------------------------------------
# Request context
# --------------------------------------------------------------------------


def request_id(request: Request) -> uuid.UUID | str:
    return getattr(request.state, "request_id", "unknown")


def language(request: Request) -> Language:
    return getattr(request.state, "language", Language.EN)


def config_value(request: Request, key: str) -> Any:
    """Read one runtime setting from the snapshot this process holds."""
    return request.app.state.config.get(key)


def client_ip(request: Request) -> str | None:
    """Best-effort source address, for abuse counters only.

    Deliberately ignores ``X-Forwarded-For``: without a trusted reverse proxy
    the header is caller-controlled, and with Docker's userland proxy the peer
    address is the bridge gateway anyway. Never make an access decision from
    this value (doc 06).
    """
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:512] if value else None


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


def ok(
    request: Request,
    data: Any,
    *,
    status_code: int = 200,
    cached: bool | None = None,
    duration_ms: int | None = None,
    cursor: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Success envelope with this request's correlation id attached."""
    return envelope.success(
        data,
        request_id(request),
        cached=cached,
        duration_ms=duration_ms,
        cursor=cursor,
        extra=extra,
        status_code=status_code,
    )


def page(
    request: Request,
    items: Sequence[Any],
    *,
    cursor: str | None = None,
    has_more: bool = False,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """A list response with the opaque cursor in ``meta``.

    The cursor is echoed back verbatim by the caller; its shape is a platform
    detail that never leaves the service (doc 11).
    """
    return ok(
        request,
        list(items),
        cursor={"next": cursor, "has_more": has_more},
        extra=extra,
    )


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------

Guard = Callable[..., Coroutine[Any, Any, Principal]]


def has_scope(principal: Principal, scopes: Sequence[Scope]) -> bool:
    """Whether the caller may reach an endpoint gated on ``scopes``.

    A console session is not scoped: it is bounded by the account's role, and
    the console is the surface those roles were written for. An API key is
    bounded by the scopes it was minted with - including a key owned by an
    administrator, because doc 06 requires that a plain read key cannot reach
    identity management no matter who created it.
    """
    if not scopes:
        return True
    if principal.api_key_id is None:
        return True
    if Scope.ADMIN in principal.scopes:
        return True
    return bool(set(scopes) & principal.scopes)


def guard(
    *,
    scopes: Sequence[Scope] = (),
    min_role: UserRole = UserRole.VIEWER,
) -> Guard:
    """Dependency enforcing scope and role, after the rate limiter.

    Depending on :func:`dtk.api.deps.enforce_rate_limit` rather than on
    ``current_principal`` is what puts the ``X-RateLimit-*`` headers on every
    authenticated response.
    """

    async def dependency(principal: Principal = Depends(enforce_rate_limit)) -> Principal:
        if not has_scope(principal, scopes):
            raise ForbiddenScope(
                "this credential lacks the scope required for this endpoint",
                details={"required": sorted(s.value for s in scopes)},
            )
        if ROLE_RANK[principal.role] < ROLE_RANK[min_role]:
            raise ForbiddenScope(
                "this operation requires a higher role",
                details={"required_role": min_role.value},
            )
        return principal

    return dependency


#: Any authenticated caller, including a read-only viewer.
authenticated = guard()
#: Console-side read access to the administrative surface.
read_admin = guard(scopes=(Scope.ADMIN, Scope.IDENTITY_MANAGE))
#: Pool, proxy and key maintenance: an operator or better.
manage_pool = guard(scopes=(Scope.ADMIN, Scope.IDENTITY_MANAGE), min_role=UserRole.OPERATOR)
#: Users, SENSITIVE settings, backup and restore: administrators only.
admin_only = guard(scopes=(Scope.ADMIN,), min_role=UserRole.ADMIN)


# --------------------------------------------------------------------------
# Parameter validation
# --------------------------------------------------------------------------


def resolve_wait(request: Request, requested: float | None) -> float:
    """Clamp ``?wait=`` against ``api.max_wait_seconds``.

    A value above the ceiling is rejected rather than silently clamped: a
    caller that asked to block for five minutes has to learn that it cannot,
    otherwise it will treat the early 202 as a failure.
    """
    if requested is None:
        return 0.0
    maximum = float(config_value(request, "api.max_wait_seconds"))
    if requested < 0:
        raise InvalidParam("wait must not be negative", details={"field": "wait"})
    if requested > maximum:
        raise InvalidParam(
            f"wait must not exceed {maximum:g} seconds",
            details={"field": "wait", "max": maximum},
        )
    return float(requested)


def resolve_count(
    value: int | None, *, default: int = DEFAULT_PAGE_SIZE, maximum: int = MAX_PAGE_SIZE
) -> int:
    if value is None:
        return default
    if value < 1:
        raise InvalidParam("count must be at least 1", details={"field": "count"})
    return min(value, maximum)


def validate_callback_url(request: Request, url: str | None) -> str | None:
    """Vet a caller-supplied webhook target.

    This is an outbound request whose destination the caller chooses, i.e. an
    SSRF primitive, so it is off unless an administrator turned it on and it
    still has to be https and off the private ranges (doc 06, doc 08).
    """
    if url is None:
        return None
    if not config_value(request, "security.enable_task_webhook"):
        raise InvalidParam(
            "task callbacks are disabled on this instance; an administrator can "
            "enable security.enable_task_webhook",
            details={"field": "callback_url"},
        )
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidUrl(
            "callback_url must be an https URL",
            details={"field": "callback_url", "reason": "scheme_not_allowed"},
        )
    if is_private_host(parts.hostname):
        raise InvalidUrl(
            "callback_url must not point at a private or loopback address",
            details={"field": "callback_url", "reason": "private_address"},
        )
    return url


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def mask_secret(value: str | None) -> str | None:
    """Keep the shape of a secret, drop the secret."""
    if value is None:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def mask_proxy_url(url: str) -> str:
    """Render a proxy URL with its credentials removed.

    The console needs to tell two egresses apart, which host and port do. The
    password is never part of that, and doc 08 forbids returning it at all.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    if not parts.hostname:
        return "***"
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    if parts.username:
        host = f"{parts.username}:***@{host}"
    return urlunsplit((parts.scheme, host, "", "", ""))


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------


async def audit(
    request: Request,
    principal: Principal,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record one sensitive operation.

    ``detail`` must describe *what changed*, never the value of a credential;
    the audit table is read by humans and copied into bug reports.
    """
    await AuditRepository(request.state.db).record(
        action=action,
        user_id=principal.user_id,
        api_key_id=principal.api_key_id,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    log.info(
        "audit.recorded",
        action=action,
        target_type=target_type,
        target_id=target_id,
        user_id=str(principal.user_id),
    )


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "DEFAULT_ADMIN_PAGE_SIZE",
    "DEFAULT_PAGE_SIZE",
    "MAX_ADMIN_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "ROLE_RANK",
    "admin_only",
    "audit",
    "authenticated",
    "client_ip",
    "config_value",
    "guard",
    "has_scope",
    "iso",
    "language",
    "manage_pool",
    "mask_proxy_url",
    "mask_secret",
    "ok",
    "page",
    "read_admin",
    "request_id",
    "resolve_count",
    "resolve_wait",
    "user_agent",
    "validate_callback_url",
]
