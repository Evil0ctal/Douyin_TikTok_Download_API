"""Locale file access.

Translated text lives in JSON files inside this package rather than in Python
source. Two reasons, both from docs/design/14-i18n.md: source files stay ASCII
so CI can reject CJK characters in ``.py`` outright, and a translator can edit a
catalogue without reading Python.

Files are read once and cached. Tests that rewrite a catalogue must call
:func:`clear_locale_cache` afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any

from dtk.core.logging import get_logger

logger = get_logger(__name__)

#: Package holding the JSON catalogues.
LOCALES_PACKAGE = "dtk.i18n.locales"


class LocaleFileError(RuntimeError):
    """A locale file is missing, unreadable, or is not a JSON object."""


def _read(filename: str) -> dict[str, Any]:
    try:
        raw = (resources.files(LOCALES_PACKAGE) / filename).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise LocaleFileError(f"locale file not readable: {filename}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocaleFileError(f"locale file is not valid JSON: {filename}") from exc
    if not isinstance(document, dict):
        raise LocaleFileError(f"locale file must hold a JSON object: {filename}")
    return document


@lru_cache(maxsize=32)
def load_locale_json(filename: str) -> dict[str, Any]:
    """Read one locale file, cached. Raises :class:`LocaleFileError`."""
    return _read(filename)


def load_locale_json_or_empty(filename: str) -> dict[str, Any]:
    """Same as :func:`load_locale_json` but degrades to an empty document.

    Used where a missing translation should leave the process running on the
    English fallback instead of taking the API down.
    """
    try:
        return load_locale_json(filename)
    except LocaleFileError:
        logger.error("i18n.locale.load_failed", filename=filename)
        return {}


def clear_locale_cache() -> None:
    """Drop cached locale documents."""
    load_locale_json.cache_clear()


def flatten(document: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a nested catalogue into dotted keys with string values.

    Values that are neither a nested object nor a string are dropped with a
    warning: a translator who types a number where a template belongs should not
    be able to crash a request.
    """
    flat: dict[str, str] = {}
    for key, value in document.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(flatten(value, f"{path}."))
        elif isinstance(value, str):
            flat[path] = value
        else:
            logger.warning("i18n.locale.non_string_value", key=path, type=type(value).__name__)
    return flat


__all__ = [
    "LOCALES_PACKAGE",
    "LocaleFileError",
    "clear_locale_cache",
    "flatten",
    "load_locale_json",
    "load_locale_json_or_empty",
]
