"""Storing media this instance has already parsed, onto the operator's disk.

Three modules, in the order a download passes through them:

``domains``  which CDN hosts may be fetched at all - the allowlist that did not
             exist before doc 18, and the one place it is defined.
``plan``     one archived post to a list of files: names, ceilings, acceptable
             content types. Every policy decision lives here.
``client``   the seam to the Go sidecar that moves the bytes.

The contract with that sidecar is the point of the split: it is a sink, not a
relay. It receives mirrors this instance resolved from its own archive, never a
caller's URL, and it never streams bytes back to an API caller. It holds no
cookie, no master key and no database address.
"""

from dtk.media.client import (
    DownloaderBusy,
    DownloaderClient,
    DownloaderHealth,
    DownloaderUnavailable,
)
from dtk.media.domains import MEDIA_DOMAINS, MEDIA_DOMAINS_BY_PLATFORM, allowed_mirrors, refusal
from dtk.media.plan import Plan, PlannedItem, build, sidecar

__all__ = [
    "MEDIA_DOMAINS",
    "MEDIA_DOMAINS_BY_PLATFORM",
    "DownloaderBusy",
    "DownloaderClient",
    "DownloaderHealth",
    "DownloaderUnavailable",
    "Plan",
    "PlannedItem",
    "allowed_mirrors",
    "build",
    "refusal",
    "sidecar",
]
