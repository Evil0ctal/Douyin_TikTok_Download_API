"""Wire codes the console has to phrase, and the two lists that go stale.

Two enumerations leave the backend as bare snake_case strings and end up in
front of an operator: the scheduler's RejectReason, in the request log, and the
proxy importer's per-line rejection reason. Neither is an ErrorCode, so nothing
in web/scripts/check-i18n.mjs covers them, and adding a member on this side is
silent on the other: the console falls through to printing the raw code.

These tests are the join. They read the codes the server can actually emit and
check that the console both lists them and has copy for them in both languages.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dtk.core.types import RejectReason

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web" / "src"
PROXY_URLS = REPO / "src" / "dtk" / "api" / "routes" / "admin" / "proxy_urls.py"
PROXY_ROUTE = REPO / "src" / "dtk" / "api" / "routes" / "admin" / "proxies.py"

pytestmark = pytest.mark.skipif(not WEB.exists(), reason="console not present")


def _string_list(source: str, declaration: str) -> set[str]:
    """The quoted entries of a `const NAME = [...]` block in a TypeScript file."""
    block = re.search(re.escape(declaration) + r"[^[]*\[(.*?)\]", source, re.S)
    assert block is not None, f"{declaration} is no longer declared the way this test reads it"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _console_catalog(language: str) -> dict[str, str]:
    raw = json.loads((WEB / "locales" / language / "console.json").read_text(encoding="utf-8"))
    flat: dict[str, str] = {}

    def walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}{key}.")
        else:
            flat[prefix.rstrip(".")] = str(node)

    walk(raw, "")
    return flat


# --------------------------------------------------------------------------
# Scheduler reject reasons
# --------------------------------------------------------------------------


def _console_reject_reasons() -> set[str]:
    source = (WEB / "lib" / "types.ts").read_text(encoding="utf-8")
    return _string_list(source, "export const REJECT_REASONS")


def test_the_console_knows_every_reject_reason() -> None:
    server = {reason.value for reason in RejectReason}
    assert server <= _console_reject_reasons(), (
        "web/src/lib/types.ts does not list "
        f"{sorted(server - _console_reject_reasons())}; the request log would "
        "show the raw code"
    )


def test_the_console_invents_no_reject_reason() -> None:
    """A code the scheduler cannot emit is dead copy that reads as live."""
    server = {reason.value for reason in RejectReason}
    assert _console_reject_reasons() <= server, (
        f"web/src/lib/types.ts lists {sorted(_console_reject_reasons() - server)}, "
        "which RejectReason no longer has"
    )


@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_reject_reason_is_phrased(language: str) -> None:
    catalog = _console_catalog(language)
    missing = [
        reason.value
        for reason in RejectReason
        if f"logs.rejectReason.{reason.value}" not in catalog
    ]
    assert not missing, (
        f"web/src/locales/{language}/console.json has no logs.rejectReason entry for {missing}"
    )


# --------------------------------------------------------------------------
# Proxy import rejections
# --------------------------------------------------------------------------


def _server_import_rejections() -> set[str]:
    parser = PROXY_URLS.read_text(encoding="utf-8")
    reasons = set(re.findall(r"_fail\(\s*\w+\s*,\s*\"([a-z_]+)\"", parser))
    assert reasons, "no _fail(..., reason) calls found; the parser was restructured"
    # The route substitutes its own reason when the parser's details lack one.
    route = PROXY_ROUTE.read_text(encoding="utf-8")
    reasons |= set(re.findall(r"\.get\(\s*\"reason\",\s*\"([a-z_]+)\"", route))
    return reasons


def _console_import_rejections() -> set[str]:
    source = (WEB / "pages" / "Proxies.tsx").read_text(encoding="utf-8")
    return _string_list(source, "const IMPORT_REJECTIONS")


def test_the_console_knows_every_import_rejection() -> None:
    server = _server_import_rejections()
    assert server <= _console_import_rejections(), (
        "web/src/pages/Proxies.tsx does not list "
        f"{sorted(server - _console_import_rejections())}; the import report "
        "would show the raw code"
    )


@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_import_rejection_is_phrased(language: str) -> None:
    catalog = _console_catalog(language)
    missing = [
        code
        for code in sorted(_console_import_rejections())
        if f"proxy.import.reason.{code}" not in catalog
    ]
    assert not missing, (
        f"web/src/locales/{language}/console.json has no proxy.import.reason entry for {missing}"
    )
