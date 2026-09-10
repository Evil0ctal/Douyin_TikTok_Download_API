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
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.api.deps import Principal
from dtk.api.routes import operations
from dtk.api.routes.openapi import ACCEPTED_RESPONSES, CREATED_RESPONSES, I18N_KEY
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
from dtk.db.models import Identity, Proxy
from dtk.i18n.catalog import t
from dtk.identity import mint_log, pool
from dtk.identity.importing import (
    REQUIRED,
    SESSION_MARKERS,
    USEFUL,
    ImportReport,
    build_report,
    mask_value,
)
from dtk.identity.pool import IdentityPool
from dtk.scheduler.health import score
from dtk.signing.native.websign import UIFID_COOKIE_NAMES, VERIFY_FP_COOKIE

log = get_logger(__name__)

router = APIRouter(prefix="/identities")


def _pool(request: Request) -> IdentityPool:
    return IdentityPool(request.app.state.cipher)


#: The session value each platform issues to a browser and no algorithm can
#: produce, with the length below which it is the bootstrap value rather than
#: the usable one. Measured 2026-09-08 across a 13-identity pool: every TikTok
#: identity holding a 152-character msToken signed successfully, and every one
#: holding the 128-character document value was refused - with a correct
#: signature, because the token is inside the sealed bytes.
#:
#: This is the signal that was missing. A spent or bootstrap session looks
#: exactly like a broken signer from the outside, and the console had no way to
#: tell an operator which one they were looking at.
_SESSION_COOKIE: Final[Mapping[str, tuple[str, int]]] = MappingProxyType(
    {
        Platform.TIKTOK.value: ("msToken", 144),
        Platform.DOUYIN.value: ("UIFID_TEMP", 32),
    }
)


def _parse_cookies(header: str) -> dict[str, str]:
    """The jar as name/value pairs. Same shape `IdentityPool.load` reads."""
    cookies: dict[str, str] = {}
    for chunk in header.split(";"):
        name, sep, value = chunk.partition("=")
        if sep:
            cookies[name.strip()] = value.strip()
    return cookies


def _session_health(platform: str, cookies: Mapping[str, str]) -> dict[str, Any]:
    """Whether this identity still holds a usable session, never what it is.

    Returns the name of the cookie that decides it and a verdict, and nothing
    that could reconstitute the value: this endpoint's contract is that it never
    widens to the cookie column, and a length is already more than it needs to
    say. `held` is a boolean rather than the length for the same reason.
    """
    expected = _SESSION_COOKIE.get(platform)
    if expected is None:
        return {"cookie": None, "verdict": "unknown"}
    name, minimum = expected
    value = cookies.get(name) or ""
    if not value:
        verdict = "missing"
    elif len(value) < minimum:
        verdict = "too_short"
    else:
        verdict = "ok"
    return {"cookie": name, "verdict": verdict, "held": bool(value)}


#: Cookies the signer reads even though the importer does not judge a paste on
#: them. `UIFID_TEMP` is what a Douyin mint yields and what the web signature
#: SDK signs with; without these here it came back as "other", and the console
#: rendered "not something this build reads" over a cookie this build cannot
#: sign without.
_SIGNING_COOKIES: Final[frozenset[str]] = frozenset({*UIFID_COOKIE_NAMES, VERIFY_FP_COOKIE})


def _cookie_role(name: str) -> str:
    """Why this cookie matters, using the sets the rest of the build judges by.

    Four buckets rather than a description per cookie: the platforms add and
    retire cookies constantly, and a server-side glossary would be a list to
    keep current in a file nobody opens. What the server knows for certain is
    which names this build treats as load bearing; the console renders the
    prose, and an unknown name still gets an honest "other".
    """
    if name in SESSION_MARKERS:
        return "session"
    if any(name in required for required in REQUIRED.values()):
        return "required"
    if name in USEFUL or name in _SIGNING_COOKIES:
        return "useful"
    return "other"


async def _proxy_labels(
    session: AsyncSession, identities: Sequence[Identity]
) -> dict[uuid.UUID, str | None]:
    """The label of every proxy this page is bound to, in one statement.

    One query for the whole page rather than one per row: the console polls
    this listing every five seconds at ``limit=200``, and each row already
    costs an AES-GCM decrypt of its jar. A lookup per row would hang two
    hundred more round-trips off that poll to answer a question about far
    fewer proxies - identities share egresses, so the distinct set is small.

    Only the label is selected. The URL is a credential (doc 08), and no shape
    of it, masked or otherwise, has any business on this endpoint.
    """
    bound = {identity.proxy_id for identity in identities if identity.proxy_id is not None}
    if not bound:
        return {}
    rows = await session.execute(select(Proxy.id, Proxy.label).where(Proxy.id.in_(bound)))
    return dict(rows.tuples().all())


def _row(
    identity: Identity,
    session: dict[str, Any] | None = None,
    health: float | None = None,
    proxy_label: str | None = None,
) -> dict[str, Any]:
    """The console view of one identity. Never widens to the cookie column."""
    fingerprint = identity.fingerprint or {}
    return {
        "session": session or {"cookie": None, "verdict": "unknown"},
        # None where the aggregate has no traffic for this identity. The
        # console falls back to the failure streak there rather than drawing a
        # number that would read as measured.
        "health": health,
        "id": str(identity.id),
        "platform": identity.platform,
        "state": identity.state,
        "source": identity.source,
        "authenticated": identity.authenticated,
        "proxy_id": str(identity.proxy_id) if identity.proxy_id else None,
        # The name a person gave the egress, or None where the identity has no
        # proxy, the proxy row went away underneath the page, or nobody named
        # it. Never the URL, not even masked: the console falls back to the id,
        # which identifies the egress without carrying its password (doc 08).
        "proxy_label": proxy_label,
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


@router.get(
    "", summary="List identities with state and health", openapi_extra={I18N_KEY: "identities_list"}
)
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

    Each identity's id, platform, state, the proxy it is bound to - its
    `proxy_id` and `proxy_label`, the name someone gave that proxy, `null` when
    there is no proxy or no name, and never its URL - when it was minted and
    last used, and its health score over the last 15 and 60 minutes: `null` for
    an identity the aggregate has no traffic for, which is not the same as zero.
    """
    stmt = select(Identity)
    if platform is not None:
        stmt = stmt.where(Identity.platform == platform.value)
    if state is not None:
        stmt = stmt.where(Identity.state == state.value)
    stmt = stmt.order_by(Identity.minted_at.desc()).limit(limit)
    rows = (await request.state.db.scalars(stmt)).all()
    # Decrypting each jar costs, and the list is bounded by `limit`. It buys the
    # one thing the board could not say before: whether an identity still holds
    # the session value its platform issued, which is the difference between
    # "retire this identity" and "the signer is broken".
    cipher = request.app.state.cipher
    sessions: list[dict[str, Any]] = []
    for row in rows:
        try:
            header = cipher.decrypt(row.cookies_encrypted, aad=str(row.id))
        except Exception:
            sessions.append({"cookie": None, "verdict": "unknown"})
            continue
        sessions.append(_session_health(row.platform, _parse_cookies(header)))
    # The same score the scheduler ranks on, over the same two windows, so the
    # column an operator reads and the ordering the pool actually uses cannot
    # disagree. The streak comes from the row rather than the aggregate: it is
    # written on every outcome, while the aggregate lags by up to one bucket.
    windows = await pool.health_inputs(request.state.db, [row.id for row in rows])
    health = {
        row.id: score(replace(windows[row.id], consecutive_fails=row.consecutive_fails))
        for row in rows
        if row.id in windows
    }
    labels = await _proxy_labels(request.state.db, rows)
    return ok(
        request,
        [
            _row(row, session, health.get(row.id), labels.get(row.proxy_id))
            for row, session in zip(rows, sessions, strict=True)
        ],
    )


@router.get(
    "/pool",
    summary="Pool level against its low-water mark",
    openapi_extra={I18N_KEY: "identities_pool"},
)
async def pool_level(request: Request, principal: Principal = Depends(read_admin)) -> Any:
    """What the refill job is looking at, per platform.

    The job exists and has since the beginning - the worker checks each platform
    every minute and mints one identity at a time until the pool is back at
    `pool.target_size` - but nothing on the identities board said so, so the
    only way to know it was running was to watch rows appear. This is that
    check, answered on demand.

    `usable` is the number the job actually compares against the mark: live
    identities whose consecutive failure streak is still under
    `pool.max_fail_streak`. It is deliberately not the row count. An identity
    that fails every request stays live, so a pool counted by rows can sit at
    its target while serving nothing.

    `minting` is whether this deployment can mint at all. Without a browser
    container there is nothing to mint with, the job is skipped entirely, and a
    low-water mark is a number with no effect - which is worth saying out loud
    rather than leaving an operator to wonder why the pool never refills.

    `activity` is what the job has actually been doing: the mint in flight if
    there is one, the last few attempts with their outcomes, and the backoff if
    repeated failures have put the sweep to sleep. It comes from the worker via
    Redis rather than from this database, because a failed mint writes no row -
    which is exactly why a pool that stubbornly will not refill used to look
    identical to one nobody asked to refill.
    """
    config = request.app.state.config
    min_size = int(config.get("pool.min_size"))
    target_size = max(min_size, int(config.get("pool.target_size")))
    max_fail_streak = max(1, int(config.get("pool.max_fail_streak")))
    can_mint = bool(getattr(request.app.state.settings, "browser_rpc_url", "") or "")

    identities = IdentityPool(request.app.state.cipher)
    platforms: list[dict[str, Any]] = []
    for platform in Platform:
        counts = await identities.counts(request.state.db, platform)
        usable = await identities.usable_count(
            request.state.db, platform, max_fail_streak=max_fail_streak
        )
        live = counts.get(IdentityState.ACTIVE.value, 0) + counts.get(
            IdentityState.COOLING.value, 0
        )
        platforms.append(
            {
                "platform": platform.value,
                "usable": usable,
                "live": live,
                "active": counts.get(IdentityState.ACTIVE.value, 0),
                "minting": counts.get(IdentityState.MINTING.value, 0),
                # The state the filler would be in on its next tick. It refills
                # from below the mark up to the target, so a pool between the
                # two is only refilling if it was already under - which this
                # cannot know from a count alone, and says the honest thing:
                # below the mark it will mint, at or above it will not.
                "below_minimum": usable < min_size,
            }
        )

    return ok(
        request,
        {
            "min_size": min_size,
            "target_size": target_size,
            "max_fail_streak": max_fail_streak,
            "can_mint": can_mint,
            "platforms": platforms,
            "activity": await mint_log.snapshot(),
        },
    )


@router.post(
    "/mint",
    summary="Mint guest identities",
    openapi_extra={I18N_KEY: "identities_mint", **ACCEPTED_RESPONSES},
)
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


@router.post(
    "/import",
    summary="Import a logged-in cookie jar",
    openapi_extra={I18N_KEY: "identities_import", **CREATED_RESPONSES},
)
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


@router.get(
    "/{identity_id}/cookies",
    summary="What one identity's jar holds",
    openapi_extra={I18N_KEY: "identities_cookies"},
)
async def identity_cookies(
    request: Request,
    identity_id: uuid.UUID = Path(description="The identity to inspect."),
    principal: Principal = Depends(read_admin),
) -> Any:
    """The names, roles and masked values of the cookies this identity holds.

    An identity was a row of verdicts. When one stopped working the console
    could say *that* the session was spent and never *what the jar contained*,
    so the next step was always the same: open a shell, decrypt the column by
    hand, and read it there. This is that step, done in the place the question
    was asked.

    **The values stay masked, and that is not an oversight.** A cookie jar is
    the credential - a logged-in one is somebody's account - and this module's
    rule is that no response carries one. What comes back instead is everything
    that makes the jar diagnosable: which cookies are present, which of them
    this build considers required, session-bearing or merely useful, how long
    each value is, and its first and last four characters, which is enough to
    tell two jars apart and useless to anyone who steals the response.

    `length` is here where ``GET /identities`` deliberately withholds it. On
    that endpoint it would be a number per row on a listing polled every five
    seconds; here it is the answer to "is this token truncated", which is a
    real failure and one that looks exactly like a wrong token from outside.

    **Returns**

    The identity's platform and source, whether its jar carries a logged-in
    session, and one entry per cookie: `name`, `role`, `masked`, `length`.
    """
    # `read_admin` already demands admin or identity:manage, which is the same
    # gate the listing sits behind - a read key cannot reach either.
    identity = await request.state.db.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity", details={"identity_id": str(identity_id)})

    cookies: dict[str, str] = {}
    readable = True
    if identity.cookies_encrypted:
        try:
            cookies = _parse_cookies(
                request.app.state.cipher.decrypt(identity.cookies_encrypted, aad=str(identity.id))
            )
        except Exception:
            # A jar this instance can no longer decrypt is a real state - the
            # secret key was rotated without re-encrypting - and it is worth
            # saying so rather than rendering an identity that appears to hold
            # nothing.
            readable = False

    return ok(
        request,
        {
            "identity_id": str(identity.id),
            "platform": identity.platform,
            "source": identity.source,
            "authenticated": identity.authenticated,
            "readable": readable,
            "retired": identity.retired_at is not None,
            "session": _session_health(identity.platform, cookies),
            "missing_required": [
                name
                for name in REQUIRED.get(Platform(identity.platform), ())
                if not cookies.get(name)
            ],
            "cookies": [
                {
                    "name": name,
                    "role": _cookie_role(name),
                    "masked": mask_value(value),
                    "length": len(value),
                }
                for name, value in sorted(cookies.items())
            ],
        },
    )


@router.post(
    "/{identity_id}/test",
    summary="Probe one identity",
    openapi_extra={I18N_KEY: "identities_test", **ACCEPTED_RESPONSES},
)
async def test_identity(
    request: Request,
    identity_id: uuid.UUID = Path(description="The identity to act on."),
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


@router.post(
    "/{identity_id}/reset",
    summary="Return an identity to rotation",
    openapi_extra={I18N_KEY: "identities_reset"},
)
async def reset_identity(
    request: Request,
    identity_id: uuid.UUID = Path(description="The identity to act on."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Clear a cooldown and a failure streak, and mark the identity active.

    The pool recovers on its own and does so slowly on purpose: an identity that
    has failed repeatedly usually deserves the probation. This is the override
    for the case that recovery cannot know about - the failures were not the
    identity's fault.

    That case is real. Until 2026-09-09 this instance read Douyin's answer for a
    post that does not exist as risk control, so looking up one wrong id cooled
    the identity that asked and added to its streak. Fixing the classifier stops
    it recurring and repairs none of the damage: a degraded identity waits out a
    cooldown it never earned, and a streak keeps it out of the pool level until
    that many successes have gone by.

    Refused on a retired identity, whose cookie jar was wiped when it was
    retired: there is no session left to return to rotation, and a button that
    appeared to bring one back would be a lie.

    **Returns**

    The identity's id and its new state.
    """
    session = request.state.db
    identity = await session.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity")

    before = identity.state
    streak = identity.consecutive_fails
    state = await _pool(request).reset(session, str(identity_id))
    await audit(
        request,
        principal,
        "identity.reset",
        target_type="identity",
        target_id=str(identity_id),
        detail={"from_state": before, "cleared_streak": streak, "platform": identity.platform},
    )
    return ok(
        request,
        {
            "id": str(identity_id),
            "state": state.value if state else IdentityState.ACTIVE.value,
            "from_state": before,
            "cleared_streak": streak,
        },
    )


@router.delete(
    "/{identity_id}", summary="Retire an identity", openapi_extra={I18N_KEY: "identities_retire"}
)
async def retire_identity(
    request: Request,
    identity_id: uuid.UUID = Path(description="The identity to act on."),
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
