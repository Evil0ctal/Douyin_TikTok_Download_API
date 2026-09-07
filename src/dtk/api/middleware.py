"""Request-scoped middleware: correlation id, language, session, rate headers."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from dtk.core.db import session_scope
from dtk.core.logging import get_logger
from dtk.i18n.negotiate import resolve_language

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


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


__all__ = [
    "REQUEST_ID_HEADER",
    "ContextMiddleware",
    "DatabaseSessionMiddleware",
    "SecurityHeadersMiddleware",
]
