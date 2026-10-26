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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.api.deps import Principal
from dtk.api.routes import operations
from dtk.api.routes.openapi import ACCEPTED_RESPONSES, CREATED_RESPONSES, I18N_KEY
from dtk.api.routes.operations import Maintenance
from dtk.api.routes.schemas import (
    IdentityBundle,
    IdentityExport,
    IdentityImport,
    IdentityUpdate,
    MintRequest,
    RetireRequest,
    RotationRequest,
    identity_note,
)
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
    session_expiry,
    to_cookie_header,
)
from dtk.identity.marks import pool_marks
from dtk.identity.minting.native import NATIVE_MINT_PLATFORMS
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
        # The visitor id finger/spi hands out. No bootstrap value of it has
        # been measured, so present is enough. Without an entry every Bilibili
        # row read "Unknown", which on this column means "this build cannot
        # tell" - not true of a cookie it refuses to import without.
        Platform.BILIBILI.value: ("buvid3", 1),
    }
)

#: Every name the session value goes by where a platform has more than one,
#: in the order the signer reads them. The name above is the one a mint yields,
#: and a browser's jar spells Douyin's `UIFID`: judged on `UIFID_TEMP` alone,
#: every login imported from a browser read "missing" and the Identities page
#: hid it as a spent session, while the signer was signing with it.
_SESSION_SPELLINGS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {Platform.DOUYIN.value: UIFID_COOKIE_NAMES}
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
    # The spelling the signer would read first, so the verdict is on the value
    # it signs with; none held, and the cell names the one a mint yields.
    name = next(
        (spelling for spelling in _SESSION_SPELLINGS.get(platform, ()) if cookies.get(spelling)),
        name,
    )
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

#: Cookies that belong to a login without being the one that proves it, so
#: they are shown as part of it. Not session markers: `bili_jct` without
#: `SESSDATA` is a CSRF token for a session the jar does not hold, and counting
#: it would mark a guest as logged in. Shown as "other" they read as noise in
#: the very paste whose login an operator is checking.
_LOGIN_COMPANIONS: Final[frozenset[str]] = frozenset(
    {"bili_jct", "DedeUserID", "DedeUserID__ckMd5"}
)


#: The export document's shape. Bumped when an entry gains or loses a field,
#: so an importer can refuse a file it would silently read wrong rather than
#: dropping a fingerprint and producing an identity the platform can tell apart
#: from the browser it came from.
EXPORT_VERSION: Final = 1


def _text(value: Any) -> str | None:
    """A field from an imported document, when it is a string worth passing on."""
    return value if isinstance(value, str) and value else None


def _bundle_note(value: Any) -> str | None:
    """An exported note, if it is still one this build would store.

    Dropped rather than refused when it is not: a note is a label, and an
    identity that would otherwise restore is worth more than the label on it.
    """
    if not isinstance(value, str):
        return None
    try:
        return identity_note(value)
    except ValueError:
        return None


def _decrypt(request: Request, identity: Identity) -> dict[str, str] | None:
    """This identity's jar, or None when it cannot be read.

    None is a real answer rather than an empty jar: the secret key was rotated
    without re-encrypting, the identity cannot sign anything either, and an
    empty mapping would render as "holds no cookies" - a different fault with a
    different fix.
    """
    if not identity.cookies_encrypted:
        return {}
    try:
        header = request.app.state.cipher.decrypt(identity.cookies_encrypted, aad=str(identity.id))
    except Exception:
        return None
    return _parse_cookies(header)


def _cookie_role(name: str, platform: Platform | None = None) -> str:
    """Why this cookie matters, using the sets the rest of the build judges by.

    Four buckets rather than a description per cookie: the platforms add and
    retire cookies constantly, and a server-side glossary would be a list to
    keep current in a file nobody opens. What the server knows for certain is
    which names this build treats as load bearing; the console renders the
    prose, and an unknown name still gets an honest "other".

    Required is judged against ``platform`` when there is one. Against every
    platform at once, a `ttwid` pasted into a Bilibili import read as required
    by the one platform that never asks for it.
    """
    if name in SESSION_MARKERS or name in _LOGIN_COMPANIONS:
        return "session"
    required_sets = [REQUIRED.get(platform, ())] if platform is not None else REQUIRED.values()
    if any(name in required for required in required_sets):
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
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """The console view of one identity. Never widens to the cookie column."""
    fingerprint = identity.fingerprint or {}
    return {
        "session": session or {"cookie": None, "verdict": "unknown"},
        # When the login lapses, read out of the session cookie: None for a
        # guest, and for a login whose cookie does not say. A date rather than
        # anything from the value, which is the credential.
        "expires_at": iso(expires_at),
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
        # None for an identity in ordinary rotation; see Identity.rotation_endpoints.
        "rotation_endpoints": identity.rotation_endpoints,
        "note": identity.note,
        # What the platform named the last time the session was checked, and
        # when. None until a check has named an account.
        "account_id": identity.account_id,
        "account_seen_at": iso(identity.account_seen_at),
        "fingerprint": {
            "browser_family": fingerprint.get("browser_family"),
            "browser_major": fingerprint.get("browser_major"),
            "platform": fingerprint.get("platform"),
            "language": fingerprint.get("language"),
            "timezone": fingerprint.get("timezone"),
        },
    }


def _summary(report: ImportReport, warnings: list[str]) -> dict[str, Any]:
    """What the import preview shows: names, roles, masks - never a value."""
    return {
        "detected_format": report.detected_format.value,
        "detected_platform": (
            report.detected_platform.value if report.detected_platform is not None else None
        ),
        "cookie_names": sorted(report.cookies),
        "cookies_masked": report.masked(),
        "cookie_roles": {
            name: _cookie_role(name, report.platform) for name in sorted(report.cookies)
        },
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


def _warnings(report: ImportReport, lang: Language) -> list[str]:
    """The preview's warnings as sentences the caller can read.

    The one about the paste carrying a live session is the warning that most
    needs to land, so it cannot be the one string on this page still in English.
    The codes travel beside the sentences: a console styling the security
    warning differently must not have to match on prose (doc 14).
    """
    return [t(warning.key, lang, **warning.args) for warning in report.warnings]


class RotationFilter(StrEnum):
    """Which rotation an identity is in, as the list filters on it."""

    #: Handed out for every endpoint: `rotation_endpoints` is null.
    ROTATING = "rotating"
    #: Only for requests that name it: an empty list.
    RESERVED = "reserved"
    #: Handed out for the endpoints its list names and nothing else.
    LIMITED = "limited"


#: The states the scheduler hands out: active, and degraded as a fallback.
#: One tuple for the coverage card's count and the list it links to, which
#: used to answer two different questions under one number.
_SERVING_STATES: Final = (IdentityState.ACTIVE.value, IdentityState.DEGRADED.value)


def _logins_only(endpoint: str) -> bool:
    """Whether rotation sends this endpoint nothing but logins, first page included.

    The scheduler's pass for such an endpoint draws signed-in identities alone,
    so a guest in rotation for it is never sent there. Counting the guests
    anyway put "8" under Can serve beside Douyin's author likes, an endpoint
    those eight could not serve once, and the list it opened was eight guests.
    """
    from dtk.platforms.base import LoginNeed
    from dtk.platforms.registry import get_adapter

    try:
        table = get_adapter(Platform(endpoint.partition(".")[0])).endpoints
    except ValueError:
        return False
    return endpoint in table and table[endpoint].login is LoginNeed.REQUIRED


def _list_conditions(
    *,
    platform: Platform | None,
    state: IdentityState | None,
    authenticated: bool | None,
    source: IdentitySource | None,
    rotation: RotationFilter | None,
    identity_id: uuid.UUID | None,
    serves: str | None = None,
    ready: bool = False,
) -> list[ColumnElement[bool]]:
    """The filters the listing and its total share, retired rows aside."""
    conditions: list[ColumnElement[bool]] = []
    if platform is not None:
        conditions.append(Identity.platform == platform.value)
    if state is not None:
        conditions.append(Identity.state == state.value)
    if authenticated is not None:
        conditions.append(Identity.authenticated.is_(authenticated))
    if source is not None:
        conditions.append(Identity.source == source.value)
    if rotation is RotationFilter.ROTATING:
        conditions.append(Identity.rotation_endpoints.is_(None))
    elif rotation is RotationFilter.RESERVED:
        conditions.append(func.cardinality(Identity.rotation_endpoints) == 0)
    elif rotation is RotationFilter.LIMITED:
        conditions.append(func.cardinality(Identity.rotation_endpoints) > 0)
    if identity_id is not None:
        conditions.append(Identity.id == identity_id)
    if serves is not None:
        # The scheduler's own test, Candidate.rotates_for, as SQL: no list is
        # every endpoint, and a list is the endpoints it names.
        conditions.append(
            or_(
                Identity.rotation_endpoints.is_(None),
                Identity.rotation_endpoints.contains([serves]),
            )
        )
        if _logins_only(serves):
            conditions.append(Identity.authenticated.is_(True))
    if ready:
        conditions.append(Identity.state.in_(_SERVING_STATES))
    return conditions


async def _count(session: AsyncSession, conditions: Sequence[ColumnElement[bool]]) -> int:
    result = await session.execute(select(func.count()).select_from(Identity).where(*conditions))
    return int(result.scalar_one())


@router.get(
    "", summary="List identities with state and health", openapi_extra={I18N_KEY: "identities_list"}
)
async def list_identities(
    request: Request,
    platform: Platform | None = Query(
        default=None, description="Only identities for this platform."
    ),
    state: IdentityState | None = Query(default=None, description="Only identities in this state."),
    authenticated: bool | None = Query(
        default=None, description="Only logged-in identities (true), or only guests (false)."
    ),
    source: IdentitySource | None = Query(
        default=None, description="Only minted, or only imported, identities."
    ),
    rotation: RotationFilter | None = Query(
        default=None,
        description=(
            "Only identities rotation hands out for every endpoint (`rotating`), only "
            "for requests that name them (`reserved`), or for a list of endpoints "
            "(`limited`)."
        ),
    ),
    include_retired: bool = Query(
        default=False,
        description=(
            "Also list retired identities. They are kept for 90 days for their "
            "statistics and hold no cookies."
        ),
    ),
    identity_id: uuid.UUID | None = Query(
        default=None, description="Only this identity, retired or not."
    ),
    serves: str | None = Query(
        default=None,
        max_length=128,
        description=(
            "Only identities rotation may hand out for this endpoint, such as "
            "`douyin.author_posts`: those in ordinary rotation and those limited to a "
            "list that names it. Reserved identities never match, and guests do not "
            "where the endpoint needs a login from its first page."
        ),
    ),
    ready: bool = Query(
        default=False,
        description=(
            "Only identities the scheduler would hand out now: active, or degraded "
            "as a fallback. With `serves`, the identities the coverage card counts "
            "as able to serve that endpoint."
        ),
    ),
    limit: int = Query(
        default=DEFAULT_ADMIN_PAGE_SIZE,
        ge=1,
        le=MAX_ADMIN_PAGE_SIZE,
        description="Maximum identities to return.",
    ),
    offset: int = Query(default=0, ge=0, description="Rows to skip."),
    principal: Principal = Depends(read_admin),
) -> Any:
    """The identity pool, newest first.

    Cookies are never returned; this is the health view, not the credential.

    Retired identities are left out unless `include_retired` is set, `state`
    asks for them, or `identity_id` names one. They used to be listed and
    hidden by the console instead, and since they are kept for 90 days they
    took up the page: a pool that retired two hundred identities in a bad
    week showed none of its older logins at all, and said nothing about it.

    **Parameters**

    - `platform` - restrict to one platform.
    - `state` - restrict to one state, such as `active` or `cooling`.
    - `authenticated` - only logins, or only guests.
    - `source` - only `minted` or only `imported` identities.
    - `rotation` - `rotating`, `reserved` or `limited`.
    - `include_retired` - list retired identities as well.
    - `identity_id` - just this one identity.
    - `serves` - only identities rotation may hand out for this endpoint.
    - `ready` - only those it would hand out now: active or degraded.
    - `limit`, `offset` - the page.

    **Returns**

    Each identity's id, platform, state, the proxy it is bound to - its
    `proxy_id` and `proxy_label`, the name someone gave that proxy, `null` when
    there is no proxy or no name, and never its URL - when it was minted and
    last used, when its login expires (`expires_at`, read from the session
    cookie; `null` for a guest), and its health score over the last 15 and 60
    minutes: `null` for an identity the aggregate has no traffic for, which is
    not the same as zero. `meta.total` is how many identities match, and
    `meta.retired` how many retired ones the same filters left out.
    """
    db = request.state.db
    conditions = _list_conditions(
        platform=platform,
        state=state,
        authenticated=authenticated,
        source=source,
        rotation=rotation,
        identity_id=identity_id,
        serves=serves,
        ready=ready,
    )
    excluding = not include_retired and state is None and identity_id is None
    retired_left_out = 0
    if excluding:
        retired_left_out = await _count(
            db, [*conditions, Identity.state == IdentityState.RETIRED.value]
        )
        conditions.append(Identity.state != IdentityState.RETIRED.value)
    total = await _count(db, conditions)
    stmt = (
        select(Identity)
        .where(*conditions)
        # The id breaks ties so that two identities minted in the same instant
        # cannot swap places between one page and the next.
        .order_by(Identity.minted_at.desc(), Identity.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    # Decrypting each jar costs, and the list is bounded by `limit`. It buys the
    # one thing the board could not say before: whether an identity still holds
    # the session value its platform issued, which is the difference between
    # "retire this identity" and "the signer is broken" - and, for a login,
    # when it runs out.
    cipher = request.app.state.cipher
    sessions: list[dict[str, Any]] = []
    expiries: list[datetime | None] = []
    for row in rows:
        try:
            header = cipher.decrypt(row.cookies_encrypted, aad=str(row.id))
        except Exception:
            sessions.append({"cookie": None, "verdict": "unknown"})
            expiries.append(None)
            continue
        cookies = _parse_cookies(header)
        sessions.append(_session_health(row.platform, cookies))
        expiries.append(session_expiry(cookies) if row.authenticated else None)
    # The same score the scheduler ranks on, over the same two windows, so the
    # column an operator reads and the ordering the pool actually uses cannot
    # disagree. The streak comes from the row rather than the aggregate: it is
    # written on every outcome, while the aggregate lags by up to one bucket.
    windows = await pool.health_inputs(db, [row.id for row in rows])
    health = {
        row.id: score(replace(windows[row.id], consecutive_fails=row.consecutive_fails))
        for row in rows
        if row.id in windows
    }
    labels = await _proxy_labels(db, rows)
    return ok(
        request,
        [
            _row(row, session, health.get(row.id), labels.get(row.proxy_id), expires_at)
            for row, session, expires_at in zip(rows, sessions, expiries, strict=True)
        ],
        extra={"total": total, "retired": retired_left_out},
    )


def _login_required_endpoints(platform: Platform) -> frozenset[str]:
    """The endpoints a guest cannot serve at all, on some page or every page."""
    from dtk.platforms.base import LoginNeed
    from dtk.platforms.registry import get_adapter

    table = get_adapter(platform).endpoints
    return frozenset(
        name
        for name in table.names()
        if LoginNeed.REQUIRED in (table[name].login, table[name].paging_login)
    )


#: The states rotation leases from; a cooling identity waits out its timer.
_LEASABLE: Final = frozenset({IdentityState.ACTIVE.value, IdentityState.DEGRADED.value})


def _count_logins(
    rows: Iterable[tuple[str, str, Sequence[str] | None]],
    needs_login: Mapping[str, frozenset[str]],
) -> dict[str, dict[str, int]]:
    """Each platform's logged-in identities, by what rotation can do with them.

    Mirrors `Scheduler._rotation_pool`: an endpoint that requires a login
    leases from active and degraded identities that rotate for it, and skips a
    reserved one even then. So "reserved" is checked first - a reserved
    identity that is also cooling is still one rotation will never reach - and
    a limited one counts as rotating only where its list names an endpoint that
    needs it.
    """
    coverage = {
        platform.value: {"rotating": 0, "limited": 0, "reserved": 0, "cooling": 0}
        for platform in Platform
    }
    for platform, state, endpoints in rows:
        counts = coverage.get(platform)
        if counts is None:
            continue
        if endpoints is not None and len(endpoints) == 0:
            counts["reserved"] += 1
        elif state == IdentityState.COOLING.value:
            counts["cooling"] += 1
        elif state in _LEASABLE:
            required = needs_login.get(platform, frozenset())
            serves = endpoints is None or not required or bool(required & set(endpoints))
            counts["rotating" if serves else "limited"] += 1
    return coverage


async def _login_coverage(
    session: AsyncSession, needs_login: Mapping[str, frozenset[str]]
) -> dict[str, dict[str, int]]:
    """:func:`_count_logins` over every live login, in one statement.

    Three columns of the few authenticated rows rather than a count per
    platform and bucket: the pool card polls this every five seconds, and a
    pool holds a handful of logins beside its guests.
    """
    rows = await session.execute(
        select(Identity.platform, Identity.state, Identity.rotation_endpoints).where(
            Identity.authenticated.is_(True),
            Identity.state != IdentityState.RETIRED.value,
        )
    )
    return _count_logins(rows.tuples().all(), needs_login)


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
    identical to one nobody asked to refill. `activity.backoffs` is keyed by
    platform: the browser and plain HTTP back off separately, so Douyin can be
    resting while Bilibili mints.

    `logins` counts each platform's logged-in identities by what rotation can
    do with them: `rotating` ones it hands out to the endpoints that need a
    login, `reserved` ones only a request naming them reaches, `cooling` ones
    it will hand out again, and `limited` ones it hands out only for endpoints
    that need no login. `login_required_for` names the endpoints that refuse a
    guest outright - on every page after the first - and answer
    IDENTITY_LOGIN_REQUIRED when `rotating` is zero.
    """
    config = request.app.state.config
    # The global pair, which is what a platform with no override of its own
    # follows. Kept at the top level so a client written before the marks were
    # per platform still reads the numbers it always did.
    min_size = int(config.get("pool.min_size"))
    target_size = max(min_size, int(config.get("pool.target_size")))
    max_fail_streak = max(1, int(config.get("pool.max_fail_streak")))
    browser = bool(getattr(request.app.state.settings, "browser_rpc_url", "") or "")

    identities = IdentityPool(request.app.state.cipher)
    needs_login = {platform.value: _login_required_endpoints(platform) for platform in Platform}
    logins = await _login_coverage(request.state.db, needs_login)
    platforms: list[dict[str, Any]] = []
    for platform in Platform:
        marks = pool_marks(config, platform)
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
                "below_minimum": marks.auto and usable < marks.min_size,
                "min_size": marks.min_size,
                "target_size": marks.target_size,
                # Per platform: Bilibili guests are minted over plain HTTP, so
                # they can be minted where the others need a browser.
                "can_mint": browser or platform in NATIVE_MINT_PLATFORMS,
                # Whether each number is this platform's own or the global one,
                # so the console can offer "back to the default" honestly.
                "min_inherited": marks.min_inherited,
                "target_inherited": marks.target_inherited,
                "auto": marks.auto,
                # The logins rotation can reach, and the endpoints that answer
                # IDENTITY_LOGIN_REQUIRED without one. Nothing on the board
                # said either, so the first sign that the only imported login
                # had been reserved, had cooled or had lapsed was that error.
                "logins": logins[platform.value],
                "login_required_for": sorted(needs_login[platform.value]),
            }
        )

    return ok(
        request,
        {
            "min_size": min_size,
            "target_size": target_size,
            "max_fail_streak": max_fail_streak,
            # Whether any platform can be minted here. Kept for clients written
            # before the per-platform flag; read that one to decide a button.
            "can_mint": browser or bool(NATIVE_MINT_PLATFORMS),
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

    Minting takes seconds and is not on the request path - Douyin and TikTok
    drive a real browser through the proxy, Bilibili makes one plain HTTP
    request through it - so it is queued like any other job (doc 02).
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

    The report says which role each cookie plays for the chosen platform and
    which platform the cookie names point at. The console kept its own copy of
    the first, which knew only ByteDance cookies and labelled a whole Bilibili
    jar "other"; the second is how it offers to switch a paste it was given
    under the wrong platform.

    ``reserved`` stores the identity already out of rotation, so a login that
    was imported to serve the requests that name it is never handed to anything
    else - not even in the moment between storing it and reserving it.

    A logged-in jar is the account itself. Platforms rarely ban an account for
    reading data with it, but a ban cannot be ruled out, and importing one is
    at the importer's own risk; the console says so before it stores one.
    """
    report = build_report(
        body.cookies,
        body.platform,
        user_agent=body.user_agent,
        language=body.language,
        timezone=body.timezone,
    )
    warnings = _warnings(report, language(request))
    summary = _summary(report, warnings)

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
        note=body.note,
    )
    if body.reserved:
        stored = await request.state.db.get(Identity, identity_id)
        if stored is not None:
            stored.rotation_endpoints = []
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
            "reserved": body.reserved,
            "noted": body.note is not None,
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

    decrypted = _decrypt(request, identity)
    readable = decrypted is not None
    cookies = decrypted or {}

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
                    "role": _cookie_role(name, Platform(identity.platform)),
                    "masked": mask_value(value),
                    "length": len(value),
                }
                for name, value in sorted(cookies.items())
            ],
        },
    )


@router.post(
    "/import/bundle",
    summary="Import an export document",
    openapi_extra={I18N_KEY: "identities_import_bundle", **CREATED_RESPONSES},
)
async def import_bundle(
    request: Request,
    body: IdentityBundle,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Restore identities from a file this build exported.

    The other import takes a paste, which is what somebody has when they copy a
    jar out of a browser. This takes what somebody has when they exported a
    pool: many identities at once, each already carrying the fingerprint it was
    minted or imported with.

    Carrying the fingerprint is the point. Re-pasting an exported jar through
    the paste route infers the browser from a user agent that is no longer
    beside it, so a restored identity signs with a different fingerprint from
    the one the platform first saw it with - which is exactly the difference
    risk control is looking for.

    Every entry is judged on its own. One jar that has expired since it was
    exported does not stop the rest: the response says what was stored, what
    was skipped and why, and `dry_run` answers that question without writing.

    **Parameters**

    - `version` - the document's shape. A newer one is refused rather than
      read on a guess.
    - `identities` - the entries, as `POST /identities/export` wrote them.
    - `proxy_id` - bind every restored identity to this egress. Optional.
    - `dry_run` - report without storing.

    **Returns**

    `stored`, and one result per entry: its platform, whether it was usable,
    why it was skipped (`reason`: `no_cookies`, `unknown_platform` or
    `unusable`), when its login expires, and the id it was given.
    """
    if body.version > EXPORT_VERSION:
        raise InvalidParam(
            "this file was written by a newer build than this instance",
            details={"version": body.version, "supported": EXPORT_VERSION},
        )

    proxy_id = _as_uuid(body.proxy_id, "proxy_id") if body.proxy_id else None
    lang = language(request)
    results: list[dict[str, Any]] = []
    stored = 0

    for index, entry in enumerate(body.identities):
        outcome: dict[str, Any] = {"index": index}
        platform_value = entry.get("platform")
        cookies = entry.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            # A retired identity exports with an empty jar on purpose, so this
            # is the ordinary case rather than a malformed file.
            results.append({**outcome, "usable": False, "reason": "no_cookies"})
            continue
        try:
            platform = Platform(str(platform_value))
        except ValueError:
            results.append({**outcome, "usable": False, "reason": "unknown_platform"})
            continue

        report = build_report(
            to_cookie_header({str(k): str(v) for k, v in cookies.items()}),
            platform,
            user_agent=_text(entry.get("user_agent")),
            language=_text(entry.get("language")),
            timezone=_text(entry.get("timezone")),
        )
        outcome |= {
            "platform": platform.value,
            "authenticated": report.authenticated,
            # Restored all the same: a lapsed login still signs as a guest.
            # Said here so the preview can show which logins will come back
            # as guests in all but name.
            "expires_at": iso(report.expires_at),
            "usable": report.usable,
            "missing_required": list(report.missing_required),
            "warnings": _warnings(report, lang),
        }
        note = _bundle_note(entry.get("note"))
        if note is not None:
            outcome["note"] = note
        if not report.usable:
            results.append({**outcome, "reason": "unusable"})
            continue
        if body.dry_run:
            results.append(outcome)
            continue

        identity_id = await _pool(request).add(
            request.state.db,
            platform=platform,
            cookies=report.cookies,
            fingerprint=report.fingerprint,
            source=IdentitySource.IMPORTED,
            proxy_id=proxy_id,
            authenticated=report.authenticated,
            note=note,
        )
        stored += 1
        results.append({**outcome, "identity_id": str(identity_id)})

    if not body.dry_run and stored:
        await audit(
            request,
            principal,
            "identity.bundle_imported",
            target_type="identity",
            target_id=",".join(
                str(result["identity_id"]) for result in results if "identity_id" in result
            )[:512],
            detail={"stored": stored, "submitted": len(body.identities)},
        )

    return ok(
        request,
        {"stored": stored, "submitted": len(body.identities), "results": results},
        status_code=201 if stored else 200,
    )


@router.get(
    "/{identity_id}/cookies/reveal",
    summary="Read one identity's cookies in full",
    openapi_extra={I18N_KEY: "identities_cookies_reveal"},
)
async def reveal_cookies(
    request: Request,
    identity_id: uuid.UUID = Path(description="The identity to read."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """The jar as it will be sent, values and all.

    A deliberate exception to this module's rule, and the reason it is a route
    of its own rather than a flag on the inventory: the masked view is what the
    drawer loads on every open and this is what somebody asks for on purpose.
    Separating them means the audit line records the second and is not drowned
    by the first, and the wider scope - operator, not any admin reader - is
    enforced by the dependency rather than by a branch inside a handler.

    Why it exists at all: an operator debugging a jar ends up comparing it
    against a browser's, and a mask cannot be compared. The alternative on
    offer was a shell and a hand-rolled decrypt, which is the same disclosure
    with no audit line and no scope check in front of it.

    **Returns**

    The cookies as a mapping, the same jar as one ``Cookie:`` header, and the
    expiry read out of the session cookie where there is one.
    """
    identity = await request.state.db.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity", details={"identity_id": str(identity_id)})

    cookies = _decrypt(request, identity)
    if cookies is None:
        raise InvalidParam(
            "this identity's cookies cannot be decrypted with the current secret key"
        )

    await audit(
        request,
        principal,
        "identity.cookies_revealed",
        target_type="identity",
        target_id=str(identity_id),
        detail={"platform": identity.platform, "count": len(cookies)},
    )
    return ok(
        request,
        {
            "identity_id": str(identity.id),
            "platform": identity.platform,
            "authenticated": identity.authenticated,
            "cookies": cookies,
            "header": to_cookie_header(cookies),
            "expires_at": iso(session_expiry(cookies)),
        },
    )


@router.post(
    "/export",
    summary="Export identities as a file",
    openapi_extra={I18N_KEY: "identities_export"},
)
async def export_identities(
    request: Request,
    body: IdentityExport,
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Hand back whole identities - jars, fingerprints and all - as a document.

    The console could import a jar and never get one back out, so an operator
    who had built a working pool could not move it to a second instance, keep a
    copy before a risky change, or hand one identity to somebody debugging.
    The only route out was the database.

    **What comes back is a credential file.** Every jar in it works until the
    platform expires it, and a logged-in one is somebody's account. It is
    audited by identity, and the document says so in a field the importer
    ignores, so a person who finds one later knows what they are holding.

    **Parameters**

    - `identity_ids` - which identities. Between one and 200.

    **Returns**

    A document with a `version`, the export time, and one entry per identity.
    Retired identities export as an entry with an empty jar rather than being
    dropped: silently returning fewer than were asked for is how an operator
    finds out mid-restore.
    """
    wanted = [_as_uuid(value, "identity_ids") for value in body.identity_ids]
    rows = (
        (await request.state.db.execute(select(Identity).where(Identity.id.in_(wanted))))
        .scalars()
        .all()
    )
    found = {row.id: row for row in rows}
    missing = [str(value) for value in wanted if value not in found]
    if missing:
        raise NotFound("no such identity", details={"identity_ids": missing})

    entries = []
    for identity_id in wanted:
        identity = found[identity_id]
        cookies = _decrypt(request, identity) or {}
        fingerprint = identity.fingerprint or {}
        entries.append(
            {
                "identity_id": str(identity.id),
                "platform": identity.platform,
                "source": identity.source,
                "authenticated": identity.authenticated,
                "minted_at": iso(identity.minted_at),
                "retired_at": iso(identity.retired_at),
                "cookies": cookies,
                # A label, not a credential, and the one thing that says whose
                # account a login in the file is. Read back by the bundle import.
                "note": identity.note,
                "user_agent": fingerprint.get("user_agent"),
                "language": fingerprint.get("language"),
                "timezone": fingerprint.get("timezone"),
            }
        )

    await audit(
        request,
        principal,
        "identity.exported",
        target_type="identity",
        target_id=",".join(str(value) for value in wanted[:20]),
        detail={"count": len(entries)},
    )
    return ok(
        request,
        {
            "version": EXPORT_VERSION,
            "exported_at": iso(datetime.now(UTC)),
            "warning": (
                "This file contains working credentials. Anyone holding it can "
                "make requests as these identities."
            ),
            "identities": entries,
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
    "/{identity_id}/session",
    summary="Check whether an identity's login is still valid",
    openapi_extra={I18N_KEY: "identities_session", **ACCEPTED_RESPONSES},
)
async def check_session(
    request: Request,
    identity_id: uuid.UUID = Path(description="The identity to check."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Ask the platform whose session this identity is carrying.

    ``authenticated`` on an identity is a statement about the paste it came
    from - a `sessionid` was present - and not about the account. The cookie is
    there whether or not the platform still honours it, and the difference only
    ever surfaced later, as logged-in-only data quietly missing from otherwise
    successful responses.

    This asks the platform. Both publish an endpoint that answers about the
    cookies that asked rather than about a user named in the request, which is
    what makes it a login check and not a lookup.

    Queued rather than answered inline, like the probe beside it: it is a real
    upstream call with a real timeout.

    **Returns**

    A task id. Its result carries `logged_in`, the `account_id` the platform
    named, and a `reason` - `live`, `expired`, `guest`, `refused` or
    `unreachable` - because "the session is over" and "the platform said
    nothing" send an operator to different places.
    """
    identity = await request.state.db.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity", details={"identity_id": str(identity_id)})
    task_id, _state = await operations.submit(
        request,
        principal,
        endpoint=Maintenance.IDENTITY_SESSION.value,
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


@router.post(
    "/{identity_id}/rotation",
    summary="Choose what rotation uses an identity for",
    openapi_extra={I18N_KEY: "identities_rotation"},
)
async def set_rotation(
    request: Request,
    body: RotationRequest,
    identity_id: uuid.UUID = Path(description="The identity to act on."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Keep an identity out of rotation, wholly or for all but some endpoints.

    An account somebody imported to read one thing with is put at risk by
    every other request rotation hands it, and until this there was no way to
    stop that: naming it with `identity=` served the one request, while the
    scheduler went on spending it on everything else (#789). An empty list
    keeps it for the requests that name it and nothing else; a list of
    endpoints adds those back to rotation.

    A request that names the identity is never refused by this. It is also
    left out of the pool level, so the filler does not count an account it
    may not spend as one it can. It does not travel with an export: it is a
    decision about this pool, not part of the identity.

    **Parameters**

    - `endpoints` - null for every endpoint, the default; `[]` for none; or
      names such as `douyin.author_posts`, which must belong to the identity's
      own platform.

    **Returns**

    The identity's id and the endpoints rotation may now use it for.
    """
    session = request.state.db
    identity = await session.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity")
    if identity.state == IdentityState.RETIRED.value:
        raise InvalidParam(
            "a retired identity is in no rotation and cannot be put back in one",
            details={"id": str(identity_id), "state": identity.state},
        )

    chosen: list[str] | None = None
    if body.endpoints is not None:
        from dtk.platforms.registry import get_adapter

        known = set(get_adapter(identity.platform).endpoints)
        unknown = sorted(set(body.endpoints) - known)
        if unknown:
            # Refused rather than stored: a typo here reads as "reserved for
            # nothing", which keeps the account out of the very endpoint it
            # was meant for and says nothing about why.
            raise InvalidParam(
                f"not {identity.platform} endpoints: {', '.join(unknown)}",
                details={"unknown": unknown, "platform": identity.platform},
            )
        chosen = sorted(set(body.endpoints))

    before = identity.rotation_endpoints
    identity.rotation_endpoints = chosen
    await audit(
        request,
        principal,
        "identity.rotation_changed",
        target_type="identity",
        target_id=str(identity_id),
        detail={"from": before, "to": chosen, "platform": identity.platform},
    )
    # One commit for the change and its audit row, so neither lands alone.
    await session.commit()
    log.info(
        "identity.rotation_changed",
        identity_id=str(identity_id),
        endpoints=chosen,
        restricted=chosen is not None,
    )
    return ok(request, {"id": str(identity_id), "rotation_endpoints": chosen})


@router.patch(
    "/{identity_id}",
    summary="Change an identity's note",
    openapi_extra={I18N_KEY: "identities_update"},
)
async def update_identity(
    request: Request,
    body: IdentityUpdate,
    identity_id: uuid.UUID = Path(description="The identity to act on."),
    principal: Principal = Depends(manage_pool),
) -> Any:
    """Write the note an operator keeps with an identity, or clear it.

    Three imported logins were three UUIDs, and the only way to know which
    account was which was to remember the order they went in. The note is the
    operator's - the account a login signs in as, where its cookie came from -
    and nothing in the request path reads it.

    A retired identity keeps its note and may still have it changed: retired
    rows stay for their statistics, and "which account was this" is exactly the
    question somebody asks of one afterwards.

    **Parameters**

    - `note` - one line, at most 200 characters once trimmed; null or blank
      clears it.

    **Returns**

    The identity as the list shows it.
    """
    session = request.state.db
    identity = await session.get(Identity, identity_id)
    if identity is None:
        raise NotFound("no such identity", details={"identity_id": str(identity_id)})
    before = identity.note
    identity.note = body.note
    if before != body.note:
        await audit(
            request,
            principal,
            "identity.note_changed",
            target_type="identity",
            target_id=str(identity_id),
            # Whether, not what: a note is free text, and somebody writes the
            # phone number a login belongs to into it. The audit trail is read
            # more widely than the pool is managed.
            detail={
                "platform": identity.platform,
                "had_note": before is not None,
                "has_note": body.note is not None,
            },
        )
    await session.commit()
    return ok(request, _row(identity))


@router.get(
    "/coverage",
    summary="Which identities rotation sends to each endpoint",
    openapi_extra={I18N_KEY: "identities_coverage"},
)
async def rotation_coverage(request: Request, principal: Principal = Depends(read_admin)) -> Any:
    """Per platform and per endpoint, how many identities rotation may send.

    The pool page counted identities, and the scheduler hands them out per
    endpoint: an identity limited to `douyin.author_posts` is in the pool and
    absent from every other endpoint, a reserved one is in the pool and absent
    from all of them, and a login is what decides whether an endpoint that needs
    one can be served at all. None of that was visible without reading each
    identity's rotation one at a time.

    Counted the way the scheduler counts: an identity serves an endpoint when it
    has no rotation list or its list names the endpoint - and is a login, where
    the endpoint needs one from its first page - and it is `ready` when it is
    active or degraded. A request that names an identity with `identity=` is
    never limited by any of this.

    **Returns**

    One entry per platform: `guests_first`, the platform's
    `pool.<platform>.guests_first`; `reserved`, how many of its identities serve
    only the requests that name them; and one entry per endpoint it serves,
    with the capability table's fields (`login`, `paging_login`,
    `session_bound`) and `ready`, `signed_in` (of those ready, how many are
    logins), `cooling` (resting, back in rotation when they recover) and
    `dedicated` (ready ones limited to a list that names this endpoint).
    """
    from dtk.api.routes.capabilities import capability_table

    config = request.app.state.config
    serving = _SERVING_STATES
    rows = (
        await request.state.db.execute(
            select(
                Identity.platform,
                Identity.state,
                Identity.authenticated,
                Identity.rotation_endpoints,
            ).where(Identity.state.in_((*serving, IdentityState.COOLING.value)))
        )
    ).all()
    table = capability_table()
    platforms: list[dict[str, Any]] = []
    for platform in Platform:
        mine = [row for row in rows if row.platform == platform.value]
        endpoints: list[dict[str, Any]] = []
        for entry in table.get(platform.value, []):
            name = entry["endpoint"]
            logins_only = _logins_only(name)
            rotating = [
                row
                for row in mine
                if (row.rotation_endpoints is None or name in row.rotation_endpoints)
                and (row.authenticated or not logins_only)
            ]
            ready = [row for row in rotating if row.state in serving]
            endpoints.append(
                {
                    **entry,
                    "ready": len(ready),
                    "signed_in": sum(1 for row in ready if row.authenticated),
                    "cooling": sum(
                        1 for row in rotating if row.state == IdentityState.COOLING.value
                    ),
                    "dedicated": sum(1 for row in ready if row.rotation_endpoints is not None),
                }
            )
        platforms.append(
            {
                "platform": platform.value,
                "guests_first": bool(config.get(f"pool.{platform.value}.guests_first")),
                "reserved": sum(1 for row in mine if row.rotation_endpoints == []),
                "endpoints": endpoints,
            }
        )
    return ok(request, {"platforms": platforms})


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
