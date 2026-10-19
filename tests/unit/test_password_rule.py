"""One password floor, wherever a password is set.

The server validates against ``MIN_PASSWORD_LENGTH``; the CLI and the console
each carry a copy, because neither can import it - the CLI must not drag the
API in, and the console is TypeScript. Copies drift, and these did: the setup
wizard asked for 12 characters while the server, the CLI and the Users page
took 8, so a new instance's first administrator was told a rule nobody else was
held to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dtk.api.routes.passwords import MIN_PASSWORD_LENGTH

PASSWORDS_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "passwords.ts"


def test_the_cli_holds_the_same_floor() -> None:
    from dtk.cli import users

    assert users.MIN_PASSWORD_LENGTH == MIN_PASSWORD_LENGTH


def test_the_console_holds_the_same_floor() -> None:
    if not PASSWORDS_TS.exists():
        pytest.skip("console not present")
    match = re.search(r"^export const MIN_PASSWORD_LENGTH = (\d+)$", PASSWORDS_TS.read_text(), re.M)
    assert match is not None, "MIN_PASSWORD_LENGTH is no longer declared the way this test reads it"
    assert int(match.group(1)) == MIN_PASSWORD_LENGTH


def test_no_console_page_keeps_a_floor_of_its_own() -> None:
    """The wizard's 12 lived in a local constant beside the form; one of those
    is all it takes for the copies to disagree again."""
    pages = PASSWORDS_TS.parents[1] / "pages"
    if not pages.exists():
        pytest.skip("console not present")
    local = sorted(
        path.name
        for path in pages.glob("*.tsx")
        if re.search(r"^const MIN_PASSWORD_LENGTH\b", path.read_text(encoding="utf-8"), re.M)
    )
    assert local == [], f"import MIN_PASSWORD_LENGTH from '@/lib/passwords' instead: {local}"
