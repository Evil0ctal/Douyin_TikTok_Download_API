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
VariantCatalog = Mapping[Language, Mapping[tuple[ErrorCode, str], str]]

#: Separates a code from a cause in a catalogue key: ``SIGNING_FAILED.user_agent_mismatch``
#: is the message for a ``SIGNING_FAILED`` whose ``details.cause`` names that
#: cause. A code's own message says what usually went wrong, and for a cause
#: that is something else - a User-Agent browser-rpc cannot sign under, not an
#: algorithm out of date (#832 review) - the code's sentence points the reader
#: at the wrong thing. The code stays the contract; only the sentence changes.
VARIANT_SEPARATOR = "."


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
        if VARIANT_SEPARATOR in raw_code:
            continue
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


def _load_variants(language: Language) -> dict[tuple[ErrorCode, str], str]:
    """The ``CODE.cause`` entries of one language's catalogue."""
    document = load_locale_json_or_empty(_filename(language))
    variants: dict[tuple[ErrorCode, str], str] = {}
    for key, template in document.items():
        raw_code, separator, cause = key.partition(VARIANT_SEPARATOR)
        if not separator:
            continue
        try:
            code = ErrorCode(raw_code)
        except ValueError:
            logger.warning("i18n.errors.unknown_code", code=key, language=language.value)
            continue
        if not cause or not isinstance(template, str):
            logger.warning("i18n.errors.invalid_template", code=key, language=language.value)
            continue
        variants[(code, cause)] = template
    return variants


def _load_all() -> dict[Language, dict[ErrorCode, str]]:
    return {language: _load(language) for language in SUPPORTED_LANGUAGES}


#: Message templates, keyed by language and then by error code.
MESSAGES: dict[Language, dict[ErrorCode, str]] = _load_all()

#: Messages for one cause of a code, keyed by language and then by
#: ``(code, cause)``; see `VARIANT_SEPARATOR`.
VARIANTS: dict[Language, dict[tuple[ErrorCode, str], str]] = {
    language: _load_variants(language) for language in SUPPORTED_LANGUAGES
}


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


def missing_variants(
    variants: VariantCatalog | None = None,
) -> dict[Language, tuple[tuple[ErrorCode, str], ...]]:
    """``(code, cause)`` messages some language has and another lacks."""
    source = VARIANTS if variants is None else variants
    every = {key for language in SUPPORTED_LANGUAGES for key in source.get(language, {})}
    gaps: dict[Language, tuple[tuple[ErrorCode, str], ...]] = {}
    for language in SUPPORTED_LANGUAGES:
        absent = tuple(sorted(every - set(source.get(language, {}))))
        if absent:
            gaps[language] = absent
    return gaps


def check_catalog(catalog: Catalog | None = None, variants: VariantCatalog | None = None) -> None:
    """Raise :class:`IncompleteCatalog` unless every code has every language.

    The CI gate. Tests call this; the import path does not, because refusing to
    start is a worse answer than serving one message in English. A cause's
    message is held to the same rule: present in one language, present in all.
    """
    gaps = missing_translations(catalog)
    if gaps:
        raise IncompleteCatalog(f"error message catalogue is incomplete -> {_describe(gaps)}")
    variant_gaps = missing_variants(variants)
    if variant_gaps:
        detail = "; ".join(
            f"{language.value}: "
            + ", ".join(f"{code.value}{VARIANT_SEPARATOR}{cause}" for code, cause in keys)
            for language, keys in sorted(variant_gaps.items(), key=lambda item: item[0].value)
        )
        raise IncompleteCatalog(f"error message catalogue is incomplete -> {detail}")


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
    cause = args.get("cause")
    if isinstance(cause, str) and cause:
        variant = VARIANTS.get(lang, {}).get((code, cause)) or VARIANTS.get(
            DEFAULT_LANGUAGE, {}
        ).get((code, cause))
        if variant is not None:
            return interpolate(variant, args, lang, f"error.{code.value}.{cause}")
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
    "VARIANTS",
    "Catalog",
    "IncompleteCatalog",
    "VariantCatalog",
    "check_catalog",
    "missing_translations",
    "missing_variants",
    "render",
    "render_error",
    "template",
]
