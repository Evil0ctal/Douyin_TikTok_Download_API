"""Building and running the MCP server.

The server is a thin registration layer: it binds the eight tool methods to the
SDK and hands them a context. Everything else - waiting, prose, routing - lives
in the modules those tools call, so the transport in use never changes what a
tool does.

Two transports are supported, as required by docs/design/06-api-auth-mcp.md:

* ``stdio``, for a local agent that spawns this process directly. Set up here in
  :func:`serve_stdio`, which owns its own database, Redis and settings
  lifecycle because there is no FastAPI application around it.
* ``streamable-http``, mounted on the API application and authenticated with the
  same API keys as REST. See :mod:`dtk.mcp.http`.
"""

# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Final

from mcp.server.mcpserver import MCPServer

from dtk import __version__
from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher
from dtk.core.db import dispose_engine, init_engine
from dtk.core.logging import configure, get_logger
from dtk.core.redis import close_redis, init_redis
from dtk.identity.pool import IdentityPool
from dtk.mcp.context import McpContext
from dtk.mcp.gateway import ServiceHistoryReader, ServicePoolReporter, ServiceTaskGateway
from dtk.mcp.tools import TOOL_METHODS, ToolSet

log = get_logger(__name__)

SERVER_NAME: Final = "dtk"

#: Shown to the agent once, before any tool call. It is the cheapest place to
#: prevent the two mistakes that waste the most turns: paging by hand through
#: ids the tools accept as links, and retrying a permanent failure.
INSTRUCTIONS: Final = (
    "Douyin and TikTok data, served from a self-hosted identity pool.\n"
    "\n"
    "Start from parse_url when you have a link; use get_video, get_user, "
    "list_user_posts and list_comments when you already have ids. Ids are always "
    "strings.\n"
    "\n"
    "Every tool waits for its own answer. If one returns status 'pending', the "
    "work is still running: call get_task_result with the task id it gave you "
    "rather than repeating the original call, which would queue the work twice.\n"
    "\n"
    "When a call fails, read the message: it says whether a retry can help. If it "
    "says it cannot, change the request instead of repeating it. pool_status "
    "explains why the service is degraded.\n"
    "\n"
    "There is no tool for cookies, proxies or identities, by design."
)


def build_server(context: McpContext, *, name: str = SERVER_NAME) -> MCPServer:
    """Register the tool set against a new server instance."""
    server: MCPServer = MCPServer(
        name=name,
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    tools = ToolSet(context)
    for method in TOOL_METHODS:
        server.add_tool(getattr(tools, method), name=method)
    log.info("mcp.server.built", tools=len(TOOL_METHODS))
    return server


def service_context(cipher: Cipher, config: Callable[[], Config] | None = None) -> McpContext:
    """Wire the tools to the live services layer."""
    return McpContext(
        tasks=ServiceTaskGateway(),
        pool=ServicePoolReporter(IdentityPool(cipher)),
        history=ServiceHistoryReader(),
        config=config or Config.defaults,
    )


def _log_to_stderr() -> None:
    """Move logging off stdout.

    On stdio, stdout *is* the JSON-RPC channel. One log line written there
    corrupts the stream and the client disconnects with a parse error, so this
    has to happen before anything logs.
    """
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.setStream(sys.stderr)


@contextlib.asynccontextmanager
async def runtime(settings: BootstrapSettings) -> AsyncIterator[McpContext]:
    """Own the process-wide resources a standalone MCP server needs.

    The API application does this in its own lifespan; a stdio server has no
    application around it, so it does the same work here and tears it down on
    the way out.
    """
    from dtk.services.settings_store import load_config, start_watcher, stop_watcher

    init_engine(settings.database_url)
    init_redis(settings.redis_url)
    holder = SimpleNamespace(state=SimpleNamespace(config=Config.defaults()))
    try:
        holder.state.config = await load_config()
        await start_watcher(holder)
        try:
            yield service_context(Cipher(settings.secret_key), lambda: holder.state.config)
        finally:
            await stop_watcher(holder)
    finally:
        await close_redis()
        await dispose_engine()


async def serve_stdio(settings: BootstrapSettings | None = None) -> None:
    """Run the server over stdio until the client disconnects."""
    settings = settings or BootstrapSettings()
    configure(level=settings.log_level, json_output=settings.log_json)
    _log_to_stderr()
    async with runtime(settings) as context:
        server = build_server(context)
        log.info("mcp.stdio.started", version=__version__)
        await server.run_stdio_async()


def run_stdio(settings: BootstrapSettings | None = None) -> None:
    """Synchronous entry point, for a console script or ``python -m``."""
    asyncio.run(serve_stdio(settings))


__all__ = [
    "INSTRUCTIONS",
    "SERVER_NAME",
    "build_server",
    "run_stdio",
    "runtime",
    "serve_stdio",
    "service_context",
]
