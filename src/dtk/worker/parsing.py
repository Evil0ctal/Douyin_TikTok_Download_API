"""Turning a pasted link into a concrete endpoint call.

``POST /api/v1/parse`` and the MCP ``parse_url`` tool both exist so a caller can
hand over whatever they copied and get a result, without first working out which
of ten endpoints applies. That convenience has to be paid for somewhere, and
this is where: expand the short link, decide what the link points at, and map it
onto one of the endpoints the worker already knows how to run.

Expansion is the security-sensitive half, and in two ways. A short link cannot
be validated before it is followed - that is the whole point of it - so the
target is checked against the allowlist after every hop, never before, and the
hop count is bounded. It is also an outbound request like any other, so it
leaves through an egress from the proxy pool: expanding from this host's own
address and then fetching the content through a proxy hands the platform both
addresses *and* the fact that they belong together, which is precisely the
correlation the pool exists to prevent. See docs/design/08-security.md and
docs/design/02-identity-pool.md.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx

from dtk.core.crypto import Cipher
from dtk.core.db import session_scope
from dtk.core.errors import (
    IdentityPoolExhausted,
    Internal,
    InvalidUrl,
    NotConfigured,
    UnsupportedContent,
)
from dtk.core.logging import get_logger
from dtk.db.models import Proxy
from dtk.db.repositories import ProxyRepository
from dtk.urls import (
    MAX_REDIRECTS,
    RedirectFetcher,
    ResourceKind,
    UrlKind,
    is_allowed_host,
    resolve,
)

log = get_logger(__name__)

EXPAND_TIMEOUT_SECONDS = 10.0

#: How long a caller is asked to wait when every egress is unhealthy. The proxy
#: prober sweeps on roughly this interval, so it is the soonest the answer can
#: honestly change.
EXPAND_RETRY_AFTER_SECONDS = 300

#: The expansion hop carries no cookie and no signature, so the User-Agent is
#: the only thing the shortener has to go on. ``python-httpx/x.y`` is both a
#: giveaway and a reason to be answered differently than a phone would be.
EXPAND_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
}

#: Statuses that mean "there is another hop". Anything else settles the chain.
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})

#: Yields the proxy URL one expansion leaves from, or ``None`` for the direct
#: egress. Async because the answer comes from the database.
ProxySource = Callable[[], Awaitable[str | None]]

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


def redirect_fetcher(
    *,
    proxy_url: str | None = None,
    timeout: float = EXPAND_TIMEOUT_SECONDS,
    extra_hosts: frozenset[str] = frozenset(),
) -> RedirectFetcher:
    """Build a fetcher that reveals one hop at a time, through ``proxy_url``.

    Redirects are deliberately not followed by the client: each Location is
    handed back so the allowlist runs against it before anything else is
    requested. Letting httpx chase the chain itself would mean the first
    off-allowlist host had already been contacted by the time we looked.

    ``extra_hosts`` has to match what :func:`dtk.urls.expand` was given: this
    check guards the caller that hands over an unvalidated URL, and a narrower
    list here would refuse the very hop the operator allowlisted.
    """

    async def fetch(url: str) -> str | None:
        if not is_allowed_host(url, extra_hosts=extra_hosts):
            raise InvalidUrl(
                "the link points outside the supported platforms",
                details={"url": url},
            )
        try:
            async with (
                httpx.AsyncClient(
                    follow_redirects=False,
                    proxy=proxy_url,
                    timeout=timeout,
                    headers=EXPAND_HEADERS,
                ) as client,
                # Streamed, because the hop that settles a chain is a real
                # content page: the headers answer the question, and the body
                # would be a megabyte of HTML pulled through a metered egress.
                client.stream("GET", url) as response,
            ):
                if response.status_code not in _REDIRECT_STATUSES:
                    return None
                return response.headers.get("location") or None
        except (httpx.HTTPError, ValueError) as exc:
            # The type, never the message: an httpx error quotes the URL it
            # failed on, and a proxy URL carries a password. ValueError belongs
            # here for the same reason - a proxy row saved without a scheme is
            # rejected by httpx with the whole credential in the message, and
            # letting that escape would print it in the worker's traceback.
            log.warning("parse.expand_failed", error=type(exc).__name__)
            raise Internal(
                "the short link could not be followed",
                details={"reason": "expand_failed"},
            ) from exc

    return fetch


def egress_fetcher(
    egress: ProxySource,
    *,
    timeout: float = EXPAND_TIMEOUT_SECONDS,
    extra_hosts: frozenset[str] = frozenset(),
) -> RedirectFetcher:
    """A fetcher that binds itself to one pool egress on its first hop.

    Once per link rather than once per hop: a chain whose hops leave from two
    different exits is a correlation we would be creating ourselves. Lazily,
    because most parses are of a full URL that never takes a hop at all, and the
    lookup behind ``egress`` is a database round trip that has no business on
    that path.
    """
    bound: RedirectFetcher | None = None

    async def fetch(url: str) -> str | None:
        nonlocal bound
        if bound is None:
            bound = redirect_fetcher(
                proxy_url=await egress(), timeout=timeout, extra_hosts=extra_hosts
            )
        return await bound(url)

    return fetch


async def pick_egress(session: Any, cipher: Cipher) -> str | None:
    """Choose the exit address one expansion leaves from.

    A healthy proxy at random, and deliberately not a leased identity: the hop
    happens before the endpoint - and therefore the platform, the token bucket
    and the identity - is known, so a lease taken here would spend a real
    request's quota, cool a real identity when the shortener is slow, and add
    the scheduler's wait to the latency of every short-link parse. This request
    carries no cookie to protect. It needs an address, nothing more.

    A deployment with no proxies at all expands directly, because that is where
    its fetches go too; doc 02 already warns what running without proxies costs.
    Proxies that exist but are all unhealthy are refused instead, since falling
    back to the host address is the one answer that leaks something new.
    """
    # The whole table, not just the healthy rows: "no proxies configured" and
    # "every proxy is down" need different answers, and telling them apart with
    # a second query would be a second round trip on the latency path.
    rows = list(await ProxyRepository(session).list_all())
    if not rows:
        return None

    healthy = [row for row in rows if row.healthy]
    random.shuffle(healthy)
    for row in healthy:
        url = _decrypt(cipher, row)
        if url is not None:
            log.debug("parse.expand_egress", proxy_id=str(row.id))
            return url

    raise IdentityPoolExhausted(
        "no healthy proxy is available to expand the link",
        retry_after=EXPAND_RETRY_AFTER_SECONDS,
        details={"reason": "no_healthy_egress"},
    )


def _decrypt(cipher: Cipher, row: Proxy) -> str | None:
    try:
        return cipher.decrypt(row.url_encrypted, aad=str(row.id))
    except Exception:
        # Encrypted under a different secret key. Skipping the row beats failing
        # a parse that the next proxy would have served.
        log.warning("parse.expand_egress_undecryptable", proxy_id=str(row.id))
        return None


class PoolEgress:
    """:data:`ProxySource` over the proxies table, with its own session.

    The worker resolves the endpoint before it opens the session the fetch runs
    in, so this one cannot borrow it.
    """

    __slots__ = ("_cipher", "_session_factory")

    def __init__(
        self, cipher: Cipher, *, session_factory: Callable[[], Any] = session_scope
    ) -> None:
        self._cipher = cipher
        self._session_factory = session_factory

    async def __call__(self) -> str | None:
        async with self._session_factory() as session:
            return await pick_egress(session, self._cipher)


async def no_egress() -> str | None:
    """The default :data:`ProxySource`: refuse rather than leave from the host.

    A worker assembled without an egress still parses full URLs, since those
    take no hop. Only a short link reaches here, and answering it from this
    host's public address is the leak the module docstring is about, so the
    wiring mistake is reported instead of quietly worked around.
    """
    raise NotConfigured(
        "short-link expansion has no egress configured",
        details={"reason": "no_expand_egress"},
    )


async def resolve_link(
    url: str, fetcher: Any, *, extra_hosts: frozenset[str] = frozenset()
) -> UrlKind:
    """Expand and identify, or raise with a code the caller can branch on."""
    identified = await resolve(url, fetcher, max_hops=MAX_REDIRECTS, extra_hosts=extra_hosts)
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


async def plan(
    url: str, fetcher: Any, *, extra_hosts: frozenset[str] = frozenset()
) -> tuple[str, dict[str, Any]]:
    """The whole journey: a pasted link in, an endpoint call out.

    ``extra_hosts`` is ``security.url_allowlist``, and it only ever lets a
    redirect hop through: an entry names no platform, so a chain that ends on
    one is refused here as unrecognized rather than turned into a call.
    """
    identified = await resolve_link(url, fetcher, extra_hosts=extra_hosts)
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
    "EXPAND_HEADERS",
    "EXPAND_RETRY_AFTER_SECONDS",
    "EXPAND_TIMEOUT_SECONDS",
    "PoolEgress",
    "ProxySource",
    "egress_fetcher",
    "no_egress",
    "pick_egress",
    "plan",
    "redirect_fetcher",
    "resolve_link",
    "to_call",
]
