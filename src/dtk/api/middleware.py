"""Request middleware: CORS, correlation id, language, session, body ceiling.

Everything here is mounted unconditionally, in create_app, before the lifespan
has read the settings table. Starlette freezes the stack at the first request,
so a middleware whose existence depends on a runtime setting would answer for
the life of the process with whatever the defaults said - which is how the CORS
origin list came to be unreachable. A middleware that has to follow a setting
reads the current snapshot per request instead (doc 10).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import structlog
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dtk.api import envelope
from dtk.core.config import Config
from dtk.core.db import session_scope
from dtk.core.errors import ErrorCode
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n.catalog import t
from dtk.i18n.negotiate import resolve_language

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

#: Methods answered cross-origin. The API has no PATCH route; OPTIONS is here
#: because a preflight names it in Access-Control-Request-Method.
CORS_METHODS = ("GET", "POST", "PUT", "DELETE", "OPTIONS")

#: Response headers a browser caller is allowed to read. Without these the
#: correlation id and the rate-limit budget are invisible to fetch(), which is
#: most of what they exist for.
CORS_EXPOSE_HEADERS = (
    REQUEST_ID_HEADER,
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
)

#: Ceiling on a request body, enforced before any handler reads it.
#:
#: The largest body this API has a use for is a paste: a cookie jar or a proxy
#: list, both capped at ``MAX_PASTE_CHARS`` (200_000) characters by their
#: schema, and a 50-item URL batch is a fifth of that. Both pastes are ASCII in
#: practice - cookie octets and ``host:port`` lines - so 200_000 characters is
#: about 200 KB of JSON. One mebibyte is five times that: far enough above
#: honest input that no user meets it by accident, low enough that an
#: unauthenticated caller cannot make the process hold a large buffer.
#: tests/unit/test_api_middleware.py pins the two together.
MAX_REQUEST_BODY_BYTES = 1024 * 1024


def response_language(request: Request) -> Language:
    """The response language, defaulting when the middleware has not run yet.

    An error raised before ContextMiddleware sets it - a body refused above the
    handler, a malformed request line - still has to produce a localized
    envelope rather than crash on a missing attribute.
    """
    value = getattr(request.state, "language", None)
    return value if isinstance(value, Language) else DEFAULT_LANGUAGE


@dataclass(frozen=True, slots=True)
class CorsPolicy:
    """The origin list and credential flag actually in force for one request."""

    origins: tuple[str, ...]
    allow_credentials: bool

    @property
    def enabled(self) -> bool:
        return bool(self.origins)


def cors_policy(config: Config) -> CorsPolicy:
    """Read the live CORS settings, refusing the one pair that cannot be safe.

    A wildcard origin together with credentials lets any page a signed-in user
    visits call this API with their console cookie and read the answer, which
    is exactly what the empty default exists to prevent. The wildcard wins and
    the credentials are dropped rather than the other way round: an operator
    who typed ``*`` asked for an open anonymous API, not an open authenticated
    one, and a browser would reject the pair anyway.
    """
    raw = config.get("security.cors_allow_origins")
    values = raw if isinstance(raw, list) else []
    origins = tuple(
        dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip())
    )
    if "*" in origins:
        return CorsPolicy(("*",), allow_credentials=False)
    credentials = bool(config.get("security.cors_allow_credentials"))
    return CorsPolicy(origins, allow_credentials=credentials)


class CorsMiddleware:
    """CORS whose origin list is read per request, not per process.

    Deciding at construction time whether to mount Starlette's CORSMiddleware
    reads the setting before the lifespan has loaded it from the database, so
    the answer is always "no origins" and a console that offers the setting is
    lying. Mounted unconditionally instead: the per-request cost is a dict
    lookup and an equality check, and the delegate behind it is Starlette's own
    implementation, rebuilt only when the policy changes - preflight validation
    is not worth reimplementing.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._cached: tuple[CorsPolicy, CORSMiddleware] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        policy = cors_policy(_config_of(scope))
        if not policy.enabled:
            # Same-origin only: no CORS headers on anything, and a preflight
            # falls through to the router, which has no OPTIONS route to answer
            # it with. A browser reads both as "not allowed", which is correct.
            await self.app(scope, receive, send)
            return
        await self._delegate(policy)(scope, receive, send)

    def _delegate(self, policy: CorsPolicy) -> CORSMiddleware:
        cached = self._cached
        if cached is not None and cached[0] == policy:
            return cached[1]
        if policy.origins == ("*",):
            # Worth its own line: every site the user visits can now call this
            # API, and an operator who did not mean that should see it said.
            log.warning("api.cors.wildcard_origin")
        log.info(
            "api.cors.policy",
            origins=len(policy.origins),
            allow_credentials=policy.allow_credentials,
        )
        delegate = CORSMiddleware(
            self.app,
            allow_origins=list(policy.origins),
            allow_credentials=policy.allow_credentials,
            allow_methods=list(CORS_METHODS),
            allow_headers=["*"],
            expose_headers=list(CORS_EXPOSE_HEADERS),
        )
        self._cached = (policy, delegate)
        return delegate


def _config_of(scope: Scope) -> Config:
    """The snapshot the lifespan installed, or the code defaults before it has."""
    app = scope.get("app")
    config = getattr(app.state, "config", None) if app is not None else None
    return config if isinstance(config, Config) else Config.defaults()


class ContextMiddleware(BaseHTTPMiddleware):
    """Assigns the correlation id and resolves the response language.

    The same id is echoed in the header, in the envelope's ``meta`` and in
    ``request_log``, so a user reporting a problem only has to quote one value.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = uuid.uuid4()
        request.state.request_id = request_id
        request.state.language = resolve_language(
            request.query_params.get("lang"),
            request.headers.get("accept-language"),
            request.app.state.config.get("api.default_language"),
        )
        request.state.rate_limit = None

        structlog.contextvars.bind_contextvars(request_id=str(request_id))
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        response.headers[REQUEST_ID_HEADER] = str(request_id)
        response.headers["X-Response-Time-Ms"] = str(int((time.monotonic() - started) * 1000))

        limits = getattr(request.state, "rate_limit", None)
        if limits:
            limit, remaining, reset_at = limits
            # Standard headers so callers can back off before hitting 429
            # instead of probing for the ceiling.
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response


class DatabaseSessionMiddleware(BaseHTTPMiddleware):
    """One transactional session per request, committed on a clean exit."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        async with session_scope() as session:
            request.state.db = session
            return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


class BodyLimitMiddleware:
    """Refuses a request body over :data:`MAX_REQUEST_BODY_BYTES`.

    Mounted innermost, so a refusal still carries the correlation id and the
    caller's language, and still outside every handler, so no route has to
    remember to check. A declared Content-Length over the ceiling is refused
    without entering the application at all; a chunked upload declares nothing,
    so it is counted as it arrives and cut off the moment it goes over.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _refuse(scope, receive, send, limit=self.max_bytes, size=declared)
            return

        counted = _CountedBody(scope, receive, send, self.max_bytes)
        try:
            await self.app(scope, counted.receive, counted.send)
        except Exception:
            if not counted.answered:
                raise
            # The application is unwinding from the disconnect it was handed
            # after we answered. Its complaint about a body that stopped
            # arriving is expected, and the request already has its response.
            log.debug("api.body_limit.unwound", path=scope.get("path"))


class _CountedBody:
    """Counts one request body and answers if it runs past the ceiling.

    The refusal is sent from here rather than raised: FastAPI wraps everything
    that escapes its body read in a parse error, which would report a body that
    is too large as a body that is malformed. So the caller gets the refusal
    and the application gets a disconnect, and whatever it decides to answer
    with afterwards is dropped rather than appended to a finished response.
    """

    __slots__ = ("_limit", "_receive", "_scope", "_send", "answered", "seen", "started")

    def __init__(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._limit = limit
        self.seen = 0
        self.started = False
        self.answered = False

    async def receive(self) -> Message:
        if self.answered:
            return {"type": "http.disconnect"}
        message = await self._receive()
        if message["type"] != "http.request":
            return message
        self.seen += len(message.get("body", b""))
        if self.seen <= self._limit:
            return message
        if not self.started:
            await _refuse(self._scope, self._receive, self._send, limit=self._limit, size=self.seen)
            self.answered = True
        # Either the caller has its refusal or a response was already going out
        # and cannot be replaced; either way this body stops here.
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if self.answered:
            return
        if message["type"] == "http.response.start":
            self.started = True
        await self._send(message)


def _declared_length(scope: Scope) -> int | None:
    """Content-Length, or None when the caller declared nothing usable."""
    raw = Headers(scope=scope).get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        # An unparseable length is not a size to act on; the counter still
        # holds the ceiling for this request.
        return None


async def _refuse(scope: Scope, receive: Receive, send: Send, *, limit: int, size: int) -> None:
    """Answer one oversized request in the uniform envelope.

    INVALID_PARAM because the codes are a stable, append-only contract and none
    of them means "too large". The status is still 413, and the details carry
    the ceiling as a number plus a sentence in the caller's language, so the
    generic code does not leave the reader guessing which parameter was wrong.
    """
    request = Request(scope)
    language = response_language(request)
    log.info(
        "api.body_limit.refused",
        path=scope.get("path"),
        limit_bytes=limit,
        body_bytes=size,
    )
    response = envelope.failure(
        ErrorCode.INVALID_PARAM,
        getattr(request.state, "request_id", "unknown"),
        language=language,
        details={
            "limit_bytes": limit,
            "reason": t("api.body_limit.exceeded", language, limit_kib=limit // 1024),
        },
        status_code=413,
    )
    await response(scope, receive, send)


__all__ = [
    "CORS_EXPOSE_HEADERS",
    "CORS_METHODS",
    "MAX_REQUEST_BODY_BYTES",
    "REQUEST_ID_HEADER",
    "BodyLimitMiddleware",
    "ContextMiddleware",
    "CorsMiddleware",
    "CorsPolicy",
    "DatabaseSessionMiddleware",
    "SecurityHeadersMiddleware",
    "cors_policy",
    "response_language",
]
