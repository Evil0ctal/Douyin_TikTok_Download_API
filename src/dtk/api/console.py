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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from dtk.core.errors import NotFound
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language

log = get_logger(__name__)

#: The attribute index.html ships with, and what it is replaced by once the
#: server has negotiated a language for this particular request. The console
#: only trusts the ``lang`` attribute when the marker says "negotiated": the
#: file is built with lang="en", so trusting it unconditionally would pin every
#: unpatched build to English (web/src/lib/language.ts).
LANGUAGE_MARKER = 'data-language-source="build"'


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


def localize(html: str, language: Language) -> str:
    """Stamp the negotiated language onto the document element.

    Returns the HTML unchanged when the marker is absent, so a console built
    from a future index.html still serves rather than 500s. The absence is a
    silent loss of a feature, though, which is why
    tests/unit/test_repo_hygiene.py asserts the marker is still in the source
    file rather than leaving this to be noticed in production.
    """
    if LANGUAGE_MARKER not in html:
        return html
    head, _, tail = html.partition(LANGUAGE_MARKER)
    # Only the document element carries a lang attribute in this file, and it
    # sits before the marker, so the replacement is bounded to the opening tag.
    head = head.replace('lang="en"', f'lang="{language.value}"', 1)
    return head + 'data-language-source="negotiated"' + tail


def install(app: FastAPI, dist: Path | None = None) -> bool:
    """Mount the console if it has been built. Returns whether it was mounted."""
    roots = [dist] if dist is not None else _candidates()
    root = next((r for r in roots if (r / "index.html").is_file()), None)
    if root is None:
        log.info("console.not_built", searched=[str(r) for r in roots])
        return False
    index = root / "index.html"
    # Read once: the build output is immutable inside the image, and re-reading
    # it per request would put a disk hit on the hot path of every page load.
    index_html = index.read_text(encoding="utf-8")
    if LANGUAGE_MARKER not in index_html:
        log.warning(
            "console.language_marker_missing",
            detail=(
                "index.html has no data-language-source marker, so the console "
                "cannot be seeded with the negotiated language and will fall "
                "back to navigator.language"
            ),
        )

    # Hashed asset filenames, so they can be cached hard.
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="console-assets")

    def document(request: Request) -> HTMLResponse:
        """The SPA shell, in the language this request negotiated.

        The body now varies by Accept-Language, so it must say so and must not
        be stored by a shared cache: without that, the first visitor's language
        would be handed to everyone behind the same proxy. Hashed assets under
        /assets are unaffected and stay cacheable.
        """
        language = getattr(request.state, "language", DEFAULT_LANGUAGE)
        return HTMLResponse(
            localize(index_html, language),
            headers={
                "Cache-Control": "no-store",
                "Vary": "Accept-Language",
            },
        )

    # response_model=None: the union return type is a Response, not a schema,
    # and FastAPI would otherwise try to build a Pydantic field from it.
    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def console(request: Request, path: str) -> FileResponse | HTMLResponse:
        if request.url.path.startswith(RESERVED_PREFIXES):
            # Raised rather than assembled here, so a mistyped API path gets the
            # same localized envelope as every other error: the handler in
            # dtk.api.app already has the request's language and correlation id.
            raise NotFound("no such endpoint")

        if not path:
            return document(request)
        candidate = (root / path).resolve()
        # Containment check: a crafted path must not escape the build output.
        # index.html reached by its own name still goes through the localizer
        # rather than being served as a static file, or /index.html and / would
        # answer in different languages.
        if (
            root.resolve() in candidate.parents
            and candidate.is_file()
            and candidate != index.resolve()
        ):
            return FileResponse(candidate)
        return document(request)

    log.info("console.mounted", path=str(root))
    return True


__all__ = ["LANGUAGE_MARKER", "RESERVED_PREFIXES", "install", "localize"]
