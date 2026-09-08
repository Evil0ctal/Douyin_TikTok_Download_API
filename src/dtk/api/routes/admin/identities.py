"""Identity pool administration.

Everything here is gated on ``identity:manage`` (or ``admin``) because these
routes touch credentials: doc 06 requires that an ordinary read key cannot
reach them, whoever created it.

The hard rule of this module: **no response ever contains a cookie**. Import is
one-way. What comes back is the set of cookie *names*, their masked values, the
inferred browser and whether the jar carries a logged-in session - enough for a
person to confirm they pasted the right thing, and useless to anyone who steals
the response (doc 06, doc 08).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from dtk.api.deps import Principal
from dtk.api.routes import operations
from dtk.api.routes.operations import Maintenance
from dtk.api.routes.schemas import IdentityImport, MintRequest, RetireRequest
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    audit,
    iso,
    language,
    manage_pool,
    ok,
    read_admin,
)
from dtk.core.errors import InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import IdentitySource, IdentityState, Language, Platform
from dtk.db.models import Identity
from dtk.i18n.catalog import t
from dtk.identity.importing import ImportReport, build_report
from dtk.identity.pool import IdentityPool

log = get_logger(__name__)

router = APIRouter(prefix="/identities", tags=["admin"])


def _pool(request: Request) -> IdentityPool:
    return IdentityPool(request.app.state.cipher)


def _row(identity: Identity) -> dict[str, Any]:
    """The console view of one identity. Never widens to the cookie column."""
    fingerprint = identity.fingerprint or {}
    return {
        "id": str(identity.id),
        "platform": identity.platform,
        "state": identity.state,
        "source": identity.source,
        "authenticated": identity.authenticated,
        "proxy_id": str(identity.proxy_id) if identity.proxy_id else None,
        "consecutive_fails": identity.consecutive_fails,
        "cooldown_until": iso(identity.cooldown_until),
        "minted_at": iso(identity.minted_at),
        "last_used_at": iso(identity.last_used_at),
        "retired_at": iso(identity.retired_at),
        "retire_reason": identity.retire_reason,
        "fingerprint": {
            "browser_family": fingerprint.get("browser_family"),
            "browser_major": fingerprint.get("browser_major"),
            "platform": fingerprint.get("platform"),
            "language": fingerprint.get("language"),
            "timezone": fingerprint.get("timezone"),
        },
    }


def _warnings(report: ImportReport, lang: Language) -> list[str]:
    """The preview's warnings as sentences the caller can read.

    The one about the paste carrying a live session is the warning that most
    needs to land, so it cannot be the one string on this page still in English.
    The codes travel beside the sentences: a console styling the security
    warning differently must not have to match on prose (doc 14).
    """
    return [t(warning.key, lang, **warning.args) for warning in report.warnings]


@router.get("", summary="List identities with state and health")
async def list_identities(
    request: Request,
    platform: Platform | None = Query(
        default=None, description="Only identities for this platform."
    ),
    state: IdentityState | None = Query(default=None, description="Only identities in this state."),
    limit: int = Query(
        default=DEFAULT_ADMIN_PAGE_SIZE,
        ge=1,
        le=MAX_ADMIN_PAGE_SIZE,
        description="Maximum identities to return.",
    ),
    principal: Principal = Depends(read_admin),
) -> Any:
    """The identity pool, newest first.

    Cookies are never returned; this is the health view, not the credential.

    **Parameters**

    - `platform` - restrict to one platform.
    - `state` - restrict to one state, such as `active` or `cooling`.
    - `limit` - how many to return.

    **Returns**

    Each identity's id, platform, state, proxy, when it was minted and last
    used, and its recent success rate.
    """
    stmt = select(Identity)
    if platform is not None:
        stmt = stmt.where(Identity.platform == platform.value)
    if state is not None:
        stmt = stmt.where(Identity.state == state.value)
    stmt = stmt.order_by(Identity.minted_at.desc()).limit(limit)
    rows = (await request.state.db.scalars(stmt)).all()
    return ok(request, [_row(row) for row in rows])


@router.post("/mint", summary="Mint guest identities")
async def mint(
    request: Request,
    body: MintRequest,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Queue a minting job per identity requested.

    Minting drives a real browser through the proxy, which takes seconds and is
    not on the request path; it is queued like any other job (doc 02).
    """
    task_ids: list[str] = []
    for _ in range(body.count):
        task_id, _state = await operations.submit(
            request,
            principal,
            endpoint=Maintenance.IDENTITY_MINT.value,
            params={"platform": body.platform.value, "proxy_id": body.proxy_id},
            # Two mint requests are not the same request: coalescing them would
            # silently hand back one identity where two were asked for.
            coalesce=False,
        )
        task_ids.append(str(task_id))
    await audit(
        request,
        principal,
        "identity.mint_requested",
        target_type="platform",
        target_id=body.platform.value,
        detail={"count": body.count},
    )
    return ok(request, {"task_ids": task_ids, "count": len(task_ids)}, status_code=202)


@router.post("/import", summary="Import a logged-in cookie jar")
async def import_identity(
    request: Request,
    body: IdentityImport,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Detect the paste format, report what was understood, then store it.

    ``dry_run`` returns the report without writing anything, which is what the
    console shows before the user commits: a mistyped jar is far easier to spot
    here than in a week of failing requests.
    """
    report = build_report(
        body.cookies,
        body.platform,
        user_agent=body.user_agent,
        language=body.language,
        timezone=body.timezone,
    )
    warnings = _warnings(report, language(request))
    summary: dict[str, Any] = {
        "detected_format": report.detected_format.value,
        "cookie_names": sorted(report.cookies),
        "cookies_masked": report.masked(),
        "authenticated": report.authenticated,
        "expires_at": iso(report.expires_at),
        "missing_required": list(report.missing_required),
        "warnings": warnings,
        "warning_codes": [warning.code.value for warning in report.warnings],
        "usable": report.usable,
        "browser_family": (
            report.fingerprint.browser_family.value if report.fingerprint.browser_family else None
        ),
        "browser_major": report.fingerprint.browser_major,
    }

    if body.dry_run:
        return ok(request, {"stored": False, "report": summary})
    if not report.usable:
        raise InvalidParam(
            "this cookie set cannot be used as an identity",
            details={
                "missing_required": list(report.missing_required),
                "warnings": warnings,
                "warning_codes": [warning.code.value for warning in report.warnings],
            },
        )

    proxy_id = _as_uuid(body.proxy_id, "proxy_id")
    identity_id = await _pool(request).add(
        request.state.db,
        platform=body.platform,
        cookies=report.cookies,
        fingerprint=report.fingerprint,
        source=IdentitySource.IMPORTED,
        proxy_id=proxy_id,
        authenticated=report.authenticated,
    )
    await audit(
        request,
        principal,
        "identity.imported",
        target_type="identity",
        target_id=str(identity_id),
        detail={
            "platform": body.platform.value,
            "authenticated": report.authenticated,
            "cookie_names": sorted(report.cookies),
            "format": report.detected_format.value,
        },
    )
    log.info(
        "identity.imported",
        identity_id=str(identity_id),
        platform=body.platform.value,
        authenticated=report.authenticated,
    )
    return ok(
        request,
        {"stored": True, "identity_id": str(identity_id), "report": summary},
        status_code=201,
    )


@router.post("/{identity_id}/test", summary="Probe one identity")
async def test_identity(
    request: Request,
    identity_id: uuid.UUID,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Queue a single real request on this identity.

    The first thing anyone does while debugging is try one identity on its own.
    Without this they have to send production traffic to find out, which is
    slow and pollutes the health statistics (doc 07).
    """
    identity = await request.state.db.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity")
    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.IDENTITY_TEST.value,
        params={"identity_id": str(identity_id)},
        coalesce=False,
    )
    return ok(request, {"task_id": str(task_id)}, status_code=202)


@router.delete("/{identity_id}", summary="Retire an identity")
async def retire_identity(
    request: Request,
    identity_id: uuid.UUID,
    body: RetireRequest | None = None,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Retire and wipe. The statistics stay, the credential does not (doc 05)."""
    session = request.state.db
    identity = await session.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity")
    if identity.state == IdentityState.RETIRED.value:
        return ok(request, {"id": str(identity_id), "state": IdentityState.RETIRED.value})

    reason = body.reason if body is not None else "retired from the console"
    await _pool(request).retire(session, str(identity_id), reason)
    await audit(
        request,
        principal,
        "identity.retired",
        target_type="identity",
        target_id=str(identity_id),
        detail={"reason": reason, "platform": identity.platform},
    )
    return ok(request, {"id": str(identity_id), "state": IdentityState.RETIRED.value})


def _as_uuid(value: str | None, field: str) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise InvalidParam(f"{field} must be a UUID", details={"field": field}) from None


__all__ = ["router"]
