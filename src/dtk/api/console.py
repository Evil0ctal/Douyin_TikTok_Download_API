"""Serving the built console.

The API container also serves the single-page console, which is why it is the
only container that publishes a port. The build output is copied into the image;
in development it is absent and every route below simply does not exist, so
`npm run dev` can proxy to this process instead.

A SPA needs one thing the default static handler does not do: any path that is
not a real file has to fall back to ``index.html``, because the router lives in
the browser. That fallback must never swallow the API, so it is mounted last and
declines anything under the API prefixes.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dtk.core.errors import ErrorCode
from dtk.core.logging import get_logger

log = get_logger(__name__)


def _candidates() -> list[Path]:
    """Where the build output might be, most specific first.

    The package is installed into site-packages inside the image, so a path
    derived from ``__file__`` lands next to the dependencies rather than next to
    the console. The image also sets DTK_CONSOLE_DIR, but the fallbacks keep a
    source checkout working without it.
    """
    override = os.environ.get("DTK_CONSOLE_DIR")
    here = Path(__file__).resolve()
    return [
        *([Path(override)] if override else []),
        Path("/app/web/dist"),
        here.parents[3] / "web" / "dist",  # editable install from a checkout
        Path.cwd() / "web" / "dist",
    ]


#: Paths the fallback must never claim. Without this a typo in an API path would
#: return the console's HTML with status 200, and a client parsing JSON would
#: report something entirely unrelated to the actual mistake.
RESERVED_PREFIXES = ("/api/", "/healthz", "/readyz", "/docs", "/redoc", "/openapi.json")


def install(app: FastAPI, dist: Path | None = None) -> bool:
    """Mount the console if it has been built. Returns whether it was mounted."""
    roots = [dist] if dist is not None else _candidates()
    root = next((r for r in roots if (r / "index.html").is_file()), None)
    if root is None:
        log.info("console.not_built", searched=[str(r) for r in roots])
        return False
    index = root / "index.html"

    # Hashed asset filenames, so they can be cached hard.
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="console-assets")

    # response_model=None: the union return type is a Response, not a schema,
    # and FastAPI would otherwise try to build a Pydantic field from it.
    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def console(request: Request, path: str) -> FileResponse | JSONResponse:
        if request.url.path.startswith(RESERVED_PREFIXES):
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "data": None,
                    "error": {
                        "code": ErrorCode.NOT_FOUND.value,
                        "message": "No such endpoint.",
                    },
                    "meta": {"request_id": str(getattr(request.state, "request_id", ""))},
                },
            )

        candidate = (root / path).resolve() if path else index
        # Containment check: a crafted path must not escape the build output.
        if path and root.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    log.info("console.mounted", path=str(root))
    return True


__all__ = ["RESERVED_PREFIXES", "install"]
