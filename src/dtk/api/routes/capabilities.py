"""Which operations each platform serves, for a client that has to know first.

The platforms are not symmetric past the core five, and the only answer a
caller had was to ask and be refused: a Bilibili author's likes come back as
``UNSUPPORTED_CONTENT``, and so do six other endpoints the console's workbench
listed for every platform alike. Seven refusals is a poor way to learn a table.

The table already exists - :data:`dtk.worker.registry.ENDPOINTS`, the same one
:func:`dtk.api.routes.content.supported` checks before it refuses - so this
publishes it rather than restating it. ``GET /api/v1/admin/endpoints/health``
looked like it already did, and does not: it walks each adapter's own endpoint
table, which also holds the internal calls no route serves (Bilibili's stream
lookup, every platform's session check), and it costs two Redis reads per row
to answer a question that never changes while the process runs.

Open to every role including the demo account, and to any key: what a platform
can be asked is documented in the API reference anyway, and a console that can
only learn it at the operator role would grey out the wrong rows for everyone
else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import demo_read, ok
from dtk.core.types import LoginNeed
from dtk.platforms.base import EndpointSpec
from dtk.platforms.registry import available_platforms, get_adapter
from dtk.worker import registry

router = APIRouter(tags=["system"])


def capability_table() -> dict[str, list[dict[str, Any]]]:
    """Every platform, each with the operations it serves, sorted by name.

    A platform with an adapter but nothing in the registry still gets a key,
    with an empty list: "this instance knows the platform and serves nothing on
    it" is a different answer from "this instance has never heard of it".

    ``session_bound`` is carried because it changes what a caller has to send,
    not just whether the call exists: such an endpoint answers only about the
    identity sending it, so it is refused without ``identity``.

    ``login`` and ``paging_login`` say what a guest identity loses on it -
    ``none``, ``preferred`` or ``required`` - for the first page and for every
    page after it, ``paging_login`` being null where later pages are no
    different. It is what decides which identities rotation sends, and the
    console could not offer "only the endpoints that need a login" without it.
    """
    table: dict[str, list[dict[str, Any]]] = {name: [] for name in available_platforms()}
    for definition in sorted(registry.ENDPOINTS.values(), key=lambda entry: entry.name):
        spec = _spec(definition.platform.value, definition.name)
        table.setdefault(definition.platform.value, []).append(
            {
                "operation": definition.capability.value,
                "endpoint": definition.name,
                "session_bound": definition.session_bound,
                "login": spec.login.value if spec else LoginNeed.NONE.value,
                "paging_login": (
                    spec.paging_login.value if spec and spec.paging_login is not None else None
                ),
            }
        )
    return table


def _spec(platform: str, endpoint: str) -> EndpointSpec | None:
    """The adapter's own declaration of an endpoint the registry serves."""
    # EndpointTable has no .get(); ruff's suggestion to use one does not apply.
    table = get_adapter(platform).endpoints
    if endpoint not in table:
        return None
    return table[endpoint]


@router.get(
    "/api/v1/system/capabilities",
    summary="What each platform serves",
    openapi_extra={I18N_KEY: "system_capabilities"},
)
async def capabilities(request: Request, principal: Principal = Depends(demo_read)) -> Any:
    """The operations each platform serves, from the endpoint registry.

    Static for the life of the process: the registry is built once at import
    from the platform adapters, so nothing here reads the database or Redis.
    """
    return ok(request, capability_table())


__all__ = ["capability_table", "router"]
