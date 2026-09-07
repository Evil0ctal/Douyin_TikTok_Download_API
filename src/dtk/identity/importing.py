"""Importing a cookie set that the user pasted in.

Users arrive with one of four shapes depending on where they copied from, and
supporting only one of them generates a steady stream of "format error" reports.
All four are recognised automatically here; when recognition fails the caller
shows an example rather than an error code.

The parsed result is reported back before anything is saved so the user can
confirm what was understood: which cookies were found, which of them are the
load-bearing ones, the inferred browser, and - for a logged-in cookie set - when
it expires. See docs/design/07-frontend.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import unquote

from dtk.core.logging import get_logger
from dtk.core.types import BrowserFamily, Platform
from dtk.transport.base import Fingerprint

log = get_logger(__name__)


class CookieFormat(StrEnum):
    HEADER = "header"  # ttwid=a; odin_tt=b
    JSON_ARRAY = "json_array"  # EditThisCookie and similar extensions
    NETSCAPE = "netscape"  # cookies.txt
    LOOSE = "loose"  # one key=value per line, hand-copied


#: Cookies that must be present for a guest identity to be usable.
REQUIRED: dict[Platform, tuple[str, ...]] = {
    Platform.DOUYIN: ("ttwid",),
    Platform.TIKTOK: ("ttwid",),
}

#: Presence of any of these means the cookie set carries a logged-in session,
#: which is far more valuable and far more damaging to leak.
SESSION_MARKERS: frozenset[str] = frozenset(
    {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "sid_ucp_v1"}
)

#: Cookies that materially improve a guest identity but are not required.
USEFUL: frozenset[str] = frozenset(
    {"odin_tt", "s_v_web_id", "msToken", "passport_csrf_token", "tt_csrf_token", "__ac_nonce"}
)


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What was understood from the pasted text, shown before saving."""

    detected_format: CookieFormat
    cookies: dict[str, str]
    platform: Platform
    authenticated: bool
    fingerprint: Fingerprint
    expires_at: datetime | None
    missing_required: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return not self.missing_required and self.fingerprint.emulatable

    def masked(self) -> dict[str, str]:
        """Cookie names with masked values, for display. Never the real values."""
        return {k: _mask(v) for k, v in self.cookies.items()}


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


# --- format detection and parsing ---------------------------------------------


def detect_format(text: str) -> CookieFormat:
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return CookieFormat.JSON_ARRAY
    if "\t" in stripped and re.search(r"^#?\s*\S+\t", stripped, re.MULTILINE):
        return CookieFormat.NETSCAPE
    if ";" in stripped:
        return CookieFormat.HEADER
    return CookieFormat.LOOSE


def _parse_header(text: str) -> dict[str, str]:
    text = re.sub(r"^\s*cookie\s*:\s*", "", text.strip(), flags=re.IGNORECASE)
    out: dict[str, str] = {}
    for chunk in text.split(";"):
        name, sep, value = chunk.partition("=")
        if sep and name.strip():
            out[name.strip()] = value.strip()
    return out


def _parse_json(text: str) -> dict[str, str]:
    data = json.loads(text)
    if isinstance(data, dict):
        # Some exporters emit {name: value}; others a single cookie object.
        if "name" in data and "value" in data:
            return {str(data["name"]): str(data["value"])}
        return {str(k): str(v) for k, v in data.items()}
    out: dict[str, str] = {}
    for item in data:
        if isinstance(item, dict) and "name" in item:
            out[str(item["name"])] = str(item.get("value", ""))
    return out


def _parse_netscape(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            out[parts[5].strip()] = parts[6].strip()
    return out


def _parse_loose(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in re.split(r"[\r\n]+", text):
        name, sep, value = line.strip().partition("=")
        if sep and name.strip():
            out[name.strip()] = value.strip().rstrip(";")
    return out


_PARSERS = {
    CookieFormat.HEADER: _parse_header,
    CookieFormat.JSON_ARRAY: _parse_json,
    CookieFormat.NETSCAPE: _parse_netscape,
    CookieFormat.LOOSE: _parse_loose,
}


def parse_cookies(text: str) -> tuple[CookieFormat, dict[str, str]]:
    fmt = detect_format(text)
    try:
        cookies = _PARSERS[fmt](text)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        cookies = {}
    if not cookies and fmt is not CookieFormat.LOOSE:
        # Detection is a heuristic; fall back rather than rejecting outright.
        cookies = _parse_loose(text)
        if cookies:
            fmt = CookieFormat.LOOSE
    return fmt, {k: v for k, v in cookies.items() if k and v}


# --- browser inference ---------------------------------------------------------

_UA_PATTERNS: tuple[tuple[re.Pattern[str], BrowserFamily], ...] = (
    (re.compile(r"(?:Edg|Edge)/(\d+)"), BrowserFamily.CHROME),
    (re.compile(r"OPR/(\d+)"), BrowserFamily.CHROME),
    (re.compile(r"Chrome/(\d+)"), BrowserFamily.CHROME),
    (re.compile(r"Firefox/(\d+)"), BrowserFamily.FIREFOX),
    (re.compile(r"Version/(\d+)[.\d]*\s+Safari"), BrowserFamily.SAFARI),
)


def infer_browser(user_agent: str | None) -> tuple[BrowserFamily | None, int | None]:
    """Derive the browser family and major version from a User-Agent.

    Edge and Opera are Chromium underneath and share Chrome's TLS profile, so
    they map onto CHROME. Order matters: their tokens appear alongside
    "Chrome/", so they must be tested first.
    """
    if not user_agent:
        return None, None
    for pattern, family in _UA_PATTERNS:
        m = pattern.search(user_agent)
        if m:
            try:
                return family, int(m.group(1))
            except (ValueError, IndexError):
                return family, None
    return None, None


# --- session expiry ------------------------------------------------------------

#: Douyin encodes the session lifetime inside sid_guard as
#: "<sessionid>|<issued_unix>|<max_age_seconds>|<http_date>", URL-encoded.
_SID_GUARD_RE = re.compile(r"^([^|]+)\|(\d+)\|(\d+)\|(.*)$")


def session_expiry(cookies: dict[str, str]) -> datetime | None:
    """Best-effort expiry for a logged-in cookie set.

    Knowing this up front turns a silent mass failure - every request from that
    identity starting to fail at once - into a warning days ahead of time.
    """
    guard = cookies.get("sid_guard")
    if not guard:
        return None
    m = _SID_GUARD_RE.match(unquote(guard))
    if not m:
        return None
    try:
        issued = int(m.group(2))
        max_age = int(m.group(3))
    except ValueError:
        return None
    if max_age <= 0:
        return None
    try:
        return datetime.fromtimestamp(issued + max_age, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


# --- top level -----------------------------------------------------------------


def build_report(
    text: str,
    platform: Platform,
    *,
    user_agent: str | None = None,
    language: str | None = None,
    timezone: str | None = None,
) -> ImportReport:
    fmt, cookies = parse_cookies(text)
    authenticated = bool(SESSION_MARKERS & cookies.keys())
    family, major = infer_browser(user_agent)

    warnings: list[str] = []
    if not cookies:
        warnings.append("no cookies could be parsed from the pasted text")
    if authenticated:
        warnings.append(
            "this cookie set contains a logged-in session and is equivalent to the "
            "account password; prefer a dedicated account over a primary one"
        )
    if family is None:
        # Doc 02 requires refusing rather than defaulting: a mismatched
        # fingerprint is more dangerous than having no identity at all.
        warnings.append(
            "the browser could not be inferred from the User-Agent, so no TLS "
            "profile can be matched; supply the exact User-Agent used to obtain "
            "these cookies"
        )
    if not USEFUL & cookies.keys() and not authenticated:
        warnings.append("none of the optional cookies are present; this identity will be weak")

    missing = tuple(c for c in REQUIRED.get(platform, ()) if c not in cookies)

    return ImportReport(
        detected_format=fmt,
        cookies=cookies,
        platform=platform,
        authenticated=authenticated,
        fingerprint=Fingerprint(
            browser_family=family,
            browser_major=major,
            user_agent=user_agent,
            language=language,
            timezone=timezone,
        ),
        expires_at=session_expiry(cookies) if authenticated else None,
        missing_required=missing,
        warnings=tuple(warnings),
    )


def to_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


__all__ = [
    "REQUIRED",
    "SESSION_MARKERS",
    "USEFUL",
    "CookieFormat",
    "ImportReport",
    "build_report",
    "detect_format",
    "infer_browser",
    "parse_cookies",
    "session_expiry",
    "to_cookie_header",
]
