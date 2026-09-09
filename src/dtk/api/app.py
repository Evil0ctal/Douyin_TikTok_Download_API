"""FastAPI application factory.

The API process also hosts the MCP server and serves the built console, so this
is the only container that exposes a port. It performs no upstream requests
itself: it authenticates, validates, hands work to the services layer and
assembles the reply.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dtk import __version__
from dtk.api import envelope
from dtk.api.middleware import (
    BodyLimitMiddleware,
    ContextMiddleware,
    CorsMiddleware,
    DatabaseSessionMiddleware,
    SecurityHeadersMiddleware,
    response_language,
)
from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher
from dtk.core.db import dispose_engine, init_engine
from dtk.core.errors import HTTP_STATUS, DtkError, ErrorCode
from dtk.core.logging import configure, get_logger
from dtk.core.redis import close_redis, init_redis

log = get_logger(__name__)

DESCRIPTION = (
    "Self-hosted Douyin and TikTok data API. Requests are served from a pool of "
    "rotating identities; responses share one envelope and a stable error-code "
    "enum. Set `?lang=zh` or send `Accept-Language: zh` for Chinese messages."
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: BootstrapSettings = app.state.settings
    configure(level=settings.log_level, json_output=settings.log_json)

    init_engine(settings.database_url)
    init_redis(settings.redis_url)
    app.state.cipher = Cipher(settings.secret_key)

    from dtk.services.settings_store import load_config, start_watcher, stop_watcher

    app.state.config = await load_config()
    await start_watcher(app)
    log.info("api.started", version=__version__, settings_version=app.state.config.version)
    try:
        yield
    finally:
        await stop_watcher(app)
        await close_redis()
        await dispose_engine()
        log.info("api.stopped")


#: Keys pydantic attaches to a validation error that must never leave the
#: process. ``input`` is the offending value, and for a body that failed to
#: parse at all it is the raw request bytes; ``ctx`` can carry the same value
#: again inside an exception it wrapped; ``url`` is a docs link, not an error.
_UNSAFE_ERROR_KEYS: Final[frozenset[str]] = frozenset({"input", "ctx", "url"})


def _safe_validation_fields(exc: RequestValidationError) -> list[dict[str, Any]]:
    """The field path and the reason, and nothing the caller sent.

    FastAPI's ``errors()`` takes no arguments - it hands back pydantic's list
    verbatim - so the stripping happens here rather than at the call.
    """
    fields: list[dict[str, Any]] = []
    for error in list(exc.errors())[:10]:
        if not isinstance(error, dict):
            continue
        fields.append({k: v for k, v in error.items() if k not in _UNSAFE_ERROR_KEYS})
    return fields


def create_app(settings: BootstrapSettings | None = None) -> FastAPI:
    settings = settings or BootstrapSettings()

    app = FastAPI(
        title="dtk",
        version=__version__,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Replaced during lifespan by the database-backed snapshot; defaults keep the
    # app importable and testable without a database.
    app.state.config = Config.defaults()

    # Order matters: the outermost middleware runs first on the way in, and
    # every middleware here is mounted unconditionally. Nothing may depend on a
    # runtime setting to decide whether it exists: this runs before the
    # lifespan has read the settings table, so it would see the defaults and
    # keep them for the life of the process (doc 10). CORS is outermost so a
    # preflight is answered without waking the rest of the stack; the body
    # ceiling is innermost so its refusal still carries the correlation id and
    # the caller's language.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(DatabaseSessionMiddleware)
    app.add_middleware(ContextMiddleware)
    app.add_middleware(CorsMiddleware)

    _install_error_handlers(app)
    _install_routes(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DtkError)
    async def _domain_error(request: Request, exc: DtkError) -> JSONResponse:
        if exc.http_status >= 500:
            log.error("api.error", code=exc.code.value, detail=str(exc))
        return envelope.from_exception(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Answer a malformed request without quoting it back.

        ``include_input=False`` is the whole point. Pydantic puts the offending
        value in every error, and for a body that failed to parse at all - a
        JSON document sent with `Content-Type: text/plain`, which is what plain
        `curl -d` does - that value is the raw request bytes. Serializing them
        raised `TypeError: Object of type bytes is not JSON serializable`, the
        catch-all turned it into a 500, and the traceback wrote the whole body
        into the container log: an imported cookie jar on one endpoint, a
        plaintext password on another, and reachable unauthenticated through
        `POST /auth/login`.

        So the input never travels. The field path and the reason are what a
        caller needs; the value is something they already have.
        """
        return envelope.failure(
            ErrorCode.INVALID_PARAM,
            getattr(request.state, "request_id", "unknown"),
            language=response_language(request),
            details={"fields": _safe_validation_fields(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHENTICATED,
            403: ErrorCode.FORBIDDEN_SCOPE,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
            409: ErrorCode.SETUP_ALREADY_DONE,
            415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            429: ErrorCode.RATE_LIMITED,
            501: ErrorCode.NOT_CONFIGURED,
        }.get(exc.status_code, ErrorCode.INTERNAL)
        # Everything the framework attached, `Allow` above all: RFC 9110 makes
        # it a MUST on a 405, and rebuilding the response without it dropped it.
        # Falling back to INTERNAL for a 405 was worse than untidy - INTERNAL's
        # own contract tells the caller to file a bug quoting a request id that
        # is never logged, for what is only a method they got wrong, and it is
        # absent from NON_RETRYABLE so a code-branching client retries forever.
        return envelope.failure(
            code,
            getattr(request.state, "request_id", "unknown"),
            language=response_language(request),
            status_code=exc.status_code or HTTP_STATUS[code],
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak a traceback to the caller; the request id ties the log line
        # to the report.
        log.exception("api.unhandled", error=type(exc).__name__)
        return envelope.failure(
            ErrorCode.INTERNAL,
            getattr(request.state, "request_id", "unknown"),
            language=response_language(request),
        )


def _install_routes(app: FastAPI) -> None:
    from dtk.api.routes import register_routes

    register_routes(app)

    # Last, because its catch-all would otherwise shadow every API route.
    from dtk.api import console

    console.install(app)


__all__ = ["DESCRIPTION", "create_app", "lifespan"]
