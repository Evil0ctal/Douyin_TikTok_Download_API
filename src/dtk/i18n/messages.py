"""Localized error messages.

The error code is the contract; only the message is translated. A response
carries both, so a client can branch on ``IDENTITY_POOL_EXHAUSTED`` while a
human reads the sentence in their own language (docs/design/14-i18n.md).

Templates live in ``locales/errors.<lang>.json`` rather than in this module so
that no non-ASCII text sits in Python source.

Completeness is a CI contract, not a runtime one. :func:`check_catalog` raises
:class:`IncompleteCatalog` on the first untranslated code and the unit tests
call it, so a code added without translations cannot merge. At runtime the
opposite policy applies: a gap logs ``i18n.errors.catalog_incomplete`` and the
message falls back to English. Translations always lag features, and the
locale files ship as editable JSON in a self-hosted install, so a translator's
typo must cost one English sentence rather than the whole instance's boot
(docs/design/14-i18n.md).
"""

from __future__ import annotations

from collections.abc import Mapping

from dtk.core.errors import DtkError, ErrorCode
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n._loader import load_locale_json_or_empty
from dtk.i18n.catalog import interpolate
from dtk.i18n.negotiate import SUPPORTED_LANGUAGES, coerce_language

logger = get_logger(__name__)

Catalog = Mapping[Language, Mapping[ErrorCode, str]]


class IncompleteCatalog(RuntimeError):
    """A supported language is missing a template for some error code."""


def _filename(language: Language) -> str:
    return f"errors.{language.value}.json"


def _load(language: Language) -> dict[ErrorCode, str]:
    # An unreadable or malformed file yields an empty catalogue, which renders
    # every message for that language in English. Raising here would make one
    # bad JSON edit stop the process from importing at all.
    document = load_locale_json_or_empty(_filename(language))
    templates: dict[ErrorCode, str] = {}
    for raw_code, template in document.items():
        try:
            code = ErrorCode(raw_code)
        except ValueError:
            # A code removed from the enum but left in the catalogue. Ignore it
            # rather than fail: the stale entry harms nothing.
            logger.warning("i18n.errors.unknown_code", code=raw_code, language=language.value)
            continue
        if not isinstance(template, str):
            logger.warning("i18n.errors.invalid_template", code=raw_code, language=language.value)
            continue
        templates[code] = template
    return templates


def _load_all() -> dict[Language, dict[ErrorCode, str]]:
    return {language: _load(language) for language in SUPPORTED_LANGUAGES}


#: Message templates, keyed by language and then by error code.
MESSAGES: dict[Language, dict[ErrorCode, str]] = _load_all()


def missing_translations(catalog: Catalog | None = None) -> dict[Language, tuple[ErrorCode, ...]]:
    """Error codes with no template, per language. Empty when complete."""
    source = MESSAGES if catalog is None else catalog
    gaps: dict[Language, tuple[ErrorCode, ...]] = {}
    for language in SUPPORTED_LANGUAGES:
        templates = source.get(language, {})
        absent = tuple(code for code in ErrorCode if code not in templates)
        if absent:
            gaps[language] = absent
    return gaps


def _describe(gaps: Mapping[Language, tuple[ErrorCode, ...]]) -> str:
    return "; ".join(
        f"{language.value}: {', '.join(code.value for code in codes)}"
        for language, codes in sorted(gaps.items(), key=lambda item: item[0].value)
    )


def check_catalog(catalog: Catalog | None = None) -> None:
    """Raise :class:`IncompleteCatalog` unless every code has every language.

    The CI gate. Tests call this; the import path does not, because refusing to
    start is a worse answer than serving one message in English.
    """
    gaps = missing_translations(catalog)
    if not gaps:
        return
    raise IncompleteCatalog(f"error message catalogue is incomplete -> {_describe(gaps)}")


def _warn_on_gaps() -> None:
    gaps = missing_translations()
    if gaps:
        logger.error(
            "i18n.errors.catalog_incomplete",
            detail=_describe(gaps),
            codes=sum(len(codes) for codes in gaps.values()),
        )


def template(code: ErrorCode, language: Language | str = DEFAULT_LANGUAGE) -> str | None:
    """The raw template for one code, without interpolation or fallback."""
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    return MESSAGES.get(lang, {}).get(code)


def render(
    code: ErrorCode,
    language: Language | str = DEFAULT_LANGUAGE,
    /,
    **args: object,
) -> str:
    """Render the message for one error code.

    A template missing from the requested language falls back to English with a
    warning; arguments the caller did not supply render as the localized
    "unknown" marker instead of leaving braces on screen. ``code`` and
    ``language`` are positional-only so a template argument may use those names.
    The stable code is returned only when no language defines the template at
    all, which is still more useful to a bug report than an empty string.
    """
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    text = MESSAGES.get(lang, {}).get(code)
    if text is None:
        text = MESSAGES.get(DEFAULT_LANGUAGE, {}).get(code)
        if text is None:
            logger.error("i18n.errors.absent", code=code.value, language=lang.value)
            return code.value
        logger.warning("i18n.errors.missing_translation", code=code.value, language=lang.value)
    return interpolate(text, args, lang, f"error.{code.value}")


def render_error(error: DtkError, language: Language | str = DEFAULT_LANGUAGE) -> str:
    """Render the message for a raised error, using its own format arguments."""
    return render(error.code, language, **error.format_args())


_warn_on_gaps()

__all__ = [
    "MESSAGES",
    "Catalog",
    "IncompleteCatalog",
    "check_catalog",
    "missing_translations",
    "render",
    "render_error",
    "template",
]
