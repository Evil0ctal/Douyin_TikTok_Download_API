"""Backend internationalization.

English is the default and the fallback; Chinese is the second language. Error
codes, enum values, field names, config keys and log messages are never
translated, only human-facing text is (docs/design/14-i18n.md).

Four pieces, in the order a request meets them::

    negotiate  ?lang= / Accept-Language -> Language
    messages   ErrorCode + Language     -> localized error message
    catalog    dotted key + Language    -> notification and OpenAPI text
    format     count / timestamp        -> locale-aware rendering

Translations live in ``locales/*.json`` so that Python source stays ASCII.
"""

from __future__ import annotations

from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n.catalog import has, interpolate, missing_keys, t
from dtk.i18n.format import (
    format_count,
    format_duration,
    format_number,
    format_relative_time,
)
from dtk.i18n.messages import (
    MESSAGES,
    IncompleteCatalog,
    check_catalog,
    missing_translations,
    render,
    render_error,
)
from dtk.i18n.negotiate import (
    SUPPORTED_LANGUAGES,
    coerce_language,
    parse_accept_language,
    resolve_language,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "MESSAGES",
    "SUPPORTED_LANGUAGES",
    "IncompleteCatalog",
    "Language",
    "check_catalog",
    "coerce_language",
    "format_count",
    "format_duration",
    "format_number",
    "format_relative_time",
    "has",
    "interpolate",
    "missing_keys",
    "missing_translations",
    "parse_accept_language",
    "render",
    "render_error",
    "resolve_language",
    "t",
]
