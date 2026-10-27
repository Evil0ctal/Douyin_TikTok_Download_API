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
    "related_posts",
    "music_detail",
    "music_detail_missing",
    "music_posts_page1",
    "hashtag_detail",
    "hashtag_detail_missing",
    "author_mixes",
    "live_room_on",
    "live_room_off",
    "live_room_missing",
    # The same rooms by the broadcast's own id: Douyin's reflow answer,
    # TikTok's webcast host. Bilibili numbers a room once.
    "live_room_by_id_on",
    "live_room_by_id_missing",
    # Many broadcasts at once, both on the webcast host. Bilibili has no such
    # call.
    "live_status",
)

#: Bilibili's set differs where the platform does. It has no image albums, and
#: a deleted or private video is answered with an error code (``-404``,
#: ``62002``) rather than a payload a parser could read - those shapes are in
#: ``tests/unit/test_absence_matrix.py``, where the classifier meets them. Its
#: risk page is HTML and cannot be a JSON fixture; ``risk_control_voucher`` is
#: the JSON refusal. The rest are the shapes it has that the others do not: the
#: ``playurl`` answer, a multi-part video, the guest's one-page comment list and
#: the folder endpoints.
BILIBILI_FIXTURE_NAMES: tuple[str, ...] = (
    "video_normal",
    "video_long_desc",
    "video_multipart",
    "content_streams",
    "user_profile",
    "user_posts_page1",
    "user_posts_empty",
    "comments_with_replies",
    "comments_guest_first_page",
    "comment_replies_page1",
    "user_collections",
    "user_collections_none",
    "collection_posts_page1",
    "risk_control_voucher",
    "content_parts",
    "content_parts_missing",
    "popular_hot_page1",
    "popular_hot_end",
    "popular_weekly_index",
    "popular_weekly_issue",
    "popular_weekly_missing",
    "popular_precious",
    "ranking_all",
    "related_posts",
    "search_posts_page1",
    "search_authors_page1",
    "search_authors_none",
    "author_mixes",
    "mix_posts_page1",
    "mix_posts_missing",
    "live_room_on",
    "live_room_off",
    "live_room_missing",
    "live_streams",
    "trending",
)


#: Douyin's set is the common one plus the shapes only it has: an album of
#: live photos, whose slides each carry a clip of their own (#792), the hot
#: board and a live room's audience ranking.
DOUYIN_FIXTURE_NAMES: tuple[str, ...] = (
    *FIXTURE_NAMES,
    "video_live_photo",
    "trending_hot",
    # A broadcast's audience ranking, which only Douyin has measured.
    "live_ranking",
    "live_ranking_missing",
)

#: TikTok's adds the posts under a hashtag, which Douyin serves only to a
#: signed-in session and no capture of that exists.
TIKTOK_FIXTURE_NAMES: tuple[str, ...] = (
    *FIXTURE_NAMES,
    "hashtag_posts_page1",
    # And search, which Douyin refuses to a guest.
    "search_posts_page1",
    "search_posts_end",
    "search_authors_page1",
    "author_mixes_none",
)


def fixture_names(platform: str) -> tuple[str, ...]:
    """The fixtures ``platform`` ships."""
    if platform == "bilibili":
        return BILIBILI_FIXTURE_NAMES
    return DOUYIN_FIXTURE_NAMES if platform == "douyin" else TIKTOK_FIXTURE_NAMES


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
    "BILIBILI_FIXTURE_NAMES",
    "DOUYIN_FIXTURE_NAMES",
    "FETCHED_AT",
    "FIXTURES",
    "FIXTURE_NAMES",
    "FORBIDDEN_KEY_PATTERN",
    "TIKTOK_FIXTURE_NAMES",
    "fixture_names",
    "keys_in",
    "load",
    "nulled",
    "walk",
    "without",
]
