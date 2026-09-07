"""The HTTP surface: three endpoints, no authentication.

The service listens only on the internal compose network. Adding authentication
between two containers that already share a private network would add key
rotation and one more thing to misconfigure without moving the trust boundary
(docs/design/04-transport-signing.md).

The routes stay thin on purpose: parse, hand to the service, shape the reply.
Every decision worth testing lives in `service.py`, which needs no HTTP client
to test.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from browser_rpc import __version__
from browser_rpc.backends import build_backend
from browser_rpc.backends.base import BrowserBackend
from browser_rpc.errors import RpcError
from browser_rpc.models import (
    HealthResponse,
    MintRequest,
    MintResponse,
    SignRequest,
    SignResponse,
)
from browser_rpc.service import BrowserRpcService
from browser_rpc.settings import Settings
from browser_rpc.validation import parse_platform

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Plain stdlib logging to stdout: the container log is the only sink."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def create_app(
    settings: Settings | None = None,
    backend: BrowserBackend | None = None,
) -> FastAPI:
    """Build the application.

    ``backend`` is injected by the tests; in the container it comes from the
    registry, which refuses to start on an unknown name rather than falling back
    to something that mints unusable identities.
    """
    settings = settings or Settings.from_env()
    service = BrowserRpcService(settings, backend or build_backend(settings))

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(
        title="dtk browser-rpc",
        version=__version__,
        description=(
            "Headless-browser side of dtk: mints guest identities through an "
            "identity's own proxy and signs requests with the platform's own code."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.service = service

    @app.exception_handler(RpcError)
    async def _rpc_error(request: Request, exc: RpcError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("rpc.error code=%s message=%s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.as_body())

    @app.post("/rpc/mint", response_model=MintResponse)
    async def mint(payload: MintRequest) -> MintResponse:
        platform = parse_platform(payload.platform)
        outcome = await service.mint(platform, payload.proxy_url, payload.geo_hint)
        profile = outcome.profile
        return MintResponse(
            cookies=dict(profile.cookies),
            platform=platform.value,
            browser_family=profile.browser_family,
            browser_major=profile.browser_major,
            user_agent=profile.user_agent,
            platform_hint=profile.navigator_platform,
            screen=profile.screen,
            language=profile.language or outcome.geo.languages,
            timezone=profile.timezone or outcome.geo.timezone,
            exit_ip=outcome.exit_ip,
            country=outcome.geo.country,
            locale=outcome.geo.locale,
        )

    @app.post("/rpc/sign", response_model=SignResponse)
    async def sign(payload: SignRequest) -> SignResponse:
        platform = parse_platform(payload.platform)
        params = {str(k): str(v) for k, v in payload.params.items() if v is not None}
        # The caller's own query string wins: it is the byte sequence the
        # signature has to cover, and re-encoding a parameter map here would
        # reorder or re-escape it.
        query = payload.query if payload.query is not None else urlencode(params)
        signed = await service.sign(
            platform,
            payload.url,
            query,
            params=params,
            user_agent=payload.user_agent,
        )
        # Echo the UA the signature was computed under so the caller can assert
        # it is the one it will actually send. TikTok rejects any mismatch.
        return SignResponse(
            params=signed, user_agent=service.user_agent_for(platform, payload.user_agent)
        )

    @app.get("/rpc/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(**_health_body(service))

    return app


def _health_body(service: BrowserRpcService) -> dict[str, Any]:
    """Health must answer even when everything else is broken."""
    try:
        return service.health()
    except Exception as exc:
        logger.exception("rpc.health_failed")
        return {
            "status": "unavailable",
            "backend": "unknown",
            "backend_version": "unknown",
            "warm_contexts": 0,
            "uptime": 0.0,
            "error": str(exc),
        }


__all__ = ["configure_logging", "create_app"]
