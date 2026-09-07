"""``python -m dtk.mcp`` - run the server over stdio.

The form a local agent uses: it spawns this process and talks JSON-RPC over the
pipe. Configuration comes from the environment, exactly as for the API process,
so the two never disagree about which database they are looking at.
"""

from __future__ import annotations

from dtk.mcp.server import run_stdio

if __name__ == "__main__":
    run_stdio()
