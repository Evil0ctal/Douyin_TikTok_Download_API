"""The scheduler.

Answers one question - "which identity may send this request right now?" - or
refuses with a reason that can be reported back to the caller.

Two rotation dimensions are in play at once. Identities take turns, and each
identity has an independent budget per endpoint. See docs/design/03-scheduler.md.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from dtk.core.errors import EndpointCircuitOpen, IdentityPoolExhausted
from dtk.core.logging import get_logger
from dtk.core.types import IdentityState, Outcome, Platform, RejectReason
from dtk.scheduler import circuit
from dtk.scheduler.health import HealthInput, bucket, score
from dtk.scheduler.leases import Lease, last_used_map, release, try_acquire
from dtk.scheduler.policies import EndpointPolicy, policy_for

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from dtk.ops.notify import Alerter

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Candidate:
    """The scheduling view of an identity: just enough to rank it."""

    identity_id: str
    platform: Platform
    state: IdentityState
    #: Epoch seconds; None means never used, which sorts first under LRU.
    last_used_at: float | None
    health: HealthInput


class CandidateSource(Protocol):
    """Supplies schedulable identities.

    A Protocol rather than a direct database dependency so the scheduler can be
    tested against an in-memory list, which is how the concurrency tests reach
    the atomicity guarantees.
    """

    async def candidates(self, platform: Platform, state: IdentityState) -> Sequence[Candidate]: ...


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    max_wait_seconds: float = 10.0
    poll_interval_seconds: float = 0.25
    inflight_ttl_seconds: int = 60
    #: Hard bound on retries, independent of the clock. The deadline alone is
    #: not enough: a stalled or non-monotonic clock would spin here forever.
    max_attempts: int = 64
    health_prior: float = 0.8
    #: Recency is compared to this granularity. On the exact timestamp every
    #: candidate is distinct, so the random tie-break is never reached; rounding
    #: creates the ties that let concurrent callers diverge, while an identity
    #: idle far longer than one quantum still sorts ahead.
    #:
    #: Measured fairness is not sensitive to this value - see the note in
    #: tests/integration/test_scheduler_fairness.py - so it is a knob, not a
    #: tuned constant. Set it to 0 for strict least-recently-used ordering.
    lru_quantum_seconds: float = 0.5
    circuit: circuit.CircuitConfig = field(default_factory=circuit.CircuitConfig)


class Rejected(Exception):
    """Internal signal carrying why no lease could be issued."""

    def __init__(self, reason: RejectReason, detail: str = "", retry_after: int = 0) -> None:
        super().__init__(detail or reason.value)
        self.reason = reason
        self.detail = detail
        self.retry_after = retry_after


class Scheduler:
    def __init__(
        self,
        source: CandidateSource,
        config: SchedulerConfig,
        *,
        clock: object | None = None,
        rng: random.Random | None = None,
        alerter: Alerter | None = None,
    ) -> None:
        self._source = source
        self._config = config
        # Injectable so tests can advance time without sleeping.
        self._now = clock if callable(clock) else _monotonic_epoch
        # Injectable so a test can make tie-breaking reproducible.
        self._rng = rng or random.Random()
        # Optional, like the background jobs': a deployment with no channels
        # configured passes None and the scheduler goes on working.
        self._alerter = alerter

    # -- selection ---------------------------------------------------------

    def _rank(
        self,
        candidates: Sequence[Candidate],
        seed: int,
        recent: dict[str, float],
    ) -> list[Candidate]:
        """Order by health tier, then least-recently-used, then a stable jitter.

        Two things make the tie-break actually work, and both were needed:

        The seed varies PER CALL, not per retry count. Concurrent callers all read
        the same last-used snapshot before any of them wins a lease, so a seed
        derived from the attempt number ranks every first attempt identically.

        Recency is ROUNDED before comparing. On the exact timestamp every
        candidate is distinct, the jitter is never reached, and callers cascade
        down one shared order. Rounding creates the ties the jitter needs, while
        an identity idle far longer than a quantum still sorts ahead.

        Both changes are kept because neither can make ordering worse and the
        second is required for the first to have any effect at all. Neither is
        claimed to improve fairness: measured against a uniform random baseline
        the resulting spread is no better than chance, and quantizing recency
        does not beat leaving it exact. See the note in
        tests/integration/test_scheduler_fairness.py for the numbers.
        """
        prior = self._config.health_prior
        quantum = self._config.lru_quantum_seconds

        def key(c: Candidate) -> tuple[int, float, int]:
            tier = bucket(score(c.health, prior=prior))
            # Take whichever source saw this identity most recently. The database
            # column lags behind by a write, so ranking on it alone lets several
            # back-to-back requests all see the same stale order and pick the
            # same identity: rotation collapses to "always the healthiest one".
            last_used = max(c.last_used_at or 0.0, recent.get(c.identity_id, 0.0))
            lru = int(last_used / quantum) if quantum > 0 else last_used
            jitter = hash((c.identity_id, seed)) & 0xFFFF
            return (-tier, lru, jitter)

        return sorted(candidates, key=key)

    async def _attempt(
        self, endpoint: str, platform: Platform, policy: EndpointPolicy, seed: int
    ) -> Lease:
        now = self._now()

        is_open, retry_after, reason = await circuit.state(endpoint, now=now)
        if is_open and not await circuit.allow_probe(endpoint, self._config.circuit):
            raise Rejected(RejectReason.CIRCUIT_OPEN, reason, retry_after)

        pool = list(await self._source.candidates(platform, IdentityState.ACTIVE))
        if not pool:
            # Degraded identities are the last resort, used only once the
            # healthy pool is empty.
            pool = list(await self._source.candidates(platform, IdentityState.DEGRADED))
        if not pool:
            raise Rejected(RejectReason.NO_IDENTITY, "no active or degraded identity")

        recent = await last_used_map()
        saw_inflight = False
        saw_no_token = False
        for candidate in self._rank(pool, seed, recent):
            lease, why = await try_acquire(
                candidate.identity_id,
                endpoint,
                policy,
                now=now,
                inflight_ttl=self._config.inflight_ttl_seconds,
            )
            if lease is not None:
                log.debug(
                    "scheduler.lease.granted",
                    identity_id=candidate.identity_id,
                    endpoint=endpoint,
                    tokens_left=round(lease.tokens_left, 3),
                )
                return lease
            saw_inflight |= why == "inflight"
            saw_no_token |= why == "no_token"

        if saw_no_token and not saw_inflight:
            raise Rejected(RejectReason.NO_TOKEN, "every identity is out of quota")
        raise Rejected(RejectReason.ALL_INFLIGHT, "every identity is busy")

    # -- public API --------------------------------------------------------

    async def acquire(self, endpoint: str, platform: Platform) -> Lease:
        """Wait briefly for a lease, then give up with an explainable error.

        Rejecting early beats queueing indefinitely: a caller left hanging for a
        minute before failing is worse off than one told to retry immediately.
        """
        policy = policy_for(endpoint)
        deadline = self._now() + self._config.max_wait_seconds
        attempt = 0
        # One seed per call, so two callers ranking the same snapshot do not
        # produce the same order and race for the same identity.
        seed = self._rng.getrandbits(32)
        last: Rejected | None = None

        while True:
            try:
                return await self._attempt(endpoint, platform, policy, seed + attempt)
            except Rejected as exc:
                last = exc
                if exc.reason is RejectReason.CIRCUIT_OPEN:
                    break  # waiting cannot help
                if self._now() >= deadline:
                    last = Rejected(RejectReason.WAIT_TIMEOUT, exc.detail, exc.retry_after)
                    break
            attempt += 1
            await asyncio.sleep(self._config.poll_interval_seconds)

        assert last is not None
        log.info(
            "scheduler.lease.rejected",
            endpoint=endpoint,
            platform=platform.value,
            reject_reason=last.reason.value,
            detail=last.detail,
        )
        if last.reason is RejectReason.CIRCUIT_OPEN:
            raise EndpointCircuitOpen(
                last.detail,
                retry_after=last.retry_after or self._config.circuit.open_seconds,
                details={"endpoint": endpoint, "reject_reason": last.reason.value},
            )
        raise IdentityPoolExhausted(
            last.detail,
            retry_after=max(1, int(self._config.max_wait_seconds)),
            details={"endpoint": endpoint, "reject_reason": last.reason.value},
        )

    async def release(self, lease: Lease, outcome: Outcome) -> None:
        """Return a lease and fold the result into the endpoint's window."""
        policy = policy_for(lease.endpoint)
        now = self._now()
        await release(lease, outcome, policy, now=now)
        await circuit.record(lease.endpoint, lease.identity_id, outcome, now=now)

        if outcome is Outcome.RISK_CONTROL:
            trip, why = await circuit.should_trip(lease.endpoint, self._config.circuit, now=now)
            if trip:
                await circuit.trip(lease.endpoint, self._config.circuit, why, now=now)
                self._page_circuit_open(lease.endpoint, why)
        elif outcome is Outcome.OK:
            is_open, _, _ = await circuit.state(lease.endpoint, now=now)
            if is_open:
                # A probe succeeded, so the endpoint is working again.
                await circuit.reset(lease.endpoint)

    def _page_circuit_open(self, endpoint: str, reason: str) -> None:
        """Page the operator without making the request that tripped it wait.

        This is the alert doc 15 leads with and the only place that knows the
        circuit has just opened - :func:`circuit.trip` is called nowhere else.
        It is also the request path, where a channel that takes its full
        timeout twice would charge those seconds to whichever request happened
        to be last, so the delivery is scheduled rather than awaited. The
        30-minute window belongs to the notifier, so the thousand requests that
        follow this one into an open circuit page nobody.
        """
        # Imported here, not at the top: dtk.ops reaches the identity pool,
        # which imports this module, so the package-level import is a cycle.
        from dtk.ops.notify import NotifyEvent, alert_in_background

        parsed = circuit.parse_reason(reason)
        alert_in_background(
            self._alerter,
            NotifyEvent.ENDPOINT_CIRCUIT_OPEN,
            endpoint=endpoint,
            platform=_platform_of(endpoint),
            retry_after=self._config.circuit.open_seconds,
            # The risk rate as a percentage, the sample count and how many
            # identities it spanned - the same numbers, rendered the same way,
            # as the reason the console shows for this trip.
            **(parsed.template_args() if parsed is not None else {}),
        )


def _platform_of(endpoint: str) -> str | None:
    """The platform an endpoint name starts with, for the alert's first word.

    Endpoint names are ``<platform>.<call>`` (see :mod:`dtk.scheduler.policies`).
    An unregistered name has no platform to report, and letting the alert say
    "unknown" reads better than naming the same endpoint twice in one sentence.
    """
    try:
        return Platform(endpoint.partition(".")[0]).value
    except ValueError:
        return None


def _monotonic_epoch() -> float:
    import time

    return time.time()


__all__ = [
    "Candidate",
    "CandidateSource",
    "Rejected",
    "Scheduler",
    "SchedulerConfig",
]
