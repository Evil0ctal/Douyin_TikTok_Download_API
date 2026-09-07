"""Pure helpers shared by every platform parser.

Nothing here does IO. The functions exist so that the two platform parsers make
the same decision about the same ambiguity - an empty string, a numeric id that
arrives as an int, a zero timestamp - instead of each inventing its own rule.

The three normalization rules from ``docs/design/11-data-contracts.md`` are
implemented here once:

* absent is ``None``, never ``0`` or ``""``;
* identifiers are ``str``;
* a missing required field raises :class:`~dtk.core.errors.UpstreamChanged`
  carrying the dotted path that went missing - see :class:`Node`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dtk.core.errors import UpstreamChanged
from dtk.models import Image

#: Upper bound for a plausible second-precision epoch. Platforms occasionally
#: hand back millisecond timestamps in a field documented as seconds; anything
#: past this is treated as milliseconds.
_MAX_EPOCH_SECONDS = 10_000_000_000


def optional_str(value: Any) -> str | None:
    """Normalize to a non-empty string, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, int | float):
        return str(value)
    return None


def optional_id(value: Any) -> str | None:
    """Normalize an identifier that may be absent, zero or a placeholder."""
    text = optional_str(value)
    if text is None or text in {"0", "-1"}:
        return None
    return text


def optional_int(value: Any) -> int | None:
    """Normalize a metric to ``int`` or ``None``.

    Strings are accepted because TikTok's ``statsV2`` returns counts as strings
    to survive numbers above the JavaScript safe integer range. A blank or
    unparsable value is absent, not zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return None
    return None


def optional_positive_int(value: Any) -> int | None:
    """Like :func:`optional_int`, but treat ``0`` and negatives as absent.

    Reserved for fields where zero is a placeholder rather than a measurement:
    a duration of 0 ms and a play count of 0 on an endpoint that never fills it
    in are 'not stated', and recording them as zero puts a fact into the
    snapshot series that the platform never asserted.
    """
    number = optional_int(value)
    return None if number is None or number <= 0 else number


def optional_bool(value: Any, *, default: bool = False) -> bool:
    """Normalize the several ways platforms spell a boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no", ""}:
            return False
    return default


def epoch_to_datetime(value: Any) -> datetime | None:
    """Convert an epoch to a timezone-aware UTC datetime.

    Zero is treated as absent: every platform in scope uses it as a placeholder
    for 'not set', and 1970-01-01 in a creation-time column is worse than a
    missing value.
    """
    number = optional_int(value)
    if number is None or number <= 0:
        return None
    seconds = number / 1000 if number > _MAX_EPOCH_SECONDS else number
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def seconds_to_ms(value: Any) -> int | None:
    """Convert a duration in seconds to milliseconds. Zero counts as absent."""
    seconds = optional_positive_int(value)
    return None if seconds is None else seconds * 1000


def split_title(desc: str) -> tuple[str, str]:
    """Split a single description field into ``(title, description)``.

    Douyin exposes exactly one ``desc`` field. The contract asks for the first
    line as the title and the whole text as the description, and explicitly asks
    that the title not be left empty when a description exists - hence the first
    *non-blank* line rather than ``desc.split("\\n")[0]``.
    """
    description = desc.strip()
    if not description:
        return "", ""
    for line in description.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped, description
    return "", description


def clean_urls(values: Iterable[Any]) -> list[str]:
    """Keep the http(s) URLs from a CDN mirror list, de-duplicated, in order.

    Mirrors are kept in full on purpose: downloads go straight from the browser
    to the CDN, so a client that only ever sees one expired direct link has no
    way to recover.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        url = value.strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def image_from_url_list(node: Any) -> Image | None:
    """Build an :class:`Image` from the ``{url_list, width, height}`` shape.

    This is the Douyin URL container, also used by both platforms' comment
    endpoints, which return the aweme-style user object.
    """
    if not isinstance(node, Mapping):
        return None
    urls = clean_urls(node.get("url_list") or [])
    if not urls:
        return None
    return Image(
        url=urls[0],
        urls=urls,
        width=optional_int(node.get("width")),
        height=optional_int(node.get("height")),
    )


def image_from_urls(*candidates: Any, width: Any = None, height: Any = None) -> Image | None:
    """Build an :class:`Image` from bare URL strings or lists of them."""
    flat: list[Any] = []
    for candidate in candidates:
        if isinstance(candidate, str):
            flat.append(candidate)
        elif isinstance(candidate, Sequence):
            flat.extend(candidate)
    urls = clean_urls(flat)
    if not urls:
        return None
    return Image(
        url=urls[0],
        urls=urls,
        width=optional_int(width),
        height=optional_int(height),
    )


@dataclass(frozen=True, slots=True)
class Node:
    """A payload node that remembers the dotted path it was reached by.

    Parsers walk deeply nested responses, and the whole value of
    :class:`~dtk.core.errors.UpstreamChanged` is the path it carries: a bug
    report saying ``aweme_detail.video.play_addr.url_list`` is actionable, one
    saying 'parse error' is not. Threading the path by hand through every helper
    is where that discipline breaks down, so the node carries it instead.
    """

    data: Mapping[str, Any]
    path: str = ""
    platform: str | None = None

    def at(self, key: str) -> str:
        """The full dotted path of ``key`` within this node."""
        return f"{self.path}.{key}" if self.path else key

    def missing(self, key: str) -> UpstreamChanged:
        return UpstreamChanged(self.at(key), platform=self.platform)

    def get(self, key: str, default: Any = None) -> Any:
        value = self.data.get(key, default)
        return default if value is None else value

    def has(self, key: str) -> bool:
        return key in self.data

    def present(self, key: str) -> Any:
        """Require the key to exist and not be ``null``; any type is accepted."""
        if key not in self.data or self.data[key] is None:
            raise self.missing(key)
        return self.data[key]

    def child(self, key: str) -> Node:
        value = self.present(key)
        if not isinstance(value, Mapping):
            raise self.missing(key)
        return Node(value, self.at(key), self.platform)

    def opt_child(self, key: str) -> Node | None:
        value = self.data.get(key)
        if not isinstance(value, Mapping):
            return None
        return Node(value, self.at(key), self.platform)

    def children(self, key: str) -> list[Node]:
        """Require a list whose every element is an object.

        An element that is not an object is reported rather than skipped: a
        ``null`` in ``aweme_list`` means the caller silently receives one item
        fewer than the platform sent, and a page that is quietly short is the
        silent degradation ``docs/design/11-data-contracts.md`` forbids.
        """
        value = self.present(key)
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise self.missing(key)
        nodes: list[Node] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise UpstreamChanged(f"{self.at(key)}[{index}]", platform=self.platform)
            nodes.append(Node(item, f"{self.at(key)}[{index}]", self.platform))
        return nodes

    def opt_children(self, key: str) -> list[Node]:
        """Best-effort list access: absent is empty, junk elements are skipped.

        Used for decorative lists (hashtags, bitrate gears) where a single odd
        element is not worth failing an otherwise complete answer over.
        """
        value = self.data.get(key)
        if isinstance(value, str) or not isinstance(value, Sequence):
            return []
        return [
            Node(item, f"{self.at(key)}[{index}]", self.platform)
            for index, item in enumerate(value)
            if isinstance(item, Mapping)
        ]

    def id(self, key: str) -> str:
        """Require a non-empty identifier, coerced to ``str``."""
        value = self.present(key)
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise self.missing(key)
        text = str(value).strip()
        if not text:
            raise self.missing(key)
        return text

    def text(self, key: str) -> str:
        """Require a string field whose value may legitimately be empty."""
        value = self.present(key)
        if not isinstance(value, str):
            raise self.missing(key)
        return value

    def raw(self) -> dict[str, Any]:
        return dict(self.data)


__all__ = [
    "Node",
    "clean_urls",
    "epoch_to_datetime",
    "image_from_url_list",
    "image_from_urls",
    "optional_bool",
    "optional_id",
    "optional_int",
    "optional_positive_int",
    "optional_str",
    "seconds_to_ms",
    "split_title",
]
