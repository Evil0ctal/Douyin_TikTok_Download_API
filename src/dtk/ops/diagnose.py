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

**The report is redacted on the way out.** Its entire purpose is to be pasted
into an issue, so proxy passwords, cookies, API keys and signature parameters
are masked by the renderer rather than by whoever is pasting it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from dtk import __version__
from dtk.core.crypto import Cipher
from dtk.core.logging import get_logger
from dtk.core.types import IdentityState, Platform
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
    r"msToken|a_bogus|X-Bogus|_signature|verifyFp|s_v_web_id)\s*[=:]\s*([^\s;,&\"'\]}]+)"
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
    return value


class StepStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class StepResult:
    """One step: what it checked, how it went, and what to do about it."""

    number: int
    step: str
    status: StepStatus
    reason: str
    action: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in (StepStatus.PASS, StepStatus.SKIP)

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "step": self.step,
            "status": self.status.value,
            "reason": redact(self.reason),
            "action": redact(self.action) if self.action else None,
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
        return all(step.ok for step in self.steps)

    @property
    def failures(self) -> tuple[StepResult, ...]:
        return tuple(step for step in self.steps if step.status is StepStatus.FAIL)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "passed": self.passed,
            "steps": [step.as_dict() for step in self.steps],
        }

    def render_text(self) -> str:
        """Plain-text report, redacted. Safe to paste in public."""
        lines = [
            f"dtk diagnostics {self.version}",
            f"started  {self.started_at.isoformat()}",
            f"finished {self.finished_at.isoformat()}",
            f"verdict  {'PASS' if self.passed else 'FAIL'}",
            "",
        ]
        for step in self.steps:
            lines.append(f"[{step.status.value.upper():4}] {step.number}. {step.step}")
            lines.append(f"       {redact(step.reason)}")
            if step.action:
                lines.append(f"       action: {redact(step.action)}")
            for key, value in step.details.items():
                lines.append(f"       {key}: {redact(str(redact_value(value, key)))}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


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
    started = time.perf_counter()
    components: list[ComponentHealth] = [
        await check_postgres(ctx.engine),
        await check_redis(ctx.redis),
        await check_browser_rpc(ctx.rpc),
    ]
    details = {c.name: c.as_dict() for c in components}
    broken = [c for c in components if not c.ok and c.name != "browser_rpc"]
    rpc = next(c for c in components if c.name == "browser_rpc")

    if broken:
        return _result(
            1,
            "components",
            StepStatus.FAIL,
            "unreachable: " + ", ".join(f"{c.name} ({c.detail})" for c in broken),
            "Check that the postgres and redis containers are running and that "
            "DTK_DATABASE_URL and DTK_REDIS_URL point at them.",
            details,
            started,
        )
    if not rpc.ok:
        return _result(
            1,
            "components",
            StepStatus.WARN,
            f"postgres and redis are reachable; browser-rpc is not ({rpc.detail})",
            "Minting and the signing fallback are unavailable. Start the browser "
            "container, or import cookies manually and stay on the native signer.",
            details,
            started,
        )
    return _result(
        1, "components", StepStatus.PASS, "all components reachable", None, details, started
    )


async def check_egress(ctx: DiagnoseContext) -> StepResult:
    """Step 2: direct reachability of the platform domains, without a proxy."""
    started = time.perf_counter()
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
        return _result(
            2,
            "egress",
            StepStatus.FAIL,
            "no platform domain could be reached directly",
            "This machine has no usable route to the platforms. Check DNS, the "
            "host firewall, and whether outbound traffic needs a proxy.",
            details,
            started,
        )
    if reachable < len(ctx.egress_targets):
        return _result(
            2,
            "egress",
            StepStatus.WARN,
            f"{reachable} of {len(ctx.egress_targets)} platform domains reachable",
            "Requests for the unreachable platform will depend entirely on a working proxy.",
            details,
            started,
        )
    return _result(
        2, "egress", StepStatus.PASS, "all platform domains reachable", None, details, started
    )


async def check_proxies(ctx: DiagnoseContext) -> StepResult:
    """Step 3: probe every proxy for reachability, exit IP and GeoIP."""
    started = time.perf_counter()
    if ctx.session is None or ctx.cipher is None:
        return _result(
            3,
            "proxies",
            StepStatus.SKIP,
            "no database session or cipher available to read the proxy list",
            None,
            {},
            started,
        )

    rows = await safe_execute(
        ctx.session,
        "SELECT id, label, country, healthy, url_encrypted FROM proxies "
        f"ORDER BY created_at LIMIT {MAX_PROXIES_PROBED}",
        event="ops.diagnose.proxy_list_failed",
    )
    if rows is None:
        return _result(
            3,
            "proxies",
            StepStatus.SKIP,
            "the proxy list could not be read; see the component step",
            None,
            {},
            started,
        )
    if not rows:
        return _result(
            3,
            "proxies",
            StepStatus.WARN,
            "no proxies configured",
            "Every identity will share this machine's egress IP. That is fine "
            "for a first look and a correlation risk for a busy pool.",
            {},
            started,
        )

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
        return _result(
            3,
            "proxies",
            StepStatus.FAIL,
            f"none of the {len(rows)} configured proxies answered",
            "Check the proxy credentials and whether the provider still allows "
            "this machine's IP. Identities bound to a dead proxy cannot recover.",
            details,
            started,
        )
    if working < len(rows):
        return _result(
            3,
            "proxies",
            StepStatus.WARN,
            f"{working} of {len(rows)} proxies answered",
            "Retire or replace the proxies that failed; their identities will "
            "keep failing until you do.",
            details,
            started,
        )
    return _result(
        3, "proxies", StepStatus.PASS, f"all {working} proxies answered", None, details, started
    )


async def check_pool(ctx: DiagnoseContext) -> StepResult:
    """Step 4: usable identities, and how long since the oldest one worked."""
    started = time.perf_counter()
    if ctx.session is None:
        return _result(
            4, "pool", StepStatus.SKIP, "no database session available", None, {}, started
        )

    counts: dict[str, int] = {state.value: 0 for state in IdentityState}
    rows = await safe_execute(
        ctx.session,
        "SELECT state, count(*) FROM identities GROUP BY state",
        event="ops.diagnose.pool_counts_failed",
    )
    if rows is None:
        return _result(
            4,
            "pool",
            StepStatus.SKIP,
            "the identity table could not be read; see the component step",
            None,
            {},
            started,
        )
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
        return _result(
            4,
            "pool",
            StepStatus.FAIL,
            "no active identity",
            "Mint identities, or import a cookie jar. Until the pool has one "
            "usable identity every request is rejected or queued.",
            details,
            started,
        )
    if active < ctx.min_active:
        return _result(
            4,
            "pool",
            StepStatus.WARN,
            f"{active} active identities, below the low-water mark of {ctx.min_active}",
            "Mint more identities, or lower pool.min_size if this is deliberate.",
            details,
            started,
        )
    return _result(
        4, "pool", StepStatus.PASS, f"{active} active identities", None, details, started
    )


async def check_signing(ctx: DiagnoseContext) -> StepResult:
    """Step 5: native signatures against browser-rpc, per platform."""
    started = time.perf_counter()
    if ctx.registry is None:
        return _result(
            5,
            "signing",
            StepStatus.SKIP,
            "no signer registry supplied",
            None,
            {},
            started,
        )
    if ctx.rpc is None or not ctx.rpc.configured:
        return _result(
            5,
            "signing",
            StepStatus.SKIP,
            "browser-rpc is not configured, so there is no second signer to "
            "compare the native algorithm against",
            "Configure DTK_BROWSER_RPC_URL to enable the shadow comparison that "
            "detects a stale signing algorithm before the risk rate does.",
            {},
            started,
        )

    fingerprint = StaticFingerprint(user_agent=PROBE_USER_AGENT)
    details: dict[str, Any] = {}
    mismatched: list[str] = []
    for platform, url in SIGNING_PROBE_URLS.items():
        spec = RequestSpec.get(url, params={"aweme_id": "0", "device_platform": "webapp"})
        try:
            agreed = await ctx.registry.compare_shadow(spec, fingerprint, platform=platform)
        except Exception as exc:
            details[platform.value] = f"comparison failed: {type(exc).__name__}: {exc}"
            continue
        result = ctx.registry.shadow_result(platform, spec.endpoint)
        if result is not None and not result.compared:
            details[platform.value] = f"not compared: {result.detail}"
            continue
        details[platform.value] = (
            "match" if agreed else f"mismatch: {result.detail if result else ''}"
        )
        if not agreed:
            mismatched.append(platform.value)

    if mismatched:
        return _result(
            5,
            "signing",
            StepStatus.FAIL,
            f"native signing disagrees with the browser for {', '.join(mismatched)}",
            "The native algorithm has drifted from the platform's. Those "
            "platforms now sign through browser-rpc; open an issue with this "
            "report so the algorithm can be updated.",
            details,
            started,
        )
    return _result(
        5, "signing", StepStatus.PASS, "native and browser signatures agree", None, details, started
    )


async def check_smoke(ctx: DiagnoseContext) -> StepResult:
    """Step 6: one public link through the whole pipeline."""
    started = time.perf_counter()
    if ctx.smoke is None or not ctx.smoke_url:
        return _result(
            6,
            "smoke",
            StepStatus.SKIP,
            "no smoke test link configured",
            None,
            {},
            started,
        )
    try:
        outcome = await ctx.smoke(ctx.smoke_url)
    except Exception as exc:
        return _result(
            6,
            "smoke",
            StepStatus.FAIL,
            f"end-to-end fetch failed: {type(exc).__name__}: {exc}",
            "Read the failing step above first; a smoke failure is usually a "
            "symptom of the proxy, pool or signing step, not its own fault.",
            {"url": ctx.smoke_url},
            started,
        )
    return _result(
        6,
        "smoke",
        StepStatus.PASS,
        "end-to-end fetch succeeded",
        None,
        {"url": ctx.smoke_url, "result": _summarize(outcome)},
        started,
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
                    reason=f"the check itself failed: {type(exc).__name__}: {exc}",
                    action="This is a bug in dtk. Please report it with this report attached.",
                )
            )
    report = DiagnosticReport(
        version=__version__,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        steps=tuple(results),
    )
    log.info("ops.diagnose.completed", passed=report.passed, failures=len(report.failures))
    return report


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _result(
    number: int,
    step: str,
    status: StepStatus,
    reason: str,
    action: str | None,
    details: dict[str, Any],
    started: float,
) -> StepResult:
    return StepResult(
        number=number,
        step=step,
        status=status,
        reason=reason,
        action=action,
        details=details,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )


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
    "DEFAULT_EGRESS_TARGETS",
    "DEFAULT_PROXY_PROBE_URL",
    "MASK",
    "SENSITIVE_DETAIL_KEYS",
    "SIGNING_PROBE_URLS",
    "STEPS",
    "DiagnoseContext",
    "DiagnosticReport",
    "SmokeRunner",
    "StepResult",
    "StepStatus",
    "check_components",
    "check_egress",
    "check_pool",
    "check_proxies",
    "check_signing",
    "check_smoke",
    "redact",
    "redact_value",
    "run_diagnostics",
]
