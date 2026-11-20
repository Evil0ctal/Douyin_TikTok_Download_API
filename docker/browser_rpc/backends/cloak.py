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
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

from browser_rpc.backends.base import (
    BackendInfo,
    MintedProfile,
    MintPlan,
    SignPlan,
)
from browser_rpc.errors import BackendFailure, BackendUnavailable, RpcError, Suspect
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
#: `deviceMemory` is Chromium-only and absent elsewhere; `_string_fields` drops
#: an undefined value, so a Firefox mint simply carries no memory and the query
#: keeps its default rather than claiming a machine nobody measured.
FINGERPRINT_SCRIPT = """() => ({
  userAgent: navigator.userAgent,
  platform: navigator.platform,
  screen: `${screen.width}x${screen.height}`,
  language: navigator.language,
  languages: (navigator.languages || []).join(','),
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  hardwareConcurrency: navigator.hardwareConcurrency,
  deviceMemory: navigator.deviceMemory,
})"""

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

#: The error `SIGN_SCRIPT` answers with when the page it runs in is no longer on
#: the platform. A wire value between the script and `CloakSigningContext`.
PAGE_LEFT_PLATFORM: Final = "page_left_platform"

#: Schemes a signing page can be on and still be the platform's page. Every
#: other one - about:blank, chrome-error://chromewebdata/, data: - has an opaque
#: origin, and none of them holds the SDK.
PAGE_SCHEMES: Final = frozenset({"http", "https"})

#: How much of a browser error reaches a message. Playwright appends a
#: JavaScript stack after the first line, and the first line is the finding.
ERROR_EXCERPT_CHARS: Final = 300

#: What Playwright says about a page whose renderer died. Kept in every crash
#: message of ours as well, because it is the phrase the troubleshooting guide
#: and the self-check tell an operator to look for: the crash event used to be
#: reported as "its renderer crashed", which nobody could find in the docs.
TARGET_CRASHED: Final = "Target crashed"

#: Every way the driver says a page or its browser died, as phrased by
#: Playwright 1.64. A renderer killed while ``goto`` waits is "Page crashed"
#: (``Page._didCrash``), not "Target crashed", which only the CDP crash error is
#: rewritten to; a dead browser process is the closed-target default. Matching
#: only the first blamed an OOM-killed page load on the exit (review of the
#: #830 fix), which is the misattribution `Suspect` was added to stop.
BROWSER_DEATH: Final = (
    TARGET_CRASHED,
    "Page crashed",
    "Navigation failed because page crashed",
    "Target page, context or browser has been closed",
    "Browser has been closed",
)


def names_a_dead_browser(text: str) -> bool:
    """Whether a driver message says the page or its browser died."""
    return any(phrase in text for phrase in BROWSER_DEATH)


#: What Playwright says when the document an evaluate ran in was replaced
#: under it: the page was navigating. On its own it names a driver detail, and
#: the page's URL can still be the platform's at that instant.
NAVIGATING: Final = "Execution context was destroyed"

#: How long a signature that met a navigation watches the driver for the page's
#: new address, and how often it looks. The driver learns it a moment after the
#: old document is torn down. Measured in dtk-sig2 on 2026-11-20, reloading a
#: warm TikTok page through a route that refuses every connection: a signature
#: sent 0.05s in met "Execution context was destroyed" while the page still
#: read as tiktok.com, and waiting for a load state returned at once without
#: changing that; from 0.15s on the page read as chrome-error://chromewebdata.
#: Bounded well below the sign budget.
NAVIGATION_SETTLE_SECONDS: Final = 1.0
NAVIGATION_POLL_SECONDS: Final = 0.05

#: How long cleaning up after a failed open may take. A failed open can be a
#: cancelled one, and a close that hangs would hold the cancellation up with it.
CLOSE_TIMEOUT_SECONDS: Final = 5.0

#: The session cookie a mint must come home with, per platform, as
#: ``(name, minimum length)``. Only TikTok has one: its ``msToken`` arrives twice,
#: and the short first value produces signatures the platform refuses. Douyin has
#: no equivalent - its signing inputs are present as soon as the jar is.
#:
#: It is also what a signing page loaded with such a token has to seal before
#: it counts as ready - see `_await_sdk` - because the token a page seals is the
#: one its request carries.
SESSION_COOKIE_MINIMUMS: Final[Mapping[Platform, tuple[str, int]]] = MappingProxyType(
    {Platform.TIKTOK: ("msToken", 144)}
)

#: How long to keep polling for it, and how often. The SDK normally replaces the
#: bootstrap token within a second of the flat wait above.
SESSION_COOKIE_WAIT_SECONDS: Final = 15.0
SESSION_COOKIE_POLL_SECONDS: Final = 0.5

#: Asks the SDK to sign one request and returns whatever it added to the query.
#:
#: The parameter names are deliberately NOT hardcoded: whatever the SDK appends
#: is what gets returned. Douyin currently adds a_bogus, verifyFp, fp, uifid,
#: timestamp and x-secsdk-web-signature; TikTok adds X-Gnarly, X-Dynosaur,
#: msToken and a vestigial one-character X-Bogus. Those sets have changed before
#: and will change again, and a table of expected names would silently drop
#: whatever was added next.
#:
#: A page that has left the platform says so instead of signing. Issue #830: a
#: warm TikTok page whose navigation after load failed sat on
#: chrome-error://chromewebdata/, and the shim was still there - init scripts
#: run on every document, error pages and about:blank included - so the guard
#: below passed and the script died resolving the captured URL against
#: `location.origin`, which on an opaque origin is the string "null". Every
#: signature from that page came back as "TypeError: Failed to construct 'URL':
#: Invalid base URL", on both of the service's attempts. The captured URL is
#: now resolved against the target, which is absolute and is all the
#: parameters were ever read from; and the page's whereabouts are checked
#: first, so the error names where the page went rather than a JavaScript
#: detail of what failed there.
SIGN_SCRIPT = """
async (input) => {
  const state = window.__dtkSign;
  if (!state) return { error: 'capture shim not installed' };

  const host = location.hostname;
  const onPlatform = (location.protocol === 'https:' || location.protocol === 'http:')
    && (!input.domain || host === input.domain || host.endsWith('.' + input.domain));
  if (!onPlatform) {
    return { error: 'page_left_platform', at: host ? `${location.protocol}//${host}` : location.protocol };
  }

  const target = input.query ? `${input.url}?${input.query}` : input.url;
  const before = new Set([...new URL(target).searchParams.keys()]);

  // The body as well as the verb: Douyin's signature hashes the body, so a
  // POST signed without one carries a signature over a request never sent.
  const init = { method: input.method || 'GET' };
  if (input.body != null) {
    init.body = input.body;
    if (input.contentType) init.headers = { 'Content-Type': input.contentType };
  }

  state.capture = true;
  state.url = null;
  try {
    await window.fetch(target, init);
  } catch (e) {
    // AbortError is the expected path: the shim stopped it.
  } finally {
    state.capture = false;
  }

  if (!state.url) return { error: 'the SDK did not dispatch a request' };
  const signed = new URL(state.url, input.url);
  const added = {};
  for (const [k, v] of signed.searchParams) {
    if (!before.has(k)) added[k] = v;
  }
  return Object.keys(added).length ? { params: added } : { error: 'the SDK added nothing' };
}
"""

#: Pauses the page's video now and whenever it starts again. Run once a warm
#: page can sign, because what the page does with the rest of its life is play
#: its feed to nobody.
#:
#: Both signing pages are the platforms' home feeds, which autoplay. The demo's
#: Douyin page, measured on 2026-10-21, had held a renderer at 84% and a GPU
#: process at 34% of a 2-core server for the 7.5 hours since it was opened -
#: video decode and software compositing - and no signature had been asked of it
#: in that time: a warm page is only rebuilt when it is used. Locally a TikTok
#: page held 195% the same way. Whether a feed autoplays depends on the platform
#: and the visitor's region, which is why it went unseen until then.
#:
#: Pausing is what a viewer does; nothing native is replaced, so there is no
#: patched `play` for the platform's scripts to find. Measured fixes that did
#: NOT work, so nobody tries them again: Page.setWebLifecycleState "frozen"
#: left the video playing and the GPU process at 185%, and
#: Emulation.setCPUThrottlingRate raised the renderer from 27% to 106%, because
#: Chromium throttles by spinning.
QUIET_MEDIA_SCRIPT = """
() => {
  const hush = (el) => { if (el instanceof HTMLMediaElement && !el.paused) el.pause(); };
  document.querySelectorAll('video, audio').forEach(hush);
  document.addEventListener('play', (event) => hush(event.target), true);
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


def platform_domain(platform: Platform) -> str:
    """The domain a platform's signing page has to stay on.

    The cookie domain rather than every domain the platform owns, because the
    page is only worth anything while it holds the jar, and the jar is
    installed on this domain: a Douyin page that ended up on iesdouyin.com would
    sign without the cookies the request will carry.
    """
    return COOKIE_DOMAINS[platform].lstrip(".")


def off_platform(platform: Platform, url: str | None) -> str | None:
    """Where a page is when that is not on its platform; None when it is.

    The answer is scheme and host only - "chrome-error://chromewebdata",
    "about:blank" - never a path or a query, which on a platform page can carry
    the identity's tokens. An unknown URL is not evidence of anything and
    answers None.
    """
    if not url:
        return None
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    domain = platform_domain(platform)
    if scheme in PAGE_SCHEMES and (host == domain or host.endswith("." + domain)):
        return None
    if host:
        return f"{scheme}://{host}"
    # about:blank has no host and its "path" is the whole point; a data: URL's
    # path is a document, so only about: gets one.
    return f"about:{parts.path}"[:32] if scheme == "about" else f"{scheme}:"


def left_platform_message(platform: Platform, where: str) -> str:
    """What to say about a signing page found somewhere other than its platform."""
    return (
        f"the {platform.value} signing page is no longer on {platform_domain(platform)} "
        f"(it is on {where}): a navigation after it loaded failed or was blocked - "
        "look at the exit it loads through (DTK_BROWSER_SIGN_PROXY_URL, the "
        "identity's proxy, or this host)"
    )


def left_platform(platform: Platform, where: str) -> BackendFailure:
    """The failure for a signing page found off its platform, attributed to the exit."""
    return BackendFailure(left_platform_message(platform, where), suspect=Suspect.EXIT)


def renderer_crashed(platform: Platform) -> BackendFailure:
    """The failure for a signing page whose renderer died."""
    return BackendFailure(
        f"the {platform.value} signing page's renderer crashed ({TARGET_CRASHED})",
        suspect=Suspect.BROWSER,
    )


def suspect_of(error: object) -> Suspect | None:
    """What a driver error implicates, read from its text; None when it says nothing."""
    if isinstance(error, RpcError):
        return error.suspect
    return Suspect.BROWSER if names_a_dead_browser(str(error)) else None


def _page_url(page: Any) -> str | None:
    """The page's current URL, read without running anything in it."""
    url = getattr(page, "url", None)
    return url if isinstance(url, str) else None


def _excerpt(error: object) -> str:
    """The first line of a browser error, bounded."""
    lines = str(error).strip().splitlines()
    return (lines[0] if lines else type(error).__name__)[:ERROR_EXCERPT_CHARS]


class CloakSigningContext:
    """A warm page holding the platform's signing JavaScript in memory."""

    def __init__(
        self,
        platform: Platform,
        context: Any,
        page: Any,
        profile_dir: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._platform = platform
        self._context = context
        self._page = page
        self._user_agent = user_agent
        #: Removed on close. A warm context owns its directory exclusively, so
        #: leaving it behind would slowly fill the profile tmpfs across the
        #: rebuild every 30 minutes.
        self._profile_dir = profile_dir
        self._closed = False
        self._crashed = False
        # A dead renderer leaves the page object behind, and every evaluate on
        # it answers "Target crashed" from then on. Hearing about it here is
        # what lets the service drop the page before a caller's attempt is
        # spent finding out. Stub pages in the tests have no events.
        with contextlib.suppress(AttributeError):
            page.on("crash", self._on_crash)

    @property
    def platform(self) -> Platform:
        return self._platform

    @property
    def user_agent(self) -> str | None:
        return self._user_agent

    @property
    def problem(self) -> str | None:
        """Why this page cannot sign any more, or None while it still can.

        Read without evaluating anything in the page: the service asks before
        it hands a warm page out, and asking must not cost a probe.
        """
        if self._closed:
            return "it is closed"
        if self._crashed:
            return f"its renderer crashed ({TARGET_CRASHED})"
        is_closed = getattr(self._page, "is_closed", None)
        if callable(is_closed) and is_closed():
            return "its page was closed"
        where = off_platform(self._platform, _page_url(self._page))
        if where is not None:
            return f"it left {platform_domain(self._platform)} for {where}"
        return None

    def _on_crash(self, *_: object) -> None:
        self._crashed = True

    async def _explain(self, error: Exception) -> BackendFailure:
        """What an evaluate that raised says about the page, rather than about the driver.

        The precheck in `sign` reads the URL the driver last reported, and that
        lags a navigation that is failing: in dtk-sig2 a page reloaded through a
        failing exit reported no problem right after the reload, while it was
        already being replaced by chrome-error://chromewebdata. A signature
        arriving in that window ran the script, and the attempt failed with
        "signing script failed: Page.evaluate: Execution context was destroyed,
        most likely because of a navigation" - spent, and its log line silent
        about the page having left the platform. So a navigation is waited out
        briefly and the page asked again where it is.
        """
        detail = _excerpt(error)
        if self._crashed or names_a_dead_browser(detail):
            return renderer_crashed(self._platform)
        navigating = NAVIGATING in detail
        where = (
            await self._landing()
            if navigating
            else off_platform(self._platform, _page_url(self._page))
        )
        if where is not None:
            return left_platform(self._platform, where)
        if navigating:
            return BackendFailure(
                f"the {self._platform.value} signing page was navigating when it was asked "
                f"to sign, and is still on {platform_domain(self._platform)} ({detail})"
            )
        return BackendFailure(f"signing script failed: {detail}", suspect=suspect_of(error))

    async def _landing(self) -> str | None:
        """Where a navigating page went, once the driver has heard; None if still home.

        Read from the driver's record rather than by evaluating anything: the
        page is between documents, and an evaluate there is what just failed.
        """
        deadline = time.monotonic() + NAVIGATION_SETTLE_SECONDS
        while True:
            where = off_platform(self._platform, _page_url(self._page))
            if where is not None or time.monotonic() >= deadline:
                return where
            await asyncio.sleep(NAVIGATION_POLL_SECONDS)

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
        where = off_platform(self._platform, _page_url(self._page))
        if where is not None:
            raise left_platform(self._platform, where)
        if self._crashed:
            raise renderer_crashed(self._platform)

        # No User-Agent in here. One used to be sent and `SIGN_SCRIPT` never
        # read it: the page signs under its own `navigator.userAgent` whatever
        # the plan says, which is why the service refuses a plan naming one the
        # platform would not accept before it gets this far (#832).
        payload = {
            "url": plan.url,
            "query": plan.query,
            "method": plan.method or "GET",
            "body": plan.body,
            "contentType": plan.content_type,
            "domain": platform_domain(self._platform),
        }
        try:
            result = await self._page.evaluate(SIGN_SCRIPT, payload)
        except Exception as exc:
            raise await self._explain(exc) from exc

        if isinstance(result, dict) and result.get("error") == PAGE_LEFT_PLATFORM:
            # The script's own reading of `location`, for the case the URL the
            # driver last reported had not caught up with.
            raise left_platform(self._platform, str(result.get("at") or "unknown")[:64])
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                (result or {}).get("error", "no result")
                if isinstance(result, dict)
                else "no result"
            )
            # For whoever edits this file: re-run the live analysis in
            # docs/design/04-transport-signing.md before touching the script.
            # Not in the message, which an operator reads in a log or a 502
            # body, where that path does not exist (docs/ is not shipped).
            raise BackendFailure(
                f"the {self._platform.value} SDK produced no signature ({reason}). "
                "Either the page had not finished loading its security bundle, or "
                "the platform changed how it signs; if this exit loads the site, "
                "open an issue."
            )

        signed = _signed_fields(result.get("params"))
        if not any(signed.values()):
            raise BackendFailure(f"the {self._platform.value} SDK added no parameters")
        # NOTHING is added here, and nothing taken away. What the SDK put in the
        # URL is the whole answer, empty values included.
        #
        # Issue #832: a TikTok page seals `msToken=` with no value while its
        # SDK has no token of its own yet, and X-Gnarly's query hash covers
        # that `&msToken=`. This used to go through the copier the fingerprint
        # uses, which drops an empty value as "not reported", so the request
        # went out without the pair its seal names. Kept now for fidelity: it
        # was not shown to change TikTok's answer - replayed under one seal on
        # 2026-11-20, the query with and without the pair answered alike, 8/8
        # each, and what decided the outcome was whether the msToken in the
        # Cookie header was live.
        #
        # There used to be a "helpful" fallback that appended the page's msToken
        # cookie when the SDK had not put one in the query. It is a signature
        # forgery: Douyin's a_bogus covers the exact query string, so a
        # parameter the SDK did not sign turns a valid signature into
        # "403 Blocked by ArgusSecurityPlugin Sign Invalid".
        #
        # It was invisible at first and then fatal, which is the worst shape a
        # bug can have. A freshly opened page has no msToken cookie, so the
        # fallback did nothing and signing worked; Douyin's own scripts set one
        # after the page has been alive a while, and from that moment every
        # signature from that page was rejected. Measured on 2026-09-08: the
        # same warm context went from 8/8 returning data to 6/6 refused, with
        # msToken the only difference in the parameter set.
        return signed

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
        self._user_agent: str | None = None

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
            "the CloakBrowser backend is not installed in this image. Rebuild it "
            "with Compose (docker compose --profile browser build browser-rpc), "
            "which installs the revision docker/compose.yml pins - a plain docker "
            "build needs --build-arg CLOAKBROWSER_COMMIT=<sha> - or set "
            "DTK_BROWSER_BACKEND=fake for local testing with synthetic identities.",
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
            user_agent=self._user_agent,
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

        # Parameter names verified against cloakbrowser 0.5.10 and 0.6.3, not assumed:
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

    async def _await_session_cookie(
        self, context: Any, plan: MintPlan, page: Any = None
    ) -> dict[str, str]:
        """Read the jar once the platform's session token is the usable one.

        The flat wait above is enough for the guest cookies, but not always for
        TikTok's ``msToken``: the document sets a short bootstrap value and the
        SDK replaces it a moment later with the one its own requests carry. Both
        are called ``msToken`` and both look plausible in the pool.

        Measured on 2026-09-08 across a 13-identity pool: every identity minted
        with a 152-character token signed successfully, and every identity minted
        with a 128-character one was refused - with a correct signature, because
        the token is inside the sealed bytes. Nothing downstream can tell the two
        apart, so an identity minted too early is a permanently broken pool entry
        that reports as a signing failure. Failing the mint is the cheaper
        outcome, and the caller already retries.

        A landing page that has left the platform ends the wait at once: no
        token can arrive on chrome-error://, and the 15 seconds spent finding
        that out used to end in "msToken is 0 characters", which reads as a
        timing problem and is an exit problem (issue #830).
        """
        minimum = SESSION_COOKIE_MINIMUMS.get(plan.platform)
        deadline = time.monotonic() + SESSION_COOKIE_WAIT_SECONDS
        cookies = await _collect_cookies(context)
        if minimum is None:
            return cookies
        name, length = minimum
        where = off_platform(plan.platform, _page_url(page))
        while len(cookies.get(name, "")) < length and where is None and time.monotonic() < deadline:
            await asyncio.sleep(SESSION_COOKIE_POLL_SECONDS)
            cookies = await _collect_cookies(context)
            where = off_platform(plan.platform, _page_url(page))
        held = len(cookies.get(name, ""))
        if held < length:
            raise BackendFailure(
                _session_cookie_shortfall(plan.platform, name, held, length, where)
            )
        return cookies

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
            cookies = await self._await_session_cookie(context, plan, page)
            if not cookies:
                raise BackendFailure(
                    f"{plan.platform.value} set no cookies; the exit is most likely blocked"
                )

            user_agent = fingerprint.get("userAgent")
            major = browser_major_of(user_agent)
            if major is not None:
                self._chromium_major = major
            if user_agent:
                self._user_agent = user_agent
            return MintedProfile(
                cookies=cookies,
                user_agent=user_agent,
                browser_family=browser_family_of(user_agent),
                browser_major=major,
                navigator_platform=fingerprint.get("platform"),
                screen=fingerprint.get("screen"),
                language=fingerprint.get("languages") or fingerprint.get("language"),
                timezone=fingerprint.get("timezone"),
                hardware_concurrency=_as_int(fingerprint.get("hardwareConcurrency")),
                device_memory=_as_int(fingerprint.get("deviceMemory")),
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
        try:
            context = await self._launch_context(
                profile_dir, geo, proxy, self._settings.context_open_timeout_seconds
            )
        except BaseException:
            # Chromium creates the directory before it fails to start, and
            # nothing else knows its name. Seen in dtk-sig2 under memory
            # pressure: "could not open a browser context: ... has been closed"
            # left a 13-16 MiB warm-douyin-* behind on the 1g profile tmpfs, and
            # a run of failed launches fills it.
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise
        try:
            jar = cookie_payload(platform, cookies)
            if jar:
                # Before the first navigation: the SDK reads these while the
                # document loads and caches what it read.
                await context.add_cookies(jar)
            page = await self._page_of(context)
            try:
                await page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=self._settings.context_open_timeout_seconds * 1000,
                )
            except Exception as exc:
                # A navigation that failed is the exit's doing unless the
                # browser says it died; either way the first line is the finding
                # and Playwright's call log after it is not.
                raise BackendFailure(
                    f"could not load {page_url}: {_excerpt(exc)}",
                    suspect=suspect_of(exc) or Suspect.EXIT,
                ) from exc
            await _await_sdk(
                page,
                platform,
                self._settings.sdk_ready_timeout_seconds,
                expect_token=_holds_session_token(platform, cookies),
            )
            fingerprint = await self._read_fingerprint(page)
            # Kept whole now, not just its major: it is the User-Agent every
            # signature from this page carries (#832).
            user_agent = fingerprint.get("userAgent")
            major = browser_major_of(user_agent)
            if major is not None:
                self._chromium_major = major
            if user_agent:
                self._user_agent = user_agent
            await _send_to_background(context, page, platform)
        except BaseException as exc:
            # BaseException, so a service-side timeout - which cancels this
            # coroutine - closes the browser it launched too. It used to skip
            # this block entirely and leave a live Chromium and its profile
            # behind for every open that ran out of time.
            await _abandon(context, profile_dir)
            if not isinstance(exc, Exception):
                raise
            raise _warm_failure(platform, exc) from exc
        return CloakSigningContext(platform, context, page, profile_dir, user_agent)


async def _send_to_background(context: Any, page: Any, platform: Platform) -> None:
    """Leave a warm page the way a visitor leaves a tab they are not watching.

    Two steps, because each one stops what the other does not. Pausing the media
    stops video decode and compositing - TikTok's page went from 195% to 2%. A
    blank tab brought to the front makes the warm page a background tab, which
    Chromium stops rendering: Douyin's page kept its loading spinners and other
    CSS animations at 60 layouts a second with the video paused, and 1 a second
    once behind a tab - 2-6% in all, against 60-80% as loaded. The page still
    reports itself visible, because the driver emulates focus on every page, and
    it signs from the background as fast as from the front: 8ms either way.

    Neither step is allowed to cost a working page. A failure here means a
    warm page that burns CPU, which is what it did before this existed; it
    signs all the same.
    """
    try:
        await page.evaluate(QUIET_MEDIA_SCRIPT)
    except Exception as exc:
        logger.warning("backend.cloak.quiet_media_failed platform=%s error=%s", platform.value, exc)
    try:
        cover = await context.new_page()
        await cover.bring_to_front()
    except Exception as exc:
        logger.warning("backend.cloak.background_failed platform=%s error=%s", platform.value, exc)


async def _await_sdk(
    page: Any, platform: Platform, budget: float, *, expect_token: bool = False
) -> None:
    """Block until the page can actually sign, by asking it to; raise if it never does.

    The probe is a real signature over a throwaway query, which is the only
    question whose answer is not a proxy for the one that matters. It costs no
    upstream request - the capture shim aborts the fetch before it leaves the
    browser - so polling it is cheap.

    Fatal, and it was not always. This used to log and return when the budget
    ran out or the page stopped answering, on the reasoning that the caller's
    own signature would then fail with the SDK's message, which says more than
    "opening a context timed out". The point about the message was right and the
    conclusion was not. Issue #830: a TikTok page that left tiktok.com for
    chrome-error:// after loading answered every probe with an exception, was
    handed out after 1.3s anyway, and every signature from it failed with a
    JavaScript TypeError that named nothing the operator could act on - on both
    of the service's attempts, each of which paid for a full open first. A
    rate-limit page that never grew an SDK went out the same way and failed as
    "the platform changed how it signs". So the failure is raised here, carrying
    the probe's last answer - which keeps what the old reasoning wanted - and it
    reaches the service as a failed open, whose retry opens a fresh page.

    On a page loaded with a jar that holds a usable msToken (``expect_token``)
    a probe that signs is not yet enough: the msToken it seals has to be
    usable too (`SESSION_COOKIE_MINIMUMS`). Issue #832, measured on 2026-11-20:
    such a page seals an EMPTY msToken for its first 0.1-1.4s - longer through
    the service, at least 4s on two of four rebinds - because the SDK seals
    from its own storage, which is still empty. This answered "ready" on the
    first probe that signed, so the first caller after every rebind got one of
    those, and its request went out with whatever msToken the jar held: an
    empty seal overrides nothing. Held to it, six such pages sealed a
    152-character token within 0.8-3.5s, and the request then carries the
    page's fresh token in its Cookie header (`dtk.signing.rpc`), whatever state
    the jar's own token is in.

    That, and not the empty seal, is what the gate buys. The token in the
    Cookie header is what TikTok judges: replayed under one empty seal, the
    jar's live token answered 8/8 and a stale one was refused; the 4/4
    refusals first measured on such early signatures, with what was taken for
    a live jar token, were not reproduced. A probe that seals no msToken at
    all is judged as before.

    Not for any other page. An anonymous one (prewarm's, the self-check's
    reference) and one for a jar with no token each sealed only the
    128-character bootstrap token for the whole 25s, once, and holding them to
    it turns what they always answered at once into a 25s failure - which
    nothing measured says is the better outcome for them. The cost is known:
    the first request after a rebind for a jar with no token is refused - 0/6
    in the #832 review, in every query and cookie variant - as it was before
    the gate, and puts the identity in cooldown; the same page signed about 60s
    later sealed a 152-character token and answered 2/2. A short bounded wait
    would not reach that, and a 60s one would hold every such rebind past the
    caller's signing budget. Such a jar is a mint that did not finish, and
    the remedy is the one for that.
    """
    domain = platform_domain(platform)
    probe = {
        "url": READY_PROBE_URLS[platform],
        "query": "dtk_ready=1",
        "method": "GET",
        "domain": domain,
    }
    deadline = time.monotonic() + budget
    started = time.monotonic()
    delay = READY_POLL_SECONDS
    attempts = 0
    last_answer = "no probe answered"
    #: The length of the msToken the last probe sealed when that was all that
    #: kept the page from being ready; None while no probe has signed.
    sealed_short: int | None = None
    # A page whose target has crashed answers every probe with an exception and
    # will never recover, so waiting out the whole budget only delays the retry
    # that could still succeed. Consecutive, because one failure can just be a
    # navigation landing mid-probe.
    consecutive_errors = 0
    while True:
        attempts += 1
        # Before the probe and from the driver's own record, so a page that has
        # already gone is named without running anything in it.
        where = off_platform(platform, _page_url(page))
        if where is not None:
            _log_left(platform, where, started, attempts)
            raise left_platform(platform, where)
        try:
            result = await page.evaluate(SIGN_SCRIPT, probe)
        except Exception as exc:  # a navigation mid-poll, or a crashed target
            consecutive_errors += 1
            last_answer = _excerpt(exc)
            logger.debug("backend.cloak.ready_probe_failed error=%s", last_answer)
            if consecutive_errors >= READY_MAX_CONSECUTIVE_ERRORS:
                elapsed = time.monotonic() - started
                logger.warning(
                    "backend.cloak.ready_page_gone platform=%s after=%.1fs probes=%d",
                    platform.value,
                    elapsed,
                    attempts,
                )
                where = off_platform(platform, _page_url(page))
                if where is not None:
                    raise left_platform(platform, where) from exc
                raise BackendFailure(
                    f"the {platform.value} signing page stopped answering after "
                    f"{elapsed:.1f}s and {attempts} probes: {last_answer}",
                    suspect=suspect_of(exc),
                ) from exc
        else:
            consecutive_errors = 0
            params = result.get("params") if isinstance(result, dict) else None
            sealed_short = (
                _short_session_token(platform, params)
                if expect_token and isinstance(params, dict)
                else None
            )
            if params and sealed_short is None:
                logger.info(
                    "backend.cloak.sdk_ready platform=%s after=%.1fs probes=%d",
                    platform.value,
                    time.monotonic() - started,
                    attempts,
                )
                return
            if isinstance(result, dict) and result.get("error") == PAGE_LEFT_PLATFORM:
                where = str(result.get("at") or "unknown")[:64]
                _log_left(platform, where, started, attempts)
                raise left_platform(platform, where)
            last_answer = (
                str(result.get("error"))[:ERROR_EXCERPT_CHARS]
                if isinstance(result, dict) and result.get("error")
                else "no result"
            )
        if time.monotonic() >= deadline:
            logger.warning(
                "backend.cloak.sdk_never_ready platform=%s waited=%.0fs probes=%d sealed_token=%s",
                platform.value,
                budget,
                attempts,
                "-" if sealed_short is None else sealed_short,
            )
            if sealed_short is not None:
                raise _unusable_sealed_token(platform, sealed_short, budget, attempts)
            # Not the exit's fault by default, and the wording must not make it
            # so. A page that stays on the platform and never signs is either
            # one whose bundle never arrived or one whose bundle no longer
            # signs this way; the second is the platform moving, and only the
            # live analysis can tell it apart from the first. The sentence is
            # the one `sign` gives an SDK that added nothing, which is where a
            # page that never signed used to end up saying it, before this was
            # fatal (a review of #830's fix found it pinned on the exit
            # instead). The quoted answer goes last:
            # it can run to 300 characters, and a reader that keeps only the
            # head of this message should lose the quote, not the remedy.
            raise BackendFailure(
                f"the {platform.value} signing page could not sign within {budget:.0f}s "
                f"({attempts} probes): either its security bundle did not load, or the "
                "platform changed how it signs - if this exit loads the site, open an "
                f"issue. Its last answer: {last_answer}",
                suspect=Suspect.BUNDLE,
            )
        await asyncio.sleep(delay)
        delay = min(delay * READY_POLL_GROWTH, READY_POLL_MAX_SECONDS)


async def _abandon(context: Any, profile_dir: str) -> None:
    """Close a context whose open failed, and remove its profile, whatever happens."""
    try:
        await asyncio.wait_for(context.close(), CLOSE_TIMEOUT_SECONDS)
    except Exception:
        logger.debug("backend.cloak.warm_context_close_failed", exc_info=True)
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def _warm_failure(platform: Platform, error: Exception) -> BackendFailure:
    """A failed open, carrying what the failure implicates.

    Our own messages pass through whole - they are one sentence, and the
    excerpt limit would cut the remedy off the end of the longest. A driver's
    message is cut to its first line.
    """
    detail = error.message if isinstance(error, RpcError) else _excerpt(error)
    return BackendFailure(
        f"could not warm a signing page for {platform.value}: {detail}",
        suspect=suspect_of(error),
    )


def _log_left(platform: Platform, where: str, started: float, attempts: int) -> None:
    logger.warning(
        "backend.cloak.page_left_platform platform=%s at=%s after=%.1fs probes=%d",
        platform.value,
        where,
        time.monotonic() - started,
        attempts,
    )


def _session_cookie_shortfall(
    platform: Platform, name: str, held: int, length: int, where: str | None
) -> str:
    """Why a mint came home without a usable session token, by how short it fell.

    The three cases point at different things. Zero means nothing of the
    platform's app answered at all - measured on a healthy TikTok load, the
    first token arrives about 1.9s in on an app API response, not on the HTML
    document - so the exit is the suspect. A short token means the app answered
    and the SDK had not replaced its bootstrap value yet. A page somewhere else
    entirely says where it went.
    """
    if where is not None:
        return (
            f"{platform.value} {name} is {held} characters: the landing page left "
            f"{platform_domain(platform)} for {where} before the platform's app answered - "
            "look at the exit this mint goes through"
        )
    shortfall = (
        f"{platform.value} {name} is {held} characters after "
        f"{SESSION_COOKIE_WAIT_SECONDS:.0f}s; the usable token is at least {length}"
    )
    if held == 0:
        return (
            f"{shortfall}. None arrived at all, and the first one comes on an app API "
            "response rather than on the page, so nothing of the platform's app answered "
            "through this exit"
        )
    return f"{shortfall}; only the bootstrap token arrived and the SDK never replaced it"


def _holds_session_token(platform: Platform, cookies: Mapping[str, str] | None) -> bool:
    """Whether a jar carries the platform's session token at a usable length."""
    minimum = SESSION_COOKIE_MINIMUMS.get(platform)
    if minimum is None:
        return False
    name, length = minimum
    return len((cookies or {}).get(name) or "") >= length


def _short_session_token(platform: Platform, params: Mapping[str, Any]) -> int | None:
    """The length of a sealed session token too short to use; None when it is fine.

    None as well when the platform has no such token or the signature sealed
    none at all: absence is not a short token, and a page whose SDK stopped
    appending one should keep signing rather than never be ready.
    """
    minimum = SESSION_COOKIE_MINIMUMS.get(platform)
    if minimum is None:
        return None
    name, length = minimum
    value = params.get(name)
    if value is None:
        return None
    held = len(str(value))
    return held if held < length else None


def _unusable_sealed_token(
    platform: Platform, held: int, budget: float, attempts: int
) -> BackendFailure:
    """The failure for a page that signs but never seals a usable session token.

    Worded like `_session_cookie_shortfall`, because it is the same token
    failing to arrive: none at all means the platform's app never answered
    through this exit, a short one that the bootstrap value was never replaced.
    The probe count is in the "(N probes)" shape the service strips when it
    compares two attempts' failures.
    """
    name, length = SESSION_COOKIE_MINIMUMS[platform]
    head = (
        f"the {platform.value} signing page could not seal a usable {name} within "
        f"{budget:.0f}s ({attempts} probes): it seals one of {held} characters, and the "
        f"usable token is at least {length}"
    )
    if held == 0:
        return BackendFailure(
            f"{head}. None has arrived, and it comes on an app API response, so nothing of "
            "the platform's app answered through this exit",
            suspect=Suspect.EXIT,
        )
    return BackendFailure(
        f"{head}; only the bootstrap token arrived and the SDK never replaced it",
        suspect=Suspect.BUNDLE,
    )


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


def _as_int(value: str | None) -> int | None:
    """A numeric fingerprint field, or None when the browser did not report one."""
    if value is None:
        return None
    try:
        # `navigator.deviceMemory` is a float in the spec (0.25, 0.5, ...), and
        # every value the platforms echo back is a whole number of GiB.
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _string_fields(value: Any) -> dict[str, str]:
    """Keep the non-empty string fields of a JavaScript result object.

    For what a page reports about itself, where an empty value means it did
    not say. Never for what an SDK signed - see `_signed_fields`.
    """
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if item is not None and str(item).strip() != ""
    }


def _signed_fields(value: Any) -> dict[str, str]:
    """The parameters an SDK appended, exactly as appended, empty values included.

    An empty value is still a parameter: the signature covers ``name=`` like
    any other pair (#832). Only an undefined one is dropped, which a URL
    cannot carry anyway.
    """
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if item is not None and not isinstance(item, dict | list)
    }


def build(settings: Settings) -> CloakBackend:
    """Factory used by the backend registry."""
    return CloakBackend(settings)


__all__ = [
    "BACKEND_NAME",
    "CAPTURE_INIT_SCRIPT",
    "CHROMIUM_ARGS",
    "PAGE_LEFT_PLATFORM",
    "SIGN_SCRIPT",
    "CloakBackend",
    "CloakSigningContext",
    "browser_family_of",
    "browser_major_of",
    "build",
    "left_platform",
    "left_platform_message",
    "off_platform",
    "platform_domain",
    "proxy_settings",
    "renderer_crashed",
    "suspect_of",
]
