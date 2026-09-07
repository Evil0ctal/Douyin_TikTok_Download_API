"""Align the browser's locale and clock with the proxy exit.

A German exit reporting `Asia/Shanghai` is a tell given away for free: the
platform sees the mismatch in one JavaScript call and never has to look at the
request rate. Timezone, locale and `Accept-Language` therefore come from where
the traffic actually leaves, not from the host running the container.

Resolution order, most trustworthy first:

1. explicit fields in the caller's ``geo_hint`` - the pool knows what it bought
2. the country of the exit, measured by asking an echo endpoint *through the
   proxy* before the browser starts
3. the configured default

Step 2 runs before the context is created because timezone and locale have to be
set at context creation; changing them afterwards is visible to the page.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GeoProfile:
    """Everything the browser needs to look like it belongs at the exit."""

    timezone: str
    locale: str
    languages: str
    country: str | None = None


@dataclass(frozen=True, slots=True)
class ExitInfo:
    """What the echo endpoint saw. Both fields are None when the probe failed."""

    ip: str | None = None
    country: str | None = None


#: One representative zone and locale per country. A country spanning several
#: zones gets the one holding most of its population: being in the right country
#: with the wrong city is ordinary, being on the wrong continent is not.
COUNTRY_PROFILES: Mapping[str, GeoProfile] = {
    "AE": GeoProfile("Asia/Dubai", "ar-AE", "ar-AE,ar;q=0.9,en;q=0.8"),
    "AU": GeoProfile("Australia/Sydney", "en-AU", "en-AU,en;q=0.9"),
    "BR": GeoProfile("America/Sao_Paulo", "pt-BR", "pt-BR,pt;q=0.9,en;q=0.8"),
    "CA": GeoProfile("America/Toronto", "en-CA", "en-CA,en;q=0.9,fr-CA;q=0.8"),
    "CH": GeoProfile("Europe/Zurich", "de-CH", "de-CH,de;q=0.9,en;q=0.8"),
    "CN": GeoProfile("Asia/Shanghai", "zh-CN", "zh-CN,zh;q=0.9"),
    "DE": GeoProfile("Europe/Berlin", "de-DE", "de-DE,de;q=0.9,en;q=0.8"),
    "ES": GeoProfile("Europe/Madrid", "es-ES", "es-ES,es;q=0.9,en;q=0.8"),
    "FR": GeoProfile("Europe/Paris", "fr-FR", "fr-FR,fr;q=0.9,en;q=0.8"),
    "GB": GeoProfile("Europe/London", "en-GB", "en-GB,en;q=0.9"),
    "HK": GeoProfile("Asia/Hong_Kong", "zh-HK", "zh-HK,zh;q=0.9,en;q=0.8"),
    "ID": GeoProfile("Asia/Jakarta", "id-ID", "id-ID,id;q=0.9,en;q=0.8"),
    "IN": GeoProfile("Asia/Kolkata", "en-IN", "en-IN,en;q=0.9,hi;q=0.8"),
    "IT": GeoProfile("Europe/Rome", "it-IT", "it-IT,it;q=0.9,en;q=0.8"),
    "JP": GeoProfile("Asia/Tokyo", "ja-JP", "ja-JP,ja;q=0.9,en;q=0.8"),
    "KR": GeoProfile("Asia/Seoul", "ko-KR", "ko-KR,ko;q=0.9,en;q=0.8"),
    "MX": GeoProfile("America/Mexico_City", "es-MX", "es-MX,es;q=0.9,en;q=0.8"),
    "MY": GeoProfile("Asia/Kuala_Lumpur", "ms-MY", "ms-MY,ms;q=0.9,en;q=0.8"),
    "NL": GeoProfile("Europe/Amsterdam", "nl-NL", "nl-NL,nl;q=0.9,en;q=0.8"),
    "PH": GeoProfile("Asia/Manila", "en-PH", "en-PH,en;q=0.9,fil;q=0.8"),
    "PL": GeoProfile("Europe/Warsaw", "pl-PL", "pl-PL,pl;q=0.9,en;q=0.8"),
    "RU": GeoProfile("Europe/Moscow", "ru-RU", "ru-RU,ru;q=0.9,en;q=0.8"),
    "SE": GeoProfile("Europe/Stockholm", "sv-SE", "sv-SE,sv;q=0.9,en;q=0.8"),
    "SG": GeoProfile("Asia/Singapore", "en-SG", "en-SG,en;q=0.9,zh-CN;q=0.8"),
    "TH": GeoProfile("Asia/Bangkok", "th-TH", "th-TH,th;q=0.9,en;q=0.8"),
    "TR": GeoProfile("Europe/Istanbul", "tr-TR", "tr-TR,tr;q=0.9,en;q=0.8"),
    "TW": GeoProfile("Asia/Taipei", "zh-TW", "zh-TW,zh;q=0.9,en;q=0.8"),
    "US": GeoProfile("America/New_York", "en-US", "en-US,en;q=0.9"),
    "VN": GeoProfile("Asia/Ho_Chi_Minh", "vi-VN", "vi-VN,vi;q=0.9,en;q=0.8"),
}

FALLBACK_PROFILE = GeoProfile(timezone="UTC", locale="en-US", languages="en-US,en;q=0.9")

#: Keys an echo endpoint may use for the country. ipinfo.io says "country",
#: ip-api.com says "countryCode"; accepting both keeps the setting swappable.
_COUNTRY_KEYS = ("country", "country_code", "countryCode")
_IP_KEYS = ("ip", "query", "ip_address")


def profile_for_country(country: str | None) -> GeoProfile | None:
    """The profile for a two-letter country code, or None when unknown."""
    if not country:
        return None
    code = country.strip().upper()
    profile = COUNTRY_PROFILES.get(code)
    if profile is None:
        return None
    return GeoProfile(
        timezone=profile.timezone,
        locale=profile.locale,
        languages=profile.languages,
        country=code,
    )


def resolve(
    geo_hint: Mapping[str, Any] | None,
    exit_country: str | None = None,
    default_country: str = "US",
) -> GeoProfile:
    """Combine caller hint, measured exit and default into one profile.

    Individual fields override individually: a caller that knows only the
    timezone still gets a locale that matches the exit country.
    """
    hint = geo_hint or {}
    hinted_country = _first_str(hint, ("country", "country_code", "countryCode"))

    base = (
        profile_for_country(hinted_country)
        or profile_for_country(exit_country)
        or profile_for_country(default_country)
        or FALLBACK_PROFILE
    )

    timezone = _first_str(hint, ("timezone", "timezone_id", "tz")) or base.timezone
    locale = _first_str(hint, ("locale", "language")) or base.locale
    languages = _first_str(hint, ("languages", "accept_language")) or base.languages
    country = hinted_country or base.country or exit_country

    return GeoProfile(
        timezone=timezone,
        locale=locale,
        languages=languages,
        country=country.upper() if country else None,
    )


async def probe_exit(
    proxy: str | None,
    probe_url: str | None,
    timeout: float,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> ExitInfo:
    """Ask an echo endpoint, through the proxy, where the traffic comes out.

    Never raises. A failed probe means the geo hint and the default decide,
    which is a worse identity but still a usable one - refusing to mint because
    an unrelated third-party endpoint is down would be the wrong trade.
    """
    if not probe_url:
        return ExitInfo()

    try:
        kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
        if proxy:
            kwargs["proxy"] = proxy
        async with client_factory(**kwargs) as client:
            response = await client.get(probe_url)
            response.raise_for_status()
            payload = response.json()
    # Any failure here - DNS, TLS, a dead endpoint, a proxy that refuses
    # CONNECT - degrades to "unknown exit" rather than failing the mint.
    except Exception as exc:
        logger.warning("geo.probe.failed error=%s", exc)
        return ExitInfo()

    if not isinstance(payload, dict):
        logger.warning("geo.probe.unexpected_body type=%s", type(payload).__name__)
        return ExitInfo()

    ip = _first_str(payload, _IP_KEYS)
    country = _first_str(payload, _COUNTRY_KEYS)
    logger.info("geo.probe.done ip=%s country=%s", ip, country)
    return ExitInfo(ip=ip, country=country.upper() if country else None)


def _first_str(source: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


__all__ = [
    "COUNTRY_PROFILES",
    "FALLBACK_PROFILE",
    "ExitInfo",
    "GeoProfile",
    "probe_exit",
    "profile_for_country",
    "resolve",
]
