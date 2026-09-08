"""Which endpoints an operator has chosen to serve without credentials.

Every endpoint requires an API key or a console session. That is the default and
it is the right one: an instance on a public server is a machine strangers can
reach, and its whole purpose is to spend someone else's identity pool.

Some deployments want a subset open anyway - a personal instance behind a
firewall, a public read-only mirror, a link-parsing helper embedded in a page.
``api.public_endpoints`` lists those, by ``"<METHOD> <path template>"`` exactly
as they appear in the API document, so what an operator types is what they read
in Swagger.

Three groups can never be opened, whatever the list says:

* ``/api/v1/admin/*`` - identities, proxies, users, API keys, settings, backups.
  Opening any of these hands the instance over.
* ``/api/v1/auth/*`` - login, sessions, password changes.
* ``/api/setup/*`` - first-run initialization, which creates the first
  administrator.

The ban is enforced here rather than left to the operator's judgement, because
the failure is unrecoverable and silent: a mistyped entry that happened to match
an admin route would not look like anything until someone found it. There is no
override, by request - see the module tests.

An anonymous caller gets read scopes only, and is rate limited by client
address rather than by key, so one open endpoint cannot become an unmetered
drain on the identity pool.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Final

from dtk.core.logging import get_logger
from dtk.core.types import Scope, UserRole

log = get_logger(__name__)

SETTING_KEY: Final = "api.public_endpoints"

#: Path prefixes that stay authenticated no matter what the setting says.
PROTECTED_PREFIXES: Final[tuple[str, ...]] = (
    "/api/v1/admin",
    "/api/v1/auth",
    "/api/setup",
)

#: The identity an unauthenticated caller is given on an opened endpoint. It is
#: a real `Principal` so nothing downstream has to special-case it, and it holds
#: exactly the two read scopes - never admin, never write.
ANONYMOUS_USER_ID: Final = uuid.UUID(int=0)
ANONYMOUS_SCOPES: Final[frozenset[Scope]] = frozenset({Scope.DOUYIN_READ, Scope.TIKTOK_READ})


def route_key(method: str, path: str) -> str:
    """The identifier an operator writes, e.g. ``GET /api/v1/{platform}/video``."""
    return f"{method.upper()} {path}"


def is_protected(path: str) -> bool:
    """Whether this path may never be opened."""
    return path.startswith(PROTECTED_PREFIXES)


def parse(raw: object) -> frozenset[str]:
    """Read the setting into a set of route keys, dropping what cannot apply.

    Tolerant of shape - a list, or one comma-separated string - because this is
    typed by a human into a settings field. Intolerant of content: an entry
    naming a protected path is dropped and logged rather than honoured, and an
    unparseable value yields an empty set, which is the safe direction.
    """
    entries: Iterable[object]
    if isinstance(raw, str):
        entries = raw.split(",")
    elif isinstance(raw, Sequence):
        entries = raw
    else:
        return frozenset()

    allowed: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        text = " ".join(entry.split()).upper() if entry.strip() else ""
        if not text or " " not in text:
            continue
        method, _, path = text.partition(" ")
        # The path is case sensitive; only the method was upper-cased above.
        original = " ".join(entry.split())
        path = original.partition(" ")[2]
        if is_protected(path):
            log.warning("api.public_endpoints.refused", method=method, path=path)
            continue
        allowed.add(route_key(method, path))
    return frozenset(allowed)


def is_public(raw: object, method: str, path: str) -> bool:
    """Whether this exact route has been opened by the operator."""
    if is_protected(path):
        return False
    return route_key(method, path) in parse(raw)


def anonymous(role: UserRole = UserRole.VIEWER) -> object:
    """Build the principal an opened endpoint runs as.

    Imported lazily by the caller to avoid a cycle: `dtk.api.deps` defines
    `Principal` and needs this module's rules.
    """
    from dtk.api.deps import Principal

    return Principal(
        user_id=ANONYMOUS_USER_ID,
        role=role,
        scopes=ANONYMOUS_SCOPES,
        api_key_id=None,
        rate_limit_per_min=None,
    )


__all__ = [
    "ANONYMOUS_SCOPES",
    "ANONYMOUS_USER_ID",
    "PROTECTED_PREFIXES",
    "SETTING_KEY",
    "anonymous",
    "is_protected",
    "is_public",
    "parse",
    "route_key",
]
