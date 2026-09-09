"""Which endpoints are open, as a list the console can render as switches.

``api.public_endpoints`` is a list of ``"<METHOD> <path>"`` strings, and asking
an operator to type those by hand is asking them to typo one. A typo here is
quiet - the entry simply never matches, and the endpoint they meant to open
stays closed until somebody notices - so the console needs the real route table
to switch against rather than a free-text box.

This is also where the permanent ban becomes visible. ``dtk.api.public_endpoints``
refuses admin, auth and setup paths whatever the setting says; a switch that
looked available and then silently did nothing would be worse than no switch, so
those rows come back flagged and the console renders them locked.

The route table is read from the application itself, so an endpoint added
tomorrow appears here without anyone editing a list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from dtk.api import public_endpoints
from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import ok, read_admin
from dtk.core.logging import get_logger

log = get_logger(__name__)

# Tagged by the aggregate router in __init__.py; repeating it here is what
# put ["admin", "admin"] on every one of these operations.
router = APIRouter()

#: Methods worth offering. HEAD and OPTIONS are answered by the framework and
#: are not something an operator opens or closes.
_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def _unauthenticated_routes(request: Request) -> frozenset[str]:
    """Route keys that require no credential at all, whatever the setting says.

    Derived from the real dependency tree, because the setting cannot express
    this and the document does not either. ``api.public_endpoints`` describes
    endpoints an operator *opened*; a handful of routes were never closed in the
    first place - the iOS Shortcut asks for its release notes before it has
    anywhere to put a key, and the setup status probe answers before there is an
    account to authenticate as.

    Without this the listing reported ``open_count: 0`` while two endpoints were
    answering 200 to anyone, and offered the operator an off switch on a route
    no switch can close. This function is the only thing here that can tell the
    difference between "closed" and "never had a lock".

    Walking the tree needs care: ``include_router`` leaves a proxy whose
    ``original_router`` holds the real routes, and those already carry their full
    path - so the prefix must NOT be re-applied. Getting that wrong is what made
    an earlier attempt at :func:`_rows` report every admin route as switchable,
    which is why that one reads the OpenAPI document instead.
    """
    from fastapi.routing import APIRoute

    from dtk.api.deps import current_principal, enforce_rate_limit

    guards = {enforce_rate_limit, current_principal}

    def dependencies(dependant: Any) -> Any:
        yield dependant
        for nested in dependant.dependencies:
            yield from dependencies(nested)

    def routes(collection: Any) -> Any:
        for route in collection:
            if isinstance(route, APIRoute):
                yield route
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from routes(getattr(inner, "routes", ()))

    open_keys: set[str] = set()
    for route in routes(request.app.routes):
        calls = {node.call for node in dependencies(route.dependant)}
        if calls & guards:
            continue
        for method in route.methods or ():
            if method.upper() in _METHODS:
                open_keys.add(public_endpoints.route_key(method, route.path))
    return frozenset(open_keys)


def _rows(request: Request, opened: frozenset[str]) -> list[dict[str, Any]]:
    """Every documented operation, with what it is and whether it is open.

    Read from the OpenAPI document rather than by walking ``app.routes``. Two
    reasons, and the second is the important one:

    * The document is what an operator sees in Swagger, and
      ``api.public_endpoints`` is matched against exactly those path templates.
      Building the switches from the same source means the switch and the
      setting cannot disagree about what a path is called.
    * Walking the route tree gives the WRONG paths. ``include_router`` leaves a
      proxy whose ``original_router`` holds the routes as the sub-router
      declared them - ``/api-keys``, not ``/api/v1/admin/api-keys`` - so the
      prefix that makes a path recognisably an admin one is missing. A first
      attempt at this function did exactly that, and every admin route came back
      unprotected and switchable. The ban would still have held at request time,
      because `dtk.api.deps` re-checks the real path, but the console would have
      shown a switch that lied.
    """
    schema = request.app.openapi()
    always_open = _unauthenticated_routes(request)
    rows: list[dict[str, Any]] = []
    for path, operations in (schema.get("paths") or {}).items():
        if not path.startswith("/api"):
            continue
        for method, operation in operations.items():
            if method.upper() not in _METHODS or not isinstance(operation, dict):
                continue
            key = public_endpoints.route_key(method, path)
            protected = public_endpoints.is_protected(path)
            rows.append(
                {
                    "key": key,
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary") or "",
                    "tags": list(operation.get("tags") or []),
                    # Locked rows can never be opened; the console shows why
                    # rather than offering a switch that would do nothing.
                    "protected": protected,
                    # Never had a lock, as opposed to having one that is open.
                    # A switch cannot close these either, so the console has to
                    # be able to tell the two apart.
                    "always_public": key in always_open,
                    "public": key in always_open or ((not protected) and key in opened),
                }
            )
    rows.sort(key=lambda row: (row["path"], row["method"]))
    return rows


@router.get(
    "/endpoints/access",
    summary="Which endpoints are served without a key",
    openapi_extra={I18N_KEY: "endpoints_access"},
)
async def endpoint_access(
    request: Request,
    principal: Principal = Depends(read_admin),
) -> Any:
    """Every endpoint, with whether it currently needs a credential.

    The list is built from the running route table, so it cannot drift from what
    the API actually serves.

    **Returns**

    One row per operation: its method and path, the summary shown in the API
    document, whether it is currently open, and whether it is permanently
    protected. Admin, authentication and setup endpoints are always protected
    and can never be opened.
    """
    configured = request.app.state.config.get(public_endpoints.SETTING_KEY)
    rows = _rows(request, public_endpoints.parse(configured))
    return ok(
        request,
        {
            "setting": public_endpoints.SETTING_KEY,
            "protected_prefixes": list(public_endpoints.PROTECTED_PREFIXES),
            "open_count": sum(1 for row in rows if row["public"]),
            "endpoints": rows,
        },
    )


__all__ = ["router"]
