"""Signer selection: native by default, browser-rpc when the evidence says so.

docs/design/04-transport-signing.md defines three behaviours, all of them here:

1. **Default.** Every request is signed natively. Microseconds, no dependency.
2. **Fallback.** When one endpoint's risk rate spikes and browser-rpc is healthy,
   that endpoint switches to browser-rpc and an alert is raised. It switches back
   on its own once the risk rate falls.
3. **Shadow comparison.** A low-rate sample signs the same request both ways and
   compares. A mismatch means the native algorithm has stopped matching the
   platform, so the platform is switched to browser-rpc permanently and an alert
   is raised.

Why the shadow comparison earns its place: a rising risk rate could be identities,
proxies, or the endpoint itself. Comparing against a browser that runs the site's
own code isolates "the signature is wrong" from everything else, which is the one
question V4 could never answer.

What "compare" can honestly mean
--------------------------------
The two signatures are not always byte-comparable, and pretending otherwise would
produce a permanent switch on the first sample:

* **X-Bogus** embeds a whole-second timestamp and nothing else random, so two
  signatures of the same query with the same User-Agent in the same second are
  identical. Compared exactly - but the browser signs hundreds of milliseconds
  after we do, so a sample straddles the remote call with a native signature on
  either side and accepts a match against either. The remote clock necessarily
  falls inside that bracket, which is what makes a second boundary harmless. A
  plain retry would not be: a retry is an independent draw, not a bracket.
* **A-Bogus** embeds millisecond timings and three random words, so it can never
  match byte for byte. Compared structurally, by
  :func:`dtk.signing.native.abogus.structure_error`, against invariants the
  algorithm fixes: alphabet, the noise-prefix bit patterns, the frame constants,
  and two internally redundant fields (the repeated ``len(browser_info)`` and the
  frame checksum). Deliberately **not** the length: A-Bogus length follows the
  window geometry, and the browser behind browser-rpc has its own, so comparing
  lengths would fail on every correct sample.
* **msToken** is per-identity. Not compared - and pinned before sampling, see
  :data:`SHADOW_MS_TOKEN`, so both signers sign the same bytes.

A sample where nothing was comparable is reported as "not compared" rather than
as a match; :meth:`SignerRegistry.compare_shadow` still returns ``True`` there,
because absence of evidence must never disable the native path.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from dtk.core.errors import DtkError, SigningFailed
from dtk.core.logging import get_logger
from dtk.core.types import Outcome, Platform
from dtk.signing.base import (
    MS_TOKEN_PARAM,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    Signer,
    SignerHealth,
    SigningFingerprint,
    SigningSession,
    endpoint_of,
    platform_of,
)
from dtk.signing.native.abogus import structure_error
from dtk.signing.protection import requires_browser_signature as signed_by_platform

logger = get_logger(__name__)

EndpointKey = tuple[Platform, str]

#: Called synchronously from inside ``sign``, so an implementation may neither
#: block nor raise. :func:`dtk.ops.notify.signing_alert_hook` is the one the
#: worker passes; it schedules the delivery and returns.
AlertHook = Callable[[str, Mapping[str, Any]], None]

#: Session token pinned for the duration of a shadow sample. ``NativeSigner``
#: invents a random ``msToken`` when the caller leaves one out and ``RpcSigner``
#: does not, so without pinning it the two signers would sign *different* byte
#: sequences and every single sample would report a mismatch. Shadow signatures
#: are compared and thrown away, never sent, so the value only has to be stable.
SHADOW_MS_TOKEN = "S" * 126 + "=="


class Comparison(StrEnum):
    """Outcome of comparing one signature parameter."""

    MATCH = "match"
    MISMATCH = "mismatch"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class RiskSample:
    """Risk rate for one endpoint over the scheduler's window."""

    rate: float
    samples: int


class RiskRateSource(Protocol):
    """Where the registry learns an endpoint's risk rate.

    The scheduler owns the authoritative numbers in Redis; injecting a source
    keeps the registry from maintaining a second, disagreeing copy.
    """

    async def __call__(self, platform: Platform, endpoint: str) -> RiskSample | None: ...


class SlidingRiskWindow:
    """In-memory risk rate over a time window; the default source.

    Fed by :meth:`SignerRegistry.observe`. Good enough for a single process and
    for tests; a deployment with several workers should inject the scheduler's
    Redis-backed source instead.
    """

    def __init__(
        self, window_seconds: float = 300.0, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: MutableMapping[EndpointKey, deque[tuple[float, bool]]] = {}

    def observe(self, platform: Platform, endpoint: str, outcome: Outcome) -> None:
        """Record one request outcome.

        ``BUSINESS_ERROR`` is deliberately counted as a non-risk sample: a
        deleted video says nothing about the signature.
        """
        events = self._events.setdefault((platform, endpoint), deque())
        events.append((self._clock(), outcome is Outcome.RISK_CONTROL))
        self._evict(events)

    def _evict(self, events: deque[tuple[float, bool]]) -> None:
        cutoff = self._clock() - self.window_seconds
        while events and events[0][0] < cutoff:
            events.popleft()

    async def __call__(self, platform: Platform, endpoint: str) -> RiskSample | None:
        events = self._events.get((platform, endpoint))
        if not events:
            return None
        self._evict(events)
        if not events:
            return None
        risky = sum(1 for _, is_risk in events if is_risk)
        return RiskSample(rate=risky / len(events), samples=len(events))


class SignatureComparator(Protocol):
    """Decides whether two signatures of the same request agree."""

    def compare(self, native: str, remote: str) -> Comparison: ...


class ExactComparator:
    """Byte equality. Correct only for deterministic algorithms."""

    def compare(self, native: str, remote: str) -> Comparison:
        return Comparison.MATCH if native == remote else Comparison.MISMATCH


class SkipComparator:
    """For values that are per-identity and carry no algorithm information."""

    def compare(self, native: str, remote: str) -> Comparison:
        return Comparison.SKIPPED


class ABogusComparator:
    """Structural comparison for A-Bogus.

    Byte equality is impossible - millisecond timings and three random words go
    into every value - so both signatures are checked against the invariants the
    algorithm itself fixes (see
    :func:`dtk.signing.native.abogus.structure_error`). A changed algorithm
    breaks at least one of them; two correct implementations break none, whatever
    browser produced them.

    Notably absent: a length comparison. A-Bogus length follows
    ``len(browser_info)``, and the browser behind browser-rpc reports its own
    window geometry rather than the identity's, so requiring equal lengths would
    mismatch on every correct sample and disable the native path permanently.
    """

    def __init__(self, alphabet: str = "s4") -> None:
        self.alphabet = alphabet

    def compare(self, native: str, remote: str) -> Comparison:
        for side, value in (("remote", remote), ("native", native)):
            problem = structure_error(value, alphabet=self.alphabet)
            if problem is not None:
                logger.info("signing.shadow.a_bogus_structure", side=side, problem=problem)
                return Comparison.MISMATCH
        return Comparison.MATCH


#: Comparator per query parameter. Keys are the parameter names as sent.
DEFAULT_COMPARATORS: Mapping[str, SignatureComparator] = {
    SignatureAlgorithm.A_BOGUS.value: ABogusComparator(),
    SignatureAlgorithm.X_BOGUS.value: ExactComparator(),
    SignatureAlgorithm.SIGNATURE.value: SkipComparator(),
    "msToken": SkipComparator(),
}


#: How the registry picks between the two signers. Surfaced as ``signing.mode``
#: in the console.
#:
#: ``rpc``    - always ask the browser. Currently the only mode that produces a
#:              signature either platform accepts.
#: ``native`` - always run the in-process algorithms, and never reach for a
#:              browser. The mode for a deployment with no browser-rpc service.
#: ``auto``   - start native and switch an endpoint to the browser once its risk
#:              rate says the native signature is being rejected.
SigningMode = Literal["rpc", "native", "auto"]


@dataclass(frozen=True, slots=True)
class RegistryPolicy:
    """Thresholds; defaults follow docs/design/03-scheduler.md.

    ``mode`` defaults to ``rpc`` because the native signers are known stale.

    Verified in a live browser on 2026-09-07 (docs/design/04-transport-signing.md):
    Douyin currently sends a 184-character ``a_bogus`` alongside ``verifyFp``,
    ``fp``, ``uifid``, ``timestamp`` and ``x-secsdk-web-signature``, and sends no
    ``msToken`` and no ``X-Bogus`` at all; TikTok signs with ``X-Gnarly`` and
    ``X-Dynosaur`` and has reduced ``X-Bogus`` to a single vestigial character.
    The implementations inherited from V4 produce neither shape.

    The golden vectors in tests/unit/test_signing_golden.py still pass: they
    prove the port is faithful to V4, which is a different claim from V4 still
    being correct. It is not.

    So the ordering in decision D6 - native first, RPC as fallback - is inverted
    here on evidence. The native path stays because it is the only thing that
    works with no browser at all, and because whoever next reverse-engineers the
    current algorithm will want somewhere to put it.
    """

    mode: SigningMode = "rpc"
    #: Whether the mode's non-preferred signer may be used when the preferred
    #: one is unavailable. Turning it off is a diagnostic: with fallback on, a
    #: signer that has stopped working looks healthy because its traffic quietly
    #: moves to the other one, which is exactly how the native signers stayed
    #: "fine" while producing signatures no platform accepts. In ``auto`` the
    #: risk-driven switch *is* the fallback, so turning it off pins auto to
    #: native.
    fallback_enabled: bool = True
    risk_threshold: float = 0.6
    risk_min_samples: int = 20
    risk_cache_seconds: float = 5.0
    shadow_interval_seconds: float = 600.0
    shadow_retries: int = 1
    health_ttl_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ShadowResult:
    """One shadow sample, kept for the console and for tests."""

    platform: Platform
    endpoint: str
    compared: bool
    matched: bool
    detail: str | None = None
    verdicts: Mapping[str, Comparison] = field(default_factory=dict)
    at: float = 0.0


class SignerRegistry:
    """Chooses a signer per request and watches whether the choice still holds."""

    def __init__(
        self,
        native: Mapping[Platform, Signer],
        rpc: Signer | None = None,
        *,
        policy: RegistryPolicy | Callable[[], RegistryPolicy] | None = None,
        risk_rate: RiskRateSource | None = None,
        comparators: Mapping[str, SignatureComparator] | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_alert: AlertHook | None = None,
    ) -> None:
        self._native = dict(native)
        self._rpc = rpc
        # A callable rather than a value, so that changing signing.mode in the
        # console reaches a long-lived worker without restarting it. A frozen
        # snapshot here would make the console page a lie: it would report the
        # new mode while every request kept using the old one.
        fixed = policy if isinstance(policy, RegistryPolicy) else None
        self._policy: Callable[[], RegistryPolicy] = (
            policy if callable(policy) else (lambda snapshot=fixed or RegistryPolicy(): snapshot)  # type: ignore[misc]
        )
        self._window = SlidingRiskWindow(clock=clock)
        self._risk_rate: RiskRateSource = risk_rate or self._window
        self._owns_window = risk_rate is None
        self._comparators = dict(comparators or DEFAULT_COMPARATORS)
        self._clock = clock
        self._on_alert = on_alert

        self._native_disabled: set[Platform] = set()
        self._fallback: set[EndpointKey] = set()
        self._risk_cache: dict[EndpointKey, tuple[float, bool]] = {}
        self._health: tuple[float, SignerHealth] | None = None
        self._last_shadow: dict[EndpointKey, ShadowResult] = {}
        self._shadow_seen: dict[EndpointKey, float] = {}

    @property
    def policy(self) -> RegistryPolicy:
        """The thresholds in force right now, re-read on every use."""
        return self._policy()

    # -- selection ---------------------------------------------------------

    async def sign(
        self,
        spec: RequestSpec,
        identity_fingerprint: SigningFingerprint,
        session: SigningSession | None = None,
        *,
        platform: Platform | None = None,
        endpoint: str | None = None,
    ) -> SignedParams:
        """Sign ``spec``, choosing the signer per the rules in the module docstring."""
        key = self._key(spec, platform, endpoint)
        signer = await self._select(
            key,
            signed_by_platform(key[0], spec.url, session.cookies if session else None),
        )
        try:
            return await signer.sign(spec, identity_fingerprint, session)
        except DtkError as exc:
            if signer is self._rpc or self._rpc is None:
                raise
            logger.warning(
                "signing.native.error", endpoint=key[1], platform=key[0].value, error=str(exc)
            )
            if not await self._rpc_healthy():
                raise
            return await self._rpc.sign(spec, identity_fingerprint, session)

    async def _select(self, key: EndpointKey, protected: bool = True) -> Signer:
        """Pick a signer for this endpoint, per ``policy.mode``.

        Every crossing to the mode's non-preferred signer is announced once per
        endpoint and carries the reason, because a silent crossing is how a
        broken signer goes on looking healthy: its traffic moves to the other
        one and the success rate never dips.

        ``protected`` says whether the platform itself signs this endpoint (see
        :mod:`dtk.signing.protection`). Only ``auto`` reads it, and the default
        is True so a caller that does not know assumes the expensive, safe path.
        """
        platform, _endpoint = key
        native = self._native.get(platform)
        native_usable = native is not None and platform not in self._native_disabled
        may_cross = self.policy.fallback_enabled

        if self.policy.mode == "rpc":
            if self._rpc is not None:
                if native_usable and may_cross and not await self._rpc_healthy():
                    assert native is not None
                    return self._cross(key, native, "browser-rpc is unhealthy")
                # An unhealthy RPC with nowhere to fall back to is still handed
                # the request: its own error says what is wrong with it, where a
                # synthetic "no signer available" would throw that away.
                return self._prefer(key, self._rpc)
            if not native_usable:
                raise SigningFailed(f"no signer available for {platform.value}")
            if not may_cross:
                raise SigningFailed(
                    "signing.mode is rpc but browser-rpc is not configured, and "
                    "signing.fallback_enabled forbids using the native signer"
                )
            assert native is not None
            return self._cross(key, native, "browser-rpc is not configured")

        if not native_usable:
            if self._rpc is None or not may_cross:
                raise SigningFailed(f"no signer available for {platform.value}")
            return self._cross(key, self._rpc, f"no native signer for {platform.value}")
        assert native is not None

        if self.policy.mode == "auto" and self._rpc is not None and may_cross:
            # The platform's own SDK signs only some endpoints. On the rest, the
            # native signer is not a degraded option - it is the same request
            # the site would send, for microseconds instead of a browser.
            # Measured 2026-09-08: 8/8 on two unprotected Douyin endpoints,
            # 3/8 on two protected ones. See dtk.signing.protection.
            if protected and await self._rpc_healthy():
                return self._cross(
                    key, self._rpc, "this endpoint needs a signature only a browser can produce"
                )
            if await self._at_risk(key) and await self._rpc_healthy():
                return self._cross(
                    key,
                    self._rpc,
                    "the endpoint's risk rate suggests the signature is being rejected",
                )

        return self._prefer(key, native)

    def _cross(self, key: EndpointKey, signer: Signer, why: str) -> Signer:
        """Use the mode's non-preferred signer, announcing the first time."""
        if key not in self._fallback:
            self._fallback.add(key)
            platform, endpoint = key
            logger.warning(
                "signing.fallback.engaged",
                platform=platform.value,
                endpoint=endpoint,
                signer=signer.name,
                reason=why,
            )
            self._alert(
                "signing.fallback.engaged",
                {
                    "platform": platform.value,
                    "endpoint": endpoint,
                    "signer": signer.name,
                    "reason": why,
                },
            )
        return signer

    def _prefer(self, key: EndpointKey, signer: Signer) -> Signer:
        """Use the mode's preferred signer, announcing a return to it."""
        if key in self._fallback:
            self._fallback.discard(key)
            platform, endpoint = key
            logger.info(
                "signing.fallback.released",
                platform=platform.value,
                endpoint=endpoint,
                signer=signer.name,
            )
        return signer

    async def _at_risk(self, key: EndpointKey) -> bool:
        cached = self._risk_cache.get(key)
        now = self._clock()
        if cached is not None and now - cached[0] < self.policy.risk_cache_seconds:
            return cached[1]
        sample = await self._risk_rate(*key)
        at_risk = (
            sample is not None
            and sample.samples >= self.policy.risk_min_samples
            and sample.rate > self.policy.risk_threshold
        )
        self._risk_cache[key] = (now, at_risk)
        return at_risk

    async def _rpc_healthy(self) -> bool:
        if self._rpc is None:
            return False
        now = self._clock()
        if self._health is not None and now - self._health[0] < self.policy.health_ttl_seconds:
            return self._health[1].healthy
        health = await self._rpc.health()
        self._health = (now, health)
        if not health.healthy:
            logger.warning("signing.rpc.unhealthy", detail=health.detail)
        return health.healthy

    # -- feedback ----------------------------------------------------------

    def observe(self, platform: Platform, endpoint: str, outcome: Outcome) -> None:
        """Feed one request outcome to the built-in risk window.

        A no-op when an external risk source was injected: the scheduler is then
        the one keeping score.
        """
        if not self._owns_window:
            return
        self._window.observe(platform, endpoint, outcome)
        self._risk_cache.pop((platform, endpoint), None)

    # -- shadow comparison -------------------------------------------------

    def should_shadow(
        self,
        spec: RequestSpec,
        *,
        platform: Platform | None = None,
        endpoint: str | None = None,
    ) -> bool:
        """Whether this request is due for a shadow sample.

        One sample per endpoint per ``shadow_interval_seconds``; the caller runs
        it out of band, never on the request's own latency path. Calling this
        claims the slot, so a caller that asks must sample.
        """
        if self._rpc is None:
            return False
        key = self._key(spec, platform, endpoint)
        if key[0] in self._native_disabled or key[0] not in self._native:
            return False
        now = self._clock()
        last = self._shadow_seen.get(key)
        if last is not None and now - last < self.policy.shadow_interval_seconds:
            return False
        self._shadow_seen[key] = now
        return True

    async def compare_shadow(
        self,
        spec: RequestSpec,
        identity_fingerprint: SigningFingerprint,
        session: SigningSession | None = None,
        *,
        platform: Platform | None = None,
        endpoint: str | None = None,
    ) -> bool:
        """Sign one request both ways and report whether they agree.

        Returns True when they agree and when no comparison could be made;
        returns False only on real disagreement, which permanently switches the
        platform to browser-rpc. Inspect :meth:`shadow_result` to tell a match
        from a skipped sample.
        """
        key = self._key(spec, platform, endpoint)
        native = self._native.get(key[0])
        if native is None or self._rpc is None:
            return self._record_shadow(key, False, True, "no second signer to compare against")

        sample = self._shadow_spec(spec)
        detail: str | None = None
        verdicts: Mapping[str, Comparison] = {}
        for attempt in range(self.policy.shadow_retries + 1):
            try:
                # The remote call takes hundreds of milliseconds, and X-Bogus
                # carries a whole-second timestamp. Bracketing it means the
                # browser's own second is one of the two we signed at, so a
                # second boundary inside the call cannot look like a mismatch.
                before = await native.sign(sample, identity_fingerprint, session)
                # The same session as the real request would use: the browser
                # signs in a page loaded with this jar, and comparing against a
                # signature taken in some other session would compare two
                # different visitors and call the difference an algorithm drift.
                remote_signed = await self._rpc.sign(sample, identity_fingerprint, session)
                after = await native.sign(sample, identity_fingerprint, session)
            except DtkError as exc:
                return self._record_shadow(key, False, True, f"sample failed: {exc}")

            verdicts = self._compare((before, after), remote_signed)
            if not verdicts:
                return self._record_shadow(key, False, True, "no comparable parameter")
            if Comparison.MISMATCH not in verdicts.values():
                if Comparison.MATCH not in verdicts.values():
                    return self._record_shadow(
                        key, False, True, "every parameter skipped", verdicts
                    )
                return self._record_shadow(key, True, True, None, verdicts)
            detail = ", ".join(
                name for name, verdict in verdicts.items() if verdict is Comparison.MISMATCH
            )
            logger.info(
                "signing.shadow.retry",
                platform=key[0].value,
                endpoint=key[1],
                attempt=attempt + 1,
                parameters=detail,
            )

        self._native_disabled.add(key[0])
        logger.error(
            "signing.shadow.mismatch", platform=key[0].value, endpoint=key[1], parameters=detail
        )
        self._alert(
            "signing.shadow.mismatch",
            {"platform": key[0].value, "endpoint": key[1], "parameters": detail},
        )
        return self._record_shadow(key, True, False, detail, verdicts)

    def _compare(
        self, natives: tuple[SignedParams, ...], remote: SignedParams
    ) -> dict[str, Comparison]:
        """Verdict per parameter, taking the best of the bracketing signatures.

        A parameter matches if it matches *either* native signature: the two
        bracket the remote call, so for a clock-dependent algorithm one of them
        was computed in the same second the browser used.
        """
        verdicts: dict[str, Comparison] = {}
        for name, native_value in natives[0].params.items():
            remote_value = remote.params.get(name)
            if remote_value is None:
                continue
            comparator = self._comparators.get(name)
            if comparator is None:
                continue
            verdict = comparator.compare(native_value, remote_value)
            for other in natives[1:]:
                if verdict is not Comparison.MISMATCH:
                    break
                alternative = other.params.get(name)
                if alternative is not None:
                    verdict = comparator.compare(alternative, remote_value)
            verdicts[name] = verdict
        return verdicts

    @staticmethod
    def _shadow_spec(spec: RequestSpec) -> RequestSpec:
        """Pin every parameter a signer would otherwise invent for itself.

        ``NativeSigner`` fills a random ``msToken`` when the caller leaves one
        out; ``RpcSigner`` passes the parameters through untouched. Signing two
        different byte sequences and then comparing the signatures reports a
        mismatch every time, which would disable the native path on the first
        sample. Fixing the value up front makes the two sides comparable; the
        signature is discarded, so the value is never sent anywhere.
        """
        params = dict(spec.params or {})
        if params.get(MS_TOKEN_PARAM):
            return spec
        params[MS_TOKEN_PARAM] = SHADOW_MS_TOKEN
        return spec.with_params(params)

    def _record_shadow(
        self,
        key: EndpointKey,
        compared: bool,
        matched: bool,
        detail: str | None = None,
        verdicts: Mapping[str, Comparison] | None = None,
    ) -> bool:
        result = ShadowResult(
            platform=key[0],
            endpoint=key[1],
            compared=compared,
            matched=matched,
            detail=detail,
            verdicts=dict(verdicts or {}),
            at=self._clock(),
        )
        self._last_shadow[key] = result
        if not compared:
            logger.info(
                "signing.shadow.skipped", platform=key[0].value, endpoint=key[1], reason=detail
            )
        elif matched:
            logger.info("signing.shadow.match", platform=key[0].value, endpoint=key[1])
        return matched

    def shadow_result(self, platform: Platform, endpoint: str) -> ShadowResult | None:
        """The last shadow sample for one endpoint, if there has been one."""
        return self._last_shadow.get((platform, endpoint))

    # -- introspection -----------------------------------------------------

    def disable_native(self, platform: Platform, *, reason: str) -> None:
        """Switch a platform to browser-rpc permanently, as an operator action."""
        self._native_disabled.add(platform)
        logger.warning("signing.native.disabled", platform=platform.value, reason=reason)
        self._alert("signing.native.disabled", {"platform": platform.value, "reason": reason})

    def enable_native(self, platform: Platform) -> None:
        """Undo :meth:`disable_native`, after the algorithm has been re-ported."""
        self._native_disabled.discard(platform)
        logger.info("signing.native.enabled", platform=platform.value)

    def native_enabled(self, platform: Platform) -> bool:
        return platform in self._native and platform not in self._native_disabled

    def fallback_endpoints(self) -> Iterable[EndpointKey]:
        """Endpoints currently signed by browser-rpc because of their risk rate."""
        return tuple(sorted(self._fallback, key=lambda item: (item[0].value, item[1])))

    async def health(self) -> dict[str, SignerHealth]:
        """Health of every signer, for the console's system information page."""
        report = {
            f"native:{platform.value}": await signer.health()
            for platform, signer in self._native.items()
        }
        if self._rpc is not None:
            report["rpc"] = await self._rpc.health()
        return report

    # -- helpers -----------------------------------------------------------

    def _key(
        self, spec: RequestSpec, platform: Platform | None, endpoint: str | None
    ) -> EndpointKey:
        resolved = platform or platform_of(spec.url)
        if resolved is None:
            raise SigningFailed(f"cannot tell which platform {spec.url} belongs to")
        return resolved, endpoint or endpoint_of(spec.url)

    def _alert(self, event: str, context: Mapping[str, Any]) -> None:
        """Announce one event to whoever wired a hook up.

        The hook belongs to the caller and runs inside ``sign``, so a hook that
        raised would turn "the fallback engaged" into a failed request - the one
        thing an alert may never do.
        """
        if self._on_alert is None:
            return
        try:
            self._on_alert(event, context)
        except Exception as exc:
            logger.warning(
                "signing.alert_failed", event=event, error=f"{type(exc).__name__}: {exc}"[:200]
            )


__all__ = [
    "DEFAULT_COMPARATORS",
    "SHADOW_MS_TOKEN",
    "ABogusComparator",
    "AlertHook",
    "Comparison",
    "EndpointKey",
    "ExactComparator",
    "RegistryPolicy",
    "RiskRateSource",
    "RiskSample",
    "ShadowResult",
    "SignatureComparator",
    "SignerRegistry",
    "SkipComparator",
    "SlidingRiskWindow",
]
