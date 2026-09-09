"""Identity pool: lifecycle, health bookkeeping and candidate supply.

The pool's atom is a whole identity - cookies, proxy, fingerprint and UA bound
together - not a bare cookie. Rotating cookies over one shared egress and one
User-Agent is a stronger anomaly than plain request frequency: to the platform
it looks like a single device cycling through visitors. See
docs/design/02-identity-pool.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.crypto import Cipher
from dtk.core.logging import get_logger
from dtk.core.types import (
    BrowserFamily,
    IdentitySource,
    IdentityState,
    Outcome,
    Platform,
)
from dtk.db.models import Identity as IdentityRow
from dtk.db.models import IdentityEvent
from dtk.identity.importing import to_cookie_header
from dtk.scheduler.health import HealthInput, cooldown_seconds
from dtk.scheduler.leases import forget_identity
from dtk.scheduler.scheduler import Candidate
from dtk.transport.base import Fingerprint

log = get_logger(__name__)

#: The two windows doc 02 defines the health score over. They are read from the
#: ``identity_health_5m`` continuous aggregate, which buckets at five minutes,
#: so each window is only ever accurate to one bucket - far finer than the
#: health tiers the scheduler actually ranks on.
HEALTH_RECENT_MINUTES: Final[int] = 15
HEALTH_RISK_MINUTES: Final[int] = 60

#: The window counts for an identity the aggregate has nothing on. Zero samples
#: is what :func:`dtk.scheduler.health.score` reads as "no history", so it falls
#: back to the configured prior instead of scoring a fresh identity as perfect
#: or as dead. It is also the right answer when the aggregate cannot be read.
_NO_TRAFFIC: Final[tuple[int, int, int, int]] = (0, 0, 0, 0)

#: Per-identity totals over both windows in one pass. The minute counts are
#: interpolated rather than bound: they are the module constants above, never
#: anything a caller supplies, and an INTERVAL literal keeps the statement
#: readable next to the view it reads.
_HEALTH_WINDOWS_SQL = text(
    "SELECT h.identity_id, "
    "coalesce(sum(h.total) FILTER (WHERE h.bucket >= now() - "
    f"INTERVAL '{HEALTH_RECENT_MINUTES} minutes'), 0) AS recent_total, "
    "coalesce(sum(h.ok) FILTER (WHERE h.bucket >= now() - "
    f"INTERVAL '{HEALTH_RECENT_MINUTES} minutes'), 0) AS recent_ok, "
    "coalesce(sum(h.total), 0) AS window_total, "
    "coalesce(sum(h.risk), 0) AS window_risk "
    "FROM identity_health_5m h "
    "JOIN identities i ON i.id = h.identity_id "
    "WHERE i.platform = :platform AND i.state = :state "
    f"AND h.bucket >= now() - INTERVAL '{HEALTH_RISK_MINUTES} minutes' "
    "GROUP BY h.identity_id"
)


#: The same two windows, keyed by identity rather than by pool. The scheduler
#: reads whole pools because it ranks them; the console reads the page it is
#: about to render, which may span platforms and states.
_HEALTH_BY_ID_SQL = text(
    "SELECT h.identity_id, "
    "coalesce(sum(h.total) FILTER (WHERE h.bucket >= now() - "
    f"INTERVAL '{HEALTH_RECENT_MINUTES} minutes'), 0) AS recent_total, "
    "coalesce(sum(h.ok) FILTER (WHERE h.bucket >= now() - "
    f"INTERVAL '{HEALTH_RECENT_MINUTES} minutes'), 0) AS recent_ok, "
    "coalesce(sum(h.total), 0) AS window_total, "
    "coalesce(sum(h.risk), 0) AS window_risk "
    "FROM identity_health_5m h "
    "WHERE h.identity_id = ANY(:identity_ids) "
    f"AND h.bucket >= now() - INTERVAL '{HEALTH_RISK_MINUTES} minutes' "
    "GROUP BY h.identity_id"
)


async def health_inputs(
    session: AsyncSession, identity_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, HealthInput]:
    """The health-score inputs for named identities, for a reader rather than the ranker.

    The scheduler reads one pool at a time because it ranks within one; a
    console page is whatever the operator filtered to. Same aggregate, same
    windows, same savepoint discipline: the view is absent without TimescaleDB,
    and a statement that raises would poison the request's transaction.

    Identities the aggregate has nothing on are simply missing from the result.
    That is not zero health - it is no history, which the caller has to
    distinguish, because scoring a freshly minted identity as dead would send
    an operator hunting for a fault that is only a lack of traffic.
    """
    if not identity_ids:
        return {}
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(_HEALTH_BY_ID_SQL, {"identity_ids": list(identity_ids)})
            ).all()
    except Exception as exc:
        log.debug(
            "identity.health_windows_unavailable",
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return {}
    return {
        row[0]: HealthInput(int(row[1]), int(row[2]), int(row[3]), int(row[4]), 0) for row in rows
    }


@dataclass(frozen=True, slots=True)
class LiveIdentity:
    """A decrypted identity, ready to make a request. Never persisted."""

    id: str
    platform: Platform
    cookies: dict[str, str]
    fingerprint: Fingerprint
    proxy_url: str | None
    authenticated: bool

    @property
    def cookie_header(self) -> str:
        return to_cookie_header(self.cookies)


def _fingerprint_from_json(data: dict) -> Fingerprint:
    family_raw = data.get("browser_family")
    family: BrowserFamily | None = None
    if family_raw:
        try:
            family = BrowserFamily(str(family_raw).lower())
        except ValueError:
            family = None
    return Fingerprint(
        browser_family=family,
        browser_major=data.get("browser_major"),
        user_agent=data.get("user_agent"),
        platform=data.get("platform"),
        screen=data.get("screen"),
        language=data.get("language"),
        timezone=data.get("timezone"),
    )


def _fingerprint_to_json(fp: Fingerprint) -> dict:
    return {
        "browser_family": fp.browser_family.value if fp.browser_family else None,
        "browser_major": fp.browser_major,
        "user_agent": fp.user_agent,
        "platform": fp.platform,
        "screen": fp.screen,
        "language": fp.language,
        "timezone": fp.timezone,
    }


class IdentityPool:
    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher

    # -- creation ----------------------------------------------------------

    async def add(
        self,
        session: AsyncSession,
        *,
        platform: Platform,
        cookies: dict[str, str],
        fingerprint: Fingerprint,
        source: IdentitySource,
        proxy_id: uuid.UUID | None = None,
        authenticated: bool = False,
    ) -> uuid.UUID:
        """Persist a new identity.

        Refuses a fingerprint with no inferable browser. Doc 02 is explicit that
        this is a rejection rather than a fallback: an identity whose TLS profile
        contradicts its User-Agent is worse than no identity at all.
        """
        if not fingerprint.emulatable:
            raise ValueError(
                "fingerprint has no browser family or major version; refusing to "
                "add an identity whose TLS profile cannot be matched"
            )
        identity_id = uuid.uuid4()
        row = IdentityRow(
            id=identity_id,
            platform=platform.value,
            cookies_encrypted=self._cipher.encrypt(to_cookie_header(cookies), aad=str(identity_id)),
            fingerprint=_fingerprint_to_json(fingerprint),
            proxy_id=proxy_id,
            authenticated=authenticated,
            source=source.value,
            state=IdentityState.ACTIVE.value,
            minted_at=datetime.now(UTC),
        )
        session.add(row)
        session.add(
            IdentityEvent(
                ts=datetime.now(UTC),
                identity_id=identity_id,
                event="minted" if source is IdentitySource.MINTED else "imported",
                detail={
                    "platform": platform.value,
                    "authenticated": authenticated,
                    "cookie_names": sorted(cookies),
                },
            )
        )
        await session.flush()
        log.info(
            "identity.added",
            identity_id=str(identity_id),
            platform=platform.value,
            source=source.value,
            authenticated=authenticated,
        )
        return identity_id

    # -- reading -----------------------------------------------------------

    async def load(
        self, session: AsyncSession, identity_id: str, *, proxy_url: str | None = None
    ) -> LiveIdentity | None:
        row = await session.get(IdentityRow, uuid.UUID(identity_id))
        if row is None or row.state == IdentityState.RETIRED.value:
            return None
        header = self._cipher.decrypt(row.cookies_encrypted, aad=str(row.id))
        cookies = {}
        for chunk in header.split(";"):
            name, sep, value = chunk.partition("=")
            if sep:
                cookies[name.strip()] = value.strip()
        return LiveIdentity(
            id=str(row.id),
            platform=Platform(row.platform),
            cookies=cookies,
            fingerprint=_fingerprint_from_json(row.fingerprint or {}),
            proxy_url=proxy_url,
            authenticated=row.authenticated,
        )

    async def candidates(
        self, session: AsyncSession, platform: Platform, state: IdentityState
    ) -> Sequence[Candidate]:
        """Supply the scheduler with rankable identities.

        Identities whose backoff has elapsed are promoted here rather than by a
        background job, so a recovered pool becomes usable on the next request
        instead of on the next sweep.

        The rates behind the health score are read here too. Ranking on the
        failure streak alone cannot separate an identity that fails every other
        request - each success wiping the streak - from one that has never
        failed at all, which is the difference doc 02's other two factors exist
        to express.
        """
        now = datetime.now(UTC)
        if state is IdentityState.ACTIVE:
            await self._promote_recovered(session, platform, now=now)

        rows = list(
            (
                await session.execute(
                    select(IdentityRow).where(
                        IdentityRow.platform == platform.value,
                        IdentityRow.state == state.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        windows = await self._health_windows(session, platform, state) if rows else {}

        candidates: list[Candidate] = []
        for row in rows:
            recent_total, recent_ok, window_total, window_risk = windows.get(row.id, _NO_TRAFFIC)
            candidates.append(
                Candidate(
                    identity_id=str(row.id),
                    platform=platform,
                    state=state,
                    last_used_at=row.last_used_at.timestamp() if row.last_used_at else None,
                    health=HealthInput(
                        recent_total,
                        recent_ok,
                        window_total,
                        window_risk,
                        row.consecutive_fails,
                    ),
                )
            )
        return candidates

    async def _promote_recovered(
        self, session: AsyncSession, platform: Platform, *, now: datetime
    ) -> None:
        """Return identities whose backoff has elapsed to the active pool.

        DEGRADED is included, and that is the point of this method. It is only
        ever reached from COOLING, when the backoff hits its ceiling, so
        promoting COOLING alone made it terminal: an identity that once ran a
        long risk-control streak stayed dead weight for the life of the
        deployment while the filler - which does not count DEGRADED as live -
        minted a replacement for it, and then for its replacement.

        The streak is deliberately not cleared. Only a successful request does
        that (see :meth:`record_outcome`), so an identity that is still broken
        computes the same ceiling cooldown on its next risk hit and drops back
        within one request, while until it succeeds its health score keeps it at
        the bottom of the ranking. That is the last-resort duty DEGRADED was
        described as, now expressed by the score rather than by a state nothing
        could leave.

        A NULL ``cooldown_until`` counts as elapsed. No writer produces that
        combination today, but a row that had it would be stuck in exactly the
        way this method exists to prevent.
        """
        promoted = (
            (
                await session.execute(
                    update(IdentityRow)
                    .where(
                        IdentityRow.platform == platform.value,
                        IdentityRow.state.in_(
                            [IdentityState.COOLING.value, IdentityState.DEGRADED.value]
                        ),
                        or_(
                            IdentityRow.cooldown_until.is_(None),
                            IdentityRow.cooldown_until <= now,
                        ),
                    )
                    .values(state=IdentityState.ACTIVE.value, cooldown_until=None)
                    .returning(IdentityRow.id)
                    # A bulk UPDATE over rows this short-lived session has not
                    # loaded; there is no in-memory state to keep in step.
                    .execution_options(synchronize_session=False)
                )
            )
            .scalars()
            .all()
        )
        if not promoted:
            return
        for identity_id in promoted:
            # "degraded" is written to this timeline, so its undoing has to be
            # too: the events table is the only record of why the pool changed
            # shape, and leaving "degraded" as the last word about an identity
            # that is working again makes it lie.
            session.add(
                IdentityEvent(
                    ts=now,
                    identity_id=identity_id,
                    event="activated",
                    detail={"reason": "cooldown_elapsed"},
                )
            )
        # Flushed here, not left pending: the health read that follows runs in a
        # savepoint, and a pending insert would be flushed inside it and rolled
        # back with it. An unreadable aggregate must not swallow the timeline.
        await session.flush()
        log.info("identity.reactivated", platform=platform.value, count=len(promoted))

    async def _health_windows(
        self, session: AsyncSession, platform: Platform, state: IdentityState
    ) -> dict[uuid.UUID, tuple[int, int, int, int]]:
        """Per-identity request counts over both health windows.

        Read from the continuous aggregate rather than from ``request_log``:
        the aggregate is materialized in five-minute buckets with real-time
        aggregation on top, so the last few minutes are included without this
        query scanning the raw hypertable on the scheduler's path.

        The savepoint is what makes the read optional. The view is absent on an
        instance without TimescaleDB, and a statement that raises poisons the
        surrounding transaction - here, the one the scheduler is picking an
        identity in. Losing the rates costs ranking accuracy for one call;
        losing the transaction costs the request.
        """
        try:
            async with session.begin_nested():
                rows = (
                    await session.execute(
                        _HEALTH_WINDOWS_SQL,
                        {"platform": platform.value, "state": state.value},
                    )
                ).all()
        except Exception as exc:
            log.debug(
                "identity.health_windows_unavailable",
                platform=platform.value,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return {}
        return {row[0]: (int(row[1]), int(row[2]), int(row[3]), int(row[4])) for row in rows}

    # -- outcome bookkeeping ------------------------------------------------

    async def record_outcome(
        self,
        session: AsyncSession,
        identity_id: str,
        outcome: Outcome,
        *,
        cooldown_base: int,
        cooldown_max: int,
        risk_weight: float = 1.0,
    ) -> IdentityState:
        """Fold one request result into the identity's state.

        BUSINESS_ERROR deliberately changes nothing: a deleted video is a fact
        about the content, not about the identity. V4 treated every non-200 the
        same, so looking up a removed video could condemn a working cookie.
        """
        row = await session.get(IdentityRow, uuid.UUID(identity_id))
        if row is None:
            return IdentityState.RETIRED

        row.last_used_at = datetime.now(UTC)

        if outcome is Outcome.OK:
            row.consecutive_fails = 0
            if row.state == IdentityState.COOLING.value:
                row.state = IdentityState.ACTIVE.value
                row.cooldown_until = None
            # A DEGRADED identity is deliberately not promoted here. Clearing
            # the streak is what one success buys it; it rejoins the active pool
            # when its ceiling cooldown elapses, so a long failing history costs
            # it a probation window instead of being undone by a lucky request.
        elif outcome is Outcome.BUSINESS_ERROR:
            pass
        elif outcome is Outcome.NETWORK_ERROR:
            row.consecutive_fails += 1
        elif outcome is Outcome.RISK_CONTROL:
            row.consecutive_fails += 1
            seconds = cooldown_seconds(
                row.consecutive_fails,
                base=cooldown_base,
                maximum=cooldown_max,
                risk_weight=risk_weight,
            )
            row.cooldown_until = datetime.now(UTC) + timedelta(seconds=seconds)
            row.state = (
                IdentityState.DEGRADED.value
                if seconds >= cooldown_max
                else IdentityState.COOLING.value
            )
            session.add(
                IdentityEvent(
                    ts=datetime.now(UTC),
                    identity_id=row.id,
                    event="cooled" if row.state == IdentityState.COOLING.value else "degraded",
                    detail={"cooldown_seconds": seconds, "streak": row.consecutive_fails},
                )
            )
            log.warning(
                "identity.cooldown",
                identity_id=identity_id,
                cooldown_seconds=seconds,
                streak=row.consecutive_fails,
                state=row.state,
            )
        await session.flush()
        return IdentityState(row.state)

    async def cool_all_on_proxy(
        self, session: AsyncSession, proxy_id: uuid.UUID, *, seconds: int
    ) -> int:
        """Pause every identity behind a proxy that has just failed its probe.

        They cool rather than retire: the cookies are fine, only the egress is
        down. But they are never moved to a different proxy - that recombination
        is exactly what doc 02 forbids - so a permanently dead proxy eventually
        means retiring them.
        """
        result = await session.execute(
            update(IdentityRow)
            .where(
                IdentityRow.proxy_id == proxy_id,
                IdentityRow.state.in_([IdentityState.ACTIVE.value, IdentityState.DEGRADED.value]),
            )
            .values(
                state=IdentityState.COOLING.value,
                cooldown_until=datetime.now(UTC) + timedelta(seconds=seconds),
            )
        )
        # rowcount lives on CursorResult; execute() is typed as returning the
        # narrower Result, which does not declare it.
        count = getattr(result, "rowcount", 0) or 0
        if count:
            log.warning("identity.proxy_cooldown", proxy_id=str(proxy_id), affected=count)
        return count

    async def retire(self, session: AsyncSession, identity_id: str, reason: str) -> None:
        """Retire an identity and wipe its credential immediately.

        The statistics are worth keeping; a discarded login is not.
        """
        row = await session.get(IdentityRow, uuid.UUID(identity_id))
        if row is None:
            return
        row.state = IdentityState.RETIRED.value
        row.retired_at = datetime.now(UTC)
        row.retire_reason = reason
        row.cookies_encrypted = b""
        session.add(
            IdentityEvent(
                ts=datetime.now(UTC),
                identity_id=row.id,
                event="retired",
                detail={"reason": reason},
            )
        )
        await session.flush()
        await forget_identity(identity_id)
        log.info("identity.retired", identity_id=identity_id, reason=reason)

    async def counts(self, session: AsyncSession, platform: Platform) -> dict[str, int]:
        rows = await session.execute(
            select(IdentityRow.state, func.count())
            .where(IdentityRow.platform == platform.value)
            .group_by(IdentityRow.state)
        )
        return {str(state): int(count) for state, count in rows.all()}


async def purge_retired(session: AsyncSession, *, days: int) -> int:
    """Delete retired identities older than ``days``, returning how many went.

    Retirement wipes the credential and keeps the row for its statistics, and
    nothing ever removed it: on a deployment that mints a replacement for every
    identity it loses, ``identities`` is the one table that only grows. The
    window is an operator's call, so it comes from the retention settings like
    every other one rather than from a constant in here.

    Their events are left to age out under ``retention.identity_events_days``.
    ``request_log`` and ``identity_events`` carry no foreign key to this table
    (doc 05: no per-row check on the highest-volume insert path), so nothing
    cascades and nothing blocks the delete.

    ``retired_at`` is written by :func:`IdentityPool.retire`, but a row that
    reached RETIRED without it would be exactly as immortal as the rows this
    deletes, so the mint date stands in for a missing one.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max(1, days))
    result = await session.execute(
        delete(IdentityRow)
        .where(
            IdentityRow.state == IdentityState.RETIRED.value,
            func.coalesce(IdentityRow.retired_at, IdentityRow.minted_at) < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    count = int(getattr(result, "rowcount", 0) or 0)
    if count:
        log.info("identity.retired_purged", deleted=count, older_than_days=days)
    return count


__all__ = ["IdentityPool", "LiveIdentity", "health_inputs", "purge_retired"]
