"""HTTP transport: one wreq client per identity, fingerprint-consistent.

Nothing above this package imports wreq. See docs/design/04-transport-signing.md.
"""

from __future__ import annotations

from dtk.transport.base import (
    CookieSink,
    Fingerprint,
    IdentityLike,
    RawResponse,
    RequestSpec,
    Transport,
    TransportFailure,
    TransportIdentity,
    mask_proxy_url,
)
from dtk.transport.classify import (
    Classification,
    ClassificationRule,
    Classifier,
    classify,
    classify_detailed,
    classify_exception,
)
from dtk.transport.emulation import (
    DriftBand,
    EmulationProfile,
    EmulationUnavailable,
    ProfileMatch,
    UnknownBrowserFamily,
    UnsupportedFingerprint,
    emulation_drift,
    emulation_for,
    known_majors,
    profile_for,
    select_profile,
)
from dtk.transport.headers import build_headers
from dtk.transport.wreq_transport import (
    ClientFactory,
    ClientOptions,
    TransportStats,
    WreqTransport,
    default_client_factory,
)

__all__ = [
    "Classification",
    "ClassificationRule",
    "Classifier",
    "ClientFactory",
    "ClientOptions",
    "CookieSink",
    "DriftBand",
    "EmulationProfile",
    "EmulationUnavailable",
    "Fingerprint",
    "IdentityLike",
    "ProfileMatch",
    "RawResponse",
    "RequestSpec",
    "Transport",
    "TransportFailure",
    "TransportIdentity",
    "TransportStats",
    "UnknownBrowserFamily",
    "UnsupportedFingerprint",
    "WreqTransport",
    "build_headers",
    "classify",
    "classify_detailed",
    "classify_exception",
    "default_client_factory",
    "emulation_drift",
    "emulation_for",
    "known_majors",
    "mask_proxy_url",
    "profile_for",
    "select_profile",
]
