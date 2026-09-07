"""Offline URL recognition, validation and normalization.

Everything in this module is a pure synchronous function: no network, no
config, no database. That is a deliberate constraint, not an accident of the
current implementation. V4 could not unit test any of this because
``AwemeIdFetcher.get_aweme_id`` performed the redirect itself, so the only way
to exercise the parsing was to hit the platform. Here the network step is
isolated in :mod:`dtk.urls.expand` behind an injected callable, and this module
stays fully testable with fixed strings.

This is also the SSRF chokepoint described in docs/design/08-security.md. Every
URL the service is asked to fetch passes :func:`is_allowed_host` here, both
before and after short-link expansion. Rules enforced:

* only ``http`` and ``https``;
* no userinfo in the authority (``https://evil.com@www.douyin.com/``), because
  it is a phishing shape and different parsers disagree about which half is the
  host;
* no non-default port;
* the host must be an allowlisted platform domain or a subdomain of one, tested
  on a label boundary so ``douyin.com.evil.com`` fails;
* loopback, private, link-local, reserved and single-label internal names are
  rejected outright, ahead of the allowlist, so the rejection reason is honest.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, unquote_plus, urlencode, urlsplit

from dtk.core.errors import InvalidUrl
from dtk.core.types import ContentKind, Platform
from dtk.urls.patterns import (
    ALLOWED_DOMAINS,
    ALLOWED_SCHEMES,
    ANY_URL_IN_TEXT_RE,
    BARE_URL_HOSTS,
    BLOCKED_HOST_SUFFIXES,
    BLOCKED_HOSTNAMES,
    DEFAULT_PORTS,
    HOSTNAME_RE,
    NUMERIC_HOST_RE,
    PLATFORM_BY_DOMAIN,
    ROUTES_BY_PLATFORM,
    SHORT_LINK_HOSTS,
    TRACKING_PARAMS,
    TRAILING_JUNK,
    ResourceKind,
)

_TRACKING_LOWER: frozenset[str] = frozenset(param.lower() for param in TRACKING_PARAMS)


@dataclass(frozen=True, slots=True)
class UrlKind:
    """What a URL turned out to be.

    ``resource_id`` is whatever the platform endpoint for that resource keys
    off, which is not the same field on both platforms:

    * Douyin user  -> ``sec_user_id`` (the stable key, not the mutable ``uid``)
    * TikTok user  -> the ``@handle``, because ``uniqueId`` is what the TikTok
      user-detail endpoint accepts; the numeric id needs a fetch first
    * work / live  -> the numeric id carried by the path or query

    ``content_kind`` is only a hint derived from the path shape (``/note/``,
    ``/photo/``). It is ``None`` whenever the URL does not say, and it is never
    authoritative: only the detail response settles video versus image album.
    """

    original: str
    platform: Platform | None = None
    resource: ResourceKind = ResourceKind.UNKNOWN
    resource_id: str | None = None
    handle: str | None = None
    content_kind: ContentKind | None = None
    #: Canonical web URL, or ``None`` when the input is not an allowed target.
    url: str | None = None
    needs_expansion: bool = False

    @property
    def allowed(self) -> bool:
        """The URL passed the host allowlist and may be fetched."""
        return self.url is not None

    @property
    def recognized(self) -> bool:
        """The URL is allowed and its resource type is known."""
        return self.platform is not None and self.resource is not ResourceKind.UNKNOWN


@dataclass(frozen=True, slots=True)
class _Target:
    """An allowlisted URL, split into the parts the route table needs."""

    scheme: str
    host: str
    path: str
    query: str
    platform: Platform


# --------------------------------------------------------------------------
# Host validation
# --------------------------------------------------------------------------


def is_private_host(host: str) -> bool:
    """True when the host points at this machine or a non-routable network.

    Handles address literals (v4, v6, bracketed, IPv4-mapped), the usual local
    names, and single-label intranet names. Decimal or hex integer forms of an
    address (``2130706433``, ``0x7f000001``) are refused as well: they are never
    a real hostname and exist only to slip past naive string checks.

    DNS is deliberately not consulted. Resolving here would add a network call
    to a pure function and would still be a time-of-check/time-of-use gap, so
    the defence is the positive allowlist plus a proxy on every outbound call.
    """
    name = host.strip().strip("[]").rstrip(".").lower()
    if not name:
        return True
    if name in BLOCKED_HOSTNAMES or name.endswith(BLOCKED_HOST_SUFFIXES):
        return True

    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        address = None
    if address is not None:
        return not address.is_global

    labels = name.split(".")
    if len(labels) == 1:
        # A bare label is an intranet name; it can never be an allowed domain.
        return True
    return any(NUMERIC_HOST_RE.match(label) for label in labels)


def _registrable_domain(host: str) -> str | None:
    """Return the allowlisted domain ``host`` belongs to, if any.

    Matching is on a label boundary, which is the whole point: a plain
    ``endswith("douyin.com")`` would happily accept ``evildouyin.com`` and
    ``endswith(".douyin.com")`` alone would miss the apex.
    """
    for domain in ALLOWED_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            return domain
    return None


def _split(url: str) -> _Target | None:
    """Parse and validate ``url``. Returns ``None`` for anything not fetchable."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return None
    if "@" in parts.netloc:
        return None

    try:
        raw_host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not raw_host:
        return None
    if port is not None and port != DEFAULT_PORTS[scheme]:
        return None

    host = raw_host.rstrip(".").lower()
    if not host.isascii() or not HOSTNAME_RE.match(host):
        return None
    if is_private_host(host):
        return None

    domain = _registrable_domain(host)
    if domain is None:
        return None

    return _Target(
        scheme=scheme,
        host=host,
        path=parts.path or "/",
        query=parts.query,
        platform=PLATFORM_BY_DOMAIN[domain],
    )


def is_allowed_host(url: str) -> bool:
    """True when ``url`` may be fetched by the service.

    Shares one implementation with :func:`identify` so the check the security
    layer performs and the check the parser performs can never drift apart.
    """
    return _split(_prepare(url)) is not None


# --------------------------------------------------------------------------
# Extraction from share text
# --------------------------------------------------------------------------


def _trim(candidate: str) -> str:
    """Drop sentence punctuation that the surrounding text glued onto a URL."""
    return candidate.rstrip(TRAILING_JUNK)


def _prepare(url: str) -> str:
    """Normalize whitespace and add the scheme a user left off.

    Only allowlisted hosts get a scheme added, so this convenience cannot widen
    the allowlist.
    """
    text = url.strip()
    if not text:
        return ""
    if "://" in text:
        return text
    head = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].lower()
    if head in BARE_URL_HOSTS:
        return f"https://{text}"
    return text


def extract_urls(text: str) -> list[str]:
    """Pull candidate URLs out of pasted share text, in the order they appear.

    Real input looks like::

        7.61 gTa:/ <caption with the work title> https://v.douyin.com/abc123/

    so a URL is bounded by whitespace, CJK text or CJK punctuation rather than
    sitting on a line of its own. Scheme-less links on allowlisted hosts are
    picked up too and get ``https://`` prepended; hosts outside the allowlist
    are not matched at all in that form.

    Returns candidates, not validated URLs: run :func:`identify` on each.
    """
    if not text:
        return []

    seen: set[str] = set()
    found: list[str] = []
    for match in ANY_URL_IN_TEXT_RE.finditer(text):
        candidate = _trim(match.group(0))
        if not candidate:
            continue
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        if candidate in seen:
            continue
        seen.add(candidate)
        found.append(candidate)
    return found


def first_url(text: str) -> str | None:
    """The first URL in ``text``, or ``None``. ``text`` may already be a URL."""
    urls = extract_urls(text)
    return urls[0] if urls else None


# --------------------------------------------------------------------------
# Recognition and normalization
# --------------------------------------------------------------------------


def _clean_query(query: str) -> str:
    """Drop share and analytics parameters, then sort what is left.

    Sorting matters: the same work shared twice produces the same canonical URL,
    which is what makes the response cache and the ``content_id`` de-duplication
    behave.
    """
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.lower() not in _TRACKING_LOWER
    ]
    kept.sort()
    return urlencode(kept)


def _generic_url(target: _Target) -> str:
    """Canonical form for an allowed host whose path shape we do not know."""
    query = _clean_query(target.query)
    path = target.path.rstrip("/") or "/"
    return f"https://{target.host}{path}" + (f"?{query}" if query else "")


def _decode(value: str, from_query: bool) -> str:
    """Percent-decode a captured group.

    ``from_query`` selects the right rule for where the group was captured.
    A ``+`` means a space in a query string but is a literal ``+`` in a path,
    so ``/search?q=cat+videos`` is the keyword "cat videos" while
    ``/search/cat+videos`` is the keyword "cat+videos".
    """
    return unquote_plus(value) if from_query else unquote(value)


def identify(url: str) -> UrlKind:
    """Classify a single URL. Never raises, never touches the network.

    An unrecognized or disallowed input comes back as ``UrlKind`` with
    ``platform=None`` and ``resource=UNKNOWN`` rather than as an exception, so
    callers that probe several candidates do not have to catch anything. Use
    :func:`normalize` when the failure should be an error.

    Route order is significant. ``modal_id`` and ``vid`` are matched before the
    path, because ``douyin.com/user/MS4w...?modal_id=7...`` is a work opened on
    top of a profile page and the work is what the user meant to share.
    """
    original = url.strip()
    target = _split(_prepare(original)) if original else None
    if target is None:
        return UrlKind(original=original)

    if target.host in SHORT_LINK_HOSTS:
        code = target.path.strip("/").split("/")[0]
        if code:
            return UrlKind(
                original=original,
                platform=target.platform,
                resource=ResourceKind.SHORT_LINK,
                resource_id=code,
                url=f"https://{target.host}/{code}",
                needs_expansion=True,
            )
        return UrlKind(original=original, platform=target.platform, url=_generic_url(target))

    haystack = target.path + (f"?{target.query}" if target.query else "")
    for route in ROUTES_BY_PLATFORM[target.platform]:
        if route.hosts is not None and target.host not in route.hosts:
            continue
        match = route.pattern.search(haystack)
        if match is None:
            continue
        groups: dict[str, str] = {
            name: value for name, value in match.groupdict().items() if value is not None
        }
        try:
            canonical = route.canonical.format_map(groups)
        except KeyError:
            # The template needs a group this match did not produce. Skip
            # rather than emit a half-built URL.
            continue
        handle = groups.get("handle")
        id_group = "id" if "id" in groups else ("handle" if handle is not None else None)
        resource_id: str | None = None
        if id_group is not None:
            resource_id = groups[id_group]
            if id_group in route.decode:
                # A group captured from the query follows query rules ("+" is a
                # space); one captured from the path follows path rules.
                from_query = match.start(id_group) > len(target.path)
                resource_id = _decode(resource_id, from_query)
        return UrlKind(
            original=original,
            platform=target.platform,
            resource=route.resource,
            resource_id=resource_id,
            handle=handle,
            content_kind=route.content_kind,
            url=canonical,
            needs_expansion=route.needs_expansion,
        )

    return UrlKind(original=original, platform=target.platform, url=_generic_url(target))


def normalize(url: str) -> str:
    """Return the canonical web URL for ``url``.

    Raises:
        InvalidUrl: the scheme, host or authority is not an allowed target.
    """
    kind = identify(url)
    if kind.url is None:
        raise InvalidUrl(
            "not a supported Douyin or TikTok URL",
            details={"reason": "host_not_allowed"},
        )
    return kind.url


def require_supported(url: str) -> UrlKind:
    """Like :func:`identify`, but raise when the resource type is unknown.

    Short links count as supported: they are ours, they simply have to be
    expanded first.
    """
    kind = identify(url)
    if not kind.allowed:
        raise InvalidUrl(
            "not a supported Douyin or TikTok URL",
            details={"reason": "host_not_allowed"},
        )
    if not kind.recognized:
        raise InvalidUrl(
            "URL is on a supported platform but does not point at a known resource",
            details={"reason": "unknown_resource", "url": kind.url},
        )
    return kind


__all__ = [
    "ResourceKind",
    "UrlKind",
    "extract_urls",
    "first_url",
    "identify",
    "is_allowed_host",
    "is_private_host",
    "normalize",
    "require_supported",
]
