"""The console shell and the API must not both claim a path.

The catch-all that serves the SPA has to step aside for anything the API owns,
and the list of what the API owns is written by hand. Getting it wrong is
silent in both directions: reserve too little and a mistyped API path answers
with HTML and a 200, reserve too much and a real console route 404s with a JSON
envelope naming a route the reader cannot find anywhere in the UI.

The second happened. ``/mcp`` was reserved as a plain string prefix, which also
claimed ``/mcp-guide`` - the console's own page explaining the MCP endpoint -
so the page 404ed the moment it was deployed while every gate in the repository
stayed green.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dtk.api.console import RESERVED_ROOTS, is_reserved

REPO = Path(__file__).resolve().parents[2]
NAV_TS = REPO / "web" / "src" / "lib" / "nav.ts"

#: A floor, not a target: a parser that stops understanding nav.ts would
#: otherwise check nothing while still passing.
MINIMUM_NAV_ITEMS = 15


def console_routes() -> list[str]:
    """Every path the console's sidebar can navigate to."""
    source = NAV_TS.read_text(encoding="utf-8")
    paths = re.findall(r"^\s*\{ path: '([^']+)'", source, re.M)
    assert len(paths) >= MINIMUM_NAV_ITEMS, (
        f"only found {len(paths)} nav paths in nav.ts; the parser is probably broken"
    )
    return paths


@pytest.mark.parametrize("route", console_routes())
def test_no_console_route_is_reserved_by_the_api(route: str) -> None:
    assert not is_reserved(route), (
        f"{route} is in the console's sidebar but the SPA fallback refuses to "
        "serve it, so opening or reloading the page returns a JSON 404"
    )


@pytest.mark.parametrize("root", RESERVED_ROOTS)
def test_a_reserved_root_covers_itself_and_everything_below_it(root: str) -> None:
    assert is_reserved(root)
    assert is_reserved(f"{root}/anything")


@pytest.mark.parametrize(
    "path",
    [
        "/mcp-guide",  # the page this rule was written for
        "/mcpx",
        "/swagger-notes",
        "/api-keys",  # a real console route, one character from "/api"
        "/redocly",
        "/healthzz",
    ],
)
def test_a_longer_name_that_merely_shares_a_prefix_is_the_consoles(path: str) -> None:
    assert not is_reserved(path)


def test_docs_stays_with_the_console() -> None:
    """/docs is the console's API reference page, not Swagger UI.

    Reserving it once made a reload of that page 404. Swagger lives at
    /swagger precisely so the two do not collide.
    """
    assert not is_reserved("/docs")
    assert is_reserved("/swagger")
