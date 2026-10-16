"""The MCP server: the third entry point onto the same services layer.

REST, MCP and the CLI share one service layer; MCP is not a wrapper around our
own HTTP API. Calling ourselves over HTTP would add a hop, re-authenticate a
caller who is already authenticated and, worst of all, skip the in-process cache
and the in-flight coalescing that protect the identity pool
(docs/design/06-api-auth-mcp.md).

Two things to know before extending this package:

* **Ten tools, and few on purpose.** Each additional tool measurably lowers an
  agent's selection accuracy, so a new capability is an argument to an existing
  tool before it is a new one. download_media and transcribe_video are separate
  because they change this instance, and each has its own write scope.
* **No credential ever crosses this boundary.** There is no identity, cookie or
  proxy tool, and :class:`~dtk.mcp.context.PoolSnapshot` carries counts rather
  than rows. That is a structural guarantee, not a check someone has to remember
  to write.

Usage::

    from dtk.mcp import mount            # inside register_routes(app)
    mount(app)                           # streamable-http at /mcp

    python -m dtk.mcp                    # stdio, for a local agent
"""

from dtk.mcp.context import (
    DownloadGateway,
    EndpointHealth,
    HistoryPoint,
    HistoryReader,
    McpContext,
    PoolReporter,
    PoolSnapshot,
    TaskGateway,
    TaskOutcome,
)
from dtk.mcp.gateway import (
    ServiceDownloadGateway,
    ServiceHistoryReader,
    ServicePoolReporter,
    ServiceTaskGateway,
)
from dtk.mcp.http import DEFAULT_PATH, ApiKeyGuard, app_context, create_http_app, mount
from dtk.mcp.server import (
    INSTRUCTIONS,
    SERVER_NAME,
    build_server,
    run_stdio,
    serve_stdio,
    service_context,
)
from dtk.mcp.tools import TOOL_METHODS, ToolSet

#: The tool names an agent sees. Kept as a tuple so a test can assert on the
#: exact surface, which is the point of keeping it small.
TOOL_NAMES = TOOL_METHODS

__all__ = [
    "DEFAULT_PATH",
    "INSTRUCTIONS",
    "SERVER_NAME",
    "TOOL_NAMES",
    "ApiKeyGuard",
    "DownloadGateway",
    "EndpointHealth",
    "HistoryPoint",
    "HistoryReader",
    "McpContext",
    "PoolReporter",
    "PoolSnapshot",
    "ServiceDownloadGateway",
    "ServiceHistoryReader",
    "ServicePoolReporter",
    "ServiceTaskGateway",
    "TaskGateway",
    "TaskOutcome",
    "ToolSet",
    "app_context",
    "build_server",
    "create_http_app",
    "mount",
    "run_stdio",
    "serve_stdio",
    "service_context",
]
