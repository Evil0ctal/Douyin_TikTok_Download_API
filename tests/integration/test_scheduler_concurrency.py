"""Scheduler atomicity against a real Redis.

The defect this file exists to catch cannot appear in a single-threaded test:
checking the token bucket and taking the inflight lock as two steps lets several
workers all observe budget and all proceed. The assertion is therefore always
"the number of leases actually issued is <= the quota", under concurrency.
"""

from __future__ import annotations

import asyncio

import pytest

from dtk.core.errors import IdentityLoginRequired, IdentityPoolExhausted, InvalidParam
from dtk.core.types import IdentityState, LoginNeed, Outcome, Platform
from dtk.scheduler import circuit
from dtk.scheduler.health import HealthInput
from dtk.scheduler.leases import release, try_acquire
from dtk.scheduler.policies import EndpointPolicy
from dtk.scheduler.scheduler import Candidate, Scheduler, SchedulerConfig

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = 1_700_000_000.0


class AdvancingClock:
    """A clock that steps forward on every read.

    A frozen clock makes the scheduler's wait loop unbounded in wall time, which
    is exactly the condition `max_attempts` exists to survive; these tests want
    the deadline path instead.
    """

    def __init__(self, start: float = NOW, step: float = 0.05) -> None:
        self.t = start
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


class ListSource:
    def __init__(self, candidates):
        self._candidates = candidates

    async def candidates(self, platform, state):
        return [c for c in self._candidates if c.platform == platform and c.state == state]


def make_candidate(
    i, *, state=IdentityState.ACTIVE, last_used=None, fails=0, rotation=None, signed_in=False
):
    return Candidate(
        identity_id=f"identity-{i}",
        platform=Platform.DOUYIN,
        state=state,
        last_used_at=last_used,
        health=HealthInput(20, 20, 60, 0, fails),
        rotation_endpoints=rotation,
        authenticated=signed_in,
    )


async def test_single_identity_grants_exactly_one_concurrent_lease(redis_client):
    """Per-identity concurrency is 1: a real session is not parallel."""
    policy = EndpointPolicy("e", capacity=10, refill_per_sec=10.0)
    results = await asyncio.gather(
        *[try_acquire("id-1", "e", policy, now=NOW, inflight_ttl=60) for _ in range(25)]
    )
    granted = [lease for lease, _ in results if lease is not None]
    assert len(granted) == 1
    assert all(why == "inflight" for lease, why in results if lease is None)


async def test_token_bucket_is_not_breached_under_concurrency(redis_client):
    """The TOCTOU regression test.

    Twenty identities, each with a two-token bucket, hammered concurrently.
    A non-atomic check-then-take issues more than the 40 available tokens.
    """
    policy = EndpointPolicy("burst", capacity=2, refill_per_sec=0.0)
    ids = [f"id-{i}" for i in range(20)]

    async def grab(identity_id):
        lease, _ = await try_acquire(identity_id, "burst", policy, now=NOW, inflight_ttl=60)
        if lease is not None:
            await release(lease, Outcome.OK, policy, now=NOW)
            return 1
        return 0

    rounds = [grab(i) for i in ids for _ in range(10)]
    issued = sum(await asyncio.gather(*rounds))

    assert issued <= len(ids) * policy.capacity, (
        f"issued {issued} leases against a hard quota of {len(ids) * policy.capacity}"
    )
    assert issued > 0


async def test_network_error_refunds_its_token(redis_client):
    """A request that never reached the platform must not be charged."""
    policy = EndpointPolicy("refund", capacity=1, refill_per_sec=0.0)

    lease, _ = await try_acquire("id-r", "refund", policy, now=NOW, inflight_ttl=60)
    assert lease is not None
    await release(lease, Outcome.NETWORK_ERROR, policy, now=NOW)

    again, why = await try_acquire("id-r", "refund", policy, now=NOW, inflight_ttl=60)
    assert again is not None, f"token was not refunded ({why})"


@pytest.mark.parametrize("outcome", [Outcome.OK, Outcome.BUSINESS_ERROR, Outcome.RISK_CONTROL])
async def test_other_outcomes_consume_their_token(redis_client, outcome):
    policy = EndpointPolicy("consume", capacity=1, refill_per_sec=0.0)
    lease, _ = await try_acquire("id-c", "consume", policy, now=NOW, inflight_ttl=60)
    assert lease is not None
    await release(lease, outcome, policy, now=NOW)

    again, why = await try_acquire("id-c", "consume", policy, now=NOW, inflight_ttl=60)
    assert again is None and why == "no_token"


async def test_stale_release_cannot_free_someone_elses_lock(redis_client):
    """A late return must not delete a lock the TTL already reassigned."""
    policy = EndpointPolicy("stale", capacity=5, refill_per_sec=5.0)

    first, _ = await try_acquire("id-s", "stale", policy, now=NOW, inflight_ttl=60)
    assert first is not None
    await redis_client.delete(f"sched:inflight:{first.identity_id}")  # simulate TTL expiry
    second, _ = await try_acquire("id-s", "stale", policy, now=NOW, inflight_ttl=60)
    assert second is not None

    status = await release(first, Outcome.OK, policy, now=NOW)
    assert status == "stale"
    assert await redis_client.get(f"sched:inflight:{second.identity_id}") == second.lease_id


async def test_bucket_refills_over_time(redis_client):
    policy = EndpointPolicy("refill", capacity=2, refill_per_sec=0.5)
    for _ in range(2):
        lease, _ = await try_acquire("id-t", "refill", policy, now=NOW, inflight_ttl=60)
        assert lease is not None
        await release(lease, Outcome.OK, policy, now=NOW)

    blocked, why = await try_acquire("id-t", "refill", policy, now=NOW, inflight_ttl=60)
    assert blocked is None and why == "no_token"

    later, _ = await try_acquire("id-t", "refill", policy, now=NOW + 4.0, inflight_ttl=60)
    assert later is not None


async def test_circuit_needs_multiple_identities_to_trip(redis_client):
    """The third condition: one flapping identity must not trip an endpoint."""
    cfg = circuit.CircuitConfig(risk_threshold=0.5, min_samples=5, min_identities=3)

    for _ in range(10):
        await circuit.record("ep", "only-one", Outcome.RISK_CONTROL, now=NOW)
    trip, why = await circuit.should_trip("ep", cfg, now=NOW)
    assert trip is False
    assert "identity" in why

    for ident in ("a", "b", "c"):
        for _ in range(4):
            await circuit.record("ep", ident, Outcome.RISK_CONTROL, now=NOW)
    trip, why = await circuit.should_trip("ep", cfg, now=NOW)
    assert trip is True


async def test_circuit_needs_enough_samples(redis_client):
    cfg = circuit.CircuitConfig(risk_threshold=0.5, min_samples=20, min_identities=2)
    for ident in ("a", "b", "c"):
        await circuit.record("few", ident, Outcome.RISK_CONTROL, now=NOW)
    trip, why = await circuit.should_trip("few", cfg, now=NOW)
    assert trip is False and "samples" in why


async def test_scheduler_rotates_across_identities(redis_client):
    """Equal-health identities must take turns rather than reusing the healthiest."""
    pool = [make_candidate(i, last_used=NOW - 100 + i) for i in range(5)]
    sched = Scheduler(
        ListSource(pool),
        SchedulerConfig(max_wait_seconds=5.0, inflight_ttl_seconds=60),
        clock=AdvancingClock(step=0.0),
    )
    seen = set()
    for _ in range(5):
        lease = await sched.acquire("douyin.content_detail", Platform.DOUYIN)
        seen.add(lease.identity_id)
        await sched.release(lease, Outcome.OK)
    assert len(seen) >= 4, f"poor rotation, only used {seen}"


async def test_scheduler_prefers_healthy_over_recently_failed(redis_client):
    pool = [
        make_candidate(1, last_used=NOW - 1000, fails=4),  # oldest but just failed
        make_candidate(2, last_used=NOW - 1),  # recent but healthy
    ]
    sched = Scheduler(
        ListSource(pool),
        SchedulerConfig(max_wait_seconds=5.0),
        clock=AdvancingClock(step=0.0),
    )
    lease = await sched.acquire("douyin.content_detail", Platform.DOUYIN)
    assert lease.identity_id == "identity-2"


async def test_scheduler_falls_back_to_degraded_only_when_empty(redis_client):
    degraded = [make_candidate(9, state=IdentityState.DEGRADED)]
    sched = Scheduler(
        ListSource(degraded), SchedulerConfig(max_wait_seconds=5.0), clock=AdvancingClock(step=0.0)
    )
    lease = await sched.acquire("douyin.content_detail", Platform.DOUYIN)
    assert lease.identity_id == "identity-9"


async def test_empty_pool_raises_pool_exhausted_with_retry_after(redis_client):
    from dtk.core.errors import IdentityPoolExhausted

    sched = Scheduler(
        ListSource([]),
        SchedulerConfig(max_wait_seconds=0.5, poll_interval_seconds=0.01),
        clock=AdvancingClock(step=0.1),
    )
    with pytest.raises(IdentityPoolExhausted) as exc:
        await sched.acquire("douyin.content_detail", Platform.DOUYIN)
    assert exc.value.retry_after and exc.value.retry_after > 0
    assert exc.value.details["reject_reason"]


async def test_open_circuit_fails_fast_without_waiting(redis_client):
    from dtk.core.errors import EndpointCircuitOpen

    cfg = SchedulerConfig(max_wait_seconds=30.0)
    await circuit.trip("douyin.author_posts", cfg.circuit, "test", now=NOW)
    sched = Scheduler(ListSource([make_candidate(1)]), cfg, clock=AdvancingClock(step=0.0))

    # Consume the single half-open probe so the next call is genuinely blocked.
    await circuit.allow_probe("douyin.author_posts", cfg.circuit)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(EndpointCircuitOpen):
        await sched.acquire("douyin.author_posts", Platform.DOUYIN)
    assert loop.time() - started < 1.0, "an open circuit must not wait out max_wait_seconds"


# --------------------------------------------------------------------------
# Pinning
#
# A pinned request is asking what one session can see. Every guarantee below
# exists because the alternative is not a degraded answer but a different
# question silently answered: an identity that cannot see the caller's private
# post returns an empty page, which reads exactly like the post being gone.
# --------------------------------------------------------------------------


async def test_a_pin_is_honoured_over_a_healthier_identity(redis_client):
    """Ranking is what the pin overrides. It is not a preference."""
    cfg = SchedulerConfig()
    healthy = make_candidate(1)
    tired = make_candidate(2, fails=4)
    sched = Scheduler(ListSource([healthy, tired]), cfg, clock=AdvancingClock(step=0.0))

    lease = await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-2")

    assert lease.identity_id == "identity-2"


async def test_a_pin_never_falls_back_when_the_named_identity_is_busy(redis_client):
    """The failure mode this rules out is the expensive one.

    Falling back would send a request meant for one account's session out on
    another, and the platform answers that with a 200 and an empty body - so
    the caller sees "your private post does not exist" rather than an error.
    """
    cfg = SchedulerConfig(max_wait_seconds=0.0)
    sched = Scheduler(
        ListSource([make_candidate(1), make_candidate(2)]), cfg, clock=AdvancingClock(step=1.0)
    )
    held = await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-1")
    assert held.identity_id == "identity-1"

    with pytest.raises(IdentityPoolExhausted) as exc:
        await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-1")

    assert exc.value.details["identity_id"] == "identity-1"
    # The reason collapses to wait_timeout once the deadline passes, as it does
    # for the unpinned pool. What must survive is which identity was refused:
    # "every identity is busy" would send an operator to look at a pool that is
    # fine, when the only thing busy is the caller's own previous request.
    assert "named identity is busy" in str(exc.value)


async def test_a_pin_on_a_cooling_identity_is_granted(redis_client):
    """Cooling is a judgement about the shared pool, not about this request.

    The identity is being rested because general traffic on it was getting
    risk-controlled. A caller reading their own account's posts with their own
    jar has knowingly stepped outside that, and refusing them would enforce a
    policy about a resource they are not competing for.
    """
    cfg = SchedulerConfig()
    cooling = make_candidate(7, state=IdentityState.COOLING)
    sched = Scheduler(ListSource([cooling]), cfg, clock=AdvancingClock(step=0.0))

    lease = await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-7")

    assert lease.identity_id == "identity-7"


async def test_an_unpinned_call_still_ignores_a_cooling_identity(redis_client):
    """The previous test must not have widened the ordinary pool."""
    cfg = SchedulerConfig(max_wait_seconds=0.0)
    sched = Scheduler(
        ListSource([make_candidate(7, state=IdentityState.COOLING)]),
        cfg,
        clock=AdvancingClock(step=1.0),
    )

    with pytest.raises(IdentityPoolExhausted):
        await sched.acquire("douyin.author_posts", Platform.DOUYIN)


async def test_a_pin_on_an_unusable_identity_fails_fast_as_a_parameter_error(redis_client):
    """Retired or still minting: waiting cannot mend either, and 503 would lie.

    The pool is not exhausted - it is fine, and the caller named something it
    does not contain. A 503 tells a client to retry, which it would then do
    forever.
    """
    cfg = SchedulerConfig(max_wait_seconds=30.0)
    sched = Scheduler(ListSource([make_candidate(1)]), cfg, clock=AdvancingClock(step=0.0))

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(InvalidParam) as exc:
        await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-404")

    assert loop.time() - started < 1.0, "an unusable pin must not wait out max_wait_seconds"
    assert exc.value.details["field"] == "identity"
    assert exc.value.details["reject_reason"] == "pinned_unavailable"


async def test_a_pin_of_the_wrong_platform_is_not_reachable(redis_client):
    """The candidate query filters on platform, so a cross-platform pin cannot
    resolve even if the route's check were somehow skipped."""
    cfg = SchedulerConfig(max_wait_seconds=0.0)
    tiktok = Candidate(
        identity_id="identity-tt",
        platform=Platform.TIKTOK,
        state=IdentityState.ACTIVE,
        last_used_at=None,
        health=HealthInput(20, 20, 60, 0, 0),
    )
    sched = Scheduler(ListSource([tiktok]), cfg, clock=AdvancingClock(step=0.0))

    with pytest.raises(InvalidParam):
        await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-tt")


# --------------------------------------------------------------------------
# Keeping an identity out of rotation (#789)
#
# An account imported to read one thing is put at risk by every other request
# rotation hands it. Reserving it has to keep unpinned traffic off it without
# ever refusing the requests that name it - that is the whole point of keeping
# it.
# --------------------------------------------------------------------------


async def test_unpinned_traffic_never_reaches_a_reserved_identity(redis_client):
    cfg = SchedulerConfig()
    reserved = make_candidate(1, rotation=())
    ordinary = make_candidate(2, fails=4)
    sched = Scheduler(ListSource([reserved, ordinary]), cfg, clock=AdvancingClock(step=0.0))

    for _ in range(3):
        lease = await sched.acquire("douyin.author_posts", Platform.DOUYIN)
        assert lease.identity_id == "identity-2"
        await sched.release(lease, Outcome.OK)


async def test_a_reserved_identity_still_serves_the_request_that_names_it(redis_client):
    cfg = SchedulerConfig()
    sched = Scheduler(
        ListSource([make_candidate(1, rotation=()), make_candidate(2)]),
        cfg,
        clock=AdvancingClock(step=0.0),
    )

    lease = await sched.acquire("douyin.author_posts", Platform.DOUYIN, identity_id="identity-1")

    assert lease.identity_id == "identity-1"


async def test_an_endpoint_list_puts_the_identity_back_for_those_endpoints_only(redis_client):
    cfg = SchedulerConfig(max_wait_seconds=0.0)
    listed = make_candidate(1, rotation=("douyin.author_posts",))
    sched = Scheduler(ListSource([listed]), cfg, clock=AdvancingClock(step=0.0))

    lease = await sched.acquire("douyin.author_posts", Platform.DOUYIN)
    assert lease.identity_id == "identity-1"
    await sched.release(lease, Outcome.OK)

    with pytest.raises(IdentityPoolExhausted):
        await sched.acquire("douyin.content_detail", Platform.DOUYIN)


async def test_a_reserved_active_identity_does_not_keep_degraded_ones_idle(redis_client):
    """The degraded fallback is for "nothing healthy may serve this", and a
    healthy identity that is reserved may not."""
    cfg = SchedulerConfig()
    sched = Scheduler(
        ListSource(
            [make_candidate(1, rotation=()), make_candidate(9, state=IdentityState.DEGRADED)]
        ),
        cfg,
        clock=AdvancingClock(step=0.0),
    )

    lease = await sched.acquire("douyin.content_detail", Platform.DOUYIN)

    assert lease.identity_id == "identity-9"


async def test_exhaustion_says_the_rest_are_reserved(redis_client):
    """Otherwise "no identity" sits beside a pool page full of active ones.

    In ``details``, not only the message: the API replaces the message with a
    localized generic one, so a count left in the text never reaches a caller.
    """
    sched = Scheduler(
        ListSource([make_candidate(1, rotation=()), make_candidate(2, rotation=())]),
        SchedulerConfig(max_wait_seconds=0.0),
        clock=AdvancingClock(step=0.0),
    )

    with pytest.raises(IdentityPoolExhausted) as exc:
        await sched.acquire("douyin.content_detail", Platform.DOUYIN)

    assert "2 more are reserved" in str(exc.value)
    assert exc.value.details["reserved"] == 2


async def test_exhaustion_without_reserved_identities_does_not_mention_them(redis_client):
    sched = Scheduler(
        ListSource([]), SchedulerConfig(max_wait_seconds=0.0), clock=AdvancingClock(step=0.0)
    )

    with pytest.raises(IdentityPoolExhausted) as exc:
        await sched.acquire("douyin.content_detail", Platform.DOUYIN)

    assert "reserved" not in exc.value.details


# --------------------------------------------------------------------------
# signed-in identities (#782, #783)
#
# Douyin pages an author's posts for signed-in accounts only and leaves the
# newest ones out of a guest's first page. Rotation used to ignore whether an
# identity was signed in, so an imported login sat idle while guests were sent
# to be refused.
# --------------------------------------------------------------------------

POSTS = "douyin.author_posts"


async def test_a_page_that_needs_a_login_never_reaches_a_guest(redis_client):
    """The guest is the healthier, never-used candidate, and still loses."""
    sched = Scheduler(
        ListSource([make_candidate(1), make_candidate(2, signed_in=True, last_used=NOW, fails=2)]),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    for _ in range(3):
        lease = await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.REQUIRED)
        assert lease.identity_id == "identity-2"
        await sched.release(lease, Outcome.OK)


async def test_with_no_login_at_all_the_refusal_says_so_at_once(redis_client):
    """Not IDENTITY_POOL_EXHAUSTED, and not after the wait: no mint produces a
    signed-in identity, so waiting out max_wait_seconds only delays it."""
    sched = Scheduler(
        ListSource([make_candidate(1), make_candidate(2)]),
        SchedulerConfig(max_wait_seconds=30.0),
        clock=AdvancingClock(),
    )
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(IdentityLoginRequired) as exc:
        await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.REQUIRED)

    assert loop.time() - started < 1.0
    assert exc.value.details["reject_reason"] == "no_login"
    assert exc.value.details["cooling"] == 0


async def test_the_refusal_counts_signed_in_identities_that_are_cooling(redis_client):
    """ "Import one" is the wrong advice to someone whose login is resting."""
    sched = Scheduler(
        ListSource(
            [
                make_candidate(1),
                make_candidate(2, signed_in=True, state=IdentityState.COOLING),
                make_candidate(3, state=IdentityState.COOLING),
            ]
        ),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    with pytest.raises(IdentityLoginRequired) as exc:
        await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.REQUIRED)

    assert exc.value.details["cooling"] == 1


async def test_a_login_kept_out_of_rotation_is_not_used_for_paging(redis_client):
    sched = Scheduler(
        ListSource([make_candidate(1, signed_in=True, rotation=())]),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    with pytest.raises(IdentityLoginRequired) as exc:
        await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.REQUIRED)

    assert exc.value.details["reserved"] == 1


async def test_a_login_kept_for_author_posts_only_serves_them(redis_client):
    """The setting the docs recommend for a logged-in account."""
    sched = Scheduler(
        ListSource([make_candidate(1, signed_in=True, rotation=(POSTS,)), make_candidate(2)]),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    paged = await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.REQUIRED)
    assert paged.identity_id == "identity-1"
    await sched.release(paged, Outcome.OK)

    other = await sched.acquire("douyin.content_detail", Platform.DOUYIN)
    assert other.identity_id == "identity-2"


async def test_a_first_page_goes_to_a_login_when_there_is_one(redis_client):
    sched = Scheduler(
        ListSource([make_candidate(1), make_candidate(2, signed_in=True, last_used=NOW)]),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    lease = await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.PREFERRED)

    assert lease.identity_id == "identity-2"


async def test_a_degraded_login_beats_an_active_guest(redis_client):
    """A degraded account still sees the newest posts; a guest never does."""
    sched = Scheduler(
        ListSource(
            [make_candidate(1), make_candidate(2, signed_in=True, state=IdentityState.DEGRADED)]
        ),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    lease = await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.PREFERRED)

    assert lease.identity_id == "identity-2"


async def test_a_first_page_falls_back_to_a_guest_when_there_is_no_login(redis_client):
    sched = Scheduler(
        ListSource([make_candidate(1)]), SchedulerConfig(), clock=AdvancingClock(step=0.0)
    )

    lease = await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.PREFERRED)

    assert lease.identity_id == "identity-1"


async def test_a_busy_login_is_waited_for_not_swapped_for_a_guest(redis_client):
    """Falling back whenever the login is mid-request would hand most first
    pages to guests on any instance with traffic - the bug, intermittently."""
    login = make_candidate(2, signed_in=True)
    sched = Scheduler(
        ListSource([make_candidate(1), login]),
        SchedulerConfig(max_wait_seconds=0.2, poll_interval_seconds=0.01),
        # Advancing, so the wait has a deadline: frozen, the second acquire
        # waited out the 120 s in-flight lock and then got the login.
        clock=AdvancingClock(),
    )
    held = await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.REQUIRED)
    assert held.identity_id == "identity-2"

    with pytest.raises(IdentityPoolExhausted):
        await sched.acquire(POSTS, Platform.DOUYIN, login=LoginNeed.PREFERRED)


async def test_a_pin_overrides_the_login_need(redis_client):
    """Naming a guest is the caller deciding which session answers."""
    sched = Scheduler(
        ListSource([make_candidate(1), make_candidate(2, signed_in=True)]),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    lease = await sched.acquire(
        POSTS, Platform.DOUYIN, identity_id="identity-1", login=LoginNeed.REQUIRED
    )

    assert lease.identity_id == "identity-1"


async def test_ordinary_endpoints_still_ignore_the_login(redis_client):
    """Comments page fine for a guest; routing them onto the operator's few
    logged-in accounts would wear those out for nothing."""
    sched = Scheduler(
        ListSource([make_candidate(1), make_candidate(2, signed_in=True, last_used=NOW)]),
        SchedulerConfig(),
        clock=AdvancingClock(step=0.0),
    )

    lease = await sched.acquire("douyin.comments", Platform.DOUYIN)

    assert lease.identity_id == "identity-1"
