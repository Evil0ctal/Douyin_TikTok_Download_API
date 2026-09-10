"""The six-step self-check.

"I deployed it but I get no data" is the most common first report, and the
cause can be in the proxy, the cookies, the signature or the network. Guessing
costs a round trip per guess, so the console and the CLI run the same six steps
and print one report (docs/design/15-operations.md):

1. component connectivity - postgres, redis, browser-rpc
2. egress - can this machine reach the platform domains directly
3. proxies - probe each one for reachability, exit IP and GeoIP
4. identity pool - how many usable identities, and how stale the oldest is
5. signing - native algorithm against browser-rpc, the same shadow comparison
   the registry runs in production
6. end-to-end smoke - one fixed public link through the whole pipeline

Step 6 is also the last step of the setup wizard; both call the same code.

**A step reports a code, not a sentence.** ``StepCode`` is the contract and
the prose is rendered from ``diagnose.reason.<code>`` per reader - the same
split :mod:`dtk.api.envelope` makes between an ``ErrorCode`` and its message.
The CLI and the logs get English; a request gets the language it negotiated,
and :func:`localize_report` re-renders a report stored before anyone's language
was known. Evidence - a driver's error, an exception's text, the per-step
``details`` - stays English: it is what a maintainer searches for
(docs/design/14-i18n.md).

**The report is redacted on the way out.** Its entire purpose is to be pasted
into an issue, so proxy passwords, cookies, API keys and signature parameters
are masked by the renderer rather than by whoever is pasting it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Any, Final

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from dtk import __version__
from dtk.core.crypto import Cipher
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, IdentityState, Language, Platform
from dtk.i18n.catalog import t
from dtk.identity.minting.client import BrowserRpcClient
from dtk.ops._sql import safe_execute, safe_scalar
from dtk.ops.health import ComponentHealth, check_browser_rpc, check_postgres, check_redis
from dtk.platforms.douyin.endpoints import DouyinAPIEndpoints
from dtk.platforms.tiktok.endpoints import TikTokAPIEndpoints
from dtk.signing.base import RequestSpec, StaticFingerprint
from dtk.signing.registry import SignerRegistry

log = get_logger(__name__)

#: Reached directly, without a proxy, to tell "no internet" from "bad proxy".
DEFAULT_EGRESS_TARGETS: Final[tuple[str, ...]] = (
    "https://www.douyin.com/",
    "https://www.tiktok.com/",
)

#: Echo service used to learn a proxy's exit address. Overridable because some
#: deployments have their own, and because a third party can disappear.
DEFAULT_PROXY_PROBE_URL: Final[str] = "https://api.ipify.org?format=json"

#: One signed request per platform, on the endpoint that matters most.
SIGNING_PROBE_URLS: Final[dict[Platform, str]] = {
    Platform.DOUYIN: DouyinAPIEndpoints.POST_DETAIL,
    Platform.TIKTOK: TikTokAPIEndpoints.POST_DETAIL,
}

#: Fingerprint used for the signing probe. A signature depends on the
#: User-Agent, so the comparison needs one; it is never sent anywhere.
PROBE_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

STEP_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_PROXIES_PROBED: Final[int] = 20

MASK: Final[str] = "[REDACTED]"

# Credentials in a proxy URL. The user survives so a proxy stays identifiable;
# the password never does.
_PROXY_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:@/\s]{1,64}):(?P<password>[^@/\s]*)@"
)
# Header-shaped text: "Cookie: ...", "authorization = ...", and the same names
# as a quoted mapping key, which is how they reach a details payload.
_HEADER_RE = re.compile(
    r"(?i)\b(cookie|set-cookie|authorization|x-api-key|proxy-authorization)\b"
    r"[\"']?\s*[:=]\s*[^\n]+"
)
# Cookie and signature parameters wherever they appear in free text.
_PARAM_RE = re.compile(
    r"(?i)\b(sessionid(?:_ss)?|sid_guard|sid_tt|uid_tt|odin_tt|passport_csrf_token|ttwid|"
    r"msToken|a_bogus|X-Bogus|X-Dynosaur|X-Gnarly|_signature|verifyFp|s_v_web_id)"
    r"\s*[=:]\s*([^\s;,&\"'\]}]+)"
)
# Full API keys: dtk_<prefix>_<secret>. The prefix is public and is kept so a
# report still says which key was in play.
_API_KEY_RE = re.compile(r"\b(dtk_[0-9a-f]{4,24})_[A-Za-z0-9_\-]{8,}")


def redact(value: str) -> str:
    """Mask every credential shape this project can emit.

    Applied at render time to the whole report rather than field by field: a
    detail added later cannot leak by being forgotten, which is the same reason
    the log processor redacts centrally (docs/design/08-security.md).
    """
    text_value = _HEADER_RE.sub(lambda m: f"{m.group(1)}: {MASK}", value)
    text_value = _PROXY_CREDENTIALS_RE.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:{MASK}@", text_value
    )
    text_value = _API_KEY_RE.sub(lambda m: f"{m.group(1)}_{MASK}", text_value)
    return _PARAM_RE.sub(lambda m: f"{m.group(1)}={MASK}", text_value)


#: Detail keys whose value is masked whatever it looks like. Mirrors the log
#: processor: a value is redacted because of where it sits, not only because it
#: matched a pattern.
SENSITIVE_DETAIL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "cookies_encrypted",
        "password",
        "proxy_authorization",
        "proxy_url",
        "secret",
        "secret_key",
        "set_cookie",
        "token",
        "url_encrypted",
        "x_api_key",
    }
)


def _is_sensitive_key(key: Any) -> bool:
    return isinstance(key, str) and key.strip().lower().replace("-", "_") in SENSITIVE_DETAIL_KEYS


def redact_value(value: Any, key: str | None = None) -> Any:
    """Recursively redact a details payload, by key name and by pattern."""
    if _is_sensitive_key(key):
        return MASK
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {name: redact_value(item, str(name)) for name, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, bool | int | float) or value is None:
        return value
    # Anything else reaches the report through its __str__, and an upstream
    # driver error object is exactly the kind of thing that carries a session
    # cookie in its text. Returning it untouched would let that text out of a
    # report whose entire purpose is to be pasted in public.
    return redact(str(value))


class StepStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class StepCode(StrEnum):
    """What a step found, as a stable identifier.

    One member per outcome a step can reach, so a console can branch on a
    finding without matching on prose.
    """

    COMPONENTS_UNREACHABLE = "components_unreachable"
    COMPONENTS_BROWSER_RPC_DOWN = "components_browser_rpc_down"
    COMPONENTS_OK = "components_ok"

    EGRESS_UNREACHABLE = "egress_unreachable"
    EGRESS_PARTIAL = "egress_partial"
    EGRESS_OK = "egress_ok"

    PROXIES_NO_SESSION = "proxies_no_session"
    PROXIES_UNREADABLE = "proxies_unreadable"
    PROXIES_NONE = "proxies_none"
    PROXIES_ALL_FAILED = "proxies_all_failed"
    PROXIES_PARTIAL = "proxies_partial"
    PROXIES_OK = "proxies_ok"

    POOL_NO_SESSION = "pool_no_session"
    POOL_UNREADABLE = "pool_unreadable"
    POOL_EMPTY = "pool_empty"
    POOL_BELOW_MINIMUM = "pool_below_minimum"
    POOL_OK = "pool_ok"

    SIGNING_NO_REGISTRY = "signing_no_registry"
    SIGNING_NO_BROWSER_RPC = "signing_no_browser_rpc"
    SIGNING_MISMATCH = "signing_mismatch"
    SIGNING_NOT_COMPARABLE = "signing_not_comparable"
    SIGNING_OK = "signing_ok"

    SMOKE_NOT_CONFIGURED = "smoke_not_configured"
    SMOKE_FAILED = "smoke_failed"
    SMOKE_OK = "smoke_ok"

    STEP_CRASHED = "step_crashed"


#: Catalogue namespaces. The code is the key's last segment, so a new finding
#: needs one member above and two entries in the locale files.
REASON_KEY_PREFIX: Final[str] = "diagnose.reason."
ACTION_KEY_PREFIX: Final[str] = "diagnose.action."

#: Codes that also carry advice. Everything else is either a pass or a step
#: skipped because a dependency is absent - states with nothing to suggest, and
#: inventing a sentence for them would only pad the report.
ACTIONABLE_CODES: Final[frozenset[str]] = frozenset(
    code.value
    for code in (
        StepCode.COMPONENTS_UNREACHABLE,
        StepCode.COMPONENTS_BROWSER_RPC_DOWN,
        StepCode.EGRESS_UNREACHABLE,
        StepCode.EGRESS_PARTIAL,
        StepCode.PROXIES_NONE,
        StepCode.PROXIES_ALL_FAILED,
        StepCode.PROXIES_PARTIAL,
        StepCode.POOL_EMPTY,
        StepCode.POOL_BELOW_MINIMUM,
        StepCode.SIGNING_NO_BROWSER_RPC,
        StepCode.SIGNING_MISMATCH,
        # The sentence for this one was written and never wired up, so the step
        # showed a warning and the terse per-platform detail under it with
        # nothing saying what it meant or whether anything was broken. It is the
        # code most in need of its paragraph: "cannot compare" reads as a fault
        # and is not one.
        StepCode.SIGNING_NOT_COMPARABLE,
        StepCode.SMOKE_FAILED,
        StepCode.STEP_CRASHED,
    )
)


def reason_key(code: StepCode | str) -> str:
    """Catalogue key for the sentence a code stands for."""
    return f"{REASON_KEY_PREFIX}{code}"


def action_key(code: StepCode | str) -> str:
    """Catalogue key for the advice a code carries."""
    return f"{ACTION_KEY_PREFIX}{code}"


@dataclass(frozen=True, slots=True)
class StepResult:
    """One step: what it checked, how it went, and what to do about it.

    ``code`` and ``args`` are the finding; the sentences come from them on the
    way out. ``reason`` and ``action`` are the English rendering, for the CLI.
    """

    number: int
    step: str
    status: StepStatus
    code: StepCode
    args: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None

    @property
    def ok(self) -> bool:
        """Whether this step found nothing wrong enough to act on.

        WARN counts. A warning is a step that ran, reached its answer and
        wants an operator to know something - a pool running thin, a setting
        left at a development default. Folding it in with FAIL made the whole
        report read "verdict FAIL" over a note, and an operator who is told
        their instance failed when it is serving traffic stops reading the
        verdict at all.
        """
        return self.status is not StepStatus.FAIL

    @property
    def action_code(self) -> StepCode | None:
        return self.code if self.code.value in ACTIONABLE_CODES else None

    @property
    def reason(self) -> str:
        return self.localized_reason(DEFAULT_LANGUAGE)

    @property
    def action(self) -> str | None:
        return self.localized_action(DEFAULT_LANGUAGE)

    def localized_reason(self, language: Language | str = DEFAULT_LANGUAGE) -> str:
        return t(reason_key(self.code), language, **self.args)

    def localized_action(self, language: Language | str = DEFAULT_LANGUAGE) -> str | None:
        code = self.action_code
        return None if code is None else t(action_key(code), language, **self.args)

    def as_dict(self, language: Language | str = DEFAULT_LANGUAGE) -> dict[str, Any]:
        action = self.localized_action(language)
        return {
            "number": self.number,
            "step": self.step,
            "status": self.status.value,
            "code": self.code.value,
            "action_code": self.action_code.value if self.action_code else None,
            # The arguments ride along so a stored report can be rendered again
            # in another language; they hold user data, so they are redacted.
            "args": redact_value(self.args),
            "reason": redact(self.localized_reason(language)),
            "action": redact(action) if action else None,
            "details": redact_value(self.details),
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """The whole run. ``render_text`` is what a user pastes into an issue."""

    version: str
    started_at: datetime
    finished_at: datetime
    steps: tuple[StepResult, ...]

    @property
    def passed(self) -> bool:
        """No step failed. Warnings do not clear it and do not break it."""
        return all(step.ok for step in self.steps)

    @property
    def failures(self) -> tuple[StepResult, ...]:
        return tuple(step for step in self.steps if step.status is StepStatus.FAIL)

    @property
    def warnings(self) -> tuple[StepResult, ...]:
        return tuple(step for step in self.steps if step.status is StepStatus.WARN)

    @property
    def verdict(self) -> str:
        """``fail``, ``warn`` or ``pass`` - the worst status any step reached.

        Three, because a run with a warning is neither of the other two, and
        collapsing it into either one loses the reason someone ran this.
        """
        if self.failures:
            return "fail"
        return "warn" if self.warnings else "pass"

    def as_dict(self, language: Language | str = DEFAULT_LANGUAGE) -> dict[str, Any]:
        """The wire shape, redacted, with the prose in one language.

        ``text`` is included so the console shows and copies exactly what the
        server rendered rather than reassembling the report itself.
        """
        payload: dict[str, Any] = {
            "version": self.version,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "passed": self.passed,
            "verdict": self.verdict,
            "steps": [step.as_dict(language) for step in self.steps],
        }
        payload["text"] = _render_text(payload)
        return payload

    def render_text(self, language: Language | str = DEFAULT_LANGUAGE) -> str:
        """Plain-text report, redacted. Safe to paste in public."""
        return str(self.as_dict(language)["text"])


def localize_report(
    payload: Mapping[str, Any], language: Language | str = DEFAULT_LANGUAGE
) -> dict[str, Any]:
    """Re-render a stored report's prose in one language.

    The console runs the self-check as a background task, so the worker writes
    the report as JSON and a later request reads it back - the first moment
    anyone's language is known. Codes and arguments survive that round trip, so
    the sentences are rebuilt here rather than frozen when the check ran.
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        # Not a report this function recognizes. Handing it back untouched
        # beats a 500 from the endpoint that exists to explain what is broken.
        return dict(payload)
    localized: dict[str, Any] = {
        **payload,
        "steps": [_localize_step(step, language) for step in steps],
    }
    localized["text"] = _render_text(localized)
    return localized


SmokeRunner = Callable[[str], Awaitable[Any]]


@dataclass(slots=True)
class DiagnoseContext:
    """Everything the steps may use. A missing dependency skips its step.

    Skipping is deliberate: running diagnostics from the CLI with no browser-rpc
    configured must report "not configured" for that step, not a red failure
    the user cannot act on.
    """

    session: AsyncSession | None = None
    engine: AsyncEngine | None = None
    redis: Redis | None = None
    rpc: BrowserRpcClient | None = None
    registry: SignerRegistry | None = None
    cipher: Cipher | None = None
    http: httpx.AsyncClient | None = None
    #: Runs one public link through the full pipeline. Supplied by the caller
    #: (setup wizard or CLI) together with the link it should use.
    smoke: SmokeRunner | None = None
    smoke_url: str | None = None
    egress_targets: tuple[str, ...] = DEFAULT_EGRESS_TARGETS
    proxy_probe_url: str = DEFAULT_PROXY_PROBE_URL
    #: Low-water mark the pool step compares against; doc 10's pool.min_size.
    min_active: int = 3


async def check_components(ctx: DiagnoseContext) -> StepResult:
    """Step 1: can this process reach postgres, redis and browser-rpc."""
    result = _step(1, "components")
    components: list[ComponentHealth] = [
        await check_postgres(ctx.engine),
        await check_redis(ctx.redis),
        await check_browser_rpc(ctx.rpc),
    ]
    details = {c.name: c.as_dict() for c in components}
    broken = [c for c in components if not c.ok and c.name != "browser_rpc"]
    rpc = next(c for c in components if c.name == "browser_rpc")

    if broken:
        # Names only. Why each one is down is already in the details, and a
        # probe's own wording is evidence that must not be translated.
        return result(
            StepStatus.FAIL,
            StepCode.COMPONENTS_UNREACHABLE,
            args={"components": ", ".join(c.name for c in broken)},
            details=details,
        )
    if not rpc.ok:
        return result(
            StepStatus.WARN,
            StepCode.COMPONENTS_BROWSER_RPC_DOWN,
            # The code, not the sentence. rpc.detail is already rendered, in
            # English, at the moment the check ran - and the report is read
            # later, by someone whose language nobody knew yet. Storing the code
            # is what let the rest of this step be translated; a rendered
            # fragment would sit in English inside a Chinese sentence.
            args=(
                {"detail_code": rpc.detail_code}
                if rpc.detail_code
                else {"detail": rpc.evidence or ""}
            ),
            details=details,
        )
    return result(StepStatus.PASS, StepCode.COMPONENTS_OK, details=details)


async def check_egress(ctx: DiagnoseContext) -> StepResult:
    """Step 2: direct reachability of the platform domains, without a proxy."""
    result = _step(2, "egress")
    details: dict[str, Any] = {}
    reachable = 0
    async with _client(ctx) as client:
        for target in ctx.egress_targets:
            try:
                response = await client.get(target, timeout=STEP_TIMEOUT_SECONDS)
            except httpx.HTTPError as exc:
                details[target] = f"error: {exc}"
                continue
            details[target] = f"http {response.status_code}"
            reachable += 1

    if reachable == 0:
        return result(StepStatus.FAIL, StepCode.EGRESS_UNREACHABLE, details=details)
    if reachable < len(ctx.egress_targets):
        return result(
            StepStatus.WARN,
            StepCode.EGRESS_PARTIAL,
            args={"reachable": reachable, "total": len(ctx.egress_targets)},
            details=details,
        )
    return result(StepStatus.PASS, StepCode.EGRESS_OK, details=details)


async def check_proxies(ctx: DiagnoseContext) -> StepResult:
    """Step 3: probe every proxy for reachability, exit IP and GeoIP."""
    result = _step(3, "proxies")
    if ctx.session is None or ctx.cipher is None:
        return result(StepStatus.SKIP, StepCode.PROXIES_NO_SESSION)

    rows = await safe_execute(
        ctx.session,
        "SELECT id, label, country, healthy, url_encrypted FROM proxies "
        f"ORDER BY created_at LIMIT {MAX_PROXIES_PROBED}",
        event="ops.diagnose.proxy_list_failed",
    )
    if rows is None:
        return result(StepStatus.SKIP, StepCode.PROXIES_UNREADABLE)
    if not rows:
        return result(StepStatus.WARN, StepCode.PROXIES_NONE)

    details: dict[str, Any] = {}
    working = 0
    for row in rows:
        label = str(row.label or row.id)
        try:
            url = ctx.cipher.decrypt(bytes(row.url_encrypted), aad=str(row.id))
        except Exception as exc:
            details[label] = f"cannot decrypt proxy URL ({type(exc).__name__})"
            continue
        exit_ip, error = await _probe_proxy(url, ctx.proxy_probe_url)
        if error is not None:
            details[label] = f"unreachable: {error}"
            continue
        working += 1
        details[label] = {
            "exit_ip": exit_ip,
            "country": row.country,
            "marked_healthy": bool(row.healthy),
        }

    if working == 0:
        return result(
            StepStatus.FAIL,
            StepCode.PROXIES_ALL_FAILED,
            args={"total": len(rows)},
            details=details,
        )
    if working < len(rows):
        return result(
            StepStatus.WARN,
            StepCode.PROXIES_PARTIAL,
            args={"working": working, "total": len(rows)},
            details=details,
        )
    return result(StepStatus.PASS, StepCode.PROXIES_OK, args={"working": working}, details=details)


async def check_pool(ctx: DiagnoseContext) -> StepResult:
    """Step 4: usable identities, and how long since the oldest one worked."""
    result = _step(4, "pool")
    if ctx.session is None:
        return result(StepStatus.SKIP, StepCode.POOL_NO_SESSION)

    counts: dict[str, int] = {state.value: 0 for state in IdentityState}
    rows = await safe_execute(
        ctx.session,
        "SELECT state, count(*) FROM identities GROUP BY state",
        event="ops.diagnose.pool_counts_failed",
    )
    if rows is None:
        return result(StepStatus.SKIP, StepCode.POOL_UNREADABLE)
    for state, count in rows:
        counts[str(state)] = int(count)

    details: dict[str, Any] = dict(counts)
    last_success = await safe_scalar(
        ctx.session,
        "SELECT max(ts) FROM request_log WHERE outcome = 'ok'",
        event="ops.diagnose.query_failed",
    )
    details["last_success_at"] = last_success.isoformat() if last_success else None
    oldest_idle = await safe_scalar(
        ctx.session,
        "SELECT min(last_used_at) FROM identities WHERE state = 'active'",
        event="ops.diagnose.query_failed",
    )
    details["oldest_active_last_used_at"] = oldest_idle.isoformat() if oldest_idle else None

    active = counts.get(IdentityState.ACTIVE.value, 0)
    if active == 0:
        return result(StepStatus.FAIL, StepCode.POOL_EMPTY, details=details)
    if active < ctx.min_active:
        return result(
            StepStatus.WARN,
            StepCode.POOL_BELOW_MINIMUM,
            args={"active": active, "minimum": ctx.min_active},
            details=details,
        )
    return result(StepStatus.PASS, StepCode.POOL_OK, args={"active": active}, details=details)


async def check_signing(ctx: DiagnoseContext) -> StepResult:
    """Step 5: native signatures against browser-rpc, per platform."""
    result = _step(5, "signing")
    if ctx.registry is None:
        return result(StepStatus.SKIP, StepCode.SIGNING_NO_REGISTRY)
    if ctx.rpc is None or not ctx.rpc.configured:
        return result(StepStatus.SKIP, StepCode.SIGNING_NO_BROWSER_RPC)

    fingerprint = StaticFingerprint(user_agent=PROBE_USER_AGENT)
    details: dict[str, Any] = {}
    mismatched: list[str] = []
    skipped: list[str] = []
    for platform, url in SIGNING_PROBE_URLS.items():
        spec = RequestSpec.get(url, params={"aweme_id": "0", "device_platform": "webapp"})
        try:
            agreed = await ctx.registry.compare_shadow(spec, fingerprint, platform=platform)
        except Exception as exc:
            details[platform.value] = f"comparison failed: {type(exc).__name__}: {exc}"
            continue
        shadow = ctx.registry.shadow_result(platform, spec.endpoint)
        if shadow is not None and not shadow.compared:
            details[platform.value] = f"not compared: {shadow.detail}"
            skipped.append(platform.value)
            continue
        details[platform.value] = (
            "match" if agreed else f"mismatch: {shadow.detail if shadow else ''}"
        )
        if not agreed:
            mismatched.append(platform.value)

    if mismatched:
        return result(
            StepStatus.FAIL,
            StepCode.SIGNING_MISMATCH,
            args={"platforms": ", ".join(mismatched)},
            details=details,
        )
    if skipped:
        # Any skip at all, not only a total one. "native and browser signatures
        # agree" is the overclaim this step exists to avoid: with one platform
        # compared and one not, it reads as evidence about both, and the one it
        # says nothing about is exactly the one an operator needs told.
        return result(
            StepStatus.WARN,
            StepCode.SIGNING_NOT_COMPARABLE,
            args={"platforms": ", ".join(skipped)},
            details=details,
        )
    return result(StepStatus.PASS, StepCode.SIGNING_OK, details=details)


async def check_smoke(ctx: DiagnoseContext) -> StepResult:
    """Step 6: one public link through the whole pipeline."""
    result = _step(6, "smoke")
    if ctx.smoke is None or not ctx.smoke_url:
        return result(StepStatus.SKIP, StepCode.SMOKE_NOT_CONFIGURED)
    try:
        outcome = await ctx.smoke(ctx.smoke_url)
    except Exception as exc:
        return result(
            StepStatus.FAIL,
            StepCode.SMOKE_FAILED,
            args={"error": f"{type(exc).__name__}: {exc}"},
            details={"url": ctx.smoke_url},
        )
    return result(
        StepStatus.PASS,
        StepCode.SMOKE_OK,
        details={"url": ctx.smoke_url, "result": _summarize(outcome)},
    )


#: The six steps, in the order doc 15 defines them.
STEPS: Final[tuple[Callable[[DiagnoseContext], Awaitable[StepResult]], ...]] = (
    check_components,
    check_egress,
    check_proxies,
    check_pool,
    check_signing,
    check_smoke,
)


async def run_diagnostics(ctx: DiagnoseContext | None = None) -> DiagnosticReport:
    """Run all six steps. A step that raises becomes a failed step, not a 500."""
    context = ctx or DiagnoseContext()
    started_at = datetime.now(UTC)
    results: list[StepResult] = []
    for number, step in enumerate(STEPS, start=1):
        try:
            results.append(await step(context))
        except Exception as exc:
            log.error("ops.diagnose.step_crashed", step=step.__name__, error=str(exc))
            results.append(
                StepResult(
                    number=number,
                    step=step.__name__.removeprefix("check_"),
                    status=StepStatus.FAIL,
                    code=StepCode.STEP_CRASHED,
                    args={"error": f"{type(exc).__name__}: {exc}"},
                )
            )
    report = DiagnosticReport(
        version=__version__,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        steps=tuple(results),
    )
    log.info(
        "ops.diagnose.completed",
        verdict=report.verdict,
        failures=len(report.failures),
        warnings=len(report.warnings),
    )
    return report


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _step(number: int, name: str) -> partial[StepResult]:
    """Bind a step's identity and start its clock.

    Every branch of a check returns the same step under a different finding, so
    its number, name and start are stated once instead of at every exit.
    """
    return partial(_result, number, name, started=time.perf_counter())


def _result(
    number: int,
    step: str,
    status: StepStatus,
    code: StepCode,
    *,
    args: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    started: float,
) -> StepResult:
    return StepResult(
        number=number,
        step=step,
        status=status,
        code=code,
        args=args or {},
        details=details or {},
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )


def _localize_step(step: Any, language: Language | str) -> Any:
    """Rewrite one stored step's prose. Anything unrecognized passes through."""
    if not isinstance(step, Mapping):
        return step
    code = step.get("code")
    if not isinstance(code, str) or not code:
        return dict(step)
    raw_args = step.get("args")
    args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
    args = _render_nested(args, language)
    localized = dict(step)
    localized["reason"] = redact(t(reason_key(code), language, **args))
    if code in ACTIONABLE_CODES:
        localized["action"] = redact(t(action_key(code), language, **args))
    return localized


def _render_nested(args: dict[str, Any], language: Language | str) -> dict[str, Any]:
    """Resolve an argument that is itself a code rather than a sentence.

    One step quotes another module's finding - the components step reports why
    browser-rpc is down, and health.py owns that vocabulary. Storing the code
    and rendering it here keeps the whole sentence in one language; storing
    health.py's already-rendered English would leave a fragment of it inside
    every other translation.
    """
    code = args.pop("detail_code", None)
    if not isinstance(code, str) or not code:
        # Left exactly as stored. Filling in a default here would hide a
        # genuinely missing argument in some other step, which interpolate()
        # otherwise reports as a warning and a visible marker.
        return args
    from dtk.ops.health import DETAIL_KEY_PREFIX

    args["detail"] = t(f"{DETAIL_KEY_PREFIX}{code}", language)
    return args


def _render_text(payload: Mapping[str, Any]) -> str:
    """Lay out an already-rendered, already-redacted report as plain text."""
    lines = [
        f"dtk diagnostics {payload.get('version', '')}",
        f"started  {payload.get('started_at', '')}",
        f"finished {payload.get('finished_at', '')}",
        f"verdict  {str(payload.get('verdict') or ('pass' if payload.get('passed') else 'fail')).upper()}",
        "",
    ]
    for step in payload.get("steps") or []:
        status = str(step.get("status", "")).upper()
        lines.append(f"[{status:4}] {step.get('number')}. {step.get('step')}")
        lines.append(f"       {step.get('reason', '')}")
        if step.get("action"):
            lines.append(f"       action: {step['action']}")
        for key, value in (step.get("details") or {}).items():
            # The payload is already redacted, but a nested structure only
            # becomes one string here. Re-running the pattern pass over the
            # rendered form costs nothing and is the last point at which
            # anything can be caught before this text is pasted somewhere.
            lines.append(f"       {key}: {redact(str(value))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class _ClientHolder:
    """Yields the shared client when there is one, else a short-lived client."""

    __slots__ = ("_client", "_owned")

    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self._client = client
        self._owned = client is None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=STEP_TIMEOUT_SECONDS, follow_redirects=True)
        return self._client

    async def __aexit__(self, *_exc: object) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
            self._client = None


def _client(ctx: DiagnoseContext) -> _ClientHolder:
    return _ClientHolder(ctx.http)


async def _probe_proxy(proxy_url: str, probe_url: str) -> tuple[str | None, str | None]:
    """Return ``(exit_ip, error)``; exactly one of the two is set."""
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url, timeout=STEP_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.get(probe_url)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        # str(exc) can embed the proxy URL, credentials included.
        return None, type(exc).__name__
    except ValueError:
        return None, "probe returned a non-JSON body"
    if isinstance(body, dict):
        value = body.get("ip") or body.get("origin")
        if value:
            return str(value), None
    return None, "probe response carried no address"


def _summarize(outcome: Any) -> str:
    if outcome is None:
        return "no payload"
    if isinstance(outcome, dict):
        return f"dict with {len(outcome)} keys"
    return type(outcome).__name__


__all__ = [
    "ACTIONABLE_CODES",
    "ACTION_KEY_PREFIX",
    "DEFAULT_EGRESS_TARGETS",
    "DEFAULT_PROXY_PROBE_URL",
    "MASK",
    "REASON_KEY_PREFIX",
    "SENSITIVE_DETAIL_KEYS",
    "SIGNING_PROBE_URLS",
    "STEPS",
    "DiagnoseContext",
    "DiagnosticReport",
    "SmokeRunner",
    "StepCode",
    "StepResult",
    "StepStatus",
    "action_key",
    "check_components",
    "check_egress",
    "check_pool",
    "check_proxies",
    "check_signing",
    "check_smoke",
    "localize_report",
    "reason_key",
    "redact",
    "redact_value",
    "run_diagnostics",
]
