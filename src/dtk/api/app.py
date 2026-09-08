"""FastAPI application factory.

The API process also hosts the MCP server and serves the built console, so this
is the only container that exposes a port. It performs no upstream requests
itself: it authenticates, validates, hands work to the services layer and
assembles the reply.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

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
        return envelope.failure(
            ErrorCode.INVALID_PARAM,
            getattr(request.state, "request_id", "unknown"),
            language=response_language(request),
            details={"fields": exc.errors()[:10]},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHENTICATED,
            403: ErrorCode.FORBIDDEN_SCOPE,
            404: ErrorCode.NOT_FOUND,
            429: ErrorCode.RATE_LIMITED,
        }.get(exc.status_code, ErrorCode.INTERNAL)
        return envelope.failure(
            code,
            getattr(request.state, "request_id", "unknown"),
            language=response_language(request),
            status_code=exc.status_code or HTTP_STATUS[code],
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
