"""CloakBrowser adapter. This file is the seam.

===========================================================================
SEAM - the only module in browser-rpc that knows a browser library exists
===========================================================================

CloakBrowser (MIT, decision D1) is a Playwright drop-in with source-level
Chromium fingerprint changes. Everything specific to it is in four places here,
and nowhere else in the service:

    1. `_load_driver()`      the import, and the only place the name appears
    2. `_launch_context()`   how a context is opened with proxy, locale and zone
    3. `_read_fingerprint()` how the page reports what it claims to be
    4. `SIGN_SCRIPT`         asks the page's SDK to sign one request

Replacing the backend means writing a module with `start`, `close`, `info`,
`mint` and `open_signing_context` (see `backends/base.py`) and adding it to the
registry. Nothing else in the service changes.

Pinning
-------
At least three GitHub organizations publish repositories called cloakbrowser
with identical descriptions. The image therefore installs one repository at one
commit, passed in as a build argument, and stamps the pin into
`DTK_BROWSER_BACKEND_PIN` so /rpc/health can report exactly what is running
(docs/design/04-transport-signing.md).

Live verification
-----------------
Two things here can only be confirmed against a real page, per
docs/design/16-salvage-and-debug.md: the driver's exact entry point, and the
signing flow in `SIGN_SCRIPT`. Both are one edit each - that is the whole
reason they are isolated in this file. `docker/README.md` records the procedure.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import re
import shutil
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from browser_rpc.backends.base import (
    BackendInfo,
    MintedProfile,
    MintPlan,
    SignPlan,
)
from browser_rpc.errors import BackendFailure, BackendUnavailable
from browser_rpc.geo import GeoProfile
from browser_rpc.settings import Settings
from browser_rpc.validation import (
    COOKIE_DOMAINS,
    READY_PROBE_URLS,
    SIGNING_PAGE_URLS,
    Platform,
    ProxyEndpoint,
)

logger = logging.getLogger(__name__)

BACKEND_NAME = "cloak"

#: Import candidates, in order: the package, then the submodule that holds the
#: launch coroutines, in case a future build stops re-exporting them.
DRIVER_MODULES: tuple[str, ...] = ("cloakbrowser", "cloakbrowser.browser")

#: The coroutine this backend drives. Checked at startup so an image without the
#: browser says so through /rpc/health instead of failing the first mint.
#:
#: Not `async_playwright`: that is Playwright's entry point, and this adapter was
#: written against its shape before anyone ran it against cloakbrowser, which
#: exposes module-level `launch_*_async` coroutines instead.
LAUNCH_ENTRY_POINT: str = "launch_persistent_context_async"

#: Chromium flags. Kept short on purpose: every flag that changes behaviour is
#: also a flag that changes the fingerprint, and the point of this backend is a
#: browser that looks ordinary.
CHROMIUM_ARGS: tuple[str, ...] = (
    # NOT the place to control --disable-dev-shm-usage. An earlier comment here
    # claimed the flag was "deliberately absent" so that the container's 1GB
    # /dev/shm would be used; reading a real launch line on 2026-09-08 disproved
    # it. The driver passes --disable-dev-shm-usage itself, unconditionally, and
    # arguments appended here cannot remove one it already set. The measured
    # consequence: /dev/shm stays at 0% while every renderer's shared memory
    # lands in /tmp, where two warm contexts hold ~290MB each as deleted-but-open
    # files. Chromium then dies with SIGSEGV or "Target crashed", which reaches
    # the caller looking exactly like a platform block. So /tmp is the budget
    # that matters and it is sized in docker/compose.yml, not here.
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
)

#: Read once per session. `navigator.webdriver` is not asked for on purpose: the
#: backend's job is to make it absent, and reading it back here would only
#: confirm what the platform already checks.
FINGERPRINT_SCRIPT = """() => ({
  userAgent: navigator.userAgent,
  platform: navigator.platform,
  screen: `${screen.width}x${screen.height}`,
  language: navigator.language,
  languages: (navigator.languages || []).join(','),
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
})"""

COOKIE_SCRIPT = """() => Object.fromEntries(
  document.cookie.split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const index = part.indexOf('=');
      return index < 0 ? [part, ''] : [part.slice(0, index), part.slice(index + 1)];
    })
)"""

#: Injected before any page script runs, so it sits BENEATH the platform SDK.
#:
#: Both platforms sign by patching ``window.fetch`` and ``XMLHttpRequest``
#: rather than by exposing a signing function - verified in a live browser on
#: 2026-09-07: on douyin.com and tiktok.com all of fetch, XHR.open, XHR.send and
#: XHR.setRequestHeader are non-native, while ``window.byted_acrawler`` exposes
#: no ``sign`` at all and the SDK bundles contain no ``a_bogus`` string, because
#: the names are built at runtime inside a bytecode VM (``_$webrt_*``).
#:
#: So there is nothing to call. The only reliable way to obtain a signature is
#: to hand the SDK a request and observe what it produces. Capturing the natives
#: first means our recorder runs *under* the SDK's patch: the SDK rewrites the
#: URL, hands it down to what it believes is the browser, and we read it there
#: and abort - so a signature costs no upstream request.
#: NOTE ON POOLING: a signing context cannot be shared between identities.
#: Measured on 2026-09-08, against a live page rather than inferred:
#:
#: * `verifyFp` and `fp` are both the browser's `s_v_web_id` cookie, verbatim.
#: * The SDK reads it once, while the document loads, and caches it. Swapping
#:   the cookie on a warm page changes nothing; only a fresh document does.
#: * Seeding `verifyFp` in the query handed to the SDK does not work either -
#:   it overwrites the value with its own cached one.
#: * `uifid` matches the identity's `UIFID_TEMP` cookie, but it also matched
#:   across two independently minted contexts, so it is device-derived and is
#:   not what separates them. `verifyFp` is.
#:
#: So the only way to obtain a signature coherent with a given jar is to load
#: the page with that jar already installed, and `browser_rpc.service` keys its
#: warm slots on the jar for exactly that reason.
CAPTURE_INIT_SCRIPT = """
(() => {
  const nativeFetch = window.fetch;
  const nativeOpen = XMLHttpRequest.prototype.open;
  const state = { capture: false, url: null };
  window.__dtkSign = state;

  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url);
    if (state.capture) {
      state.url = url;
      // Never reaches the network: the SDK has already done its work by now.
      return Promise.reject(new DOMException('dtk-capture', 'AbortError'));
    }
    return nativeFetch.apply(this, arguments);
  };

  XMLHttpRequest.prototype.open = function (method, url) {
    if (state.capture) {
      state.url = url;
      throw new DOMException('dtk-capture', 'AbortError');
    }
    return nativeOpen.apply(this, arguments);
  };
})();
"""

#: How often to ask whether the page can sign yet, and how long to keep asking.
#:
#: A page is navigable long before it can sign: `domcontentloaded` fires and the
#: security bundle arrives afterwards. Signing inside that window fails with
#: "the SDK added nothing", which reads like the platform changed its algorithm
#: and is really just impatience - observed on TikTok, which was asked 1.1s
#: after its context opened and produced nothing, twice, and then returned a
#: 502 to the caller.
#:
#: Asking "has something wrapped window.fetch?" was tried first and is too weak:
#: TikTok satisfies it within a second, long before the signing code is there.
#: The only sound question is the one the caller will ask, so readiness is
#: probed by taking a throwaway signature - see `_await_sdk`.
#:
#: The probe BACKS OFF, and that is not a nicety. Each one dispatches a fetch
#: through the SDK's own patched stack and aborts it; at a flat 0.25s a page that
#: never becomes ready was probed ~100 times in its 25s budget and Chromium then
#: died with "Target crashed" - the readiness check destroying the page it was
#: waiting for. Backing off gives ~15 probes over the same budget while keeping
#: the ready case fast, which is what matters: Douyin measured ready at 1.1s.
READY_POLL_SECONDS: float = 0.25
READY_POLL_MAX_SECONDS: float = 2.5
READY_POLL_GROWTH: float = 1.6

#: Consecutive probe exceptions that mean the page is gone rather than slow.
READY_MAX_CONSECUTIVE_ERRORS: int = 3

#: Asks the SDK to sign one request and returns whatever it added to the query.
#:
#: The parameter names are deliberately NOT hardcoded: whatever the SDK appends
#: is what gets returned. Douyin currently adds a_bogus, verifyFp, fp, uifid,
#: timestamp and x-secsdk-web-signature; TikTok adds X-Gnarly, X-Dynosaur,
#: msToken and a vestigial one-character X-Bogus. Those sets have changed before
#: and will change again, and a table of expected names would silently drop
#: whatever was added next.
SIGN_SCRIPT = """
async (input) => {
  const state = window.__dtkSign;
  if (!state) return { error: 'capture shim not installed' };

  const target = input.query ? `${input.url}?${input.query}` : input.url;
  const before = new Set([...new URL(target).searchParams.keys()]);

  state.capture = true;
  state.url = null;
  try {
    await window.fetch(target, { method: input.method || 'GET' });
  } catch (e) {
    // AbortError is the expected path: the shim stopped it.
  } finally {
    state.capture = false;
  }

  if (!state.url) return { error: 'the SDK did not dispatch a request' };
  const signed = new URL(state.url, location.origin);
  const added = {};
  for (const [k, v] of signed.searchParams) {
    if (!before.has(k)) added[k] = v;
  }
  return Object.keys(added).length ? { params: added } : { error: 'the SDK added nothing' };
}
"""

USER_AGENT_MAJOR_RE = re.compile(r"(?:Chrome|CriOS|Firefox|Version)/(\d+)")


def browser_family_of(user_agent: str | None) -> str:
    """Classify a User-Agent into the families `dtk.core.types.BrowserFamily` knows."""
    agent = user_agent or ""
    if "Firefox/" in agent:
        return "firefox"
    if "Safari/" in agent and "Chrome/" not in agent and "Chromium/" not in agent:
        return "safari"
    return "chrome"


def browser_major_of(user_agent: str | None) -> int | None:
    """The major version claimed by a User-Agent, or None when it claims none.

    None is a real answer here: the pool refuses an identity whose emulation
    profile cannot be chosen rather than guessing one
    (docs/design/02-identity-pool.md).
    """
    match = USER_AGENT_MAJOR_RE.search(user_agent or "")
    return int(match.group(1)) if match else None


def proxy_settings(proxy: ProxyEndpoint | None) -> dict[str, str] | None:
    """Translate a proxy endpoint into the driver's proxy argument."""
    if proxy is None:
        return None
    settings = {"server": proxy.server}
    if proxy.username:
        settings["username"] = proxy.username
    if proxy.password:
        settings["password"] = proxy.password
    return settings


class CloakSigningContext:
    """A warm page holding the platform's signing JavaScript in memory."""

    def __init__(
        self, platform: Platform, context: Any, page: Any, profile_dir: str | None = None
    ) -> None:
        self._platform = platform
        self._context = context
        self._page = page
        #: Removed on close. A warm context owns its directory exclusively, so
        #: leaving it behind would slowly fill the profile tmpfs across the
        #: rebuild every 30 minutes.
        self._profile_dir = profile_dir
        self._closed = False

    @property
    def platform(self) -> Platform:
        return self._platform

    async def sign(self, plan: SignPlan) -> dict[str, str]:
        """Ask the page's own SDK to sign one request and return what it added.

        There is no signing function to call. Both platforms patch fetch and
        XMLHttpRequest and sign in the transport layer, so the request is the
        only interface. The capture shim installed before page scripts sits
        beneath that patch, which is what lets a signature be taken without the
        request actually leaving the browser.
        """
        if self._closed:
            raise BackendFailure("signing context is closed")

        payload = {
            "url": plan.url,
            "query": plan.query,
            "method": getattr(plan, "method", "GET") or "GET",
            "userAgent": plan.user_agent or "",
        }
        try:
            result = await self._page.evaluate(SIGN_SCRIPT, payload)
        except Exception as exc:
            raise BackendFailure(f"signing script failed: {exc}") from exc

        if not isinstance(result, dict) or result.get("error"):
            reason = (
                (result or {}).get("error", "no result")
                if isinstance(result, dict)
                else "no result"
            )
            raise BackendFailure(
                f"the {self._platform.value} SDK produced no signature ({reason}). "
                "Either the page had not finished loading its security bundle, or "
                "the platform changed how it signs; re-run the live analysis in "
                "docs/design/04-transport-signing.md before editing this file."
            )

        signed = _string_fields(result.get("params"))
        if not signed:
            raise BackendFailure(f"the {self._platform.value} SDK added no parameters")

        # Douyin no longer sends msToken in the query; TikTok's comes from the
        # SDK itself. Only fill it in when the SDK did not, and never overwrite.
        token = await self._ms_token()
        if token:
            signed.setdefault("msToken", token)
        return signed

    async def _ms_token(self) -> str | None:
        """msToken lives in the page's cookies, not in the signature call."""
        try:
            cookies = await self._page.evaluate(COOKIE_SCRIPT)
        except Exception as exc:
            logger.warning("backend.cloak.cookie_read_failed error=%s", exc)
            return None
        if not isinstance(cookies, dict):
            return None
        value = cookies.get("msToken")
        return str(value) if value else None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._context.close()
        except Exception as exc:
            logger.warning("backend.cloak.context_close_failed error=%s", exc)
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)


class CloakBackend:
    """`BrowserBackend` on top of CloakBrowser."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: Any = None
        self._version: str | None = None
        self._chromium_major: int | None = None

    # -- lifecycle --------------------------------------------------------

    @staticmethod
    def _load_driver() -> Any:
        """SEAM 1: import the browser library.

        Failure is `BackendUnavailable`, not a crash: the message has to say
        what to do, because the usual cause is an image built without a pinned
        commit rather than a bug.
        """
        last_error: Exception | None = None
        for name in DRIVER_MODULES:
            try:
                module = importlib.import_module(name)
            except ImportError as exc:
                last_error = exc
                continue
            if hasattr(module, LAUNCH_ENTRY_POINT):
                return module
            last_error = ImportError(f"{name} has no {LAUNCH_ENTRY_POINT} entry point")
        raise BackendUnavailable(
            "the CloakBrowser backend is not installed in this image. Rebuild with "
            "a pinned revision (CLOAKBROWSER_COMMIT=<sha> docker compose --profile "
            "browser build browser-rpc), or set DTK_BROWSER_BACKEND=fake for local "
            "testing with synthetic identities.",
            detail=str(last_error) if last_error else None,
        )

    async def start(self) -> None:
        """Resolve the driver. There is no session to open.

        CloakBrowser exposes module-level coroutines rather than the
        start/stop object Playwright uses, so a context is launched directly
        and there is nothing to hold open between mints. Confirming the entry
        point here rather than at the first mint keeps "no browser in this
        image" a startup fact that /rpc/health reports.
        """
        self._driver = self._load_driver()
        self._version = getattr(self._driver, "__version__", None)
        logger.info(
            "backend.cloak.started version=%s pin=%s", self._version, self._settings.backend_pin
        )

    async def close(self) -> None:
        self._driver = None

    def info(self) -> BackendInfo:
        return BackendInfo(
            name=BACKEND_NAME,
            version=self._version,
            chromium_major=self._chromium_major,
            pin=self._settings.backend_pin,
        )

    # -- contexts ---------------------------------------------------------

    async def _launch_context(
        self,
        profile_dir: str,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None,
        timeout_seconds: float,
    ) -> Any:
        """SEAM 2: open a browser context.

        Locale and timezone are set here rather than patched afterwards: a page
        that starts in one zone and changes to another has already reported the
        first one.
        """
        if self._driver is None:
            raise BackendUnavailable("browser driver is not started")

        # Parameter names verified against cloakbrowser 0.5.10, not assumed:
        # it takes `timezone`, not Playwright's `timezone_id`, and has no
        # extra_http_headers - Accept-Language is set on the context below,
        # where it also survives a navigation.
        options: dict[str, Any] = {
            "user_data_dir": profile_dir,
            "headless": self._settings.headless,
            "locale": geo.locale,
            "timezone": geo.timezone,
            "args": list(CHROMIUM_ARGS),
        }
        settings = proxy_settings(proxy)
        if settings:
            options["proxy"] = settings

        try:
            context = await self._driver.launch_persistent_context_async(**options)
            with contextlib.suppress(Exception):
                await context.set_extra_http_headers({"Accept-Language": geo.languages})
        except Exception as exc:
            raise BackendFailure(f"could not open a browser context: {exc}") from exc

        # Older drivers only take a per-call timeout; navigation passes one.
        with contextlib.suppress(AttributeError):
            context.set_default_timeout(timeout_seconds * 1000)
        return context

    @staticmethod
    async def _page_of(context: Any) -> Any:
        pages = getattr(context, "pages", None) or []
        page = pages[0] if pages else await context.new_page()
        # Before any page script, so the recorder is underneath the SDK's patch.
        await page.add_init_script(CAPTURE_INIT_SCRIPT)
        return page

    async def _read_fingerprint(self, page: Any) -> dict[str, str]:
        """SEAM 3: ask the page what it claims to be."""
        try:
            values = await page.evaluate(FINGERPRINT_SCRIPT)
        except Exception as exc:
            raise BackendFailure(f"could not read the page fingerprint: {exc}") from exc
        return _string_fields(values)

    # -- operations -------------------------------------------------------

    async def mint(self, plan: MintPlan) -> MintedProfile:
        context = await self._launch_context(
            plan.profile_dir, plan.geo, plan.proxy, plan.timeout_seconds
        )
        try:
            page = await self._page_of(context)
            try:
                await page.goto(
                    plan.landing_url,
                    wait_until="domcontentloaded",
                    timeout=plan.timeout_seconds * 1000,
                )
                # The guest cookies are set by scripts that run after the
                # document is ready, so the landing page needs a moment before
                # the jar is worth reading.
                await page.wait_for_timeout(3000)
            except Exception as exc:
                raise BackendFailure(f"could not load {plan.landing_url}: {exc}") from exc

            fingerprint = await self._read_fingerprint(page)
            cookies = await _collect_cookies(context)
            if not cookies:
                raise BackendFailure(
                    f"{plan.platform.value} set no cookies; the exit is most likely blocked"
                )

            user_agent = fingerprint.get("userAgent")
            major = browser_major_of(user_agent)
            if major is not None:
                self._chromium_major = major
            return MintedProfile(
                cookies=cookies,
                user_agent=user_agent,
                browser_family=browser_family_of(user_agent),
                browser_major=major,
                navigator_platform=fingerprint.get("platform"),
                screen=fingerprint.get("screen"),
                language=fingerprint.get("languages") or fingerprint.get("language"),
                timezone=fingerprint.get("timezone"),
            )
        finally:
            # Single use, always. A profile that survives its session carries
            # the identity's traces into the next one.
            try:
                await context.close()
            except Exception as exc:
                logger.warning("backend.cloak.mint_context_close_failed error=%s", exc)

    async def open_signing_context(
        self,
        platform: Platform,
        geo: GeoProfile,
        proxy: ProxyEndpoint | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> CloakSigningContext:
        page_url = SIGNING_PAGE_URLS[platform]
        # A warm context still harvests nothing, but it is no longer anonymous:
        # it carries the jar of the identity it signs for, so that `verifyFp`
        # and the cookies the request sends come from one session. The directory
        # is unique per context and not per platform, because Chromium locks a
        # persistent profile: sharing one name would make the second context of
        # a DTK_BROWSER_WARM_CONTEXTS=2 pool fail to launch, and would let a
        # rebuilt page collide with the one it is replacing. It being unique
        # also means no trace of the previous identity survives a rebind.
        profile_dir = f"{self._settings.profile_root}/warm-{platform.value}-{uuid.uuid4().hex[:8]}"
        context = await self._launch_context(
            profile_dir, geo, proxy, self._settings.context_open_timeout_seconds
        )
        try:
            jar = cookie_payload(platform, cookies)
            if jar:
                # Before the first navigation: the SDK reads these while the
                # document loads and caches what it read.
                await context.add_cookies(jar)
            page = await self._page_of(context)
            await page.goto(
                page_url,
                wait_until="domcontentloaded",
                timeout=self._settings.context_open_timeout_seconds * 1000,
            )
            await _await_sdk(page, platform, self._settings.sdk_ready_timeout_seconds)
            fingerprint = await self._read_fingerprint(page)
            major = browser_major_of(fingerprint.get("userAgent"))
            if major is not None:
                self._chromium_major = major
        except Exception as exc:
            try:
                await context.close()
            except Exception:
                logger.debug("backend.cloak.warm_context_close_failed", exc_info=True)
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise BackendFailure(
                f"could not warm a signing page for {platform.value}: {exc}"
            ) from exc
        return CloakSigningContext(platform, context, page, profile_dir)


async def _await_sdk(page: Any, platform: Platform, budget: float) -> None:
    """Block until the page can actually sign, by asking it to.

    The probe is a real signature over a throwaway query, which is the only
    question whose answer is not a proxy for the one that matters. It costs no
    upstream request - the capture shim aborts the fetch before it leaves the
    browser - so polling it is cheap.

    Not fatal on timeout. A context that still cannot sign is returned anyway,
    and the caller's own signature then fails with the SDK's message, which says
    more about what is wrong than "opening a context timed out" would.
    """
    probe = {"url": READY_PROBE_URLS[platform], "query": "dtk_ready=1", "method": "GET"}
    deadline = time.monotonic() + budget
    started = time.monotonic()
    delay = READY_POLL_SECONDS
    attempts = 0
    # A page whose target has crashed answers every probe with an exception and
    # will never recover, so waiting out the whole budget only delays the retry
    # that could still succeed. Consecutive, because one failure can just be a
    # navigation landing mid-probe.
    consecutive_errors = 0
    while True:
        attempts += 1
        try:
            result = await page.evaluate(SIGN_SCRIPT, probe)
            consecutive_errors = 0
            if isinstance(result, dict) and result.get("params"):
                logger.info(
                    "backend.cloak.sdk_ready platform=%s after=%.1fs probes=%d",
                    platform.value,
                    time.monotonic() - started,
                    attempts,
                )
                return
        except Exception as exc:  # a navigation mid-poll, or a crashed target
            consecutive_errors += 1
            logger.debug("backend.cloak.ready_probe_failed error=%s", exc)
            if consecutive_errors >= READY_MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    "backend.cloak.ready_page_gone platform=%s after=%.1fs probes=%d",
                    platform.value,
                    time.monotonic() - started,
                    attempts,
                )
                return
        if time.monotonic() >= deadline:
            logger.warning(
                "backend.cloak.sdk_never_ready platform=%s waited=%.0fs probes=%d",
                platform.value,
                budget,
                attempts,
            )
            return
        await asyncio.sleep(delay)
        delay = min(delay * READY_POLL_GROWTH, READY_POLL_MAX_SECONDS)


def cookie_payload(platform: Platform, cookies: Mapping[str, str] | None) -> list[dict[str, str]]:
    """Translate a flat jar into the driver's cookie shape.

    The pool stores cookies as bare name/value pairs - an identity presents one
    jar wherever it is used, so the domain each was first seen on is not kept -
    and a signing context has to put them back somewhere. `COOKIE_DOMAINS` is
    the registrable domain with a leading dot, the widest scope that is still
    correct and the one the platform's own pages use for the cookies that decide
    a signature.

    Empty names and empty values are dropped rather than sent: the driver
    rejects the whole batch if one entry is malformed, which would turn a single
    junk cookie into a total signing outage.
    """
    domain = COOKIE_DOMAINS[platform]
    return [
        {"name": name, "value": value, "domain": domain, "path": "/"}
        for name, value in (cookies or {}).items()
        if name and value
    ]


async def _collect_cookies(context: Any) -> dict[str, str]:
    """Read the context's cookie jar into a flat name/value mapping."""
    try:
        raw: Sequence[Mapping[str, Any]] = await context.cookies()
    except Exception as exc:
        raise BackendFailure(f"could not read cookies: {exc}") from exc
    cookies: dict[str, str] = {}
    for cookie in raw or ():
        name = cookie.get("name")
        value = cookie.get("value")
        if isinstance(name, str) and name and value is not None:
            cookies[name] = str(value)
    return cookies


def _string_fields(value: Any) -> dict[str, str]:
    """Keep the non-empty string fields of a JavaScript result object."""
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if item is not None and str(item).strip() != ""
    }


def build(settings: Settings) -> CloakBackend:
    """Factory used by the backend registry."""
    return CloakBackend(settings)


__all__ = [
    "BACKEND_NAME",
    "CAPTURE_INIT_SCRIPT",
    "CHROMIUM_ARGS",
    "SIGN_SCRIPT",
    "CloakBackend",
    "CloakSigningContext",
    "browser_family_of",
    "browser_major_of",
    "build",
    "proxy_settings",
]
