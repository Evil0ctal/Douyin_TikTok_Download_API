"""First-run initialization.

A fresh deployment has no account, which leaves a window in which whoever
arrives first becomes the administrator. Doc 06 rules out the intuitive fix:

    Publishing a port with Docker's userland proxy rewrites the source address
    to the bridge gateway, so ``request.client.host`` reads as 172.17.0.1 -
    inside the private ranges. "Only accept private addresses" therefore admits
    the entire internet, and behaves differently again under
    ``network_mode: host``. ``X-Forwarded-For`` is no better: without a trusted
    reverse proxy the client writes it.

So the gate is a one-time token that only exists in the container log:
generated at startup while the ``users`` table is empty, stored in Redis with a
24 hour TTL, compared in constant time, invalidated after five failures, and
permanently closed once an account exists. It never appears in an HTTP
response, never reaches the database and never touches the disk.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI, Request

from dtk.api.routes.openapi import CREATED_RESPONSES, I18N_KEY
from dtk.api.routes.passwords import hash_password
from dtk.api.routes.schemas import SetupInit
from dtk.api.routes.support import client_ip, ok, user_agent
from dtk.core.crypto import constant_time_equals, new_setup_token
from dtk.core.db import session_scope
from dtk.core.errors import SetupAlreadyDone, SetupTokenInvalid
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import UserRole
from dtk.db.repositories import AuditRepository, UserRepository

log = get_logger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])

SETUP_TOKEN_KEY = "setup:token"
SETUP_ATTEMPTS_KEY = "setup:attempts"

#: One day is long enough to find the line in ``docker compose logs api`` and
#: short enough that an abandoned deployment does not stay claimable.
SETUP_TOKEN_TTL_SECONDS = 24 * 3600

#: Five wrong tokens invalidate the current one. Guessing 32 random bytes is
#: not the threat; a script hammering the endpoint is.
MAX_SETUP_ATTEMPTS = 5

_BANNER_WIDTH = 74


async def is_initialized() -> bool:
    """Whether an account exists, read outside any request transaction."""
    async with session_scope() as session:
        return await UserRepository(session).count() > 0


def _banner(url: str) -> str:
    """The one message in this service formatted for a human eye.

    Structured logs are the rule everywhere else, but this line is read by a
    person scrolling ``docker compose logs``, and a JSON-escaped box is not
    readable. The token appears here and nowhere else.
    """
    line = "=" * _BANNER_WIDTH
    return (
        f"\n{line}\n"
        "  dtk is not initialized yet.\n"
        "  Open this URL to create the administrator account:\n\n"
        f"    {url}\n\n"
        f"  The token expires in {SETUP_TOKEN_TTL_SECONDS // 3600} hours.\n"
        "  To issue a new one: docker compose restart api\n"
        f"{line}\n"
    )


async def ensure_setup_token(
    *,
    bind_host: str = "127.0.0.1",
    bind_port: int = 8000,
    ttl: int = SETUP_TOKEN_TTL_SECONDS,
) -> str | None:
    """Issue and announce the setup token when the instance has no account.

    Idempotent: an existing unexpired token is reused and re-announced rather
    than replaced, so a restart does not invalidate the link a user is holding.
    Returns ``None`` once an account exists.
    """
    if await is_initialized():
        # Nothing to gate any more; make sure no stale token survives.
        await get_redis().delete(SETUP_TOKEN_KEY, SETUP_ATTEMPTS_KEY)
        return None

    redis = get_redis()
    token = await redis.get(SETUP_TOKEN_KEY)
    reused = token is not None
    if token is None:
        token = new_setup_token()
        await redis.set(SETUP_TOKEN_KEY, token, ex=ttl)
        await redis.delete(SETUP_ATTEMPTS_KEY)

    host = "127.0.0.1" if bind_host in ("0.0.0.0", "::", "") else bind_host
    url = f"http://{host}:{bind_port}/setup?token={token}"
    sys.stdout.write(_banner(url))
    sys.stdout.flush()
    log.warning("setup.pending", reused_token=reused, ttl_seconds=ttl, path="/api/setup/init")
    return token


def install_startup_hook(app: FastAPI) -> None:
    """Run :func:`ensure_setup_token` after the application's own startup.

    The token needs a live Redis connection and a live database, both of which
    the application's lifespan opens, so this wraps that context rather than
    registering a separate startup handler - Starlette ignores ``on_startup``
    once a lifespan is supplied.
    """
    original = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(scoped_app: FastAPI) -> AsyncIterator[None]:
        async with original(scoped_app):
            settings = getattr(scoped_app.state, "settings", None)
            try:
                await ensure_setup_token(
                    bind_host=getattr(settings, "bind_host", "127.0.0.1"),
                    bind_port=getattr(settings, "bind_port", 8000),
                )
            except Exception as exc:  # startup must not fail over an advisory
                log.warning("setup.token_unavailable", error=str(exc)[:200])
            yield

    app.router.lifespan_context = lifespan


@router.get(
    "/status",
    summary="Whether this instance has an administrator yet",
    openapi_extra={I18N_KEY: "setup_status"},
)
async def setup_status(request: Request) -> Any:
    """Unauthenticated by necessity: it is what the wizard asks first."""
    return ok(request, {"initialized": await is_initialized()})


@router.post(
    "/init",
    summary="Create the first administrator account",
    openapi_extra={I18N_KEY: "setup_init", **CREATED_RESPONSES},
)
async def setup_init(request: Request, body: SetupInit) -> Any:
    """Consume the setup token and create the administrator.

    Ordering matters. The account check comes first so an initialized instance
    answers 409 without ever looking at the token; the token is deleted the
    moment it verifies, so two racing requests cannot both succeed.
    """
    session = request.state.db
    users = UserRepository(session)
    if await users.count() > 0:
        raise SetupAlreadyDone("this instance already has an administrator account")

    redis = get_redis()
    stored = await redis.get(SETUP_TOKEN_KEY)
    # Compare even when nothing is stored, against a value of the same shape,
    # so a missing token and a wrong token take the same time.
    matches = constant_time_equals(body.token, stored if stored else new_setup_token())
    if not stored or not matches:
        attempts = await redis.incr(SETUP_ATTEMPTS_KEY)
        await redis.expire(SETUP_ATTEMPTS_KEY, SETUP_TOKEN_TTL_SECONDS)
        remaining = max(0, MAX_SETUP_ATTEMPTS - attempts)
        if attempts >= MAX_SETUP_ATTEMPTS:
            await redis.delete(SETUP_TOKEN_KEY)
            log.error("setup.token_invalidated", attempts=attempts, ip=client_ip(request))
        else:
            log.warning("setup.token_rejected", attempts=attempts, ip=client_ip(request))
        raise SetupTokenInvalid(
            "the setup token is wrong or has expired; restart the api container to issue a new one",
            details={"attempts_remaining": remaining},
        )

    # Single use: burn it before the account is written, so a second request
    # holding the same token finds nothing to match against.
    await redis.delete(SETUP_TOKEN_KEY, SETUP_ATTEMPTS_KEY)

    user = await users.create(
        username=body.username,
        password_hash=hash_password(body.password),
        role=UserRole.ADMIN,
    )
    await AuditRepository(session).record(
        action="setup.completed",
        user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        detail={"username": user.username, "role": UserRole.ADMIN.value},
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    await session.commit()

    seeded = await _seed_settings()
    log.info("setup.completed", user_id=str(user.id), seeded_settings=seeded)

    return ok(
        request,
        {
            "initialized": True,
            "user": {
                "id": str(user.id),
                "username": user.username,
                "role": user.role,
            },
            "seeded_settings": seeded,
        },
        status_code=201,
    )


async def _seed_settings() -> int:
    """Copy RUNTIME and SENSITIVE values out of the environment, once.

    Doc 10: after this the database wins, and editing ``.env`` for those keys
    stops having an effect. A failure here must not undo the account creation,
    which is why it runs after the commit and only logs.
    """
    from dtk.services.settings_store import seed_from_env

    try:
        return await seed_from_env(dict(os.environ))
    except Exception as exc:
        log.warning("setup.seed_failed", error=str(exc)[:200])
        return 0


__all__ = [
    "MAX_SETUP_ATTEMPTS",
    "SETUP_ATTEMPTS_KEY",
    "SETUP_TOKEN_KEY",
    "SETUP_TOKEN_TTL_SECONDS",
    "ensure_setup_token",
    "install_startup_hook",
    "is_initialized",
    "router",
]
