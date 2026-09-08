"""Identity pool state transitions and the inputs behind the health score.

The pool's states are only worth anything if every one of them can be left
again. DEGRADED could not: it is reached from COOLING when the backoff hits its
ceiling, and the only promotion in the module was written ``WHERE state =
COOLING``, so an identity that ever ran a long risk-control streak stayed there
for the life of the deployment while the filler minted a replacement for it.

Nothing here needs a database. The transitions that are one statement are
asserted on the statement the pool builds - the values it binds and the columns
it touches - and the ones that are Python are driven through a fake row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import TextClause

from dtk.core.types import IdentityState, Outcome, Platform
from dtk.identity.pool import IdentityPool, purge_retired
from dtk.scheduler.health import HealthInput

COOLDOWN_BASE = 60
COOLDOWN_MAX = 21600


# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self) -> Any:
        return iter(self._rows)


class FakeResult:
    def __init__(self, rows: list[Any], *, rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _Savepoint:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    async def __aenter__(self) -> None:
        if self._fail:
            raise RuntimeError('relation "identity_health_5m" does not exist')

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class FakeSession:
    """Enough AsyncSession to drive the pool, answering by statement kind."""

    def __init__(
        self,
        *,
        identities: list[Any] | None = None,
        promoted: list[uuid.UUID] | None = None,
        windows: list[tuple[Any, ...]] | None = None,
        aggregate_missing: bool = False,
        row: Any = None,
        rowcount: int = 0,
    ) -> None:
        self._identities = identities or []
        self._promoted = promoted or []
        self._windows = windows or []
        self._aggregate_missing = aggregate_missing
        self._row = row
        self._rowcount = rowcount
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.flushes = 0

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self._aggregate_missing)

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return FakeResult(self._windows)
        rendered = str(statement).lstrip().upper()
        if rendered.startswith("UPDATE"):
            return FakeResult(list(self._promoted), rowcount=len(self._promoted))
        if rendered.startswith("DELETE"):
            return FakeResult([], rowcount=self._rowcount)
        return FakeResult(self._identities)

    async def get(self, model: Any, key: Any) -> Any:
        return self._row

    async def flush(self) -> None:
        self.flushes += 1

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def statement_starting(self, verb: str) -> Any:
        for statement in self.statements:
            if str(statement).lstrip().upper().startswith(verb):
                return statement
        raise AssertionError(f"no {verb} statement was issued")


def bound_values(statement: Any) -> set[Any]:
    """Every literal the statement carries, IN lists flattened."""
    values: set[Any] = set()
    for value in statement.compile().params.values():
        if isinstance(value, list | tuple):
            values.update(value)
        else:
            values.add(value)
    return values


def identity_row(
    *,
    state: IdentityState = IdentityState.ACTIVE,
    consecutive_fails: int = 0,
    last_used_at: datetime | None = None,
) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        platform=Platform.DOUYIN.value,
        state=state.value,
        consecutive_fails=consecutive_fails,
        cooldown_until=None,
        last_used_at=last_used_at,
        retired_at=None,
    )


def pool() -> IdentityPool:
    # candidates() and record_outcome() never touch the cipher; nothing here
    # decrypts a cookie jar.
    return IdentityPool(cipher=None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# recovery from DEGRADED
# --------------------------------------------------------------------------


async def test_a_degraded_identity_is_promoted_when_its_backoff_elapses() -> None:
    """The leak: DEGRADED had no way out, so the pool only ever shrank.

    Twelve risk-control hits take an identity past the cooldown ceiling. If the
    promotion still reads ``WHERE state = COOLING`` that identity is scheduled
    only when the healthy pool is empty, forever, and the filler mints a
    replacement it will eventually lose the same way.
    """
    session = FakeSession(identities=[])

    await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    promotion = session.statement_starting("UPDATE")
    values = bound_values(promotion)
    assert IdentityState.DEGRADED.value in values
    assert IdentityState.COOLING.value in values
    assert IdentityState.ACTIVE.value in values


async def test_promotion_leaves_the_failure_streak_alone() -> None:
    """Recovery is probation, not absolution.

    The streak is what makes the next risk hit compute the ceiling cooldown
    again and what holds the identity at the bottom of the health ranking.
    Clearing it here would hand a long-broken identity full duty on a timer.
    """
    session = FakeSession(identities=[])

    await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    assert "consecutive_fails" not in str(session.statement_starting("UPDATE"))


async def test_a_promotion_is_written_to_the_identity_timeline() -> None:
    """ "degraded" is recorded, so its undoing has to be recorded too."""
    recovered = uuid.uuid4()
    session = FakeSession(identities=[], promoted=[recovered])

    await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    events = [obj for obj in session.added if getattr(obj, "event", None) == "activated"]
    assert [event.identity_id for event in events] == [recovered]


async def test_only_the_active_query_promotes() -> None:
    """The last-resort read must not empty the tier it is reading."""
    session = FakeSession(identities=[])

    await pool().candidates(session, Platform.DOUYIN, IdentityState.DEGRADED)  # type: ignore[arg-type]

    assert not [s for s in session.statements if str(s).lstrip().upper().startswith("UPDATE")]


async def test_a_success_does_not_promote_a_degraded_identity_by_itself() -> None:
    """One good request clears the streak; it does not end the probation.

    Reproduces the finding's second half: twelve risk-control hits degrade the
    identity, and the successes that follow leave the state alone. What ends it
    is the elapsed cooldown, which is the transition tested above.
    """
    row = identity_row()
    session = FakeSession(row=row)
    identity_pool = pool()

    for _ in range(12):
        state = await identity_pool.record_outcome(
            session,  # type: ignore[arg-type]
            str(row.id),
            Outcome.RISK_CONTROL,
            cooldown_base=COOLDOWN_BASE,
            cooldown_max=COOLDOWN_MAX,
        )
    assert state is IdentityState.DEGRADED
    assert row.cooldown_until is not None

    for _ in range(6):
        state = await identity_pool.record_outcome(
            session,  # type: ignore[arg-type]
            str(row.id),
            Outcome.OK,
            cooldown_base=COOLDOWN_BASE,
            cooldown_max=COOLDOWN_MAX,
        )

    assert state is IdentityState.DEGRADED
    assert row.consecutive_fails == 0


async def test_a_cooling_identity_still_recovers_on_a_success() -> None:
    """The transition that already worked has to keep working."""
    row = identity_row(state=IdentityState.COOLING, consecutive_fails=3)
    row.cooldown_until = datetime.now(UTC) + timedelta(minutes=5)
    session = FakeSession(row=row)

    state = await pool().record_outcome(
        session,  # type: ignore[arg-type]
        str(row.id),
        Outcome.OK,
        cooldown_base=COOLDOWN_BASE,
        cooldown_max=COOLDOWN_MAX,
    )

    assert state is IdentityState.ACTIVE
    assert row.cooldown_until is None


# --------------------------------------------------------------------------
# health inputs
# --------------------------------------------------------------------------


async def test_candidates_carry_the_windowed_rates_not_only_the_streak() -> None:
    """Two of the three documented factors used to be hardcoded to zero.

    An identity that fails every other request has no streak - each success
    wipes it - so with zeros it scored exactly like one that has never failed.
    """
    row = identity_row(consecutive_fails=2)
    session = FakeSession(identities=[row], windows=[(row.id, 20, 10, 60, 15)])

    candidates = await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    assert [c.health for c in candidates] == [HealthInput(20, 10, 60, 15, 2)]


async def test_an_identity_with_no_traffic_falls_back_to_the_prior() -> None:
    """A freshly minted identity has no rows in the aggregate."""
    row = identity_row()
    session = FakeSession(identities=[row], windows=[])

    candidates = await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    assert [c.health for c in candidates] == [HealthInput(0, 0, 0, 0, 0)]


async def test_the_pool_is_still_schedulable_without_the_aggregate() -> None:
    """No TimescaleDB means no view; it must not cost the request.

    The read is in a savepoint precisely because a raised statement would
    poison the transaction the scheduler is picking an identity in.
    """
    row = identity_row(consecutive_fails=1)
    session = FakeSession(identities=[row], aggregate_missing=True)

    candidates = await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    assert [c.identity_id for c in candidates] == [str(row.id)]
    assert [c.health for c in candidates] == [HealthInput(0, 0, 0, 0, 1)]


async def test_the_aggregate_is_not_queried_for_an_empty_tier() -> None:
    session = FakeSession(identities=[])

    await pool().candidates(session, Platform.DOUYIN, IdentityState.ACTIVE)  # type: ignore[arg-type]

    assert not [s for s in session.statements if isinstance(s, TextClause)]


# --------------------------------------------------------------------------
# retired rows
# --------------------------------------------------------------------------


async def test_retired_identities_are_purged_past_their_window() -> None:
    """``identities`` is a plain table: no policy bounds it, so this must."""
    session = FakeSession(rowcount=4)

    deleted = await purge_retired(session, days=90)  # type: ignore[arg-type]

    statement = session.statement_starting("DELETE")
    assert deleted == 4
    assert str(statement).lstrip().upper().startswith("DELETE FROM IDENTITIES")
    values = bound_values(statement)
    assert IdentityState.RETIRED.value in values
    cutoff = next(v for v in values if isinstance(v, datetime))
    assert timedelta(days=89) < datetime.now(UTC) - cutoff < timedelta(days=91)


@pytest.mark.parametrize("days", [0, -30])
async def test_a_nonsense_window_never_deletes_everything(days: int) -> None:
    """A zero or negative setting would otherwise delete the whole history."""
    session = FakeSession(rowcount=0)

    await purge_retired(session, days=days)  # type: ignore[arg-type]

    cutoff = next(
        v for v in bound_values(session.statement_starting("DELETE")) if isinstance(v, datetime)
    )
    assert cutoff < datetime.now(UTC)
