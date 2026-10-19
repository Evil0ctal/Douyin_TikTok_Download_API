"""The Notifications page's trigger table is the server's, copied.

``TRIGGERS`` in ``web/src/pages/Notifications.tsx`` mirrors the one in
:mod:`dtk.ops.notify`, and nothing held the two together. The server gained
``capacity_warning``, ``capacity_paused`` and ``media_evicted``; the page did
not, could not offer them, and - because it also dropped event names it did not
know - saving any channel re-subscribed a channel narrowed to one of them to
every alert there is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dtk.ops.notify import TRIGGERS, NotifyEvent

WEB = Path(__file__).resolve().parents[2] / "web" / "src"
PAGE = WEB / "pages" / "Notifications.tsx"

_ROW = re.compile(
    r"\{ event: '(?P<event>\w+)', severity: '(?P<severity>\w+)', "
    r"dedupSeconds: (?P<count>\d+) \* (?P<unit>MINUTE|HOUR), "
    r"scope: (?:'(?P<scope>\w+)'|null) \}"
)
_UNIT = {"MINUTE": 60, "HOUR": 3600}


def _console_triggers() -> dict[str, tuple[str, int, str | None]]:
    if not PAGE.exists():
        pytest.skip("console not present")
    block = re.search(
        r"const TRIGGERS: readonly TriggerSpec\[\] = \[(.*?)\n\]", PAGE.read_text(), re.S
    )
    assert block is not None, "TRIGGERS is no longer declared the way this test reads it"
    rows = {
        match["event"]: (
            match["severity"],
            int(match["count"]) * _UNIT[match["unit"]],
            match["scope"],
        )
        for match in _ROW.finditer(block.group(1))
    }
    lines = [line for line in block.group(1).splitlines() if "event:" in line]
    assert len(rows) == len(lines), "a TRIGGERS row is written in a shape this test cannot read"
    return rows


def _server_triggers() -> dict[str, tuple[str, int, str | None]]:
    return {
        event.value: (
            spec.severity.value,
            spec.dedup_seconds,
            spec.scope_fields[0] if spec.scope_fields else None,
        )
        for event, spec in TRIGGERS.items()
        # The manual test is a button on the page, not a subscribable event.
        if event is not NotifyEvent.TEST
    }


def test_the_page_lists_every_alert_the_server_raises() -> None:
    assert _console_triggers() == _server_triggers()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_alert_is_explained_in_both_languages(language: str) -> None:
    console = json.loads((WEB / "locales" / language / "console.json").read_text("utf-8"))
    explained = console["notifications"]["eventText"]
    missing = sorted(set(_server_triggers()) - set(explained))
    assert not missing, f"{language}: notifications.eventText is missing {missing}"
