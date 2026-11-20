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
* **A-Bogus** embeds two millisecond clocks and some thirty-eight noise draws, so
  it can never match byte for byte. Compared structurally, by
  :func:`dtk.signing.native.abogus.structure_error`, against the part of the
  format noise cannot reach: the ``s4`` alphabet, the masked header magic, the
  SDK version block, the two declared lengths that bound the frame, and the XOR
  checksum the body carries over its own version block and fifty scalars. Those
  rules come from the same reversing of ``bdms.js`` v1.0.1.19-fix.01 that the
  native signer is built on, so a browser value that decodes and then breaks one
  is drift and counts as a mismatch. A value that does not decode at all is
  browser-rpc failing rather than Douyin moving, and is skipped. Deliberately
  **not** compared: the length, which follows the window geometry, and the
  browser behind browser-rpc has its own.
* **msToken** is per-identity. Not compared - and pinned before sampling, see
  :data:`SHADOW_MS_TOKEN`, so both signers sign the same bytes.
* **X-Dynosaur** seals a per-call key and clock, so no two agree byte for byte,
  but it names the webmssdk build that signed it, and both sides must name the
  one tiktok.com serves. A different build is :attr:`Comparison.DRIFT`:
  reported, never switched on - see :class:`SdkBuildComparator`.
* TikTok's **X-Bogus** is the constant ``1`` on both sides and is not compared;
  until X-Dynosaur was, a match on it was TikTok's only verdict, and the
  self-check reported "native and browser signatures agree" on the strength of
  a constant.

A sample where nothing was comparable is reported as "not compared" rather than
as a match; :meth:`SignerRegistry.compare_shadow` still returns ``True`` there,
because absence of evidence must never disable the native path.
"""

from __future__ import annotations

import hashlib
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
    RPC_PLATFORMS,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    Signer,
    SignerHealth,
    SigningFingerprint,
    SigningSession,
    StaticFingerprint,
    endpoint_of,
    platform_of,
)
from dtk.signing.native.abogus import is_decode_problem, structure_error
from dtk.signing.native.tiktok_sign import (
    DYNOSAUR_PARAM,
    UNSETTLED_STATE_HASH,
    SdkBuild,
    read_build,
)
from dtk.signing.protection import requires_browser_signature as signed_by_platform
from dtk.signing.rpc import is_user_agent_mismatch

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
    #: The same algorithm, stamped as a different build of the platform's SDK.
    #: Reported and never acted on: a MISMATCH moves the platform onto
    #: browser-rpc, and for TikTok that is the wrong answer to a build bump -
    #: tiktok.com accepted the previous build's signatures (and invented ones)
    #: when it was measured, while browser-rpc's own item_list signatures are
    #: refused for their ub_code. Switching would trade a fingerprint tell for
    #: an outage.
    DRIFT = "drift"


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


class XBogusComparator(ExactComparator):
    """Exact, except where X-Bogus is TikTok's vestigial one-character marker.

    On TikTok's HTTP requests X-Bogus is the literal ``1`` from both signers, so
    an exact comparison of it always agreed and said nothing about either. It
    was TikTok's only verdict, and the self-check passed TikTok as "native and
    browser signatures agree" through the 2.0.0.561 -> 569 bundle bump that
    neither side's X-Bogus could see. A real X-Bogus against the marker is
    still a mismatch: the platform computing it again is a change worth seeing.
    """

    def compare(self, native: str, remote: str) -> Comparison:
        if native == remote and len(native) <= 1:
            return Comparison.SKIPPED
        return super().compare(native, remote)


class SdkBuildComparator:
    """X-Dynosaur: do both signers stamp the same webmssdk build?

    The seal carries a per-call key, a clock and a nonce, so two X-Dynosaurs of
    one request never agree byte for byte - but the payload names the build that
    produced it, and the browser's names the build tiktok.com is serving right
    now. That is the drift that happened unseen: the page moved to 2.0.0.569
    while the native signer kept stamping 2.0.0.561, and nothing at runtime
    noticed (see :data:`dtk.signing.native.tiktok_sign.SDK_VERSION`).

    A build that differs is :attr:`Comparison.DRIFT`, not a mismatch; the enum
    says why. A browser page in its first seconds seals the empty-string state
    hash where the build's belongs, so that one value is not held against us.
    """

    def compare(self, native: str, remote: str) -> Comparison:
        try:
            ours = read_build(native)
        except ValueError as exc:
            # Ours, generated and read back by the same module: a value we
            # cannot read is a fault in this process, as with A-Bogus.
            logger.error(
                "signing.shadow.x_dynosaur_native_malformed",
                problem=str(exc),
                detail="the native signer produced an X-Dynosaur its own reader rejects",
            )
            return Comparison.MISMATCH
        try:
            theirs = read_build(remote)
        except ValueError as exc:
            # A browser-rpc that returned garbage, or an envelope TikTok has
            # changed; this cannot tell which, so it judges nothing.
            logger.warning(
                "signing.shadow.x_dynosaur_remote_unusable",
                problem=str(exc),
                detail="browser-rpc returned a value this envelope does not open",
            )
            return Comparison.SKIPPED
        return Comparison.MATCH if same_build(ours, theirs) else Comparison.DRIFT

    @staticmethod
    def explain(native: str, remote: str) -> str:
        """The two builds, for the record of a drift. Version strings only."""
        return (
            f"the browser signs as {describe_build(read_build(remote))}, "
            f"the native signer as {describe_build(read_build(native))}"
        )


def _any_agent(fingerprint: SigningFingerprint) -> StaticFingerprint:
    """``fingerprint`` naming no User-Agent: browser-rpc then signs under its page's."""
    return StaticFingerprint(
        user_agent="",
        browser_platform=fingerprint.browser_platform,
        screen_width=fingerprint.screen_width,
        screen_height=fingerprint.screen_height,
    )


def same_build(ours: SdkBuild, theirs: SdkBuild) -> bool:
    """Whether two builds agree, not counting a page that had not settled."""
    if (ours.sdk_version, ours.scm_version) != (theirs.sdk_version, theirs.scm_version):
        return False
    return theirs.vm_state_hash in (ours.vm_state_hash, UNSETTLED_STATE_HASH)


def describe_build(build: SdkBuild) -> str:
    return (
        f"webmssdk {build.scm_version} (sdkVersion {build.sdk_version}, "
        f"state 0x{build.vm_state_hash:08X})"
    )


class ABogusComparator:
    """Structural comparison for A-Bogus.

    Byte equality is impossible - two signatures of one query differ in two
    millisecond clocks and in the thirty-odd ``Math.random`` draws the format is
    built to discard - so both values go through
    :func:`dtk.signing.native.abogus.structure_error`, which checks only what
    noise cannot reach: the ``s4`` alphabet, the header magic under its mask, the
    SDK version block, the two declared lengths bounding the frame, and the XOR
    checksum the body carries over its own version block and fifty scalars.

    The checksum is what makes a verdict possible rather than merely plausible.
    It is computed over fields that are themselves inside the payload, so
    anything that passes it agreed with us about the version block, the
    fifty-slot permutation, the 3->4 noise expansion and the modified RC4 - all
    four at once, since getting any one of them wrong scrambles the bytes the
    others are read from. Nothing short of the real algorithm passes by accident.

    There is exactly one way a *correct* value fails, and it is worth knowing
    before trusting a verdict. The SDK's 3->4 expansion has a tail branch that
    drops a trailing zero byte, so when the body length leaves a remainder of two
    - the clock byte in the tail string printing as two digits, 90 of 256 values
    - and the checksum happens to be zero, the checksum is not recoverable and
    ``structure_error`` reports ``declared lengths overrun the frame``. Measured
    over 60,000 signatures: 1 in 690, against 90/65536 = 1 in 728 predicted. Two
    independent draws never both fell in it. That is what the bracketing pair and
    ``shadow_retries`` are for here as much as for X-Bogus's second boundary:
    both sides of a sample are re-taken before a mismatch is believed, which puts
    a spurious disable at roughly one in a million samples - one per endpoint per
    some thousands of years at the default ten-minute interval.

    Notably absent: a length comparison. A-Bogus length follows
    ``len(browser_info)``, and the browser behind browser-rpc reports its own
    window geometry rather than the identity's, so requiring equal lengths would
    mismatch on every correct sample and disable the native path permanently.
    """

    def __init__(self, alphabet: str = "s4") -> None:
        self.alphabet = alphabet

    def compare(self, native: str, remote: str) -> Comparison:
        native_problem = structure_error(native, alphabet=self.alphabet)
        if native_problem is not None:
            # Checked first, which reverses the old order: back when a remote
            # failure was the excuse to skip, it had to be looked at before
            # anything else or the skip never happened. Now the native side is
            # the finding that needs no second opinion. The generator and the
            # checker are both ours and share their constants, so a value of
            # ours that our own checker rejects is a fault in this process - a
            # geometry string longer than its declared length, an aid that does
            # not fit four bytes - and says nothing about Douyin. Disabling the
            # native signer is the right response to a signer that cannot pass
            # its own structural check.
            logger.error(
                "signing.shadow.a_bogus_native_malformed",
                problem=native_problem,
                detail="the native signer produced a value its own structural check rejects",
            )
            return Comparison.MISMATCH

        remote_problem = structure_error(remote, alphabet=self.alphabet)
        if remote_problem is None:
            return Comparison.MATCH

        if is_decode_problem(remote_problem):
            # Not an a_bogus of any version: a foreign alphabet, a length no
            # base64 emits, too few bytes to hold a header. A Douyin bundle that
            # moved on would still hand back a signature; a browser-rpc that
            # returned an empty string, an error token or a truncated read hands
            # back this. So the reference is broken, not the signer - and a
            # mismatch here would move every request on the platform onto the
            # component that has just demonstrated it cannot produce a
            # signature, which is the one response that makes things worse.
            logger.warning(
                "signing.shadow.a_bogus_remote_unusable",
                problem=remote_problem,
                detail="browser-rpc returned a value that is not an a_bogus; check the browser",
            )
            return Comparison.SKIPPED

        # Decodes as an a_bogus and then breaks a rule - the finding this
        # comparator exists for, and a mismatch now where it used to be a skip.
        #
        # The skip was right while the rules were V4's: they were written apart
        # from the generator and against an older bundle, and on 2026-09-08 the
        # live browser's own value broke them at noise byte 2 while Douyin
        # answered our native signature with full payloads on all four
        # endpoints. A rule the reference itself breaks can only report that it
        # is unfit to judge. These rules are not those: they were derived on
        # 2026-09-09 from the bundle this browser runs, three of its own
        # captures pass every check, and they are the same constants the
        # generator signs with. So a decodable value that fails one means the
        # bundle moved past the checker and the signer together, which is
        # exactly the drift the shadow comparison is here to catch and exactly
        # what SKIPPED would hide.
        #
        # The cost is affordable in the case that actually fires. A mismatch
        # disables the native signer for the platform and routes it to
        # browser-rpc - that is, to the browser running the bundle we have just
        # been told we no longer match, which is where the traffic belongs until
        # somebody re-derives. And `shadow_retries` re-signs and re-fetches
        # before the disable lands, so the one-in-690 tail case in the class
        # docstring does not cost a platform its native signer, while a real
        # drift, being a pure function of the format, reproduces every time.
        logger.error(
            "signing.shadow.a_bogus_drift",
            problem=remote_problem,
            detail=(
                "the browser's own a_bogus breaks a rule derived from bdms.js "
                "v1.0.1.19-fix.01; the bundle has changed, re-derive it"
            ),
        )
        return Comparison.MISMATCH


#: Comparator per query parameter. Keys are the parameter names as sent.
#:
#: The two skips stay skips, and for a reason that did not change when A-Bogus
#: got a real verdict. ``_signature`` is webmssdk's seal over the identity's own
#: ``msToken`` and URL, and ``msToken`` is a session token minted per identity;
#: neither carries any information about an *algorithm*. Pinning ``msToken``
#: before a sample (:data:`SHADOW_MS_TOKEN`) makes the two signers hash the same
#: bytes, but that only means an exact comparison of the token would pass
#: tautologically - it would report agreement without ever having looked at
#: anything either signer computes.
DEFAULT_COMPARATORS: Mapping[str, SignatureComparator] = {
    SignatureAlgorithm.A_BOGUS.value: ABogusComparator(),
    SignatureAlgorithm.X_BOGUS.value: XBogusComparator(),
    SignatureAlgorithm.SIGNATURE.value: SkipComparator(),
    DYNOSAUR_PARAM: SdkBuildComparator(),
    "msToken": SkipComparator(),
}


#: How the registry picks between the two signers. Surfaced as ``signing.mode``
#: in the console.
#:
#: ``rpc``    - always ask the browser. The mode to fall back to when a platform
#:              changes its SDK and the port has not caught up yet.
#: ``native`` - always run the in-process algorithms, and never reach for a
#:              browser. The mode for a deployment with no browser-rpc service.
#: ``auto``   - start native and switch an endpoint to the browser once its risk
#:              rate says the native signature is being rejected.
SigningMode = Literal["rpc", "native", "auto"]


@dataclass(frozen=True, slots=True)
class RegistryPolicy:
    """Thresholds; defaults follow docs/design/03-scheduler.md.

    ``mode`` defaults to ``native``: browser-rpc mints identities, and the
    in-process algorithms sign every request those identities then send. That is
    the ordering decision D6 always wanted, and it is now the one the evidence
    supports.

    Verified live on 2026-09-08, native signer plus wreq, no browser in the
    request path: Douyin 4/4 endpoints, TikTok 4/4 - ``author_posts`` 495KB/20
    items, ``content_detail`` 28KB, ``comments`` 8.5KB, ``author_profile`` 2.5KB.

    The last TikTok endpoint to fall was ``/api/post/item_list/``, and it is worth
    saying why, because it is the failure mode this default has to survive. It is
    the only endpoint that verifies the X-Dynosaur environment report; the rest
    answer a mis-signed request normally. Two constants in the port were wrong,
    so one path returned 200 with an empty body while everything else looked
    healthy. Proven by holding a captured request byte-identical and changing only
    the seal - the browser's returned 82KB, ours 0 bytes - and fixed by taking the
    values from a real capture instead of from a Node harness.

    Which is the argument for ``fallback_enabled`` below, not against this default:
    a signer can be broken on one endpoint and fine on every other, and the thing
    that hides it is traffic quietly moving to the other signer.
    """

    mode: SigningMode = "native"
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


class ShadowSkip(StrEnum):
    """Why a shadow sample compared nothing. Each one points somewhere else.

    A typed value rather than prose, because the self-check has to act on it.
    It used to read every skip as one state - "sample failed: ..." or "nothing
    comparable" alike - and attach a single paragraph saying the browser ran a
    different version of the algorithm. In #830 the browser had simply failed
    to sign (its page had left tiktok.com), and the operator was told to
    re-derive invariants from a bundle that had not moved. A native failure and
    a reference failure are opposite findings: one is the path serving
    traffic, the other the yardstick.
    """

    #: No native signer or no browser-rpc for this platform.
    NO_SECOND_SIGNER = "no_second_signer"
    #: The native signer raised on the sample: the serving path is at fault.
    NATIVE_FAILED = "native_failed"
    #: browser-rpc raised: nothing was compared, and nothing is known about native.
    REFERENCE_FAILED = "reference_failed"
    #: browser-rpc answered with something that is not a signature at all.
    REFERENCE_UNUSABLE = "reference_unusable"
    #: Both signed, and no parameter they share has a comparator that judges it.
    NOTHING_COMPARABLE = "nothing_comparable"


#: The words a failed sample's detail starts with, per side.
_SAMPLE_FAILURE_PREFIX: Mapping[ShadowSkip, str] = {
    ShadowSkip.NATIVE_FAILED: "native signer failed",
    ShadowSkip.REFERENCE_FAILED: "browser reference failed",
}


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
    #: Set exactly when ``compared`` is False.
    skip: ShadowSkip | None = None
    #: The failing signer's own message, for the two failure skips.
    error: str | None = None


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
        #: Identities already reported as ones browser-rpc cannot sign under,
        #: with the digest of the User-Agent it refused and what became of the
        #: request: the log names each once per outcome, because the operator's
        #: questions are which ones they are and why one failed.
        self._foreign_agents: set[tuple[Platform, str | None, str, str | None]] = set()
        #: Endpoints whose requests have already crossed to the native signer
        #: because browser-rpc refused an identity's User-Agent, so that
        #: crossing is announced once - see `_native_for_a_foreign_agent`.
        self._foreign_agent_crossings: set[EndpointKey] = set()

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
        protected = signed_by_platform(key[0], spec.url, session.cookies if session else None)
        signer = await self._select(key, protected)
        rpc = self._rpc_for(key[0])
        try:
            return await signer.sign(spec, identity_fingerprint, session)
        except DtkError as exc:
            if signer is rpc:
                native = self._native_for_a_foreign_agent(
                    key, exc, identity_fingerprint, session, protected=protected
                )
                if native is None:
                    raise
                return await native.sign(spec, identity_fingerprint, session)
            if rpc is None:
                raise
            logger.warning(
                "signing.native.error", endpoint=key[1], platform=key[0].value, error=str(exc)
            )
            if not await self._rpc_healthy():
                raise
            try:
                return await rpc.sign(spec, identity_fingerprint, session)
            except DtkError as rpc_exc:
                # The browser cannot sign for this identity at all, so the
                # native signer's failure is the one that says what is wrong.
                if is_user_agent_mismatch(rpc_exc):
                    raise exc from rpc_exc
                raise

    def _native_for_a_foreign_agent(
        self,
        key: EndpointKey,
        error: DtkError,
        fingerprint: SigningFingerprint,
        session: SigningSession | None,
        *,
        protected: bool,
    ) -> Signer | None:
        """The native signer, when browser-rpc refused this identity's User-Agent.

        Issue #832. browser-rpc's page signs under its own User-Agent. TikTok
        refuses a request whose header names another, and Douyin a browser
        signature for a query naming another operating system, so such an
        identity - minted under an older CloakBrowser, or imported from the
        user's own browser - can never be signed there; browser-rpc now says
        so instead of handing back a signature the platform refuses as risk
        control. The native signer seals the identity's own User-Agent, which
        was measured to work (2026-11-20: TikTok, Chrome 145 sealed and sent, a
        full profile; Douyin, a macOS identity, 3/3). So that is who signs it -
        when the policy lets the other signer be used and native is not
        disabled.

        And when native can cover the request at all. A ``protected`` one was
        sent to the browser because only a browser can make its signature - a
        TikTok jar with no msToken, a Douyin sign-protected endpoint and a jar
        with no visitor id - and a native signature for it would come back as a
        platform refusal (TikTok with no msToken: 0/2 natively on 2026-11-20,
        where the same jar with its token answered 2/2): classified as risk
        control, which cools and degrades an identity for a cause on this side.
        The refusal is raised instead, as what it is - a signing failure that
        cools nothing and names its cause.

        Not a browser outage: rpc is not marked unhealthy and nothing reaches
        the risk window. Each identity is logged once, by id, so the operator
        can find the ones to replace; and in ``rpc`` mode, where native is the
        signer the operator chose against, the crossing is announced once per
        endpoint like every other.
        """
        if not is_user_agent_mismatch(error):
            return None
        platform = key[0]
        native = self._native.get(platform)
        blocked = self._why_not_native(platform, protected=protected)
        identity_id = session.identity_id if session else None
        agent = hashlib.sha256(fingerprint.user_agent.encode()).hexdigest()[:10]
        # Once per identity per outcome, not once per identity: the reason
        # native may not take it - a protected endpoint, a jar that lost its
        # token, fallback switched off, native disabled - changes between
        # requests and at runtime, and the line is where an operator is sent to
        # read it. Keyed without the outcome, an identity first served natively
        # then failed in silence (review of the #832 fix).
        seen = (platform, identity_id, agent, blocked)
        if seen not in self._foreign_agents:
            self._foreign_agents.add(seen)
            logger.warning(
                "signing.rpc.user_agent_mismatch",
                platform=platform.value,
                endpoint=key[1],
                identity_id=identity_id,
                user_agent_sha=agent,
                signer=native.name if native is not None and blocked is None else None,
                **({"not_native": blocked} if blocked else {}),
                detail=str(error),
            )
        if blocked is not None or native is None:
            return None
        if self.policy.mode == "rpc" and key not in self._foreign_agent_crossings:
            # Not through `_cross`: the next request on this endpoint may be an
            # identity the browser can sign, `_prefer` would then release the
            # endpoint, and a pool mixing the two would alert on every other
            # request.
            self._foreign_agent_crossings.add(key)
            why = "browser-rpc cannot sign under this identity's User-Agent"
            logger.warning(
                "signing.fallback.engaged",
                platform=platform.value,
                endpoint=key[1],
                signer=native.name,
                reason=why,
            )
            self._alert(
                "signing.fallback.engaged",
                {
                    "platform": platform.value,
                    "endpoint": key[1],
                    "signer": native.name,
                    "reason": why,
                },
            )
        return native

    def _why_not_native(self, platform: Platform, *, protected: bool) -> str | None:
        """Why the native signer may not take a request browser-rpc refused; None if it may.

        Named rather than just answered, because the request then fails and
        the log line has to tell the operator which of these to change.
        """
        if protected:
            return "protected"
        if not self.policy.fallback_enabled:
            return "fallback_disabled"
        if platform not in self._native:
            return "no_native"
        if platform in self._native_disabled:
            return "native_disabled"
        return None

    def _rpc_for(self, platform: Platform) -> Signer | None:
        """browser-rpc, if it can sign for ``platform`` at all.

        It cannot for Bilibili: its page holds Douyin's and TikTok's SDKs. Every
        decision below that would cross to it treats a platform it cannot sign
        as having no browser - otherwise `signing.mode=rpc` would hand Bilibili
        requests to a page that answers them with a signature for nothing.
        """
        return self._rpc if platform in RPC_PLATFORMS else None

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
        rpc = self._rpc_for(platform)

        if self.policy.mode == "rpc" and platform in RPC_PLATFORMS:
            if rpc is not None:
                if native_usable and may_cross and not await self._rpc_healthy():
                    assert native is not None
                    return self._cross(key, native, "browser-rpc is unhealthy")
                # An unhealthy RPC with nowhere to fall back to is still handed
                # the request: its own error says what is wrong with it, where a
                # synthetic "no signer available" would throw that away.
                return self._prefer(key, rpc)
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
            if rpc is None or not may_cross:
                raise SigningFailed(f"no signer available for {platform.value}")
            return self._cross(key, rpc, f"no native signer for {platform.value}")
        assert native is not None

        if self.policy.mode == "auto" and rpc is not None and may_cross:
            # The platform's own SDK signs only some endpoints. On the rest, the
            # native signer is not a degraded option - it is the same request
            # the site would send, for microseconds instead of a browser.
            # Measured 2026-09-08: 8/8 on two unprotected Douyin endpoints,
            # 3/8 on two protected ones. See dtk.signing.protection.
            if protected and await self._rpc_healthy():
                return self._cross(
                    key, rpc, "this endpoint needs a signature only a browser can produce"
                )
            if await self._at_risk(key) and await self._rpc_healthy():
                return self._cross(
                    key,
                    rpc,
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
        key = self._key(spec, platform, endpoint)
        if self._rpc_for(key[0]) is None:
            return False
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
        rpc = self._rpc_for(key[0])
        if native is None or rpc is None:
            return self._record_shadow(
                key,
                False,
                True,
                "no second signer to compare against",
                skip=ShadowSkip.NO_SECOND_SIGNER,
            )

        sample = self._shadow_spec(spec)
        detail: str | None = None
        verdicts: Mapping[str, Comparison] = {}
        for attempt in range(self.policy.shadow_retries + 1):
            # Which side is signing, so a failure is pinned on the right one.
            side = ShadowSkip.NATIVE_FAILED
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
                #
                # But under the browser's own User-Agent, asked for by asking
                # for none. Its page never signed under another - it runs one,
                # whatever the request names - and the comparators judge only
                # what the UA does not reach. It now refuses to be asked for
                # one the platform would not accept (#832) - on TikTok any but
                # its own - and the self-check probes with a fixed Chrome 130
                # that no page runs.
                side = ShadowSkip.REFERENCE_FAILED
                remote_signed = await rpc.sign(sample, _any_agent(identity_fingerprint), session)
                side = ShadowSkip.NATIVE_FAILED
                after = await native.sign(sample, identity_fingerprint, session)
            except DtkError as exc:
                return self._record_shadow(
                    key,
                    False,
                    True,
                    f"{_SAMPLE_FAILURE_PREFIX[side]}: {exc}",
                    skip=side,
                    error=str(exc),
                )

            verdicts = self._compare((before, after), remote_signed)
            if not verdicts:
                return self._record_shadow(
                    key, False, True, "no comparable parameter", skip=ShadowSkip.NOTHING_COMPARABLE
                )
            if Comparison.MISMATCH not in verdicts.values():
                drift = self._drift(before, remote_signed, verdicts)
                if drift is not None:
                    logger.error(
                        "signing.shadow.drift", platform=key[0].value, endpoint=key[1], detail=drift
                    )
                    return self._record_shadow(key, True, True, drift, verdicts)
                if Comparison.MATCH not in verdicts.values():
                    # Naming them, and why. "every parameter skipped" was true
                    # and told an operator nothing they could act on: which
                    # parameters, and whether this is a rule of ours that has
                    # gone stale or a signature nobody claims to compare.
                    skip, reason = self._skip_reason(remote_signed, verdicts)
                    return self._record_shadow(key, False, True, reason, verdicts, skip=skip)
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

    def _drift(
        self, native: SignedParams, remote: SignedParams, verdicts: Mapping[str, Comparison]
    ) -> str | None:
        """What drifted, in the comparators' own words; None when nothing did."""
        found: list[str] = []
        for name, verdict in verdicts.items():
            if verdict is not Comparison.DRIFT:
                continue
            explain = getattr(self._comparators.get(name), "explain", None)
            said = explain(native.params[name], remote.params[name]) if explain else None
            found.append(f"{name}: {said}" if said else name)
        return "; ".join(found) if found else None

    def _skip_reason(
        self, remote: SignedParams, verdicts: Mapping[str, Comparison]
    ) -> tuple[ShadowSkip, str]:
        """Why nothing could be compared, in the words of the thing that skipped.

        The comparators return a verdict and not a reason, which is right for
        them - they answer one question about two strings. The one A-Bogus skip
        that survives is recoverable here from the remote signature this call
        already holds: it happens when the browser's value is not an a_bogus at
        all, which points at browser-rpc rather than at the native signer. Saying
        which is the difference between an operator restarting a browser and an
        operator re-reversing a bundle that never moved.
        """
        names = ", ".join(sorted(verdicts))
        value = remote.params.get(SignatureAlgorithm.A_BOGUS.value)
        # Only when A-Bogus is what skipped. Everything else in `verdicts` skips
        # by design (per-identity values), and blaming the browser for those
        # would send an operator after a fault that is not there.
        if verdicts.get(SignatureAlgorithm.A_BOGUS.value) is Comparison.SKIPPED and isinstance(
            value, str
        ):
            # The same alphabet the comparator judged with, so the reason
            # reported here is the reason it actually skipped for.
            comparator = self._comparators.get(SignatureAlgorithm.A_BOGUS.value)
            alphabet = getattr(comparator, "alphabet", "s4")
            problem = structure_error(value, alphabet=alphabet)
            if problem is not None:
                return ShadowSkip.REFERENCE_UNUSABLE, (
                    f"nothing comparable ({names}): browser-rpc returned a value that is "
                    f"not an a_bogus ({problem}), so the sample had no reference to compare "
                    "against - check the browser, not the native signer"
                )
        return ShadowSkip.NOTHING_COMPARABLE, f"nothing comparable ({names})"

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
        *,
        skip: ShadowSkip | None = None,
        error: str | None = None,
    ) -> bool:
        result = ShadowResult(
            platform=key[0],
            endpoint=key[1],
            compared=compared,
            matched=matched,
            detail=detail,
            verdicts=dict(verdicts or {}),
            at=self._clock(),
            skip=skip,
            error=error,
        )
        self._last_shadow[key] = result
        if not compared:
            logger.info(
                "signing.shadow.skipped",
                platform=key[0].value,
                endpoint=key[1],
                skip=skip.value if skip else None,
                reason=detail,
            )
        elif matched and Comparison.DRIFT not in result.verdicts.values():
            # A drift has already been logged, at error level, by the caller;
            # a "match" line after it read as the sample having been fine.
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
    "SdkBuildComparator",
    "ShadowResult",
    "ShadowSkip",
    "SignatureComparator",
    "SignerRegistry",
    "SkipComparator",
    "SlidingRiskWindow",
    "XBogusComparator",
]
