"""The browser-rpc signer: fallback, and the yardstick the native path is held to.

browser-rpc runs CloakBrowser with the platform's own JavaScript loaded in a warm
page, so it follows the platform automatically when the algorithm changes. It
costs hundreds of milliseconds, which is why it is the fallback and not the
default (docs/design/04-transport-signing.md).

Wire contract, from doc 04::

    POST /rpc/sign    {platform, method, url, query, params, user_agent}
                    -> {a_bogus, x_bogus, ms_token, signature, ...}
    GET  /rpc/health -> {warm_contexts, backend_version, uptime}

``query`` is this client's addition to the documented shape: it is the exact
byte sequence to sign. Handing the browser a parameter map instead would let the
two sides disagree about ordering and escaping, and a signature over almost the
right bytes is simply a wrong signature. It is built by
:func:`dtk.signing.base.encode_query`, the same function ``NativeSigner`` uses,
so a request signed either way goes out over the wire byte for byte identical.

``body`` and ``content_type`` are the same idea for a POST: a_bogus hashes the
body, so the page has to be handed the one the request will carry. A service
that predates them ignores both and signs an empty body, so it is asked to
confirm with ``body_signed`` - see :meth:`RpcSigner.sign`.

Offline seam
------------
The RPC service is the external dependency of this module. Everything else -
request shaping, response mapping, health interpretation, error classification -
is exercised by tests against a stub transport.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Final
from urllib.parse import quote

import httpx

from dtk.core.errors import DtkError, SigningFailed
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.signing.base import (
    DEFAULT_ALGORITHMS,
    MS_TOKEN_PARAM,
    SIGNER_BROWSER,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    SignerHealth,
    SigningFingerprint,
    SigningSession,
    encode_query,
)

logger = get_logger(__name__)

SIGN_PATH = "/rpc/sign"
HEALTH_PATH = "/rpc/health"

#: browser-rpc's error code, and the `SigningFailed` cause, for an identity
#: whose User-Agent is not the one the signing page runs under. Not a failure
#: of the browser: the registry signs such an identity natively (#832).
USER_AGENT_MISMATCH: Final = "user_agent_mismatch"

#: The platforms that refuse a request whose User-Agent is not, byte for byte,
#: the one its signature sealed - browser-rpc's ``UA_BOUND_PLATFORMS``. TikTok:
#: one digit of the Chrome version is enough (measured 2026-09-07 and
#: 2026-11-20). Douyin is not one of them: on 2026-11-20 it answered a page's
#: signature for identities on Chrome/145, /130 and Edge/146, and refused it
#: only for a query naming another operating system - which browser-rpc
#: refuses to sign (409) - so a Douyin answer naming the page's own,
#: different User-Agent is used as it is.
UA_BOUND_PLATFORMS: Final[frozenset[Platform]] = frozenset({Platform.TIKTOK})


def is_user_agent_mismatch(error: BaseException) -> bool:
    """Whether ``error`` says browser-rpc cannot sign under this identity's User-Agent."""
    return isinstance(error, DtkError) and error.details.get("cause") == USER_AGENT_MISMATCH


#: Response field to the query parameter it becomes, most specific first. The
#: first match decides which algorithm the response is reported as.
RESPONSE_FIELDS: Mapping[str, SignatureAlgorithm] = {
    "a_bogus": SignatureAlgorithm.A_BOGUS,
    "x_bogus": SignatureAlgorithm.X_BOGUS,
    "signature": SignatureAlgorithm.SIGNATURE,
}

#: Legacy aliases: older service builds used snake_case for a few fields.
PASSTHROUGH_FIELDS: Mapping[str, str] = {"ms_token": "msToken"}

#: Parameters that identify the caller but do not prove the request. Listed
#: negatively on purpose: anything NOT here is treated as a possible signature,
#: so a newly introduced one is honoured rather than discarded.
NON_SIGNATURE_PARAMS: frozenset[str] = frozenset(
    {"msToken", "verifyFp", "fp", "uifid", "timestamp"}
)

#: Every other key the service returns is copied into the query under its own
#: name. Naming them would silently drop whatever the platform adds next, and it
#: adds a lot: verified live on 2026-09-07, Douyin signs with a_bogus, verifyFp,
#: fp, uifid, timestamp and x-secsdk-web-signature, while TikTok uses X-Gnarly,
#: X-Dynosaur and msToken. An earlier version of this module recognised four
#: names and would have discarded most of both sets.

#: Parameters whose value is percent-encoded on the way into the URL. X-Bogus
#: and msToken go in raw, which is how both platforms' own pages send them.
ENCODED_PARAMS: frozenset[str] = frozenset(
    {SignatureAlgorithm.A_BOGUS.value, SignatureAlgorithm.SIGNATURE.value}
)


class RpcSigner:
    """Signs by asking browser-rpc to run the platform's own code."""

    name = SIGNER_BROWSER

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        platform: Platform | None = None,
        algorithm: SignatureAlgorithm | None = None,
        timeout: float | Callable[[], float] = 10.0,
    ) -> None:
        self._client = client
        self.base_url = base_url.rstrip("/")
        #: Overrides the platform inferred from the request URL, for hosts the
        #: inference does not know.
        self.platform = platform
        #: Overrides the query encoding implied by the platform. Only the
        #: encoding: which signature the browser actually returns is up to the
        #: browser, and is read back off the response.
        self.algorithm = algorithm
        # Resolved per call, so signing.rpc_timeout_seconds takes effect in a
        # running worker instead of only at the next restart.
        self._timeout: Callable[[], float] = timeout if callable(timeout) else (lambda: timeout)

    @property
    def timeout(self) -> float:
        """The ceiling in force right now, for one signing or health call."""
        return self._timeout()

    async def sign(
        self,
        spec: RequestSpec,
        identity_fingerprint: SigningFingerprint,
        session: SigningSession | None = None,
    ) -> SignedParams:
        platform = self.platform or spec.platform
        if platform is None:
            raise SigningFailed(f"cannot tell which platform {spec.url} belongs to")

        params = dict(spec.params or {})
        # The same encoding NativeSigner would have used, so that whichever
        # signer handles a request the platform receives the same bytes and the
        # shadow comparison compares signatures over the same input.
        query = encode_query(
            params, self.algorithm or DEFAULT_ALGORITHMS.get(platform, SignatureAlgorithm.A_BOGUS)
        )
        payload = {
            "platform": platform.value,
            "method": spec.method,
            "url": spec.url,
            "query": query,
            "params": params,
            "user_agent": identity_fingerprint.user_agent,
            # The identity's own session. browser-rpc loads a page with these
            # cookies and signs there, so `verifyFp` names the same
            # `s_v_web_id` the request will carry. Sending nothing here is what
            # made every payload come back withheld: the browser signed in its
            # own warm session and the request went out with another one's jar.
            "cookies": dict(session.cookies) if session else {},
            "proxy_url": session.proxy_url if session else None,
            "identity_id": session.identity_id if session else None,
        }
        if spec.body:
            payload["body"] = spec.body.decode("utf-8")
            payload["content_type"] = (spec.headers or {}).get("Content-Type")

        started = time.monotonic()
        try:
            response = await self._client.post(
                f"{self.base_url}{SIGN_PATH}", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            # browser-rpc answered, and its body says why it could not sign.
            # This branch used to fall into the one below and report a running
            # service as "unreachable: 502 Bad Gateway", throwing away the one
            # line that named the cause - in #830, a warm page that had left
            # tiktok.com - and leaving the self-check to guess.
            reason = _rpc_failure(exc.response)
            cause = _rpc_error_code(exc.response)
            if cause == USER_AGENT_MISMATCH:
                # Not the browser failing, and not this module's to report:
                # the registry decides what happens to the request, and logs
                # it with the identity it concerns. At WARNING here it was a
                # second line per request under the name a browser-rpc outage
                # uses (#832 review).
                logger.debug(
                    "signing.rpc.user_agent_refused",
                    endpoint=spec.endpoint,
                    identity_id=session.identity_id if session else None,
                )
                raise SigningFailed(reason, details={"cause": cause}) from exc
            logger.warning(
                "signing.rpc.refused",
                endpoint=spec.endpoint,
                status=exc.response.status_code,
                error=reason,
            )
            raise SigningFailed(reason) from exc
        except httpx.HTTPError as exc:
            # `str()` on a timeout is the empty string, so reporting only the
            # message logged `error=""` and said nothing at all. The class name
            # is what distinguishes "browser-rpc is down" from "it took longer
            # than signing.rpc_timeout_seconds", which are different problems.
            logger.warning(
                "signing.rpc.failed",
                endpoint=spec.endpoint,
                error=_describe(exc),
                timeout=self.timeout,
            )
            raise SigningFailed(_no_answer(exc, self.timeout)) from exc
        except ValueError as exc:
            logger.warning("signing.rpc.malformed", endpoint=spec.endpoint, error=str(exc))
            raise SigningFailed("browser-rpc returned a non-JSON body") from exc

        if not isinstance(body, dict):
            raise SigningFailed("browser-rpc returned a non-object body")

        # The service returns {params, user_agent}; older builds returned the
        # parameter map alone. Accept both so a partially upgraded deployment
        # does not go dark.
        raw_params = body.get("params")
        returned: Mapping[str, Any] = raw_params if isinstance(raw_params, dict) else body
        signed_ua = body.get("user_agent")

        # TikTok binds the signature to the byte-exact User-Agent: verified live
        # on 2026-09-07, changing Chrome 151 to 150 makes the API answer with an
        # 18-byte empty body, and changing it back restores the data. If the
        # browser signed under a different UA than this identity will send, the
        # request is already lost - so fail here, where the reason is legible,
        # rather than at the platform, where it looks like rate limiting.
        #
        # This never fired until #832: browser-rpc echoed the request's own UA
        # back while its page signed under another. It now answers with the
        # page's, and refuses a mismatch itself; this stays for a service from
        # before that, which would never say so. Only where the platform binds
        # the whole string: a Douyin page now honestly names its own UA, and
        # Douyin accepts its signature under another browser or version.
        wanted_ua = identity_fingerprint.user_agent
        if platform in UA_BOUND_PLATFORMS and signed_ua and wanted_ua and signed_ua != wanted_ua:
            raise SigningFailed(
                "browser-rpc signed under a different User-Agent than this "
                "identity uses; the platform validates the two byte for byte",
                details={"cause": USER_AGENT_MISMATCH},
            )
        # A service from before request bodies drops the field without a word
        # and signs an empty one, so the request would go out carrying a body
        # its signature says it does not have. Douyin did not check that when
        # it was measured on 2026-10-17 - signed over an empty body, a full
        # page still came back - which is the reason to refuse here rather
        # than at the platform: a signature that misdescribes its request works
        # until the day the binding is checked, and then fails as identities
        # being refused, far from the image that caused it.
        if spec.body and body.get("body_signed") is not True:
            raise SigningFailed(
                "browser-rpc did not confirm signing the request body; it is "
                "older than this API, so update the browser-rpc image"
            )

        signed = self._build(query, params, returned)
        logger.info(
            "signing.rpc.signed",
            endpoint=spec.endpoint,
            algorithm=signed.algorithm.value,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return signed

    @staticmethod
    def _build(query: str, params: Mapping[str, str], body: Mapping[str, Any]) -> SignedParams:
        """Turn an RPC response into the query string to send.

        Everything the service returns is copied through under its own name.
        Only the signature field itself is recognised, and only to report which
        algorithm was used; every other parameter is opaque here on purpose.
        Verified live on 2026-09-07: Douyin signs with a_bogus plus verifyFp,
        fp, uifid, timestamp and x-secsdk-web-signature, and TikTok with
        X-Gnarly, X-Dynosaur and msToken. A recognised-names list would have
        dropped most of both.
        """
        added: dict[str, str] = {}

        for field, value in body.items():
            # An empty value stays, where the page put it. TikTok's page seals
            # `msToken=` with nothing after it while its SDK has no token yet,
            # and X-Gnarly hashes the query with that pair in it, so the query
            # sent now matches the one sealed (#832). For fidelity only: it was
            # not shown to change TikTok's answer. Replayed under the same seal
            # on 2026-11-20, the query without the pair answered 8/8 (and 7/7)
            # and with it 8/8, both with the jar's live msToken in the Cookie
            # header; what decided the outcome was that token being live.
            if value is None:
                continue
            # Legacy snake_case aliases from older service builds.
            param = PASSTHROUGH_FIELDS.get(field, field)
            if param in RESPONSE_FIELDS:
                param = RESPONSE_FIELDS[param].value
            # A parameter the caller already sent is inside the signed query.
            # Appending the browser's copy as well would put it in the URL twice
            # and hand the platform a query the signature does not cover.
            if param in params:
                continue
            added[param] = str(value)

        algorithm: SignatureAlgorithm | None = None
        for field, candidate in RESPONSE_FIELDS.items():
            if body.get(field) or added.get(candidate.value):
                algorithm = candidate
                break
        if algorithm is None:
            # A signature under a name this client has never heard of is still a
            # signature; report it as the platform's default rather than
            # discarding a response that is probably fine.
            algorithm = SignatureAlgorithm.A_BOGUS
        # Carriers, not signatures. A response holding only these has told us
        # which identity to present but not how to prove the request, and the
        # platform will answer with an empty body - better to say so here. An
        # empty value proves nothing either, now that one is kept.
        if not any(v for k, v in added.items() if k not in NON_SIGNATURE_PARAMS):
            raise SigningFailed(
                f"browser-rpc returned no signature parameter, only {sorted(added) or 'nothing'}"
            )

        parts = [query] if query else []
        for param, value in added.items():
            parts.append(f"{param}={quote(value, safe='') if param in ENCODED_PARAMS else value}")

        # The msToken a page seals is its own, not the jar's, so the Cookie
        # header has to name the same one. The page is loaded with the
        # identity's jar, but TikTok rotates the token the moment it loads and
        # the SDK keeps refreshing its own copy - it is neither the jar's value
        # nor the page's cookie a moment later, and planting the jar's back
        # does not get it sealed (all measured 2026-10-21). X-Gnarly covers the
        # token in the query, so the query cannot follow the jar; the cookie
        # follows the query instead. The native signer has no such gap: it
        # seals the jar's own token.
        #
        # Except when the page sealed an empty one: then nothing is overridden
        # and the Cookie header carries the jar's own token. #832 proposed an
        # empty msToken cookie for that case, and on 2026-11-20
        # /api/user/detail/ refused it 0/3 and 0/8, refused no msToken cookie
        # 0/3, and answered 8/8 when the jar's live token rode along - and
        # refused that too when the jar's token was stale. The token in the
        # Cookie header is what TikTok judged, not the query's shape.
        token = added.get(MS_TOKEN_PARAM)
        return SignedParams(
            query="&".join(parts),
            params=added,
            signer=SIGNER_BROWSER,
            algorithm=algorithm,
            cookies={MS_TOKEN_PARAM: token} if token else {},
        )

    async def health(self) -> SignerHealth:
        """Probe browser-rpc. Never raises: an unhealthy signer is an answer."""
        started = time.monotonic()
        try:
            response = await self._client.get(f"{self.base_url}{HEALTH_PATH}", timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("signing.rpc.health_failed", error=_describe(exc))
            return SignerHealth(signer=self.name, healthy=False, detail=_describe(exc))

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        if not isinstance(body, dict):
            return SignerHealth(
                signer=self.name,
                healthy=False,
                detail="health endpoint returned a non-object body",
                latency_ms=latency_ms,
            )

        warm = body.get("warm_contexts")
        warm_contexts = warm if isinstance(warm, int) and not isinstance(warm, bool) else None
        uptime = body.get("uptime")
        # ``isinstance(True, int)`` is True; a boolean is not a duration.
        if isinstance(uptime, bool):
            uptime = None
        starved = warm_contexts is not None and warm_contexts <= 0
        return SignerHealth(
            signer=self.name,
            healthy=not starved,
            detail="no warm signing context" if starved else None,
            latency_ms=latency_ms,
            warm_contexts=warm_contexts,
            backend_version=str(body["backend_version"]) if body.get("backend_version") else None,
            uptime_seconds=float(uptime) if isinstance(uptime, int | float) else None,
        )


#: How much of browser-rpc's own error message is carried into ours. Its
#: messages are one sentence; a stack or an HTML error page is not, and neither
#: belongs in a log line or a diagnostic report.
#:
#: It was 300, which is shorter than the sentence #830 itself produces: with the
#: service's prefixes that is 376-384 characters, and the cut fell inside its
#: remedy - "look at the exit it lo" - so the list of exits to check reached
#: neither the API error, nor the log line, nor the self-check. 600 holds every
#: sentence browser-rpc composes around a short quote. Where it quotes a long
#: driver error it puts the remedy before the quote, so a cut drops the quote's
#: tail; and a cut ends on a word and says so.
RPC_ERROR_EXCERPT_CHARS = 600


def _rpc_error_code(response: httpx.Response) -> str | None:
    """The ``code`` of browser-rpc's ``{"error": {...}}`` answer, if it sent one."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else None


def _rpc_failure(response: httpx.Response) -> str:
    """browser-rpc's own account of a failure: status, code and first line.

    The service answers every failure with ``{"error": {"code", "message"}}``
    (``browser_rpc.errors``). Anything else - a proxy's HTML 502, an empty body
    - still yields the status, which is more than nothing.
    """
    head = f"HTTP {response.status_code}"
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if not isinstance(error, dict):
        return f"browser-rpc failed ({head})"
    code = error.get("code")
    if isinstance(code, str) and code:
        head = f"{head} {code}"
    lines = str(error.get("message") or "").strip().splitlines()
    message = _clip(lines[0], RPC_ERROR_EXCERPT_CHARS) if lines else ""
    return f"browser-rpc failed ({head}): {message}" if message else f"browser-rpc failed ({head})"


def _clip(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a word and marked as cut."""
    if len(text) <= limit:
        return text
    head = text[: limit - 3]
    space = head.rfind(" ")
    # A message with no space in its last stretch is not prose; cut it where it is.
    if space > limit // 2:
        head = head[:space]
    return f"{head.rstrip()}..."


def _no_answer(error: httpx.HTTPError, timeout: float) -> str:
    """What a call that got no reply at all says, by why there was none.

    Everything here used to read "browser-rpc unreachable", and a ReadTimeout is
    not that: the service was up and still working when this client stopped
    waiting - with a bad exit, its two attempts can outlast
    signing.rpc_timeout_seconds - so "the container is down" sent the operator
    to a container that was fine. Its own log has the open_failed lines.
    """
    if isinstance(error, httpx.ReadTimeout):
        return f"browser-rpc did not answer within {timeout:g}s: {_describe(error)}"
    return f"browser-rpc unreachable: {_describe(error)}"


def _describe(exc: BaseException) -> str:
    """A message that survives an exception whose ``str()`` is empty.

    ``httpx.ReadTimeout`` and its siblings carry no message, so the obvious
    ``str(exc)`` renders as nothing and a log line reads ``error=""``. That
    happened, and it cost a debugging session: a timeout and an unreachable
    service produced identical, empty evidence.
    """
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


__all__ = [
    "HEALTH_PATH",
    "SIGN_PATH",
    "UA_BOUND_PLATFORMS",
    "USER_AGENT_MISMATCH",
    "RpcSigner",
    "is_user_agent_mismatch",
]
