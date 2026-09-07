"""Language negotiation.

Resolution order is fixed by docs/design/14-i18n.md::

    ?lang= query parameter  ->  Accept-Language header  ->  configured default

Only ``en`` and ``zh`` are recognized. Regional tags match by primary subtag
(``zh-CN``, ``zh-Hans``, ``zh-TW`` all resolve to ``zh``); everything else falls
back to English rather than being half-served in a language we do not have.
"""

from __future__ import annotations

import re

from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language

logger = get_logger(__name__)

#: Languages this build can render. Adding one means adding locale files, not
#: touching this tuple by hand: it is derived from the shared enum.
SUPPORTED_LANGUAGES: tuple[Language, ...] = tuple(Language)

#: Accept-Language is attacker-controlled input. Parsing is bounded so a
#: pathological header cannot turn into CPU time.
MAX_HEADER_CHARS = 512
MAX_RANGES = 32

_WILDCARD = "*"

# RFC 9110 language-range grammar plus the optional q weight. Anything that does
# not match is dropped: guessing at a malformed range is how a client ends up
# silently served the wrong language.
_RANGE_RE = re.compile(
    r"""
    ^\s*
    (?P<tag> \* | [A-Za-z]{1,8} (?:-[A-Za-z0-9]{1,8})* )
    \s*
    (?: ; \s* [Qq] \s* = \s* (?P<q> [0-9](?:\.[0-9]{1,3})? | \.[0-9]{1,3} ) \s* )?
    $
    """,
    re.VERBOSE,
)


def match_language(tag: str) -> Language | None:
    """Map one language tag onto a supported language, or None."""
    primary = tag.split("-", 1)[0].strip().lower()
    for language in SUPPORTED_LANGUAGES:
        if primary == language.value:
            return language
    return None


def coerce_language(value: str | Language | None) -> Language | None:
    """Normalize a caller-supplied language value, or None if unsupported."""
    if value is None:
        return None
    if isinstance(value, Language):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    return match_language(value)


def parse_accept_language(header: str | None) -> tuple[tuple[str, float], ...]:
    """Parse an Accept-Language header into ``(tag, quality)`` pairs.

    Ordered by descending quality, ties keeping header order. Ranges with
    ``q=0`` are dropped because zero means "not acceptable", and ranges that do
    not match the grammar are ignored rather than repaired.
    """
    if not header:
        return ()
    ranges: list[tuple[int, str, float]] = []
    for position, chunk in enumerate(header[:MAX_HEADER_CHARS].split(",")):
        if len(ranges) >= MAX_RANGES:
            break
        matched = _RANGE_RE.match(chunk)
        if matched is None:
            continue
        raw_q = matched.group("q")
        quality = 1.0 if raw_q is None else min(float(raw_q), 1.0)
        if quality <= 0.0:
            continue
        ranges.append((position, matched.group("tag"), quality))
    ranges.sort(key=lambda item: (-item[2], item[0]))
    return tuple((tag, quality) for _, tag, quality in ranges)


def resolve_language(
    query_lang: str | None = None,
    accept_language: str | None = None,
    configured_default: str | Language | None = None,
) -> Language:
    """Pick the response language for one request.

    ``query_lang`` wins when it names a supported language; an unsupported value
    is ignored and negotiation continues with the header, so ``?lang=fr`` still
    honours ``Accept-Language: zh``. A ``*`` range resolves to the configured
    default, which is what "any language will do" means for this service.
    """
    default = coerce_language(configured_default) or DEFAULT_LANGUAGE

    if query_lang:
        chosen = coerce_language(query_lang)
        if chosen is not None:
            return chosen
        logger.debug("i18n.language.unsupported_query", requested=query_lang[:16])

    for tag, _quality in parse_accept_language(accept_language):
        if tag == _WILDCARD:
            return default
        chosen = match_language(tag)
        if chosen is not None:
            return chosen

    return default


__all__ = [
    "MAX_HEADER_CHARS",
    "MAX_RANGES",
    "SUPPORTED_LANGUAGES",
    "coerce_language",
    "match_language",
    "parse_accept_language",
    "resolve_language",
]
