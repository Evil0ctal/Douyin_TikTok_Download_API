"""The streamable-http transport, mounted on the API application.

Remote agents reach the same tool set over HTTP, authenticated with the same API
keys as REST (docs/design/06-api-auth-mcp.md). The MCP server runs inside the
API process and calls the services layer directly, so a tool call over this
transport does exactly what a stdio tool call does.

Two decisions worth stating:

* **API key only, never the console session cookie.** The cookie exists for a
  browser; accepting it here would make the MCP endpoint reachable by anything
  that can make a request from inside that browser. A key is presented
  deliberately, by a program, which is what this endpoint is for.
* **Failures use the same envelope as REST.** An MCP client that gets a 401 sees
  the identical ``{"success": false, "error": {"code": ...}}`` shape it would get
  from ``/api/v1``, so one error handler covers both.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any, Final

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.types import Receive, Send
from starlette.types import Scope as ASGIScope

from dtk.api import envelope
from dtk.api.deps import current_principal, enforce_rate_limit
from dtk.core.crypto import Cipher
from dtk.core.errors import DtkError, ForbiddenScope, Unauthenticated
from dtk.core.logging import get_logger
from dtk.core.types import Platform, Scope
from dtk.identity.pool import IdentityPool
from dtk.mcp.context import EndpointHealth, McpContext, PoolSnapshot
from dtk.mcp.gateway import ServiceHistoryReader, ServicePoolReporter, ServiceTaskGateway
from dtk.mcp.server import build_server

log = get_logger(__name__)

#: Where the transport is mounted on the API application.
DEFAULT_PATH: Final = "/mcp"

#: What the transport itself can check: the key is read-capable somewhere. Which
#: platform a call touches is inside the JSON-RPC body, not in the headers, so
#: the per-platform half of the rule is enforced in the tool bodies through
#: :func:`authorize_platform` - exactly as ``/api/v1/parse`` checks "any read
#: scope" first and the resolved platform second.
READ_SCOPES: Final[tuple[Scope, ...]] = (Scope.DOUYIN_READ, Scope.TIKTOK_READ)


class _DeferredPoolReporter:
    """Builds the real reporter on first use.

    Routes are registered while the application is still being constructed, and
    the cipher only exists once the lifespan has run. Resolving lazily keeps the
    wiring in one place instead of pushing a second initialisation hook into the
    application factory.
    """

    __slots__ = ("_factory", "_resolved")

    def __init__(self, factory: Callable[[], ServicePoolReporter]) -> None:
        self._factory = factory
        self._resolved: ServicePoolReporter | None = None

    def _reporter(self) -> ServicePoolReporter:
        if self._resolved is None:
            self._resolved = self._factory()
        return self._resolved

    async def snapshot(self) -> PoolSnapshot:
        return await self._reporter().snapshot()

    async def endpoint(self, endpoint: str) -> EndpointHealth:
        return await self._reporter().endpoint(endpoint)


def read_scope(platform: Platform) -> Scope:
    """The scope a key needs to read one platform.

    Derived from the platform name, and falling back to ``admin`` for a platform
    with no scope of its own: an authorization decision fails closed. Same rule,
    and same fallback, as ``dtk.api.routes.content.read_scope``.
    """
    try:
        return Scope(f"{platform.value}:read")
    except ValueError:
        return Scope.ADMIN


def authorize_platform(platform: Platform, caller: Any) -> None:
    """Refuse a platform this API key was not granted.

    ``ApiKeyGuard`` can only check that the key reads *something*; the platform
    arrives inside the JSON-RPC body. Without this, a key scoped to
    ``tiktok:read`` would read Douyin over MCP while ``GET /api/v1/douyin/video``
    refuses it - the same credential, two answers.

    The rule itself is not restated here: :func:`dtk.api.routes.support.has_scope`
    decides, so an MCP call and a REST call cannot diverge. It is imported inside
    the function because the route package imports this module to mount the
    server, and a module-level import would close that loop.
    """
    if caller is None:  # stdio: no transport identity to restrict
        return
    from dtk.api.routes.support import has_scope

    scope = read_scope(platform)
    if not has_scope(caller, (scope,)):
        raise ForbiddenScope(
            "this credential lacks the scope required for this platform",
            details={"required": [scope.value], "platform": platform.value},
        )


def app_context(app: FastAPI) -> McpContext:
    """An :class:`McpContext` bound to a running API application."""

    def reporter() -> ServicePoolReporter:
        cipher: Cipher = app.state.cipher
        return ServicePoolReporter(IdentityPool(cipher))

    return McpContext(
        tasks=ServiceTaskGateway(),
        pool=_DeferredPoolReporter(reporter),
        history=ServiceHistoryReader(),
        config=lambda: app.state.config,
        authorize=authorize_platform,
    )


class ApiKeyGuard:
    """ASGI wrapper enforcing API key authentication, scopes and rate limits."""

    __slots__ = ("_app", "_scopes")

    def __init__(self, app: Any, *, scopes: tuple[Scope, ...] = READ_SCOPES) -> None:
        self._app = app
        self._scopes = scopes

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        try:
            await self._authenticate(request)
        except DtkError as exc:
            log.info("mcp.http.rejected", code=exc.code.value)
            response = envelope.from_exception(request, exc)
            if exc.http_status == 401:
                response.headers["WWW-Authenticate"] = 'Bearer realm="dtk"'
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)

    async def _authenticate(self, request: Request) -> None:
        from dtk.api.routes.support import has_scope

        if not _has_api_key(request):
            raise Unauthenticated(
                "the MCP endpoint requires an API key in the Authorization or "
                "X-API-Key header; the console session cookie is not accepted here"
            )
        principal = await current_principal(request)
        if principal.api_key_id is None:
            raise Unauthenticated("this endpoint accepts API keys only")
        # has_scope, not Principal.require: a key is bounded by the scopes it was
        # minted with even when an administrator owns it (doc 06), and the tool
        # bodies decide the same way.
        if not has_scope(principal, self._scopes):
            raise ForbiddenScope(
                "this credential lacks read access to any platform",
                details={"required": [scope.value for scope in self._scopes]},
            )
        await enforce_rate_limit(request, principal)
        # The tool bodies re-check the platform they were actually asked for, and
        # they find the caller here rather than resolving the key a second time.
        request.state.principal = principal


def _has_api_key(request: Request) -> bool:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return bool(header[7:].strip())
    return bool(request.headers.get("x-api-key"))


def create_http_app(
    server: MCPServer,
    *,
    stateless: bool = True,
    json_response: bool = False,
) -> Starlette:
    """The SDK's streamable-http application, ready to be mounted at a prefix.

    ``stateless`` is the default because the API process is meant to scale
    horizontally: a session pinned to one worker would break the moment a second
    one is started.

    DNS rebinding protection is left off deliberately. It defends an
    unauthenticated local server against a browser that was tricked into
    resolving a hostname; here every request must carry an API key, which a
    rebinding attacker cannot obtain, and enabling it would reject the perfectly
    ordinary case of reaching this endpoint through the user's own domain.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=stateless,
        json_response=json_response,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


def _chain_lifespan(app: FastAPI, sub: Starlette) -> None:
    """Run the sub-application's lifespan alongside the main one.

    A mounted ASGI application never receives its own lifespan event, and the
    streamable-http transport needs one: its session manager runs inside a task
    group started there. Wrapping the existing context keeps that fact inside
    this module rather than spreading it into the application factory.
    """
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def combined(scope_app: Any) -> AsyncIterator[Any]:
        async with previous(scope_app) as state, sub.router.lifespan_context(sub):
            yield state

    app.router.lifespan_context = combined


def _redirect_bare_path(app: FastAPI, path: str) -> None:
    """Send ``/mcp`` to ``/mcp/`` rather than answering 404.

    A Mount matches only the paths beneath it, so the bare prefix is left for
    whatever comes next - and for a while that was the console's SPA catch-all,
    which answered ``GET /mcp`` with the console shell and **200**. A client
    probing the documented path saw apparent success and nothing that hinted at
    the missing slash; ``POST /mcp`` got a 405 from the same partial match.
    Adding the prefix to the console's reserved list stopped the wrong body but
    left a bare 404, which is honest and still unhelpful.

    Registered after the mount so the mount keeps precedence for everything
    under it.
    """

    async def _to_slash(_: Request) -> RedirectResponse:
        # 307, not 302: the method and body have to survive, and the client
        # that hits this is usually POSTing an initialize call.
        return RedirectResponse(url=f"{path}/", status_code=307)

    app.add_api_route(
        path,
        _to_slash,
        methods=["GET", "POST"],
        include_in_schema=False,
        name="mcp_trailing_slash",
    )


def mount(
    app: FastAPI,
    *,
    path: str = DEFAULT_PATH,
    context: McpContext | None = None,
    scopes: tuple[Scope, ...] = READ_SCOPES,
    stateless: bool = True,
) -> MCPServer:
    """Mount the MCP server on ``app`` and return it.

    Call this from route registration. The endpoint lands at ``path`` and is
    guarded by :class:`ApiKeyGuard`, so it authenticates exactly like the REST
    surface it sits beside.
    """
    server = build_server(context or app_context(app))
    http_app = create_http_app(server, stateless=stateless)
    app.mount(path, ApiKeyGuard(http_app, scopes=scopes))
    _redirect_bare_path(app, path)
    _chain_lifespan(app, http_app)
    log.info("mcp.http.mounted", path=path, stateless=stateless)
    return server


__all__ = [
    "DEFAULT_PATH",
    "READ_SCOPES",
    "ApiKeyGuard",
    "app_context",
    "authorize_platform",
    "create_http_app",
    "mount",
    "read_scope",
]
