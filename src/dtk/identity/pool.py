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

from sqlalchemy import func, select, update
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

        Cooling identities whose window has elapsed are promoted here rather
        than by a background job, so a recovered pool becomes usable on the next
        request instead of on the next sweep.
        """
        now = datetime.now(UTC)
        if state is IdentityState.ACTIVE:
            await session.execute(
                update(IdentityRow)
                .where(
                    IdentityRow.platform == platform.value,
                    IdentityRow.state == IdentityState.COOLING.value,
                    IdentityRow.cooldown_until.is_not(None),
                    IdentityRow.cooldown_until <= now,
                )
                .values(state=IdentityState.ACTIVE.value, cooldown_until=None)
            )

        rows = (
            await session.execute(
                select(IdentityRow).where(
                    IdentityRow.platform == platform.value,
                    IdentityRow.state == state.value,
                )
            )
        ).scalars()

        return [
            Candidate(
                identity_id=str(r.id),
                platform=platform,
                state=state,
                last_used_at=r.last_used_at.timestamp() if r.last_used_at else None,
                # Windowed rates live in the continuous aggregates; the streak is
                # the part the scheduler needs on the hot path and it is kept on
                # the row so ranking never waits on an aggregate refresh.
                health=HealthInput(0, 0, 0, 0, r.consecutive_fails),
            )
            for r in rows
        ]

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
        count = result.rowcount or 0
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
        return dict(rows.all())


__all__ = ["IdentityPool", "LiveIdentity"]
