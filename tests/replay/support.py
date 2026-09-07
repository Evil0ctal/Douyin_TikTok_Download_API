"""Helpers shared by the replay tests.

The replay layer feeds recorded response bodies straight into the parsers with
no network, no database and no configuration; see ``docs/design/13-testing.md``.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: A fixed clock, so ``fetched_at`` is deterministic and parser output can be
#: compared field by field.
FETCHED_AT = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)

#: The fixture set every platform provides. Kept as one list so a platform that
#: forgets a shape fails the suite instead of quietly testing less.
FIXTURE_NAMES: tuple[str, ...] = (
    "video_normal",
    "video_image_album",
    "video_deleted",
    "video_private",
    "video_long_desc",
    "user_profile",
    "user_posts_page1",
    "comments_with_replies",
    "comment_replies_page1",
    "risk_control_empty",
    "risk_control_captcha",
)

#: Key names that must never appear in a fixture. Fixtures come from real
#: captured traffic, and a single echoed credential in a committed file is a
#: leak - see the redaction rules in ``tests/fixtures/README.md``.
#:
#: A bare ``secret`` is deliberately not listed: TikTok's ``itemStruct.secret``
#: is a post visibility flag, and a check that cries wolf on a legitimate field
#: is a check people learn to switch off.
FORBIDDEN_KEY_PATTERN = re.compile(
    r"cookie|token|sessionid|session_id|sid_tt|sid_guard|passport|ttwid|odin_tt"
    r"|verifyfp|s_v_web_id|_signature|authorization|bearer|password"
    r"|api[_-]?key|client[_-]?secret|access[_-]?key|credential",
    re.IGNORECASE,
)

_INDEX = re.compile(r"^(?P<key>[^\[\]]+)(?:\[(?P<index>\d+)\])?$")


def load(platform: str, name: str) -> dict[str, Any]:
    """Read one fixture. Returns a fresh copy on every call."""
    path = FIXTURES / platform / f"{name}.json"
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


def walk(payload: Any, path: str) -> Any:
    """Follow a dotted path with optional ``[index]`` segments."""
    node = payload
    for segment in path.split("."):
        match = _INDEX.match(segment)
        assert match is not None, f"bad path segment: {segment}"
        node = node[match.group("key")]
        if match.group("index") is not None:
            node = node[int(match.group("index"))]
    return node


def without(payload: dict[str, Any], path: str) -> dict[str, Any]:
    """Deep copy ``payload`` with the value at ``path`` removed.

    Used to build truncated variants in the test rather than committing a
    fixture per missing field: the point of these cases is the *path* the parser
    reports, and generating them keeps that path visible right next to the
    assertion.
    """
    clone = deepcopy(payload)
    head, _, tail = path.rpartition(".")
    parent = walk(clone, head) if head else clone
    match = _INDEX.match(tail)
    assert match is not None, f"bad path segment: {tail}"
    key = match.group("key")
    index = match.group("index")
    if index is None:
        del parent[key]
    else:
        del parent[key][int(index)]
    return clone


def nulled(payload: dict[str, Any], path: str) -> dict[str, Any]:
    """Deep copy ``payload`` with the value at ``path`` set to ``null``.

    Platforms null a field out at least as often as they drop the key, and the
    parser must treat both the same way.
    """
    clone = deepcopy(payload)
    head, _, tail = path.rpartition(".")
    parent = walk(clone, head) if head else clone
    match = _INDEX.match(tail)
    assert match is not None, f"bad path segment: {tail}"
    key = match.group("key")
    index = match.group("index")
    if index is None:
        parent[key] = None
    else:
        parent[key][int(index)] = None
    return clone


def keys_in(payload: Any) -> set[str]:
    """Every mapping key anywhere in a payload, for the hygiene check."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(key)
            found |= keys_in(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= keys_in(item)
    return found


__all__ = [
    "FETCHED_AT",
    "FIXTURES",
    "FIXTURE_NAMES",
    "FORBIDDEN_KEY_PATTERN",
    "keys_in",
    "load",
    "nulled",
    "walk",
    "without",
]
