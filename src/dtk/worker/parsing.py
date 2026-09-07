"""Turning a pasted link into a concrete endpoint call.

``POST /api/v1/parse`` and the MCP ``parse_url`` tool both exist so a caller can
hand over whatever they copied and get a result, without first working out which
of ten endpoints applies. That convenience has to be paid for somewhere, and
this is where: expand the short link, decide what the link points at, and map it
onto one of the endpoints the worker already knows how to run.

Expansion is the security-sensitive half. A short link cannot be validated
before it is followed - that is the whole point of it - so the target is checked
against the allowlist after every hop, never before, and the hop count is
bounded. See docs/design/08-security.md.
"""

from __future__ import annotations

from typing import Any

import httpx

from dtk.core.errors import InvalidUrl, UnsupportedContent
from dtk.core.logging import get_logger
from dtk.urls import MAX_REDIRECTS, ResourceKind, UrlKind, is_allowed_host, resolve

log = get_logger(__name__)

EXPAND_TIMEOUT_SECONDS = 10.0

#: Resource kinds that map onto a P0 endpoint. Everything else is refused with a
#: code the caller can act on rather than a generic failure.
_ENDPOINT_BY_RESOURCE: dict[ResourceKind, str] = {
    ResourceKind.VIDEO: "content_detail",
    ResourceKind.USER: "author_profile",
}

#: The canonical parameter each endpoint expects the resolved id under.
_PARAM_BY_RESOURCE: dict[ResourceKind, str] = {
    ResourceKind.VIDEO: "content_id",
    ResourceKind.USER: "author_id",
}


def make_redirect_fetcher(client: httpx.AsyncClient | None = None):
    """Build a fetcher that reveals one hop at a time.

    Redirects are deliberately not followed by the client: each Location is
    handed back so the allowlist runs against it before anything else is
    requested. Letting httpx chase the chain itself would mean the first
    off-allowlist host had already been contacted by the time we looked.
    """
    owned = client is None

    async def fetch(url: str) -> str | None:
        nonlocal client
        if client is None:
            client = httpx.AsyncClient(follow_redirects=False, timeout=EXPAND_TIMEOUT_SECONDS)
        if not is_allowed_host(url):
            raise InvalidUrl(
                "the link points outside the supported platforms",
                details={"url": url},
            )
        try:
            response = await client.head(url)
        except httpx.HTTPError as exc:
            log.warning("parse.expand_failed", error=str(exc)[:160])
            return None
        location = response.headers.get("location")
        return location or None

    fetch.owns_client = owned  # type: ignore[attr-defined]
    return fetch


async def resolve_link(url: str, fetcher: Any) -> UrlKind:
    """Expand and identify, or raise with a code the caller can branch on."""
    identified = await resolve(url, fetcher, max_hops=MAX_REDIRECTS)
    if identified.platform is None:
        raise InvalidUrl(
            "the URL was not recognized as a supported Douyin or TikTok link",
            details={"url": url},
        )
    if identified.resource not in _ENDPOINT_BY_RESOURCE:
        raise UnsupportedContent(
            f"links of this kind are not supported yet: {identified.resource.value}",
            details={"resource": identified.resource.value},
        )
    if not identified.resource_id:
        raise InvalidUrl(
            "the link is recognized but carries no identifier",
            details={"url": url, "resource": identified.resource.value},
        )
    return identified


def to_call(identified: UrlKind) -> tuple[str, dict[str, Any]]:
    """Map an identified link onto ``(endpoint_name, canonical_params)``."""
    platform = identified.platform
    if platform is None:  # pragma: no cover - resolve_link already refused this
        raise InvalidUrl("the link carries no platform", details={"url": identified.original})
    capability = _ENDPOINT_BY_RESOURCE[identified.resource]
    param = _PARAM_BY_RESOURCE[identified.resource]
    return f"{platform.value}.{capability}", {param: identified.resource_id}


async def plan(url: str, fetcher: Any) -> tuple[str, dict[str, Any]]:
    """The whole journey: a pasted link in, an endpoint call out."""
    identified = await resolve_link(url, fetcher)
    endpoint, params = to_call(identified)
    log.info(
        "parse.resolved",
        platform=identified.platform.value if identified.platform else None,
        resource=identified.resource.value,
        endpoint=endpoint,
        expanded=identified.url != identified.original,
    )
    return endpoint, params


__all__ = [
    "EXPAND_TIMEOUT_SECONDS",
    "make_redirect_fetcher",
    "plan",
    "resolve_link",
    "to_call",
]
