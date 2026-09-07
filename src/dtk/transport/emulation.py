"""Fingerprint to wreq emulation profile mapping.

The table is built by introspecting `wreq.Emulation` at import time instead of
being hardcoded. A hardcoded list goes stale on every wreq upgrade, and the
failure mode of a stale list is silent: an identity minted by a Chrome 152
browser keeps getting a Chrome 149 TLS profile long after wreq grew a 152
profile, and nobody notices because nothing errors.

Selection is exact match first, then nearest known version *below* the requested
one - never a global default. A default profile is how a Firefox identity ends up
speaking Chrome's TLS while its User-Agent says Firefox, which is the single
loudest self-report a client can make (docs/design/04-transport-signing.md).

An identity whose browser family cannot be inferred is refused outright rather
than emulated as something plausible: docs/design/02-identity-pool.md treats a
mismatched fingerprint as worse than no identity at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from wreq import Emulation

from dtk.core.errors import DtkError, ErrorCode
from dtk.core.logging import get_logger
from dtk.core.types import BrowserFamily
from dtk.transport.base import Fingerprint

log = get_logger(__name__)

#: `Chrome149`, `Safari18_3_1`, `OkHttp4_12`. The family part is non-greedy so
#: a family name that ends in a digit (OkHttp3) still splits at the version.
_PROFILE_NAME_RE = re.compile(r"^(?P<family>[A-Za-z][A-Za-z0-9]*?)(?P<version>\d+(?:_\d+)*)$")

#: Which introspected family key backs each browser family we mint or import.
#: Deliberately narrow: `SafariIos`, `FirefoxAndroid` and friends are separate
#: keys in the table and are never silently substituted for the desktop profile.
FAMILY_KEYS: Final[MappingProxyType[BrowserFamily, str]] = MappingProxyType(
    {
        BrowserFamily.CHROME: "chrome",
        BrowserFamily.FIREFOX: "firefox",
        BrowserFamily.SAFARI: "safari",
    }
)

#: Tolerance band from docs/design/04-transport-signing.md. Equality is not
#: required: wreq profiles necessarily lag the Chromium release train, and a
#: hard assertion would just get bypassed the first time CloakBrowser updates.
DRIFT_OK_MAX: Final[int] = 2
DRIFT_WARN_MAX: Final[int] = 4


class DriftBand(StrEnum):
    """How far apart two major versions may drift before it is a problem."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class UnsupportedFingerprint(DtkError):
    """The fingerprint cannot be emulated, so the identity must not be used."""

    code = ErrorCode.INVALID_PARAM


class UnknownBrowserFamily(UnsupportedFingerprint):
    """No browser family, or one wreq has no profiles for."""


class EmulationUnavailable(UnsupportedFingerprint):
    """The family is known but no profile is close enough to the version."""


@dataclass(frozen=True, slots=True)
class EmulationProfile:
    """One `wreq.Emulation` member, with its name parsed into a version."""

    name: str
    family: str
    version: tuple[int, ...]
    emulation: Any

    @property
    def major(self) -> int:
        return self.version[0]


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    """The profile chosen for a requested major, and how far off it is."""

    profile: EmulationProfile
    requested_major: int
    exact: bool

    @property
    def drift(self) -> int:
        return abs(self.requested_major - self.profile.major)

    @property
    def band(self) -> DriftBand:
        return emulation_drift(self.requested_major, self.profile.major)


def _parse_profile_name(name: str) -> tuple[str, tuple[int, ...]] | None:
    """Split an enum member name into a family key and a version tuple.

    Returns None for members that carry no version, such as `random`.
    """
    match = _PROFILE_NAME_RE.match(name)
    if match is None:
        return None
    version = tuple(int(part) for part in match.group("version").split("_"))
    return match.group("family").lower(), version


def build_table(namespace: type | Any = Emulation) -> dict[str, dict[int, EmulationProfile]]:
    """Introspect an emulation namespace into `{family: {major: profile}}`.

    When several members share a major - `Safari26`, `Safari26_1` ... - the
    highest full version wins, because that is the build a real browser of that
    major is most likely to be running.

    Takes the namespace as an argument so the mapping rules can be tested
    against a synthetic namespace without depending on which profiles the
    installed wreq happens to ship.
    """
    table: dict[str, dict[int, EmulationProfile]] = {}
    for name in dir(namespace):
        if name.startswith("_"):
            continue
        parsed = _parse_profile_name(name)
        if parsed is None:
            continue
        family, version = parsed
        profile = EmulationProfile(
            name=name,
            family=family,
            version=version,
            emulation=getattr(namespace, name),
        )
        by_major = table.setdefault(family, {})
        current = by_major.get(profile.major)
        if current is None or profile.version > current.version:
            by_major[profile.major] = profile
    return table


#: Built once at import. Read-only: extending it at runtime would let one caller
#: change the profile another caller already logged.
EMULATION_TABLE: Final[MappingProxyType[str, MappingProxyType[int, EmulationProfile]]] = (
    MappingProxyType(
        {
            family: MappingProxyType(dict(sorted(by_major.items())))
            for family, by_major in sorted(build_table().items())
        }
    )
)


def known_families() -> tuple[str, ...]:
    """Every family key wreq exposes, including ones we never emulate."""
    return tuple(EMULATION_TABLE)


def known_majors(family: BrowserFamily) -> tuple[int, ...]:
    """Ascending majors available for a browser family."""
    return tuple(_family_table(family))


def emulation_drift(chromium_major: int, profile_major: int) -> DriftBand:
    """Grade the gap between a browser major and the TLS profile major.

    Used both for a single identity and for the CloakBrowser / wreq version
    check in CI. Visibility beats blocking here: a warned drift stays in the
    logs and on the system information page, whereas a build gate that fires on
    every Chromium release gets disabled.
    """
    gap = abs(chromium_major - profile_major)
    if gap <= DRIFT_OK_MAX:
        return DriftBand.OK
    if gap <= DRIFT_WARN_MAX:
        return DriftBand.WARN
    return DriftBand.FAIL


def _family_table(family: BrowserFamily) -> MappingProxyType[int, EmulationProfile]:
    key = FAMILY_KEYS.get(family)
    if key is None:
        raise UnknownBrowserFamily(
            f"no emulation profiles are mapped for browser family {family!r}",
            details={"browser_family": str(family)},
        )
    by_major = EMULATION_TABLE.get(key)
    if not by_major:
        raise UnknownBrowserFamily(
            f"the installed wreq exposes no {key} profiles",
            details={"browser_family": str(family)},
        )
    return by_major


def select_profile(family: BrowserFamily, major: int) -> ProfileMatch:
    """Pick the profile for a family and major: exact, else nearest below.

    Raises `EmulationUnavailable` when the requested major predates every
    profile wreq ships. There is no nearest-below candidate then, and reaching
    upward would emulate a browser newer than the one that minted the cookies.
    """
    by_major = _family_table(family)
    exact = by_major.get(major)
    if exact is not None:
        return ProfileMatch(profile=exact, requested_major=major, exact=True)

    below = [known for known in by_major if known < major]
    if not below:
        raise EmulationUnavailable(
            f"no {family} profile at or below major {major}; oldest available is {min(by_major)}",
            details={"browser_family": str(family), "browser_major": major},
        )
    return ProfileMatch(profile=by_major[max(below)], requested_major=major, exact=False)


def profile_for(fingerprint: Fingerprint) -> ProfileMatch:
    """Resolve a fingerprint to a profile match, enforcing the drift band."""
    if fingerprint.browser_family is None:
        raise UnknownBrowserFamily(
            "fingerprint carries no browser family; the identity must not be pooled"
        )
    if fingerprint.browser_major is None:
        raise EmulationUnavailable(
            "fingerprint carries no browser major version",
            details={"browser_family": str(fingerprint.browser_family)},
        )

    match = select_profile(fingerprint.browser_family, fingerprint.browser_major)
    if match.band is DriftBand.FAIL:
        raise EmulationUnavailable(
            f"closest {fingerprint.browser_family} profile is {match.profile.name}, "
            f"{match.drift} majors from the claimed {fingerprint.browser_major}",
            details={
                "browser_family": str(fingerprint.browser_family),
                "browser_major": fingerprint.browser_major,
                "profile": match.profile.name,
                "drift": match.drift,
            },
        )
    if match.band is DriftBand.WARN:
        log.warning(
            "transport.emulation.drift",
            browser_family=str(fingerprint.browser_family),
            browser_major=fingerprint.browser_major,
            profile=match.profile.name,
            drift=match.drift,
            band=str(match.band),
        )
    return match


def emulation_for(fingerprint: Fingerprint) -> Any:
    """The `wreq.Emulation` member to use for this fingerprint."""
    return profile_for(fingerprint).profile.emulation


__all__ = [
    "DRIFT_OK_MAX",
    "DRIFT_WARN_MAX",
    "EMULATION_TABLE",
    "FAMILY_KEYS",
    "DriftBand",
    "EmulationProfile",
    "EmulationUnavailable",
    "ProfileMatch",
    "UnknownBrowserFamily",
    "UnsupportedFingerprint",
    "build_table",
    "emulation_drift",
    "emulation_for",
    "known_families",
    "known_majors",
    "profile_for",
    "select_profile",
]
