"""Every administrative route names a guard that knows about roles.

A console session carries no scopes and passes every scope check, so a route
gated on a scope alone is open to every signed-in role. That is how a viewer
reached `/tools/identity`, and nothing would have said so: the route worked for
the operators it was written for. This walks the mounted application instead
of trusting each route to remember, so a new admin route without a role-aware
guard fails here on the day it is written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute

from dtk.api.routes.support import admin_only, manage_pool, read_admin, read_admin_demo

#: Guards with a role floor. `read_admin` and `read_admin_demo` admit a viewer
#: and the demo account by design - they are for reading the admin surface.
READ_GUARDS = (read_admin, read_admin_demo, manage_pool, admin_only)
WRITE_GUARDS = (manage_pool, admin_only)

#: Admin writes that sit behind a read guard and check the role inline, each
#: for a reason the route documents. Listed so that joining it is a decision.
INLINE_ROLE_CHECKS = {
    # A runtime setting needs an operator, a SENSITIVE one an admin and the
    # admin scope and `confirm` - decided per key, so not one guard.
    "dtk.api.routes.admin.settings.update_setting",
    "dtk.api.routes.admin.settings.reset_setting",
}

#: Outside /admin, the routes that hand over or act as a credential.
CREDENTIAL_ROUTES = {
    ("POST", "/api/v1/tools/identity"),
}

ADMIN_MODULE = "dtk.api.routes.admin."


def _app() -> Any:
    from dtk.api.app import create_app
    from dtk.core.config import BootstrapSettings

    return create_app(
        BootstrapSettings(
            secret_key="test-secret-key-for-route-guards-0123456789abcdef",
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost:6379/0",
        )
    )


def _calls(dependant: Any) -> Iterator[Callable[..., Any]]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _calls(dependency)


def _api_routes(collection: Any) -> Iterator[APIRoute]:
    """Every endpoint, through the proxies `include_router` leaves behind.

    The same walk as `dtk.api.routes.admin.access`: a proxy's
    `original_router` holds the real routes, already carrying their full path.
    """
    for route in collection:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _api_routes(getattr(inner, "routes", ()))


def _handler(route: APIRoute) -> str:
    return f"{route.endpoint.__module__}.{route.endpoint.__name__}"


def _routes() -> list[tuple[str, str, set[Callable[..., Any]]]]:
    """(method, handler, guards) for every endpoint.

    Keyed by handler, not path: the admin sub-routers hold their routes under
    the path they declared, without the `/api/v1/admin` prefix, so the module
    is what reliably says a route is administrative.
    """
    out = []
    for route in _api_routes(_app().routes):
        calls = set(_calls(route.dependant))
        for method in sorted(route.methods or ()):
            out.append((method, _handler(route), calls))
    return out


def _paths() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in _api_routes(_app().routes)
        for method in route.methods or ()
    }


ROUTES = _routes()
ADMIN = [r for r in ROUTES if r[1].startswith(ADMIN_MODULE)]


def test_the_walk_found_the_admin_surface() -> None:
    """An empty parametrisation would pass everything below."""
    assert len(ADMIN) > 40


def _id(value: Any) -> str:
    return value.removeprefix(ADMIN_MODULE) if isinstance(value, str) else ""


@pytest.mark.parametrize(("method", "handler", "calls"), ADMIN, ids=_id)
def test_every_admin_route_has_a_role_aware_guard(
    method: str, handler: str, calls: set[Callable[..., Any]]
) -> None:
    assert calls & set(READ_GUARDS), f"{method} {handler} has no role-aware guard"


@pytest.mark.parametrize(
    ("method", "handler", "calls"), [r for r in ADMIN if r[0] not in ("GET", "HEAD")], ids=_id
)
def test_every_admin_write_needs_an_operator(
    method: str, handler: str, calls: set[Callable[..., Any]]
) -> None:
    if handler in INLINE_ROLE_CHECKS:
        return
    assert calls & set(WRITE_GUARDS), f"{method} {handler} is a write behind a read guard"


def test_the_inline_exceptions_still_exist() -> None:
    """A stale entry would quietly stop meaning anything."""
    assert {handler for _, handler, _ in ADMIN} >= INLINE_ROLE_CHECKS


@pytest.mark.parametrize(("method", "path"), sorted(CREDENTIAL_ROUTES))
def test_credential_routes_outside_admin_need_an_operator(method: str, path: str) -> None:
    assert (method, path) in _paths()
    calls = next(
        set(_calls(route.dependant))
        for route in _api_routes(_app().routes)
        if route.path == path and method in (route.methods or ())
    )
    assert manage_pool in calls
