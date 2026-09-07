"""Server-side console sessions.

The console authenticates with an httpOnly, SameSite=Lax cookie holding an
opaque token; the mapping token -> user lives in Redis, which is what makes a
session revocable the instant an administrator says so (doc 06, doc 08).

Three keys per session:

* ``session:{token}``       the value :mod:`dtk.api.deps` reads on every request
* ``session:meta:{token}``  what the session list shows a human
* ``sessions:user:{id}``    an index so "log out my other devices" is one call

The token never leaves the cookie. The console identifies a session by a digest
of it, so listing sessions cannot hand an attacker a usable credential.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response

from dtk.api.deps import SESSION_COOKIE, SESSION_KEY
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis

log = get_logger(__name__)

SESSION_META_KEY = "session:meta:{token}"
SESSION_INDEX_KEY = "sessions:user:{user_id}"

#: Long enough that the console is not a login prompt, short enough that a
#: forgotten browser tab does not stay authorized forever.
SESSION_TTL_SECONDS = 7 * 24 * 3600

#: Length of the public session identifier. It is a digest of the token, so it
#: identifies a session without being one.
SESSION_ID_CHARS = 16


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """One live session, as the console displays it."""

    id: str
    created_at: str | None
    last_seen_at: str | None
    ip: str | None
    user_agent: str | None
    current: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "current": self.current,
        }


def session_id(token: str) -> str:
    """Public identifier for a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:SESSION_ID_CHARS]


def cookie_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def is_secure_request(request: Request) -> bool:
    """Whether the connection is TLS, as far as this process can tell.

    ``X-Forwarded-Proto`` is honoured because a console behind a reverse proxy
    is the normal deployment and the flag only ever tightens the cookie. It is
    never used to make an access decision.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


async def create(
    user_id: uuid.UUID,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    ttl: int = SESSION_TTL_SECONDS,
) -> str:
    """Mint a session and return its token."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.set(SESSION_KEY.format(token=token), str(user_id), ex=ttl)
    pipe.hset(
        SESSION_META_KEY.format(token=token),
        mapping={
            "user_id": str(user_id),
            "created_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "ip": ip or "",
            "user_agent": user_agent or "",
        },
    )
    pipe.expire(SESSION_META_KEY.format(token=token), ttl)
    pipe.zadd(SESSION_INDEX_KEY.format(user_id=user_id), {token: now.timestamp()})
    pipe.expire(SESSION_INDEX_KEY.format(user_id=user_id), ttl)
    await pipe.execute()
    log.info("auth.session_created", user_id=str(user_id), session_id=session_id(token))
    return token


async def touch(token: str) -> None:
    """Record that a session was used, without extending its lifetime."""
    await get_redis().hset(
        SESSION_META_KEY.format(token=token),
        "last_seen_at",
        datetime.now(UTC).isoformat(),
    )


async def revoke(token: str) -> None:
    """Drop one session everywhere it is recorded."""
    redis = get_redis()
    user_id = await redis.hget(SESSION_META_KEY.format(token=token), "user_id")
    pipe = redis.pipeline()
    pipe.delete(SESSION_KEY.format(token=token))
    pipe.delete(SESSION_META_KEY.format(token=token))
    if user_id:
        pipe.zrem(SESSION_INDEX_KEY.format(user_id=user_id), token)
    await pipe.execute()
    log.info("auth.session_revoked", session_id=session_id(token))


async def list_for(user_id: uuid.UUID, *, current: str | None = None) -> list[SessionInfo]:
    """Live sessions for one account, newest first.

    Index entries whose session key has expired are pruned as they are found:
    Redis expires the session itself, and the index is only a convenience.
    """
    redis = get_redis()
    index_key = SESSION_INDEX_KEY.format(user_id=user_id)
    tokens = await redis.zrevrange(index_key, 0, -1)
    sessions: list[SessionInfo] = []
    for token in tokens:
        if not await redis.exists(SESSION_KEY.format(token=token)):
            await redis.zrem(index_key, token)
            continue
        meta = await redis.hgetall(SESSION_META_KEY.format(token=token))
        sessions.append(
            SessionInfo(
                id=session_id(token),
                created_at=meta.get("created_at") or None,
                last_seen_at=meta.get("last_seen_at") or None,
                ip=meta.get("ip") or None,
                user_agent=meta.get("user_agent") or None,
                current=current is not None and token == current,
            )
        )
    return sessions


async def revoke_others(user_id: uuid.UUID, *, keep: str | None = None) -> int:
    """Log the account out everywhere except ``keep``."""
    redis = get_redis()
    index_key = SESSION_INDEX_KEY.format(user_id=user_id)
    tokens = await redis.zrange(index_key, 0, -1)
    removed = 0
    for token in tokens:
        if keep is not None and token == keep:
            continue
        await revoke(token)
        removed += 1
    log.info("auth.sessions_revoked", user_id=str(user_id), count=removed)
    return removed


async def revoke_by_id(user_id: uuid.UUID, target: str) -> bool:
    """Revoke one session of ``user_id`` named by its public identifier."""
    redis = get_redis()
    tokens = await redis.zrange(SESSION_INDEX_KEY.format(user_id=user_id), 0, -1)
    for token in tokens:
        if session_id(token) == target:
            await revoke(token)
            return True
    return False


def attach_cookie(
    response: Response, request: Request, token: str, *, ttl: int = SESSION_TTL_SECONDS
) -> None:
    """Set the session cookie with the flags doc 08 requires.

    ``Secure`` is dropped on a plain-HTTP deployment because the browser would
    otherwise discard the cookie and the console would be unusable; that
    downgrade is logged so it is not silent.
    """
    secure = is_secure_request(request)
    if not secure:
        log.warning(
            "auth.cookie_insecure",
            reason="request is not https; the session cookie is set without Secure",
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


__all__ = [
    "SESSION_ID_CHARS",
    "SESSION_INDEX_KEY",
    "SESSION_META_KEY",
    "SESSION_TTL_SECONDS",
    "SessionInfo",
    "attach_cookie",
    "clear_cookie",
    "cookie_token",
    "create",
    "is_secure_request",
    "list_for",
    "revoke",
    "revoke_by_id",
    "revoke_others",
    "session_id",
    "touch",
]
