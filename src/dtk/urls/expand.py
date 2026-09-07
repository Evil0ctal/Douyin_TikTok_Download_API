"""Short-link expansion with the network call as the only seam.

A short link (``v.douyin.com/abc123``, ``vm.tiktok.com/ZSxxxx``,
``tiktok.com/t/ZTxxxx``) cannot be validated before it is followed: the host
tells you nothing about the target. docs/design/08-security.md therefore
requires the allowlist to be applied a second time, to every hop, after
expansion. That re-check lives here.

The one thing this module cannot do offline is perform the request. It is
injected as a :class:`RedirectFetcher`: an async callable that issues exactly
one request without following redirects and returns the ``Location`` header, or
``None`` when the response was not a redirect. Everything else - hop counting,
loop detection, host re-validation, canonicalization - is ordinary logic that
tests drive with a dictionary-backed fake.

The transport layer supplies the real implementation, because a short link must
be followed through the same identity-bound proxy as any other outbound request
(doc 08: "all outbound requests must go through the identity's proxy"). This
module never opens a connection itself.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urljoin

from dtk.core.errors import InvalidUrl
from dtk.core.logging import get_logger
from dtk.urls.parse import UrlKind, first_url, identify, is_allowed_host, normalize
from dtk.urls.patterns import ResourceKind

logger = get_logger(__name__)

#: Platform short links resolve in one or two hops. Five leaves room for an
#: extra regional redirect while keeping a redirect loop cheap to detect.
MAX_REDIRECTS = 5


class RedirectFetcher(Protocol):
    """Issues one request for ``url`` and reports where it points next.

    Implementations must not follow redirects themselves, must apply their own
    timeout, and must return the raw ``Location`` value (absolute or relative)
    for a 3xx response, or ``None`` for any response that is not a redirect.
    A transport failure should raise; it is not this module's job to decide
    whether a network error is retryable.
    """

    async def __call__(self, url: str, /) -> str | None: ...


async def expand(
    url: str,
    fetcher: RedirectFetcher,
    *,
    max_hops: int = MAX_REDIRECTS,
) -> str:
    """Follow ``url`` to its final destination and return the canonical form.

    Every hop is re-validated against the host allowlist before it is used, so
    a short link that redirects to an internal address, to another scheme, or
    off-platform fails closed instead of being fetched.

    Args:
        url: Starting URL. Must already be on an allowlisted host.
        fetcher: Injected single-request callable, see :class:`RedirectFetcher`.
        max_hops: Maximum number of redirects to follow.

    Returns:
        The canonical URL of the final destination.

    Raises:
        InvalidUrl: the start URL, or any hop, is not an allowed target; the
            chain loops; or it is still redirecting after ``max_hops``.
    """
    if max_hops < 1:
        raise ValueError("max_hops must be at least 1")

    current = url.strip()
    if not is_allowed_host(current):
        logger.warning("urls.expand.rejected", stage="start")
        raise InvalidUrl(
            "not a supported Douyin or TikTok URL",
            details={"reason": "host_not_allowed"},
        )

    seen = {normalize(current)}
    for hop in range(max_hops):
        location = await fetcher(current)
        if location is None or not location.strip():
            logger.debug("urls.expand.settled", hops=hop)
            return normalize(current)

        candidate = urljoin(current, location.strip())
        if not is_allowed_host(candidate):
            # This is the check doc 08 exists for: the short link itself was
            # allowlisted, its target is not.
            logger.warning("urls.expand.rejected", stage="hop", hop=hop)
            raise InvalidUrl(
                "short link redirected to a host that is not allowed",
                details={"reason": "redirect_not_allowed", "hop": hop},
            )

        canonical = normalize(candidate)
        if canonical in seen:
            logger.warning("urls.expand.loop", hop=hop)
            raise InvalidUrl(
                "short link redirect loop",
                details={"reason": "redirect_loop", "hop": hop},
            )
        seen.add(canonical)
        current = candidate
        logger.debug("urls.expand.hop", hop=hop)

    logger.warning("urls.expand.exhausted", max_hops=max_hops)
    raise InvalidUrl(
        "too many redirects while expanding short link",
        details={"reason": "too_many_redirects", "max_hops": max_hops},
    )


async def resolve(
    text: str,
    fetcher: RedirectFetcher,
    *,
    max_hops: int = MAX_REDIRECTS,
) -> UrlKind:
    """Turn whatever the user pasted into a recognized resource.

    Accepts raw share text, a bare URL, or a scheme-less link; extracts the
    first URL, classifies it, expands it when it is a short link, and
    classifies the result. This is the entry point the parse service and the
    CLI use.

    Raises:
        InvalidUrl: nothing usable in ``text``, the target is not allowed, or
            the final URL does not point at a resource we know how to fetch.
    """
    candidate = first_url(text)
    if candidate is None:
        raise InvalidUrl("no URL found in the input", details={"reason": "no_url"})

    kind = identify(candidate)
    if not kind.allowed:
        raise InvalidUrl(
            "not a supported Douyin or TikTok URL",
            details={"reason": "host_not_allowed"},
        )

    if kind.needs_expansion and kind.url is not None:
        final = await expand(kind.url, fetcher, max_hops=max_hops)
        kind = identify(final)
        logger.info("urls.resolve.expanded", resource=kind.resource.value)

    if kind.resource is ResourceKind.SHORT_LINK:
        raise InvalidUrl(
            "short link did not resolve to a known resource",
            details={"reason": "unresolved_short_link"},
        )
    if not kind.recognized:
        raise InvalidUrl(
            "URL is on a supported platform but does not point at a known resource",
            details={"reason": "unknown_resource"},
        )
    return kind


__all__ = ["MAX_REDIRECTS", "RedirectFetcher", "expand", "resolve"]
