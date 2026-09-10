"""Administrative API, mounted under ``/api/v1/admin``.

Split by subject rather than kept as one large module: identity handling,
proxy handling and user handling have almost nothing in common beyond needing
authorization, and one file per subject keeps each short enough to read whole.

Authorization is layered by what an action can break, not by which file it
lives in (doc 15):

* viewer   - read every board here
* operator - identities, proxies, API keys, diagnostics, notification tests
* admin    - users, SENSITIVE settings, backups

An API key additionally needs ``identity:manage`` or ``admin``, whatever role
its owner holds.
"""

from __future__ import annotations

from fastapi import APIRouter

from dtk.api.routes.admin import (
    access,
    api_keys,
    demo,
    health,
    identities,
    logs,
    maintenance,
    proxies,
    settings,
    users,
    watchlist,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

router.include_router(identities.router)
router.include_router(proxies.router)
router.include_router(api_keys.router)
router.include_router(settings.router)
router.include_router(demo.router)
router.include_router(users.router)
router.include_router(health.router)
router.include_router(access.router)
router.include_router(logs.router)
router.include_router(maintenance.router)
router.include_router(watchlist.router)

__all__ = ["router"]
