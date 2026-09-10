"""The public account a demo deployment publishes.

An instance put on a public server so strangers can try it needs two things
nobody should have to hand out individually: a console login that shows the
machine working, and an API key that calls the platform endpoints. This module
mints both, keeps them to one each, and can take them away again.

Three properties are the whole design, and each one is here rather than left to
the operator to remember:

**The credentials are inert unless the switch is on.** ``demo.enabled`` is
checked at authentication, not only at provisioning, so the account and the key
stop working the moment it is turned off - and start working again if it is
turned back on, with the same key. Nothing is deleted, because a demo that has
to be rebuilt is a demo that stays off.

**There is exactly one of each.** Turning the switch on twice does not make a
second account, and a rotate replaces the key rather than adding one. An
instance whose public key set grows every time somebody clicks a toggle has no
way to answer "which of these is published".

**The password is generated, never chosen.** It is shown once, when the switch
is turned on, and the operator copies it into their README. It is not stored in
readable form here or anywhere else, so a lost one is rotated rather than
recovered - which is the same thing every other credential in this project
does, and the demo account is not the place to make an exception.

What the two credentials may reach is decided elsewhere and deliberately not
duplicated here: the role ranks below viewer
(:data:`dtk.api.routes.support.ROLE_RANK`) and the key holds the two platform
read scopes, so the console gate and the scope gate each answer for one of
them.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.crypto import new_api_key
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Scope, UserRole
from dtk.db.models import ApiKey, User

log = get_logger(__name__)

#: The runtime switch. Read by the provisioner and, more importantly, by both
#: authentication paths - see :func:`dtk.api.deps.demo_mode_on`.
SETTING_KEY: Final = "demo.enabled"

#: How long a demo task's row lives before the maintenance sweep removes it.
RETENTION_SETTING: Final = "demo.task_retention_minutes"

#: The account name. Fixed, because it goes in a README and a fixed name is one
#: less thing for a visitor to get wrong.
DEMO_USERNAME: Final = "demo"

#: The key's display name, so it is recognisable in the API keys list next to
#: an operator's own keys.
DEMO_KEY_NAME: Final = "demo (public)"

#: What the published key may do: read the two platforms, and nothing else.
#:
#: The same pair the anonymous principal gets on an opened endpoint, and for the
#: same reason - it is the smallest set that makes the scraping endpoints work.
#: Notably absent: `archive:read` and `archive:export`, so the published key
#: cannot walk what this instance has already collected, and `media:write`, so
#: it cannot spend the operator's disk.
DEMO_KEY_SCOPES: Final[tuple[Scope, ...]] = (Scope.DOUYIN_READ, Scope.TIKTOK_READ)

#: Words the generated password is built from. Readable on purpose: this string
#: is going to be copied off a web page into a README and typed back in by
#: strangers, and a demo password that looks like a hash gets mistyped, reported
#: as broken, and answered by the maintainer.
_WORDS: Final[tuple[str, ...]] = (
    "amber",
    "basalt",
    "cobalt",
    "cedar",
    "delta",
    "ember",
    "fjord",
    "garnet",
    "harbor",
    "indigo",
    "jasper",
    "kelp",
    "lumen",
    "marble",
    "nimbus",
    "onyx",
    "pumice",
    "quartz",
    "ripple",
    "slate",
    "thicket",
    "umber",
    "vellum",
    "willow",
)


def generate_password() -> str:
    """A password a stranger can retype: three words and four digits."""
    words = "-".join(secrets.choice(_WORDS) for _ in range(3))
    return f"{words}-{secrets.randbelow(9000) + 1000}"


@dataclass(frozen=True, slots=True)
class DemoCredentials:
    """What provisioning produced, returned to the caller exactly once."""

    username: str
    #: Present only on the call that generated it. A later read returns None,
    #: because nothing stores it.
    password: str | None
    #: Likewise: the full key exists in this process and in the operator's
    #: clipboard, nowhere else.
    api_key: str | None
    api_key_prefix: str
    scopes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "password": self.password,
            "api_key": self.api_key,
            "api_key_prefix": self.api_key_prefix,
            "scopes": list(self.scopes),
        }


async def find_user(session: AsyncSession) -> User | None:
    """The demo account, if this instance has ever had one."""
    stmt = select(User).where(User.role == UserRole.DEMO.value)
    return (await session.scalars(stmt)).first()


async def find_key(session: AsyncSession, user_id: uuid.UUID) -> ApiKey | None:
    """The demo account's live key, if it has one."""
    stmt = select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
    return (await session.scalars(stmt)).first()


async def provision(
    session: AsyncSession, *, hash_password: Callable[[str], str]
) -> DemoCredentials:
    """Create or reset the demo account and its key, returning both in the clear.

    Idempotent in the sense that matters: called twice it still leaves one
    account and one live key. It is not idempotent in what it returns, because
    it generates a new password and a new key each time - which is what makes
    it also the "rotate" operation, and why the console calls it when the
    switch goes on rather than every time the page loads.

    ``hash_password`` is injected rather than imported: it lives in
    :mod:`dtk.cli.users` next to the argon2 parameters, and importing the CLI
    from a service would drag Typer into the API process.
    """
    now = datetime.now(UTC)
    password = generate_password()

    user = await find_user(session)
    if user is None:
        user = User(
            username=DEMO_USERNAME,
            password_hash=hash_password(password),
            role=UserRole.DEMO.value,
        )
        session.add(user)
        await session.flush()
        log.info("demo.user_created", user_id=str(user.id))
    else:
        user.password_hash = hash_password(password)
        log.info("demo.user_password_rotated", user_id=str(user.id))

    # Revoke rather than delete: the key id is referenced by task and
    # request_log rows, and a demo instance that cannot answer "which key made
    # this call" has lost the one audit trail it still keeps.
    existing = await find_key(session, user.id)
    if existing is not None:
        existing.revoked_at = now

    full, prefix, digest = new_api_key()
    session.add(
        ApiKey(
            user_id=user.id,
            name=DEMO_KEY_NAME,
            prefix=prefix,
            key_hash=digest,
            scopes=[s.value for s in DEMO_KEY_SCOPES],
        )
    )
    await session.flush()
    log.info("demo.key_minted", user_id=str(user.id), prefix=prefix)

    return DemoCredentials(
        username=user.username,
        password=password,
        api_key=full,
        api_key_prefix=prefix,
        scopes=tuple(s.value for s in DEMO_KEY_SCOPES),
    )


async def describe(session: AsyncSession) -> DemoCredentials | None:
    """What is provisioned, without the secrets. None if nothing ever was."""
    user = await find_user(session)
    if user is None:
        return None
    key = await find_key(session, user.id)
    return DemoCredentials(
        username=user.username,
        password=None,
        api_key=None,
        api_key_prefix=key.prefix if key else "",
        scopes=tuple(key.scopes or ()) if key else (),
    )


async def end_sessions(session: AsyncSession) -> int:
    """Drop every console session belonging to the demo account.

    Turning the switch off has to log people out, not merely stop new logins:
    a session cookie is good for its whole lifetime, and "the demo is off" that
    still leaves a stranger inside the console is not off.

    Sessions live in Redis keyed by an opaque token with the user id as the
    value, so there is no index from user to token and this scans. That is
    acceptable here because it runs when an administrator flips a setting, not
    on any request path, and a self-hosted instance's session count is small.
    """
    user = await find_user(session)
    if user is None:
        return 0

    redis = get_redis()
    wanted = str(user.id)
    dropped = 0
    async for key in redis.scan_iter(match="session:*", count=200):
        if await redis.get(key) == wanted:
            await redis.delete(key)
            dropped += 1
    if dropped:
        log.info("demo.sessions_ended", user_id=wanted, count=dropped)
    return dropped


__all__ = [
    "DEMO_KEY_NAME",
    "DEMO_KEY_SCOPES",
    "DEMO_USERNAME",
    "RETENTION_SETTING",
    "SETTING_KEY",
    "DemoCredentials",
    "describe",
    "end_sessions",
    "find_key",
    "find_user",
    "generate_password",
    "provision",
]
