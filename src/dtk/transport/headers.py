"""Header construction from a fingerprint.

The TLS profile already claims a browser; these headers have to claim the same
one. wreq's emulation supplies browser-shaped defaults, but it knows nothing
about *this* identity - the User-Agent, language and platform that the minting
browser actually reported. Those must win, because they are what the cookies
were issued against.

Client hints are emitted only for Chromium. Firefox and Safari do not send
`sec-ch-ua` at all, so adding it there would be a fingerprint contradiction
rather than extra realism.

Seam: the exact GREASE brand in `sec-ch-ua` varies per Chrome build and cannot
be derived offline. `SEC_CH_UA_GREASE` holds a plausible constant; once minting
returns the browser's real brand list, pass it through `RequestSpec.headers`,
which always overrides what is built here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from dtk.core.types import BrowserFamily
from dtk.transport.base import Fingerprint
from dtk.transport.emulation import UnsupportedFingerprint

#: Families that send Chromium client hints.
CLIENT_HINT_FAMILIES: Final[frozenset[BrowserFamily]] = frozenset({BrowserFamily.CHROME})

#: GREASE entry Chromium prepends to its brand list. See the module docstring.
SEC_CH_UA_GREASE: Final[tuple[str, str]] = ("Not)A;Brand", "99")

#: `navigator.platform` substring to `sec-ch-ua-platform` value, in match order.
#:
#: Android is the awkward one: Chrome on Android reports "Linux armv8l", not
#: anything containing "android", so the ARM variants have to be matched before
#: the generic Linux entry. "Linux aarch64" is left as Linux because desktop ARM
#: reports it too, and calling a desktop mobile is the more visible mistake.
PLATFORM_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("iphone", "iOS"),
    ("ipad", "iOS"),
    ("ipod", "iOS"),
    ("android", "Android"),
    ("linux armv", "Android"),
    ("win", "Windows"),
    ("mac", "macOS"),
    ("cros", "Chrome OS"),
    ("linux", "Linux"),
    ("x11", "Linux"),
    ("freebsd", "Linux"),
)

#: Platform hint values that mean a mobile device.
MOBILE_PLATFORMS: Final[frozenset[str]] = frozenset({"Android", "iOS"})


def platform_hint(platform: str | None) -> str | None:
    """Map `navigator.platform` onto a `sec-ch-ua-platform` value."""
    if not platform:
        return None
    lowered = platform.lower()
    for needle, hint in PLATFORM_HINTS:
        if needle in lowered:
            return hint
    return None


def accept_language(language: str | None) -> str | None:
    """Expand a BCP-47 tag into a weighted Accept-Language value.

    A value that already carries a list or quality factors is passed through
    untouched: the minting browser's own header beats anything reconstructed
    from a single tag.
    """
    if not language:
        return None
    value = language.strip()
    if not value:
        return None
    if "," in value or ";q=" in value:
        return value
    base, _, _ = value.partition("-")
    if not base or base == value:
        return value
    return f"{value},{base};q=0.9"


def sec_ch_ua(major: int) -> str:
    """Build the `sec-ch-ua` brand list for a Chromium major version."""
    brands = (SEC_CH_UA_GREASE, ("Chromium", str(major)), ("Google Chrome", str(major)))
    return ", ".join(f'"{brand}";v="{version}"' for brand, version in brands)


def build_headers(
    fingerprint: Fingerprint,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Headers implied by a fingerprint, with `extra` overriding them.

    Overriding is case-insensitive: sending both `User-Agent` and `user-agent`
    would be a self-report on its own.
    """
    if not fingerprint.user_agent:
        raise UnsupportedFingerprint(
            "fingerprint carries no user agent; the identity must not be pooled",
            details={"browser_family": str(fingerprint.browser_family)},
        )

    headers: dict[str, str] = {"User-Agent": fingerprint.user_agent}

    language = accept_language(fingerprint.language)
    if language is not None:
        headers["Accept-Language"] = language

    if fingerprint.browser_family in CLIENT_HINT_FAMILIES and fingerprint.browser_major:
        headers["sec-ch-ua"] = sec_ch_ua(fingerprint.browser_major)
        hint = platform_hint(fingerprint.platform)
        headers["sec-ch-ua-mobile"] = "?1" if hint in MOBILE_PLATFORMS else "?0"
        if hint is not None:
            headers["sec-ch-ua-platform"] = f'"{hint}"'

    return merge_headers(headers, extra)


def merge_headers(
    base: Mapping[str, str],
    extra: Mapping[str, str] | None,
) -> dict[str, str]:
    """Merge two header maps case-insensitively, returning a new dict."""
    merged = dict(base)
    if not extra:
        return merged
    for name, value in extra.items():
        lowered = name.lower()
        for existing in [key for key in merged if key.lower() == lowered]:
            del merged[existing]
        merged[name] = value
    return merged


__all__ = [
    "CLIENT_HINT_FAMILIES",
    "MOBILE_PLATFORMS",
    "PLATFORM_HINTS",
    "SEC_CH_UA_GREASE",
    "accept_language",
    "build_headers",
    "merge_headers",
    "platform_hint",
    "sec_ch_ua",
]
