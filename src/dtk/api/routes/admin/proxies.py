"""Proxy administration: CRUD, bulk import and probes.

A proxy URL carries a username and a password, so it is stored as AES-GCM
ciphertext bound to the row id and it leaves this service only masked. There is
no "show me the proxy password" endpoint and there will not be one (doc 08).

Deleting a proxy retires the identities behind it rather than orphaning them.
Doc 02 forbids recombining an identity with a different egress, so an identity
whose proxy is gone has no future - keeping it would eventually mean sending
its cookies out through some other address, which is the single most
correlatable thing this system could do.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from dtk.api.deps import Principal
from dtk.api.routes import operations
from dtk.api.routes.admin.proxy_urls import ProxySpec, parse_many, parse_proxy, sample_of
from dtk.api.routes.operations import Maintenance
from dtk.api.routes.schemas import ProxyCreate, ProxyImport, ProxyUpdate
from dtk.api.routes.support import (
    audit,
    iso,
    manage_pool,
    mask_proxy_url,
    ok,
    read_admin,
)
from dtk.core.errors import InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.core.types import IdentityState
from dtk.db.models import Identity, Proxy
from dtk.identity.pool import IdentityPool

log = get_logger(__name__)

router = APIRouter(prefix="/proxies", tags=["admin"])


def _cipher(request: Request) -> Any:
    return request.app.state.cipher


def _row(request: Request, proxy: Proxy, *, identity_count: int | None = None) -> dict[str, Any]:
    """One proxy, with its credentials removed.

    Decrypting only to mask looks redundant, but the host and port are what let
    an operator tell two egresses apart, and they are not the secret part.
    """
    try:
        masked = mask_proxy_url(_cipher(request).decrypt(proxy.url_encrypted, aad=str(proxy.id)))
    except Exception:
        # A blob that will not decrypt means the master key changed; say so
        # rather than 500, because the fix is to restore the old key.
        log.error("proxy.undecryptable", proxy_id=str(proxy.id))
        masked = None
    payload: dict[str, Any] = {
        "id": str(proxy.id),
        "url_masked": masked,
        "decryptable": masked is not None,
        "label": proxy.label,
        "country": proxy.country,
        "timezone": proxy.timezone,
        "healthy": proxy.healthy,
        "last_check_at": iso(proxy.last_check_at),
        "created_at": iso(proxy.created_at),
    }
    if identity_count is not None:
        payload["identity_count"] = identity_count
    return payload


@router.get("", summary="List proxies")
async def list_proxies(
    request: Request,
    healthy_only: bool = Query(
        default=False, description="Only list proxies that passed their last probe."
    ),
    principal: Principal = Depends(read_admin),
) -> Any:
    """Every configured egress, oldest first.

    Credentials in a proxy URL are masked; the full URL is never returned.

    **Parameters**

    - `healthy_only` - hide proxies that failed their last probe.

    **Returns**

    Each proxy's masked URL, label, country, timezone, health and how many
    identities are bound to it.
    """
    stmt = select(Proxy).order_by(Proxy.created_at)
    if healthy_only:
        stmt = stmt.where(Proxy.healthy.is_(True))
    rows = (await request.state.db.scalars(stmt)).all()
    return ok(request, [_row(request, row) for row in rows])


@router.post("", summary="Add one proxy")
async def create_proxy(
    request: Request,
    body: ProxyCreate,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Add one egress to the pool.

    The URL is stored encrypted and only ever returned masked. To add many at
    once, use `/proxies/import` instead.

    **Parameters**

    - `url` - the proxy URL, `scheme://[user:pass@]host:port`.
    - `label` - an optional name to recognise it by.
    - `country`, `timezone` - optional overrides; otherwise they come from a
      GeoIP lookup of the exit address when the proxy is probed.

    **Returns**

    The stored proxy, with its URL masked.
    """
    spec = parse_proxy(body.url)
    proxy = await _store(
        request, spec, label=body.label, country=body.country, timezone=body.timezone
    )
    await audit(
        request,
        principal,
        "proxy.created",
        target_type="proxy",
        target_id=str(proxy.id),
        detail={"masked": spec.masked, "label": body.label},
    )
    return ok(request, _row(request, proxy), status_code=201)


@router.post("/import", summary="Bulk import a pasted proxy list")
async def import_proxies(
    request: Request,
    body: ProxyImport,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Accept a block of lines and report the fate of each one.

    Partial success is the point: a provider list with three bad rows should
    still import the other ninety-seven (doc 07).
    """
    created: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for line, outcome in parse_many(body.text):
        if not isinstance(outcome, ProxySpec):
            rejected.append(
                {
                    # The credential-free fragment the parser already built;
                    # recomputing it here is how the two drift apart.
                    "line": outcome.details.get("sample", sample_of(line)),
                    "error": outcome.details.get("reason", "unparsable"),
                }
            )
            continue
        proxy = await _store(request, outcome, label=body.label)
        created.append(_row(request, proxy))

    await audit(
        request,
        principal,
        "proxy.imported",
        target_type="proxy",
        target_id=None,
        detail={"created": len(created), "rejected": len(rejected)},
    )
    log.info("proxy.imported", created=len(created), rejected=len(rejected))
    return ok(
        request,
        {
            "created": created,
            "rejected": rejected,
            "counts": {
                "created": len(created),
                "rejected": len(rejected),
            },
        },
        status_code=201 if created else 200,
    )


@router.put("/{proxy_id}", summary="Update one proxy")
async def update_proxy(
    request: Request,
    body: ProxyUpdate,
    proxy_id: uuid.UUID = Path(description="The proxy to update."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Change one proxy's URL, label or geography.

    Only the fields you send are changed. Replacing the URL re-encrypts it and
    leaves the identities bound to this proxy in place.

    **Parameters**

    - `proxy_id` - the proxy to update.
    - `url`, `label`, `country`, `timezone` - any subset of these.

    **Returns**

    The updated proxy, with its URL masked.
    """
    session = request.state.db
    proxy = await session.get(Proxy, proxy_id)
    if proxy is None:
        raise NotFound("no such proxy")

    changed: list[str] = []
    if body.url is not None:
        spec = parse_proxy(body.url)
        # Re-encrypt under this row's id: the id is the additional
        # authenticated data, so a blob is not portable between rows.
        proxy.url_encrypted = _cipher(request).encrypt(spec.url, aad=str(proxy.id))
        changed.append("url")
    for field in ("label", "country", "timezone", "healthy"):
        value = getattr(body, field)
        if value is not None:
            setattr(proxy, field, value)
            changed.append(field)
    if not changed:
        raise InvalidParam("nothing to update", details={"fields": []})

    await session.flush()
    await audit(
        request,
        principal,
        "proxy.updated",
        target_type="proxy",
        target_id=str(proxy_id),
        detail={"fields": changed},
    )
    return ok(request, _row(request, proxy))


@router.post("/{proxy_id}/test", summary="Probe one proxy")
async def test_proxy(
    request: Request,
    proxy_id: uuid.UUID,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Queue a connectivity and GeoIP probe for this egress."""
    if await request.state.db.get(Proxy, proxy_id) is None:
        raise NotFound("no such proxy")
    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.PROXY_TEST.value,
        params={"proxy_id": str(proxy_id)},
        coalesce=False,
    )
    return ok(request, {"task_id": str(task_id)}, status_code=202)


@router.delete("/{proxy_id}", summary="Delete a proxy and retire what used it")
async def delete_proxy(
    request: Request,
    proxy_id: uuid.UUID = Path(description="The proxy to delete."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Delete one egress, and retire every identity bound to it.

    An identity is tied to the exit it was minted behind, so it cannot outlive
    that exit: the identities go too, and their cookies are wiped. This cannot
    be undone.

    **Parameters**

    - `proxy_id` - the proxy to delete.

    **Returns**

    How many identities were retired along with it.
    """
    session = request.state.db
    proxy = await session.get(Proxy, proxy_id)
    if proxy is None:
        raise NotFound("no such proxy")

    bound = (
        await session.scalars(
            select(Identity).where(
                Identity.proxy_id == proxy_id,
                Identity.state != IdentityState.RETIRED.value,
            )
        )
    ).all()
    pool = IdentityPool(_cipher(request))
    for identity in bound:
        await pool.retire(session, str(identity.id), "proxy removed")

    await session.delete(proxy)
    await session.flush()
    await audit(
        request,
        principal,
        "proxy.deleted",
        target_type="proxy",
        target_id=str(proxy_id),
        detail={"retired_identities": len(bound)},
    )
    log.info("proxy.deleted", proxy_id=str(proxy_id), retired_identities=len(bound))
    return ok(request, {"id": str(proxy_id), "retired_identities": len(bound)})


async def _store(
    request: Request,
    spec: ProxySpec,
    *,
    label: str | None = None,
    country: str | None = None,
    timezone: str | None = None,
) -> Proxy:
    """Persist one proxy, encrypting under an id chosen up front.

    The id has to exist before the ciphertext does, because it is the AAD; that
    is why the row is not left to the database default here.
    """
    proxy_id = uuid.uuid4()
    proxy = Proxy(
        id=proxy_id,
        url_encrypted=_cipher(request).encrypt(spec.url, aad=str(proxy_id)),
        label=label,
        country=country,
        timezone=timezone,
    )
    request.state.db.add(proxy)
    await request.state.db.flush()
    return proxy


__all__ = ["router"]
