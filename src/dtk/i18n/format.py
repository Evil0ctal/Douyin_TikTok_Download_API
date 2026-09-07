"""Locale-aware number and time formatting.

Unit scaling cannot be solved by translating a string. English groups counts by
thousands (K / M / B); Chinese groups them by ten-thousands, so 12800 reads as
12.8K in one language and 1.28 ten-thousands in the other. The magnitudes
differ, not just the suffix, which is why this dispatches per locale instead of
looking up a word (docs/design/14-i18n.md).

The scales, relative-time patterns and unit names live in
``locales/format.<lang>.json``; this module holds only the arithmetic. Both
languages round to three significant digits, which is what makes 12800 render
as 12.8K and 1.28 wan rather than 13K and 1wan.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Any, Final

from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n._loader import clear_locale_cache, load_locale_json_or_empty
from dtk.i18n.negotiate import coerce_language

logger = get_logger(__name__)

#: Digits kept when a count is scaled onto a unit.
SIGNIFICANT_DIGITS: Final = 3

#: Below this, a timestamp reads as "just now" rather than "12 seconds ago".
JUST_NOW_SECONDS: Final = 45

#: Seconds per unit, largest first. Month and year are the average Gregorian
#: lengths, matching what Intl.RelativeTimeFormat assumes on the frontend.
RELATIVE_UNITS: Final[tuple[tuple[str, int], ...]] = (
    ("year", 31_557_600),
    ("month", 2_629_800),
    ("week", 604_800),
    ("day", 86_400),
    ("hour", 3_600),
    ("minute", 60),
    ("second", 1),
)


@dataclass(frozen=True, slots=True)
class ScaleUnit:
    """One step of a locale's count scale."""

    threshold: int
    suffix: str


@dataclass(frozen=True, slots=True)
class LocaleFormats:
    """Everything one language needs to format a number or a duration."""

    scale: tuple[ScaleUnit, ...]
    group_separator: str
    suffix_separator: str
    now: str
    past: str
    future: str
    duration: str
    units: Mapping[str, Mapping[str, str]]

    def unit_name(self, unit: str, value: int) -> str:
        forms = self.units.get(unit, {})
        return forms.get("one" if abs(value) == 1 else "other") or forms.get("other") or unit


_FALLBACK = LocaleFormats(
    scale=(),
    group_separator=",",
    suffix_separator="",
    now="just now",
    past="{value} {unit} ago",
    future="in {value} {unit}",
    duration="{value} {unit}",
    units=MappingProxyType({}),
)

_cache: dict[Language, LocaleFormats] = {}


def _parse_scale(raw: Any, language: Language) -> tuple[ScaleUnit, ...]:
    units: list[ScaleUnit] = []
    if not isinstance(raw, list):
        logger.error("i18n.format.scale_invalid", language=language.value)
        return ()
    for entry in raw:
        try:
            threshold = int(entry["threshold"])
            suffix = str(entry["suffix"])
        except (KeyError, TypeError, ValueError):
            logger.error("i18n.format.scale_entry_invalid", language=language.value)
            continue
        if threshold > 0:
            units.append(ScaleUnit(threshold=threshold, suffix=suffix))
    units.sort(key=lambda unit: unit.threshold, reverse=True)
    return tuple(units)


def formats(language: Language | str = DEFAULT_LANGUAGE) -> LocaleFormats:
    """Load, and cache, the formatting data for one language."""
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    cached = _cache.get(lang)
    if cached is not None:
        return cached
    document = load_locale_json_or_empty(f"format.{lang.value}.json")
    count = document.get("count", {})
    relative = document.get("relative", {})
    duration = document.get("duration", {})
    units_raw = document.get("units", {})
    # Read-only: this object is cached and handed to every caller, so a
    # mutation here would silently rewrite the locale for the whole process.
    units = MappingProxyType(
        {
            name: MappingProxyType({form: str(text) for form, text in forms.items()})
            for name, forms in units_raw.items()
            if isinstance(forms, dict)
        }
    )
    resolved = LocaleFormats(
        scale=_parse_scale(count.get("scale", []), lang),
        group_separator=str(count.get("group_separator", _FALLBACK.group_separator)),
        suffix_separator=str(count.get("suffix_separator", _FALLBACK.suffix_separator)),
        now=str(relative.get("now", _FALLBACK.now)),
        past=str(relative.get("past", _FALLBACK.past)),
        future=str(relative.get("future", _FALLBACK.future)),
        duration=str(duration.get("pattern", _FALLBACK.duration)),
        units=units,
    )
    _cache[lang] = resolved
    return resolved


def reload() -> None:
    """Drop cached formatting data so locale files are re-read on next use.

    The loader caches the parsed JSON too, so clearing only the derived data
    here would hand back the previous file contents.
    """
    clear_locale_cache()
    _cache.clear()


def _trim(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _round_significant(value: Decimal) -> Decimal:
    """Round to three significant digits, given a value below 1000."""
    if value < 10:
        places = SIGNIFICANT_DIGITS - 1
    elif value < 100:
        places = SIGNIFICANT_DIGITS - 2
    else:
        places = 0
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def format_count(
    value: int | float | None,
    language: Language | str = DEFAULT_LANGUAGE,
) -> str | None:
    """Render a count the way the locale writes large numbers.

    ``12800`` becomes ``12.8K`` in English and ``1.28`` ten-thousands in
    Chinese. Values below the smallest unit are written out in full. ``None``
    stays ``None``: an unknown play count is not zero.
    """
    if value is None:
        return None
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    data = formats(lang)
    number = Decimal(int(value))
    sign = "-" if number < 0 else ""
    magnitude = abs(number)

    for index, unit in enumerate(data.scale):
        if magnitude < unit.threshold:
            continue
        scaled = _round_significant(magnitude / Decimal(unit.threshold))
        # Rounding can push a value onto the next unit: 9999.9 ten-thousands is
        # one hundred million, not 10000 ten-thousands.
        if index > 0 and scaled * unit.threshold >= data.scale[index - 1].threshold:
            unit = data.scale[index - 1]
            scaled = _round_significant(magnitude / Decimal(unit.threshold))
        return f"{sign}{_trim(scaled)}{data.suffix_separator}{unit.suffix}"

    return f"{sign}{_trim(magnitude)}"


def format_number(
    value: int | float | None,
    language: Language | str = DEFAULT_LANGUAGE,
) -> str | None:
    """Render an exact number with the locale's digit grouping."""
    if value is None:
        return None
    data = formats(language)
    grouped = f"{int(value):,}"
    if data.group_separator != ",":
        grouped = grouped.replace(",", data.group_separator)
    return grouped


def _split_units(seconds: int) -> tuple[str, int]:
    """Largest relative unit that fits, and the truncated value in that unit."""
    for name, size in RELATIVE_UNITS:
        if seconds >= size:
            return name, seconds // size
    return "second", seconds


def format_duration(
    seconds: int | float | None,
    language: Language | str = DEFAULT_LANGUAGE,
) -> str | None:
    """Render a length of time, truncated to its largest whole unit."""
    if seconds is None:
        return None
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    data = formats(lang)
    total = int(abs(seconds))
    unit, value = _split_units(total)
    sign = "-" if seconds < 0 else ""
    return sign + data.duration.format(value=value, unit=data.unit_name(unit, value))


def format_relative_time(
    when: dt.datetime,
    language: Language | str = DEFAULT_LANGUAGE,
    /,
    *,
    now: dt.datetime | None = None,
) -> str:
    """Render a timestamp as a distance from ``now``.

    Naive datetimes are read as UTC rather than as local time: every timestamp
    this service stores is UTC, and guessing the process timezone would shift
    "3 minutes ago" by hours on a badly configured host.
    """
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    data = formats(lang)
    reference = now or dt.datetime.now(dt.UTC)
    moment = when if when.tzinfo is not None else when.replace(tzinfo=dt.UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=dt.UTC)

    delta = (reference - moment).total_seconds()
    if abs(delta) < JUST_NOW_SECONDS:
        return data.now

    unit, value = _split_units(int(abs(delta)))
    pattern = data.past if delta > 0 else data.future
    return pattern.format(value=value, unit=data.unit_name(unit, value))


__all__ = [
    "JUST_NOW_SECONDS",
    "RELATIVE_UNITS",
    "SIGNIFICANT_DIGITS",
    "LocaleFormats",
    "ScaleUnit",
    "format_count",
    "format_duration",
    "format_number",
    "format_relative_time",
    "formats",
    "reload",
]
