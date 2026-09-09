"""HTTP surface.

``dtk.api.app.create_app`` calls :func:`register_routes` and nothing else in
this package, so the whole route table is assembled here.

Layout, in the order a caller meets it:

======================  ===================================================
``system``              ``/healthz``, ``/readyz``, ``/api/v1/system/status``
``setup``               ``/api/setup/*`` - the one-time bootstrap
``auth``                ``/api/v1/auth/*`` - console sessions
``ios``                 ``/api/v1/ios/shortcut``
``tasks``               ``/api/v1/tasks/{id}`` and its event stream
``content``             ``/api/v1/parse`` and the per-platform reads
``tools``               ``/api/v1/tools/*`` - signing, link parsing
``archive``             ``/api/v1/archive/*`` - what this instance has stored
``downloads``           ``/api/v1/downloads/*`` - media stored on the local disk
``admin``               ``/api/v1/admin/*``
======================  ===================================================

Registration order is not cosmetic. ``/api/v1/tasks/{task_id}`` is registered
before ``/api/v1/{platform}/video`` so a task id can never be read as a
platform name, and the localized documentation routes replace FastAPI's own,
which were installed when the application object was built.
"""

from __future__ import annotations

from fastapi import FastAPI

from dtk.api.routes import (
    admin,
    archive,
    auth,
    content,
    downloads,
    ios,
    openapi,
    setup,
    system,
    tasks,
    tools,
)
from dtk.core.logging import get_logger
from dtk.mcp import mount as mount_mcp

log = get_logger(__name__)

#: Routers in registration order.
ROUTERS = (
    system.router,
    setup.router,
    auth.router,
    ios.router,
    tasks.router,
    content.router,
    archive.router,
    downloads.router,
    tools.router,
    admin.router,
)


def register_routes(app: FastAPI) -> None:
    """Mount every router and install the two application-level hooks."""
    for router in ROUTERS:
        app.include_router(router)

    # Replaces FastAPI's /openapi.json, /swagger and /redoc with ?lang= aware
    # versions (doc 14).
    openapi.install(app)
    # Issues and prints the first-run setup token once the application's own
    # lifespan has opened the database and Redis (doc 06).
    setup.install_startup_hook(app)

    # The MCP server shares this process and this authentication. mount() also
    # chains its session manager onto the application lifespan: a mounted ASGI
    # app never receives lifespan events of its own, and without that chaining
    # the streamable-http endpoint fails with "Task group is not initialized".
    mount_mcp(app)

    log.debug("api.routes_registered", routers=len(ROUTERS))


__all__ = ["ROUTERS", "register_routes"]
