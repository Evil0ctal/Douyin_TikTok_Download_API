"""Process entry point: `python -m browser_rpc`.

Configuration errors are reported here, before uvicorn binds anything, so a
misconfigured container fails with one readable line instead of a traceback
buried in a worker start-up log.
"""

from __future__ import annotations

import sys

import uvicorn

from browser_rpc.errors import ConfigError
from browser_rpc.main import configure_logging, create_app
from browser_rpc.settings import Settings


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"browser-rpc: refusing to start: {exc}", file=sys.stderr)
        return 78  # EX_CONFIG

    configure_logging(settings.log_level)

    try:
        app = create_app(settings)
    except ConfigError as exc:
        print(f"browser-rpc: refusing to start: {exc}", file=sys.stderr)
        return 78

    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
