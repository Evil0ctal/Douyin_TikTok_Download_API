"""Catalogue for non-error strings.

Notification bodies, OpenAPI summaries and any other server-rendered text live
here, keyed by a dotted ``domain.object.attribute`` identifier. Keys are always
English identifiers; only values are translated.

A key missing from the requested language falls back to English and logs a
warning. It never renders as the raw key: a user seeing
``notify.pool_empty.title`` reads it as a broken product, while an English
sentence in an otherwise Chinese message reads as one untranslated string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n._loader import clear_locale_cache, flatten, load_locale_json_or_empty
from dtk.i18n.negotiate import SUPPORTED_LANGUAGES, coerce_language

logger = get_logger(__name__)

#: Key used for the value substituted when a template argument is missing.
UNKNOWN_KEY = "common.unknown"

_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")

_cache: dict[Language, Mapping[str, str]] = {}


def _filename(language: Language) -> str:
    return f"{language.value}.json"


def catalog(language: Language | str = DEFAULT_LANGUAGE) -> Mapping[str, str]:
    """Return the flattened catalogue for one language."""
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    cached = _cache.get(lang)
    if cached is None:
        cached = MappingProxyType(flatten(load_locale_json_or_empty(_filename(lang))))
        _cache[lang] = cached
    return cached


def keys(language: Language | str = DEFAULT_LANGUAGE) -> frozenset[str]:
    """Every key defined for one language."""
    return frozenset(catalog(language))


def has(key: str, language: Language | str = DEFAULT_LANGUAGE) -> bool:
    """Whether a key is defined for one language, ignoring the fallback."""
    return key in catalog(language)


def missing_keys() -> dict[Language, tuple[str, ...]]:
    """Keys present in some language but absent from another.

    English is the union's anchor in practice, but the comparison is symmetric
    so a Chinese-only key is reported too: a key nobody can read in English is
    just as broken.
    """
    union: set[str] = set()
    per_language: dict[Language, frozenset[str]] = {}
    for language in SUPPORTED_LANGUAGES:
        per_language[language] = keys(language)
        union |= per_language[language]
    gaps: dict[Language, tuple[str, ...]] = {}
    for language, defined in per_language.items():
        absent = union - defined
        if absent:
            gaps[language] = tuple(sorted(absent))
    return gaps


def humanize(key: str) -> str:
    """Last-resort rendering for a key no language defines."""
    tail = key.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ").strip()
    if not tail:
        return "unknown"
    return tail[0].upper() + tail[1:]


class _Arguments(dict[str, object]):
    """Format mapping that reports gaps instead of raising."""

    def __init__(self, values: Mapping[str, object], key: str, language: Language) -> None:
        super().__init__(values)
        self._key = key
        self._language = language

    def __missing__(self, name: str) -> str:
        logger.warning(
            "i18n.template.missing_argument",
            key=self._key,
            argument=name,
            language=self._language.value,
        )
        return unknown_text(self._language)


def unknown_text(language: Language | str = DEFAULT_LANGUAGE) -> str:
    """Placeholder used where an argument was not supplied."""
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    return catalog(lang).get(UNKNOWN_KEY) or catalog(DEFAULT_LANGUAGE).get(UNKNOWN_KEY) or "unknown"


def degrade(template: str, marker: str) -> str:
    """Strip a template down to readable text when it cannot be formatted.

    Placeholders become the "unknown" marker. Any brace left over belongs to a
    malformed placeholder such as ``retry in {retry_after seconds`` and is
    dropped: a stray ``{`` in a notification reads as a broken product, which is
    the failure mode the whole fallback chain exists to avoid.
    """
    text = _PLACEHOLDER_RE.sub(lambda _match: marker, template)
    return text.replace("{", "").replace("}", "")


def interpolate(
    template: str,
    args: Mapping[str, object],
    language: Language | str = DEFAULT_LANGUAGE,
    key: str = "",
) -> str:
    """Fill a template, substituting a readable marker for missing arguments.

    Arguments explicitly set to ``None`` are treated as absent, so an optional
    ``retry_after`` never renders as the literal ``None``.

    Every way ``str.format_map`` can fail is caught. Locale files are edited by
    translators, so ``{platform.name}`` or ``{items[0]}`` in a template is a
    typo to log, never a 500 on a request that was otherwise fine.
    """
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    supplied = {name: value for name, value in args.items() if value is not None}
    try:
        return template.format_map(_Arguments(supplied, key, lang))
    except (AttributeError, LookupError, TypeError, ValueError) as exc:
        logger.warning("i18n.template.invalid", key=key, language=lang.value, error=str(exc))
        return degrade(template, unknown_text(lang))


def t(
    key: str,
    language: Language | str = DEFAULT_LANGUAGE,
    /,
    **args: object,
) -> str:
    """Render one catalogue entry.

    ``key`` and ``language`` are positional-only so that a template argument may
    itself be named ``key`` or ``language``.
    """
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    template = catalog(lang).get(key)
    if template is None:
        template = catalog(DEFAULT_LANGUAGE).get(key)
        if template is None:
            logger.error("i18n.catalog.unknown_key", key=key, language=lang.value)
            return humanize(key)
        logger.warning("i18n.catalog.missing_key", key=key, language=lang.value)
    return interpolate(template, args, lang, key)


def reload() -> None:
    """Re-read every catalogue from disk."""
    clear_locale_cache()
    _cache.clear()


def _warn_on_parity_gaps() -> None:
    for language, absent in missing_keys().items():
        logger.warning(
            "i18n.catalog.incomplete",
            language=language.value,
            missing=len(absent),
            first=absent[0],
        )


_warn_on_parity_gaps()

__all__ = [
    "UNKNOWN_KEY",
    "catalog",
    "degrade",
    "has",
    "humanize",
    "interpolate",
    "keys",
    "missing_keys",
    "reload",
    "t",
    "unknown_text",
]
