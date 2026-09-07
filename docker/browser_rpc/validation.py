"""Platform table and input validation.

The allowlist here mirrors `dtk.urls.patterns`. It is duplicated rather than
imported because browser-rpc ships as its own image with its own dependency set,
and pulling the whole `dtk` package in to reuse one frozenset would tie the
browser image to the application's release cycle.

Doc 04 is explicit about why validation exists on an unauthenticated internal
service: it defends against this project's own mistakes - a caller handing over
a URL it built wrong, a proxy string that is really a shell fragment - not
against an outside attacker, who cannot reach this port at all.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlsplit, urlunsplit

from browser_rpc.errors import InvalidRequest


class Platform(StrEnum):
    """Mirrors `dtk.core.types.Platform`; the wire values must stay identical."""

    DOUYIN = "douyin"
    TIKTOK = "tiktok"


#: Registrable domains, subdomains included. Same list as dtk.urls.patterns.
DOUYIN_DOMAINS: frozenset[str] = frozenset({"douyin.com", "iesdouyin.com", "amemv.com"})
TIKTOK_DOMAINS: frozenset[str] = frozenset({"tiktok.com"})
ALLOWED_DOMAINS: frozenset[str] = DOUYIN_DOMAINS | TIKTOK_DOMAINS

DOMAINS_BY_PLATFORM: dict[Platform, frozenset[str]] = {
    Platform.DOUYIN: DOUYIN_DOMAINS,
    Platform.TIKTOK: TIKTOK_DOMAINS,
}

#: Where a mint session goes to be handed guest cookies. The platform sets
#: `ttwid` and friends on the first HTML document, not on an API call.
LANDING_URLS: dict[Platform, str] = {
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.TIKTOK: "https://www.tiktok.com/",
}

#: The page a warm signing context keeps loaded, so the platform's own signing
#: JavaScript is already in memory when a request arrives.
SIGNING_PAGE_URLS: dict[Platform, str] = {
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.TIKTOK: "https://www.tiktok.com/",
}

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: socks5h keeps DNS resolution on the proxy side, which matters: resolving the
#: platform's hostname locally leaks the request to the deployment's own resolver
#: and can return a different edge than the exit would.
ALLOWED_PROXY_SCHEMES: frozenset[str] = frozenset({"http", "https", "socks5", "socks5h"})

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$")


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    """A proxy URL split the way a browser wants it: server plus credentials."""

    server: str
    username: str | None = None
    password: str | None = None

    def masked(self) -> str:
        """Safe for logs: credentials are as sensitive as the cookies."""
        return self.server if not self.username else f"{self.server} (authenticated)"


def parse_platform(raw: object) -> Platform:
    """Coerce the wire value into a platform, or reject it."""
    if isinstance(raw, Platform):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidRequest("platform is required")
    try:
        return Platform(raw.strip().lower())
    except ValueError as exc:
        known = ", ".join(sorted(p.value for p in Platform))
        raise InvalidRequest(f"unknown platform {raw!r}; known platforms: {known}") from exc


def registrable_domain(host: str) -> str | None:
    """The allowlisted domain ``host`` belongs to, if any."""
    for domain in ALLOWED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return None


def validate_target_url(raw: str | None, *, platform: Platform | None = None) -> str:
    """Check a URL the browser is about to load, and return it normalized.

    A URL that is merely on an allowlisted domain is not enough when the caller
    also named a platform: signing on the wrong platform's page produces a
    signature that verifies nowhere, and the failure would surface much later as
    an unexplained risk-control hit.
    """
    if not raw or not raw.strip():
        raise InvalidRequest("url is required")

    parts = urlsplit(raw.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise InvalidRequest(f"url scheme {scheme or '(none)'!r} is not allowed")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidRequest("url has no host")
    if not HOSTNAME_RE.match(host):
        # Rejects address literals and unicode homographs in one step; neither
        # can match an allowlisted domain anyway.
        raise InvalidRequest(f"url host {host!r} is not a plain DNS name")

    domain = registrable_domain(host)
    if domain is None:
        raise InvalidRequest(f"url host {host!r} is not an allowlisted platform domain")
    if platform is not None and domain not in DOMAINS_BY_PLATFORM[platform]:
        raise InvalidRequest(f"url host {host!r} does not belong to platform {platform.value}")

    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def validate_proxy_url(raw: str | None) -> ProxyEndpoint | None:
    """Split a proxy URL into server and credentials, or reject it.

    Private addresses are deliberately *not* rejected here. A proxy container on
    the same host is a supported deployment, and the SSRF rule from doc 08
    applies to the destination, which `validate_target_url` covers.
    """
    if raw is None or not raw.strip():
        return None

    parts = urlsplit(raw.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_PROXY_SCHEMES:
        allowed = ", ".join(sorted(ALLOWED_PROXY_SCHEMES))
        raise InvalidRequest(f"proxy scheme {scheme or '(none)'!r} is not allowed; use {allowed}")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidRequest("proxy url has no host")
    if not HOSTNAME_RE.match(host) and not _is_ip_literal(host):
        raise InvalidRequest(f"proxy host {host!r} is neither a DNS name nor an IP address")

    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidRequest("proxy port is not a number") from exc

    server = f"{scheme}://{host}" if port is None else f"{scheme}://{host}:{port}"
    # Credentials are percent-encoded in a URL and literal in the browser's
    # proxy settings. Handing "pass%20word" straight through authenticates with
    # the wrong password, and the proxy answers 407 with no hint why.
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password is not None else None
    return ProxyEndpoint(server=server, username=username, password=password)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


__all__ = [
    "ALLOWED_DOMAINS",
    "ALLOWED_PROXY_SCHEMES",
    "ALLOWED_URL_SCHEMES",
    "DOMAINS_BY_PLATFORM",
    "LANDING_URLS",
    "SIGNING_PAGE_URLS",
    "Platform",
    "ProxyEndpoint",
    "parse_platform",
    "registrable_domain",
    "validate_proxy_url",
    "validate_target_url",
]
