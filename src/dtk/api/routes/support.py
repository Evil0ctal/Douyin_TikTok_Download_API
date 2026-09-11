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
  :func:`client_ip_is_trustworthy` is the second half of that: a counter keyed
  on the address may only refuse a request where the address separates one
  caller from the next, which is a property of the deployment, not of the code.
* :func:`mask_proxy_url` is the only way a stored proxy is allowed to reach a
  response. No endpoint returns a credential in clear text (doc 08).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Coroutine, Sequence
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from dtk.api import envelope, request_proxy
from dtk.api.deps import Principal, enforce_rate_limit
from dtk.core.errors import ForbiddenScope, InvalidParam, InvalidUrl, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import IdentityState, Language, Platform, Scope, UserRole
from dtk.db.models import Identity
from dtk.db.repositories import AuditRepository
from dtk.urls import is_private_host

log = get_logger(__name__)

#: Roles are a ladder, not a set: an administrator can do anything an operator
#: can. Comparing ranks keeps every endpoint from listing three roles.
#:
#: DEMO sits at the bottom, and the gap below VIEWER is doing real work. Every
#: guard in this file defaults to ``min_role=VIEWER``, so a role numbered under
#: it is refused everywhere by default and each page a demo instance is meant
#: to show has to say so - :data:`demo_read` and :data:`read_admin_demo` below.
#: The alternative, giving demo the same rank as viewer and denying pages one at
#: a time, fails the wrong way round: a route added next year would be public on
#: a demo box until somebody remembered it.
ROLE_RANK: dict[UserRole, int] = {
    UserRole.DEMO: 0,
    UserRole.VIEWER: 1,
    UserRole.OPERATOR: 2,
    UserRole.ADMIN: 3,
}

#: Page size ceiling for every list endpoint. A caller that wants more pages
#: through; one that wants everything at once is a caller to slow down.
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20

#: Ceiling for administrative listings, which are read from the local database
#: rather than from a platform and are therefore cheaper.
MAX_ADMIN_PAGE_SIZE = 500
DEFAULT_ADMIN_PAGE_SIZE = 100

#: uvicorn's ``--forwarded-allow-ips``, which ``docker/entrypoint.sh`` passes
#: through only when this is set. Its presence is the operator declaring that a
#: reverse proxy sits in front, and therefore that the peer address has been
#: rewritten from ``X-Forwarded-For``.
FORWARDED_ALLOW_IPS_ENV = "DTK_FORWARDED_ALLOW_IPS"


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
    this value (doc 06), and see :func:`client_ip_is_trustworthy` before
    refusing anything because of it.
    """
    return request.client.host if request.client else None


def client_ip_is_trustworthy() -> bool:
    """Whether :func:`client_ip` tells one caller apart from the next.

    Only where the operator has declared a reverse proxy. uvicorn rewrites the
    peer address from ``X-Forwarded-For`` for the hops named in
    ``DTK_FORWARDED_ALLOW_IPS`` and for no one otherwise, so behind a TLS
    terminator - or behind Docker's published-port userland proxy, which is the
    default - every request in the world arrives with the same address. A
    counter keyed on that is one global bucket, and refusing on it hands any
    stranger a lever on everyone else's access.

    A wildcard is not a declaration: it tells uvicorn to believe the header from
    whoever sends it, which makes the address forgeable rather than merely
    shared, and a forgeable address is a worse lockout key than a shared one.
    """
    declared = os.environ.get(FORWARDED_ALLOW_IPS_ENV, "").strip()
    return bool(declared) and "*" not in declared


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

    One rule, defined on the principal itself, so a route that calls
    ``principal.require`` and one that depends on :func:`guard` cannot disagree
    about who is allowed through - which they did until 2026-09-08.
    """
    return principal.permits(*scopes)


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
                details=principal.denial(scopes=scopes),
            )
        if ROLE_RANK[principal.role] < ROLE_RANK[min_role]:
            raise ForbiddenScope(
                "this operation requires a higher role",
                details=principal.denial(roles=[min_role]),
            )
        return principal

    return dependency


#: Any authenticated caller, including a read-only viewer. Not the demo
#: account: see :data:`demo_read` for the endpoints a demo instance opens.
authenticated = guard()
#: Console-side read access to the administrative surface.
read_admin = guard(scopes=(Scope.ADMIN, Scope.IDENTITY_MANAGE))
#: Pool, proxy and key maintenance: an operator or better.
manage_pool = guard(scopes=(Scope.ADMIN, Scope.IDENTITY_MANAGE), min_role=UserRole.OPERATOR)
#: Users, SENSITIVE settings, backup and restore: administrators only.
admin_only = guard(scopes=(Scope.ADMIN,), min_role=UserRole.ADMIN)

#: Reachable by the public demo account as well as by every real role.
#:
#: Put this on an endpoint only after deciding it is safe for a stranger, and
#: note what the two demo credentials can do with it, because they are not the
#: same caller:
#:
#: * the demo **session** is unscoped, like any console session, so this gate
#:   alone decides what the demo console can read;
#: * the demo **API key** carries `douyin:read` and `tiktok:read` and nothing
#:   else, so it reaches only the endpoints whose scopes it also satisfies.
#:
#: That is why the pages below keep their scope tuple unchanged and lower only
#: the role: the scope check is what stops the published API key from reading
#: the request log, without a second rule having to say so.
demo_read = guard(min_role=UserRole.DEMO)
#: An administrative *read* a demo instance shows: the logs, endpoint health and
#: pool counters behind the overview. Same scopes as :data:`read_admin`, so a
#: session passes and the published key does not.
read_admin_demo = guard(scopes=(Scope.ADMIN, Scope.IDENTITY_MANAGE), min_role=UserRole.DEMO)


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


def resolve_request_proxy(request: Request, value: str | None) -> str | None:
    """Vet a caller-supplied egress against this instance's setting.

    The mode lives in ``security.request_proxy`` and defaults to refusing the
    parameter; :mod:`dtk.api.request_proxy` explains why, and does the checking.
    An unrecognised setting value is treated as ``deny`` rather than as a
    permissive default - a typo in an operator's configuration must not be the
    thing that opens their network.
    """
    if value is None or not value.strip():
        return None
    raw = str(config_value(request, request_proxy.SETTING_KEY) or "")
    try:
        mode = request_proxy.RequestProxyMode(raw)
    except ValueError:
        log.warning("api.request_proxy.unknown_mode", configured=raw)
        mode = request_proxy.RequestProxyMode.DENY
    return request_proxy.normalize(value, mode=mode)


#: The scopes and role a caller needs before they may name an identity. They
#: are exactly ``manage_pool``'s, restated here because this is an inline check
#: on a route gated for reading rather than a dependency on the route itself.
_PIN_SCOPES: Final[tuple[Scope, ...]] = (Scope.ADMIN, Scope.IDENTITY_MANAGE)


async def resolve_explain(request: Request, principal: Principal, value: bool) -> bool:
    """Vet a request for the outbound call to be described back.

    Gated like pool management and not like reading, for the same reason
    :func:`resolve_request_identity` is: the answer contains the identity's
    cookie jar. A `douyin:read` key may ask this instance to *use* a jar; it
    may not ask to be handed one, and "show me the request you made" is the
    same disclosure by a longer route.

    Audited, because a credential leaving the instance should leave a record
    behind it - the same bargain :func:`reveal_cookies` makes on the identities
    page. The audit line names no jar: it says who asked and for which
    endpoint, which is what an operator reading it later needs.
    """
    if not value:
        return False
    principal.require(*_PIN_SCOPES)
    if ROLE_RANK[principal.role] < ROLE_RANK[UserRole.OPERATOR]:
        raise ForbiddenScope(
            "explaining a request requires an operator role",
            details={**principal.denial(roles=[UserRole.OPERATOR]), "field": "explain"},
        )
    await audit(
        request,
        principal,
        "request.explained",
        target_type="endpoint",
        target_id=request.url.path,
    )
    return True


async def resolve_request_identity(
    request: Request,
    principal: Principal,
    value: str | None,
    *,
    platform: Platform | None = None,
) -> str | None:
    """Vet a caller-supplied identity, so a bad pin is a 400 and not a dead task.

    Pinning is gated like pool management rather than like reading, and the
    reason is worth stating plainly: the identity pool is instance-wide and its
    rows have no owner, so naming one is asking to send a request as whoever
    imported that jar. On a self-hosted instance that is usually the same
    person; it is not guaranteed to be, and a plain read key must not be able
    to reach someone's logged-in session.

    Existence, retirement and platform are all checked here rather than left to
    the worker. The worker checks again - a row can be retired between
    submission and execution - but a caller who mistyped a uuid deserves an
    immediate answer naming the field, not a task that queues, runs and fails.
    """
    if value is None or not value.strip():
        return None
    principal.require(*_PIN_SCOPES)
    if ROLE_RANK[principal.role] < ROLE_RANK[UserRole.OPERATOR]:
        raise ForbiddenScope(
            "naming an identity requires an operator role",
            details={**principal.denial(roles=[UserRole.OPERATOR]), "field": "identity"},
        )
    try:
        identity_id = uuid.UUID(value.strip())
    except ValueError:
        raise InvalidParam("identity must be a uuid", details={"field": "identity"}) from None

    row = await request.state.db.get(Identity, identity_id)
    if row is None:
        raise NotFound("no identity with that id", details={"field": "identity"})
    if row.state == IdentityState.RETIRED.value:
        # Retirement wipes the ciphertext, so there is no jar left to sign
        # with. Saying so beats the pool-exhausted 503 the scheduler would
        # otherwise produce several seconds later.
        raise InvalidParam(
            "that identity has been retired and no longer holds a session",
            details={"field": "identity", "reason": "retired"},
        )
    if platform is not None and row.platform != platform.value:
        raise InvalidParam(
            "that identity belongs to a different platform than this endpoint",
            details={
                "field": "identity",
                "identity_platform": row.platform,
                "endpoint_platform": platform.value,
            },
        )
    return str(identity_id)


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
    "FORWARDED_ALLOW_IPS_ENV",
    "MAX_ADMIN_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "ROLE_RANK",
    "admin_only",
    "audit",
    "authenticated",
    "client_ip",
    "client_ip_is_trustworthy",
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
    "resolve_request_identity",
    "resolve_wait",
    "user_agent",
    "validate_callback_url",
]
