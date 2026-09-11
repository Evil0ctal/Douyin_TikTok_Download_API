"""What the public demo account may change, which is almost nothing.

Scopes already stop the published demo *key* from doing damage: it holds two
platform read scopes, so every endpoint gated on `media:write`, `archive:read`
or `admin` refuses it. That is not enough on its own, because the demo
**session** is a console session and console sessions are deliberately not
scoped - they are bounded by role, and the role gates are on reads.

So a stranger logged into the demo console would arrive at
``POST /api/v1/downloads`` holding a principal that passes every scope check
there is. This module is what stops them, and it is written as a deny-by-method
rule with a short allowlist rather than as a role gate per route, for one
reason: there are 55 endpoints that change something today and there will be
more next year, and a rule that has to be remembered on each new one is a rule
that will be forgotten on one of them. Here, a new endpoint is closed to the
demo the moment it is written, and opening it is a deliberate edit to a list
that is short enough to read.

The allowlist holds exactly the read-shaped POSTs a demo needs:

* ``/api/v1/parse`` and ``/api/v1/tasks/batch`` - the scraping calls. They are
  POSTs because they carry a body, not because they change this instance;
  what they change is nothing, and what they spend is an identity, which is the
  thing a demo exists to demonstrate.
* the three ``/api/v1/tools`` calls that compute an answer locally - splitting
  pasted text into links, decoding a signature, generating one. None of them
  touches the database or the pool.
* ``/api/v1/auth/logout``, so a visitor can leave.

Deliberately absent, and each one for its own reason:

* ``/api/v1/auth/password`` - the demo password is shared. One visitor changing
  it locks out every other visitor and the README along with them.
* ``/api/v1/tools/identity`` - mints a real guest identity through the browser
  container. Read-shaped in its response, expensive and stateful in fact.
* ``/api/v1/downloads`` and everything under it - spends the operator's disk.
* every ``/api/v1/admin`` route - the account cannot reach most of them by role
  anyway, and the ones it can reach are reads.
"""

from __future__ import annotations

from typing import Final

from fastapi import Request

from dtk.core.errors import ForbiddenScope
from dtk.core.types import UserRole

#: Methods that cannot change anything by definition, so they are never checked
#: here and are decided by the role and scope gates like any other caller's.
SAFE_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})

#: The writes a demo caller may perform, as ``"<METHOD> <path template>"`` -
#: the same spelling :mod:`dtk.api.public_endpoints` uses, so the two lists read
#: alike and a path copied from one works in the other.
ALLOWED_WRITES: Final[frozenset[str]] = frozenset(
    {
        "POST /api/v1/parse",
        "POST /api/v1/tasks/batch",
        "POST /api/v1/tools/parse-batch",
        "POST /api/v1/tools/decode",
        "POST /api/v1/tools/sign",
        "POST /api/v1/auth/logout",
    }
)


def route_template(request: Request) -> str | None:
    """The matched route's path template, or None when nothing matched."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None


def is_allowed(method: str, path: str | None) -> bool:
    """Whether a demo caller may issue this request.

    An unmatched route is refused rather than allowed. A request that reached
    authentication without matching a route is on its way to a 404, and
    answering "forbidden" to it costs a demo visitor nothing while keeping the
    default on the closed side.
    """
    upper = method.upper()
    if upper in SAFE_METHODS:
        return True
    if path is None:
        return False
    return f"{upper} {path}" in ALLOWED_WRITES


def refuse_write(request: Request, role: UserRole) -> None:
    """Raise unless this request is something the demo account may do.

    A no-op for every role but DEMO, so it can sit on the authentication path
    without every other caller paying for it.
    """
    if role is not UserRole.DEMO:
        return
    if is_allowed(request.method, route_template(request)):
        return
    # Named the caller and the method and stopped there, which told a reader
    # what they were and nothing about what would work. `required_role` is the
    # floor of the ladder above DEMO: every other role may write.
    raise ForbiddenScope(
        "the demo account is read-only",
        details={
            "required_roles": [UserRole.VIEWER.value],
            "have_role": UserRole.DEMO.value,
            "have_scopes": [],
            "via": "session",
            "method": request.method.upper(),
        },
    )


__all__ = [
    "ALLOWED_WRITES",
    "SAFE_METHODS",
    "is_allowed",
    "refuse_write",
    "route_template",
]
