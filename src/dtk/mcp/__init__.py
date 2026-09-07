"""The MCP server: the third entry point onto the same services layer.

REST, MCP and the CLI share one service layer; MCP is not a wrapper around our
own HTTP API. Calling ourselves over HTTP would add a hop, re-authenticate a
caller who is already authenticated and, worst of all, skip the in-process cache
and the in-flight coalescing that protect the identity pool
(docs/design/06-api-auth-mcp.md).

Two things to know before extending this package:

* **Eight tools, and no more.** Each additional tool measurably lowers an
  agent's selection accuracy. A new capability should be an argument to an
  existing tool before it is a ninth one.
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
    EndpointHealth,
    HistoryPoint,
    HistoryReader,
    McpContext,
    PoolReporter,
    PoolSnapshot,
    TaskGateway,
    TaskOutcome,
)
from dtk.mcp.gateway import ServiceHistoryReader, ServicePoolReporter, ServiceTaskGateway
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
    "EndpointHealth",
    "HistoryPoint",
    "HistoryReader",
    "McpContext",
    "PoolReporter",
    "PoolSnapshot",
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
