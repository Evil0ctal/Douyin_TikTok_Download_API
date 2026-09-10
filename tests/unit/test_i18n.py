"""Backend internationalization: negotiation, catalogues and locale formatting.

Chinese expectations are written as escape sequences. Source files in this
repository stay ASCII so CI can reject CJK characters outright, and a test file
is source like any other; the escapes are annotated with what they read as.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from types import MappingProxyType

import pytest

from dtk.core.errors import ErrorCode, IdentityPoolExhausted, RateLimited, UpstreamChanged
from dtk.core.types import Language
from dtk.i18n import _loader as loader_module
from dtk.i18n import catalog as catalog_module
from dtk.i18n import format as format_module
from dtk.i18n import messages as messages_module
from dtk.i18n.catalog import humanize, interpolate, missing_keys, t
from dtk.i18n.format import (
    RELATIVE_UNITS,
    format_count,
    format_duration,
    format_number,
    format_relative_time,
    formats,
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
    MAX_RANGES,
    SUPPORTED_LANGUAGES,
    coerce_language,
    match_language,
    parse_accept_language,
    resolve_language,
)
from tests.support.marks import is_project_mark

WAN = "\u4e07"  # ten-thousands unit
YI = "\u4ebf"  # hundred-millions unit
JUST_NOW = "\u521a\u521a"  # "just now"
MINUTE = "\u5206\u949f"  # "minute"
HOUR = "\u5c0f\u65f6"  # "hour"
SECOND = "\u79d2"  # "second"
AGO = "\u524d"  # trailing "ago"
LATER = "\u540e"  # trailing "from now"

LOCALES = Path(format_module.__file__).parent / "locales"
_READ = loader_module._read

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
# CJK ideographs plus the CJK punctuation and fullwidth blocks.
_CJK_RE = re.compile("[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


# --------------------------------------------------------------------------
# Accept-Language parsing
# --------------------------------------------------------------------------


def test_parse_orders_by_quality_and_keeps_header_order_on_ties():
    assert parse_accept_language("en;q=0.5,zh;q=0.9,de;q=0.9") == (
        ("zh", 0.9),
        ("de", 0.9),
        ("en", 0.5),
    )


def test_parse_defaults_missing_quality_to_one():
    assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == (
        ("zh-CN", 1.0),
        ("zh", 0.9),
        ("en", 0.8),
    )


def test_parse_drops_zero_quality_because_it_means_not_acceptable():
    assert parse_accept_language("zh;q=0,en;q=0.3") == (("en", 0.3),)


def test_parse_clamps_quality_above_one():
    assert parse_accept_language("en;q=9") == (("en", 1.0),)


def test_parse_accepts_uppercase_q_and_bare_decimal():
    assert parse_accept_language("EN;Q=.5,zh;q=1") == (("zh", 1.0), ("EN", 0.5))


@pytest.mark.parametrize(
    "header",
    [
        "en;q=high",  # quality is not a number
        "en_US",  # underscore is not the RFC separator
        "toolongsubtag-x",  # primary subtag over eight characters
        "en;charset=utf-8",  # only q is allowed
        "!!",  # not a language range at all
    ],
)
def test_parse_ignores_malformed_ranges(header):
    assert parse_accept_language(header) == ()


@pytest.mark.parametrize("header", [None, "", "   ", ",,,"])
def test_parse_handles_absent_or_empty_headers(header):
    assert parse_accept_language(header) == ()


def test_parse_is_bounded_against_a_pathological_header():
    header = ",".join(f"en-{index:03d};q=0.5" for index in range(500))
    assert len(parse_accept_language(header)) <= MAX_RANGES


def test_parse_keeps_the_wildcard_range():
    assert parse_accept_language("*;q=0.4") == (("*", 0.4),)


# --------------------------------------------------------------------------
# Language matching and resolution
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["zh", "zh-CN", "zh-Hans", "zh-TW", "ZH-Hant", "zh-hans-cn"])
def test_regional_chinese_tags_match_by_primary_subtag(tag):
    assert match_language(tag) is Language.ZH


@pytest.mark.parametrize("tag", ["en", "en-GB", "EN-us"])
def test_regional_english_tags_match_by_primary_subtag(tag):
    assert match_language(tag) is Language.EN


@pytest.mark.parametrize("tag", ["fr", "de-DE", "zzz", "e", "chinese", ""])
def test_unsupported_tags_do_not_match(tag):
    assert match_language(tag) is None


def test_coerce_language_accepts_enum_string_and_rejects_junk():
    assert coerce_language(Language.ZH) is Language.ZH
    assert coerce_language("ZH-cn") is Language.ZH
    assert coerce_language("fr") is None
    assert coerce_language(None) is None
    assert coerce_language("  ") is None


def test_query_parameter_wins_over_header():
    assert resolve_language("zh", "en,en-GB;q=0.9") is Language.ZH
    assert resolve_language("en", "zh-CN") is Language.EN


def test_unknown_query_language_falls_through_to_the_header():
    assert resolve_language("fr", "zh-CN,zh;q=0.9") is Language.ZH


def test_header_decides_when_no_query_parameter_is_given():
    assert resolve_language(None, "zh-TW;q=0.9,en;q=0.4") is Language.ZH
    assert resolve_language(None, "en-GB;q=0.9,zh;q=0.4") is Language.EN


def test_unsupported_header_tags_fall_back_to_the_default():
    assert resolve_language(None, "fr,de;q=0.8") is Language.EN
    assert resolve_language(None, "fr,de;q=0.8", "zh") is Language.ZH


def test_wildcard_resolves_to_the_configured_default():
    assert resolve_language(None, "fr;q=0.5,*;q=0.9", "zh") is Language.ZH
    assert resolve_language(None, "*") is Language.EN


def test_a_supported_tag_outranks_a_wildcard_of_lower_quality():
    assert resolve_language(None, "*;q=0.4,en;q=0.9", "zh") is Language.EN


def test_english_is_the_final_fallback():
    assert resolve_language() is Language.EN
    assert resolve_language(None, None, "fr") is Language.EN
    assert resolve_language(None, None, Language.ZH) is Language.ZH


# --------------------------------------------------------------------------
# Error message catalogue
# --------------------------------------------------------------------------


def test_every_error_code_is_translated_in_every_language():
    """Fails the moment an ErrorCode is added without its translations."""
    assert missing_translations() == {}
    for language in SUPPORTED_LANGUAGES:
        assert set(MESSAGES[language]) == set(ErrorCode)


def test_check_catalog_rejects_an_untranslated_code():
    doctored = {
        Language.EN: dict(MESSAGES[Language.EN]),
        Language.ZH: {
            code: text
            for code, text in MESSAGES[Language.ZH].items()
            if code is not ErrorCode.NOT_FOUND
        },
    }
    with pytest.raises(IncompleteCatalog) as excinfo:
        check_catalog(doctored)
    assert "NOT_FOUND" in str(excinfo.value)
    assert "zh" in str(excinfo.value)


def test_check_catalog_accepts_the_shipped_catalogue():
    check_catalog()


def test_an_incomplete_catalogue_degrades_instead_of_breaking_the_import(monkeypatch):
    """A translator's typo costs one English sentence, not the whole instance.

    check_catalog() is the CI gate and stays strict; the import path must not
    raise, or an edited locale file in a self-hosted install stops the service
    from starting at all.
    """
    monkeypatch.setitem(messages_module.MESSAGES, Language.ZH, {})
    messages_module._warn_on_gaps()
    assert render(ErrorCode.NOT_FOUND, Language.ZH) == MESSAGES[Language.EN][ErrorCode.NOT_FOUND]


def test_an_unreadable_locale_file_yields_an_empty_catalogue_not_an_exception(monkeypatch):
    def explode(filename):
        raise loader_module.LocaleFileError(filename)

    monkeypatch.setattr(loader_module, "load_locale_json", explode)
    assert messages_module._load(Language.ZH) == {}


def test_placeholders_are_identical_across_languages():
    """A template argument dropped in translation silently loses information."""
    for code in ErrorCode:
        english = set(_PLACEHOLDER_RE.findall(MESSAGES[Language.EN][code]))
        for language in SUPPORTED_LANGUAGES:
            assert set(_PLACEHOLDER_RE.findall(MESSAGES[language][code])) == english, code


def test_translations_are_not_copies_of_the_english_text():
    for code in ErrorCode:
        assert MESSAGES[Language.ZH][code] != MESSAGES[Language.EN][code], code


def test_render_uses_the_requested_language():
    english = render(ErrorCode.NOT_FOUND, Language.EN)
    chinese = render(ErrorCode.NOT_FOUND, Language.ZH)
    assert english == MESSAGES[Language.EN][ErrorCode.NOT_FOUND]
    assert chinese != english
    assert _CJK_RE.search(chinese)


def test_render_accepts_a_plain_string_language():
    assert render(ErrorCode.NOT_FOUND, "zh") == render(ErrorCode.NOT_FOUND, Language.ZH)


def test_unsupported_language_renders_english():
    assert render(ErrorCode.NOT_FOUND, "fr") == render(ErrorCode.NOT_FOUND, Language.EN)


def test_missing_translation_falls_back_to_english(monkeypatch):
    partial = {
        code: text for code, text in MESSAGES[Language.ZH].items() if code is not ErrorCode.INTERNAL
    }
    monkeypatch.setitem(messages_module.MESSAGES, Language.ZH, partial)
    assert render(ErrorCode.INTERNAL, Language.ZH) == MESSAGES[Language.EN][ErrorCode.INTERNAL]


def test_render_interpolates_arguments_in_both_languages():
    english = render(ErrorCode.IDENTITY_POOL_EXHAUSTED, Language.EN, retry_after=45)
    chinese = render(ErrorCode.IDENTITY_POOL_EXHAUSTED, Language.ZH, retry_after=45)
    assert "45" in english
    assert "45" in chinese
    assert "{" not in english and "{" not in chinese


def test_missing_argument_renders_a_marker_rather_than_a_placeholder():
    english = render(ErrorCode.RATE_LIMITED, Language.EN)
    chinese = render(ErrorCode.RATE_LIMITED, Language.ZH)
    assert "{retry_after}" not in english
    assert "{retry_after}" not in chinese
    assert catalog_module.unknown_text(Language.EN) in english
    assert catalog_module.unknown_text(Language.ZH) in chinese


def test_none_argument_counts_as_missing_and_never_prints_none():
    assert "None" not in render(ErrorCode.RATE_LIMITED, Language.EN, retry_after=None)


def test_unexpected_arguments_are_ignored():
    assert (
        render(ErrorCode.NOT_FOUND, Language.EN, whatever=1)
        == MESSAGES[Language.EN][ErrorCode.NOT_FOUND]
    )


def test_render_error_uses_the_errors_own_arguments():
    message = render_error(IdentityPoolExhausted(retry_after=45), Language.EN)
    assert "45" in message
    assert "aweme.statistics" in render_error(UpstreamChanged("aweme.statistics"), Language.ZH)


def test_render_error_defaults_to_english():
    assert render_error(RateLimited(retry_after=30)) == render(
        ErrorCode.RATE_LIMITED, Language.EN, retry_after=30
    )


def test_error_codes_themselves_are_never_translated():
    for code in ErrorCode:
        for language in SUPPORTED_LANGUAGES:
            assert render(code, language) != code.value


# --------------------------------------------------------------------------
# Generic catalogue
# --------------------------------------------------------------------------


def test_catalogue_keys_match_across_languages():
    assert missing_keys() == {}


def test_catalogue_placeholders_are_identical_across_languages():
    """A notify template that drops an argument in translation loses information."""
    english = catalog_module.catalog(Language.EN)
    for key, template in english.items():
        expected = set(_PLACEHOLDER_RE.findall(template))
        for language in SUPPORTED_LANGUAGES:
            translated = catalog_module.catalog(language).get(key)
            assert translated is not None, (language, key)
            assert set(_PLACEHOLDER_RE.findall(translated)) == expected, (language, key)


def test_no_catalogue_entry_degrades_into_a_word_glued_to_the_marker():
    """Rendered without its arguments, every template must still read as prose.

    "probed again in {retry_after}s" degrades to "in unknowns"; the template
    has to spell the unit out so the fallback stays legible.
    """
    for language in SUPPORTED_LANGUAGES:
        marker = catalog_module.unknown_text(language)
        for key, template in catalog_module.catalog(language).items():
            rendered = interpolate(template, {}, language, key)
            for fragment in rendered.split(marker)[1:]:
                assert not fragment[:1].isalnum(), (language, key, rendered)


def test_t_renders_and_interpolates():
    body = t("notify.pool_below_min.body", Language.EN, platform="douyin", active=1, minimum=3)
    assert "douyin" in body and "1" in body and "3" in body
    chinese = t("notify.pool_below_min.body", Language.ZH, platform="douyin", active=1, minimum=3)
    assert _CJK_RE.search(chinese)
    assert chinese != body


def test_t_falls_back_to_english_when_a_key_is_untranslated(monkeypatch):
    monkeypatch.setitem(catalog_module._cache, Language.ZH, MappingProxyType({}))
    assert t("notify.backup_failed.title", Language.ZH) == t(
        "notify.backup_failed.title", Language.EN
    )


def test_unknown_key_never_renders_as_the_raw_key():
    rendered = t("notify.nothing.here", Language.ZH)
    assert rendered == humanize("notify.nothing.here") == "Here"
    assert "." not in rendered


def test_interpolate_tolerates_argument_names_that_shadow_parameters():
    assert interpolate("{key}/{language}", {"key": "a", "language": "b"}, Language.EN) == "a/b"


@pytest.mark.parametrize(
    "template",
    [
        "{}",  # positional placeholder, no positional arguments
        "{0}",  # same, numbered
        "retry in {retry_after seconds",  # unbalanced brace, a common typo
        "{platform.name} tripped",  # attribute access on a plain value
        "{items[0]} left",  # subscript on a plain value
        "{count:d} items",  # numeric format spec, argument absent
        "}{",  # not a placeholder at all
    ],
)
def test_a_broken_template_never_raises_and_never_leaks_braces(template):
    """Locale files are translator-editable; a typo may not 500 a request."""
    rendered = interpolate(template, {"platform": "douyin", "items": 3}, Language.EN, "notify.x")
    assert "{" not in rendered and "}" not in rendered


def test_degrade_marker_is_inserted_literally_not_as_a_regex_replacement():
    assert catalog_module.degrade("a {x} b", r"\g<0>") == r"a \g<0> b"


def test_unsupported_language_uses_the_english_catalogue():
    assert t("common.unknown", "fr") == t("common.unknown", Language.EN)


# --------------------------------------------------------------------------
# Locale-aware formatting
# --------------------------------------------------------------------------


COUNTS = [
    (0, "0", "0"),
    (1, "1", "1"),
    (999, "999", "999"),
    (1000, "1K", "1000"),
    (1001, "1K", "1001"),
    (1234, "1.23K", "1234"),
    (9999, "10K", "9999"),
    (10000, "10K", f"1{WAN}"),
    (12800, "12.8K", f"1.28{WAN}"),
    (99999, "100K", f"10{WAN}"),
    (100000, "100K", f"10{WAN}"),
    (999999, "1M", f"100{WAN}"),
    (1000000, "1M", f"100{WAN}"),
    (12345678, "12.3M", f"1235{WAN}"),
    (99999999, "100M", f"1{YI}"),
    (100000000, "100M", f"1{YI}"),
    (1000000000, "1B", f"10{YI}"),
    (1234567890, "1.23B", f"12.3{YI}"),
]


@pytest.mark.parametrize(("value", "english", "chinese"), COUNTS)
def test_format_count_boundaries(value, english, chinese):
    assert format_count(value, Language.EN) == english
    assert format_count(value, Language.ZH) == chinese


def test_format_count_keeps_the_sign():
    assert format_count(-12800, Language.EN) == "-12.8K"
    assert format_count(-12800, Language.ZH) == f"-1.28{WAN}"


def test_format_count_of_none_is_none_not_zero():
    assert format_count(None, Language.EN) is None
    assert format_count(None, Language.ZH) is None


def test_format_count_falls_back_to_english_for_an_unknown_language():
    assert format_count(12800, "fr") == "12.8K"


def _decode_count(rendered: str, language: Language) -> float:
    """Turn a rendered count back into a number, using the locale's own scale."""
    for unit in formats(language).scale:
        if unit.suffix and rendered.endswith(unit.suffix):
            return float(rendered[: -len(unit.suffix)]) * unit.threshold
    return float(rendered)


def test_format_count_is_monotonic_across_the_scale():
    """Rounding must never make a larger count read as a smaller one."""
    values = (999, 1000, 9999, 10000, 99999, 999999, 12345678, 99999999, 100000000)
    for language in SUPPORTED_LANGUAGES:
        decoded = []
        for value in values:
            rendered = format_count(value, language)
            assert rendered is not None
            decoded.append(_decode_count(rendered, language))
        assert decoded == sorted(decoded)


def test_format_reload_re_reads_the_locale_file_from_disk(tmp_path, monkeypatch):
    """reload() must drop the loader's cache too, or it hands back stale data."""
    formats(Language.EN)
    patched = dict(json.loads((LOCALES / "format.en.json").read_text(encoding="utf-8")))
    patched["relative"] = dict(patched["relative"], now="moments ago")
    monkeypatch.setattr(
        loader_module,
        "_read",
        lambda filename: patched if filename == "format.en.json" else _READ(filename),
    )
    format_module.reload()
    try:
        assert formats(Language.EN).now == "moments ago"
    finally:
        monkeypatch.undo()
        # Explicit, so a regression in reload() fails this test alone instead
        # of leaking a patched locale into every test that runs after it.
        loader_module.clear_locale_cache()
        format_module._cache.clear()
    assert formats(Language.EN).now == "just now"


def test_locale_formats_are_read_only():
    with pytest.raises(TypeError):
        formats(Language.EN).units["hour"]["one"] = "h"  # type: ignore[index]


def test_format_number_is_exact_and_grouped():
    assert format_number(1234567, Language.EN) == "1,234,567"
    assert format_number(1234567, Language.ZH) == "1,234,567"
    assert format_number(None, Language.EN) is None


NOW = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def test_relative_time_recent_reads_as_just_now():
    recent = NOW - dt.timedelta(seconds=5)
    assert format_relative_time(recent, Language.EN, now=NOW) == "just now"
    assert format_relative_time(recent, Language.ZH, now=NOW) == JUST_NOW


def test_relative_time_past_uses_the_largest_whole_unit():
    assert format_relative_time(NOW - dt.timedelta(minutes=3), Language.EN, now=NOW) == (
        "3 minutes ago"
    )
    assert format_relative_time(NOW - dt.timedelta(minutes=3), Language.ZH, now=NOW) == (
        f"3{MINUTE}{AGO}"
    )
    assert format_relative_time(NOW - dt.timedelta(hours=1), Language.EN, now=NOW) == "1 hour ago"
    assert format_relative_time(NOW - dt.timedelta(hours=1), Language.ZH, now=NOW) == (
        f"1{HOUR}{AGO}"
    )


def test_relative_time_future():
    assert format_relative_time(NOW + dt.timedelta(minutes=90), Language.EN, now=NOW) == (
        "in 1 hour"
    )
    assert format_relative_time(NOW + dt.timedelta(minutes=90), Language.ZH, now=NOW) == (
        f"1{HOUR}{LATER}"
    )


def test_relative_time_pluralizes_english_and_leaves_chinese_alone():
    one = NOW - dt.timedelta(days=1)
    many = NOW - dt.timedelta(days=3)
    assert format_relative_time(one, Language.EN, now=NOW) == "1 day ago"
    assert format_relative_time(many, Language.EN, now=NOW) == "3 days ago"
    assert (
        format_relative_time(one, Language.ZH, now=NOW)[1:]
        == (format_relative_time(many, Language.ZH, now=NOW)[1:])
    )


def test_relative_time_treats_naive_timestamps_as_utc():
    naive = (NOW - dt.timedelta(hours=2)).replace(tzinfo=None)
    assert format_relative_time(naive, Language.EN, now=NOW) == "2 hours ago"


def test_relative_time_defaults_to_the_current_moment():
    assert format_relative_time(dt.datetime.now(dt.UTC), Language.EN) == "just now"


def test_format_duration():
    assert format_duration(45, Language.EN) == "45 seconds"
    assert format_duration(45, Language.ZH) == f"45{SECOND}"
    assert format_duration(3600, Language.EN) == "1 hour"
    assert format_duration(3600, Language.ZH) == f"1{HOUR}"
    assert format_duration(None, Language.EN) is None


def test_every_relative_unit_is_named_in_every_language():
    for language in SUPPORTED_LANGUAGES:
        data = formats(language)
        for unit, _seconds in RELATIVE_UNITS:
            assert data.unit_name(unit, 1)
            assert data.unit_name(unit, 5)
            assert unit in data.units, (language, unit)


# --------------------------------------------------------------------------
# Source and locale hygiene
# --------------------------------------------------------------------------


def test_python_sources_contain_no_cjk_characters():
    """docs/design/14-i18n.md makes this a CI check, not a convention.

    The whole tree, not just this package: writing a Chinese comment is the
    low-effort choice, so only a machine check holds the line.
    """
    root = Path(__file__).resolve().parents[2]
    sources = sorted((root / "src").rglob("*.py")) + sorted((root / "tests").rglob("*.py"))
    assert len(sources) > 10, "source discovery is broken; the check would pass vacuously"
    for path in sources:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # The project's mark is the one exception, and it is recognised by
            # its own alphabet rather than by which file it is in.
            assert not _CJK_RE.search(line) or is_project_mark(line), f"{path}:{lineno}"


def _leaves(document, path=""):
    for key, value in document.items():
        where = f"{path}{key}"
        if isinstance(value, dict):
            yield from _leaves(value, f"{where}.")
        else:
            yield where, value


#: Catalogues of translated text. format.*.json is excluded on purpose: it
#: holds the scale structure, which differs in shape between the languages.
TRANSLATION_FILES = ("en.json", "zh.json", "errors.en.json", "errors.zh.json")


def test_locale_files_are_non_empty_json_objects():
    names = {path.name for path in LOCALES.glob("*.json")}
    assert set(TRANSLATION_FILES) <= names, names
    for path in sorted(LOCALES.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict) and document, path


def test_translation_leaves_are_all_non_empty_strings():
    """A non-string leaf is dropped at load time, so the key silently vanishes."""
    for name in TRANSLATION_FILES:
        document = json.loads((LOCALES / name).read_text(encoding="utf-8"))
        for key, value in _leaves(document):
            assert isinstance(value, str), (name, key, type(value).__name__)
            assert value.strip(), (name, key)


def test_format_scales_are_well_formed_and_strictly_descending():
    """_parse_scale drops malformed entries silently; catch them here instead."""
    for language in SUPPORTED_LANGUAGES:
        raw = json.loads((LOCALES / f"format.{language.value}.json").read_text(encoding="utf-8"))
        entries = raw["count"]["scale"]
        assert entries, language
        for entry in entries:
            assert isinstance(entry["threshold"], int) and entry["threshold"] > 0, language
            assert isinstance(entry["suffix"], str) and entry["suffix"], language
        thresholds = [entry["threshold"] for entry in entries]
        assert thresholds == sorted(set(thresholds), reverse=True), language
        assert len(formats(language).scale) == len(entries), language


def test_translated_files_are_actually_translated():
    for name in ("zh.json", "errors.zh.json"):
        document = json.loads((LOCALES / name).read_text(encoding="utf-8"))
        assert any(_CJK_RE.search(value) for _key, value in _leaves(document)), name


def test_error_locale_files_use_error_code_names_as_keys():
    known = {code.value for code in ErrorCode}
    for language in SUPPORTED_LANGUAGES:
        path = LOCALES / f"errors.{language.value}.json"
        assert set(json.loads(path.read_text(encoding="utf-8"))) == known, path


# --------------------------------------------------------------------------
# Settings help text
#
# The registry declares a setting; the catalogue explains it. Nothing in the
# type system connects the two, so a setting added without a catalogue entry
# silently falls back to the registry's English note - which is how ten of the
# original settings reached the console with no description at all, and the
# other twenty-six reached the Chinese console in English.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("language", [lang.value for lang in Language])
def test_every_setting_is_explained_in_every_language(language: str) -> None:
    from dtk.core.config import RUNTIME_SETTINGS
    from dtk.i18n.catalog import has

    missing = [
        key for key in sorted(RUNTIME_SETTINGS) if not has(f"settings.description.{key}", language)
    ]
    assert not missing, (
        f"{language}: settings with no catalogue description: {missing}. Add "
        f"settings.description.<key> to src/dtk/i18n/locales/{language}.json; the "
        "registry's own description is a note to developers, not the text a user reads."
    )


def test_a_setting_description_is_prose_rather_than_the_key_echoed_back() -> None:
    """The fallback chain must never surface a humanized key as help text."""
    from dtk.core.config import RUNTIME_SETTINGS
    from dtk.i18n.catalog import humanize, t

    for key in sorted(RUNTIME_SETTINGS):
        for language in Language:
            text = t(f"settings.description.{key}", language)
            assert text != humanize(f"settings.description.{key}"), (
                f"{language.value}: {key} renders as its own key rather than an explanation"
            )
            assert text.strip(), f"{language.value}: {key} has an empty description"
            # A minimum length was tried here and removed. It failed on the
            # Chinese text for retention.task_days, which says in 14 characters
            # exactly what its 47-character English counterpart says. Counting
            # characters to judge whether prose is substantial is an English
            # assumption, and this file is the last place that should carry one.


# --------------------------------------------------------------------------
# OpenAPI prose
#
# The same gap as the settings block above, in the document users actually
# read. A route declares `openapi_extra={I18N_KEY: "..."}`; nothing checks that
# the catalogue answers to that name, which is how the five endpoints the API
# exists for reached Swagger UI with a one-line summary and no description at
# all - in either language.
#
# English prose comes from the route's docstring, not the catalogue, because a
# docstring sits next to the signature it describes. The English catalogue
# entry still has to exist: `missing_keys` compares the languages symmetrically,
# so a Chinese-only key is itself a failure.
# --------------------------------------------------------------------------


def _documented_operations():
    """Every operation in the schema that names a catalogue entry."""
    from dtk.api.app import create_app
    from dtk.api.routes.openapi import I18N_KEY

    schema = create_app().openapi()
    for path, methods in sorted(schema.get("paths", {}).items()):
        for method, operation in sorted(methods.items()):
            key = operation.get(I18N_KEY)
            if isinstance(key, str) and key:
                yield f"{method.upper()} {path}", key, operation


def test_every_i18n_keyed_operation_is_explained_in_every_language() -> None:
    from dtk.i18n.catalog import has

    missing = [
        f"{where} -> openapi.op.{key}.{field} ({language})"
        for where, key, _ in _documented_operations()
        for language in (lang.value for lang in Language)
        for field in ("summary", "description")
        if not has(f"openapi.op.{key}.{field}", language)
    ]
    assert not missing, (
        "operations with no catalogue prose:\n"
        + "\n".join(missing)
        + "\nAdd them to src/dtk/i18n/locales/<lang>.json under openapi.op.<key>."
    )


def test_every_documented_operation_has_an_english_description() -> None:
    """Which, for English, means the route function needs a docstring."""
    bare = [
        where
        for where, _key, op in _documented_operations()
        if not (op.get("description") or "").strip()
    ]
    assert not bare, (
        "operations rendering with no description in English:\n"
        + "\n".join(bare)
        + "\nGive the route function a docstring; FastAPI uses it as the description."
    )


def test_documented_operations_describe_every_parameter_they_accept() -> None:
    """A parameter with no description is a name and a type, which is a guess.

    `lang` is exempt: it is appended to every operation by the localizer itself
    and carries its own catalogue text.
    """
    bare = [
        f"{where} -> {parameter.get('name')}"
        for where, _key, operation in _documented_operations()
        for parameter in operation.get("parameters", [])
        if parameter.get("name") != "lang" and not (parameter.get("description") or "").strip()
    ]
    assert not bare, (
        "parameters with no description:\n"
        + "\n".join(bare)
        + "\nSet description= on the Query/Path declaration."
    )


def test_every_written_diagnose_action_is_one_a_step_can_reach() -> None:
    """A catalogue entry nothing renders is a paragraph nobody reads.

    `signing_not_comparable` was written in both languages and left out of
    ACTIONABLE_CODES, so the self-check showed "cannot compare native and
    browser signing for douyin" with no explanation under it - which reads as a
    fault the operator introduced, over a step that is working as designed.
    """
    from dtk.ops.diagnose import ACTIONABLE_CODES

    for language in ("en", "zh"):
        catalogue = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
        written = set(catalogue["diagnose"]["action"])
        unreachable = sorted(written - ACTIONABLE_CODES)
        assert not unreachable, (
            f"{language}: diagnose actions written but unreachable, because their "
            f"code is not in ACTIONABLE_CODES: {unreachable}"
        )
