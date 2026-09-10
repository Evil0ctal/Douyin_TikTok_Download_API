"""The wreq-backed transport.

One client per identity, cached and never shared. This is the part that is easy
to get wrong for the sake of efficiency: a single shared pool would let two
identities reuse one TLS connection, which binds them together at the layer the
platform inspects most closely. Two cookie sets arriving over one connection is
a stronger signal than either of them requesting too often
(docs/design/04-transport-signing.md).

The client carries what is fixed for the identity's lifetime - emulation profile
and proxy - and each request carries what is not: cookies, fingerprint headers,
timeout. The client's own cookie store stays off; cookies come from the identity
record and go back through `CookieSink`, because a jar living inside a client
would accumulate rotated `ttwid` values that never reach the database and vanish
on eviction.

Network calls are the only seam: `client_factory` builds the client, so every
behaviour here - caching, eviction, header and cookie wiring - is exercised
offline by injecting a factory that returns a recording double.

Outcome classification lives in `dtk.transport.classify`, where the ruleset is a
table rather than a branch; `WreqTransport.classify` is the same ruleset bound to
this transport.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol, cast

import wreq

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.transport.base import (
    MAX_TIMEOUT_SECONDS,
    CookieSink,
    IdentityLike,
    RawResponse,
    RequestSpec,
    TransportFailure,
    mask_proxy_url,
)
from dtk.transport.classify import Classification, Classifier, classify_exception
from dtk.transport.emulation import profile_for
from dtk.transport.headers import build_headers

log = get_logger(__name__)

#: Default per-request ceiling. Platform endpoints answer in well under a
#: second when healthy; a longer wait almost always means a dying proxy.
DEFAULT_TIMEOUT_SECONDS = 25.0

#: Connect phase gets its own, shorter budget so a dead proxy fails fast
#: instead of consuming the whole request timeout.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0

#: How long an idle connection is kept. Long enough to serve a burst on one
#: identity, short enough that a retired identity's sockets do not linger.
DEFAULT_POOL_IDLE_SECONDS = 90.0

#: Identity clients kept alive at once. Each holds sockets, so the pool is
#: bounded and evicts least-recently-used.
DEFAULT_MAX_CLIENTS = 64

#: One identity is serial by design (docs/design/01-architecture.md), so it has
#: no use for a wide per-host pool.
POOL_MAX_IDLE_PER_HOST = 2


class WreqClient(Protocol):
    """The slice of `wreq.Client` this module uses."""

    async def request(self, method: Any, url: str, **kwargs: Any) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class ClientOptions:
    """Everything a client is built from; all of it fixed for its lifetime.

    The generated repr is suppressed because `proxy_url` carries the proxy
    account credentials in its userinfo, and this object is handed to a caller
    supplied `client_factory` - one raised exception there and the credentials
    are in a traceback (docs/design/08-security.md).
    """

    identity_id: str
    emulation: Any
    emulation_name: str
    proxy_url: str | None
    connect_timeout: float
    pool_idle_timeout: float

    def __repr__(self) -> str:
        return (
            f"ClientOptions(identity_id={self.identity_id!r}, "
            f"emulation_name={self.emulation_name!r}, "
            f"proxy_url={mask_proxy_url(self.proxy_url)!r}, "
            f"connect_timeout={self.connect_timeout!r}, "
            f"pool_idle_timeout={self.pool_idle_timeout!r})"
        )


ClientFactory = Callable[[ClientOptions], WreqClient]


def default_client_factory(options: ClientOptions) -> WreqClient:
    """Build a real `wreq.Client` bound to one identity.

    `no_proxy` is set explicitly when the identity has no proxy: without it the
    client would silently inherit the host's proxy environment, and every
    "direct" identity would share an exit nobody chose.
    """
    config: dict[str, Any] = {
        "emulation": options.emulation,
        # Cookies are owned by the identity record, not by the client.
        "cookie_store": False,
        "connect_timeout": timedelta(seconds=options.connect_timeout),
        "pool_idle_timeout": timedelta(seconds=options.pool_idle_timeout),
        "pool_max_idle_per_host": POOL_MAX_IDLE_PER_HOST,
        "gzip": True,
        "brotli": True,
        "deflate": True,
        "zstd": True,
    }
    if options.proxy_url:
        config["proxies"] = [wreq.Proxy.all(options.proxy_url)]
    else:
        config["no_proxy"] = True
    return cast(WreqClient, wreq.Client(**config))


@dataclass(slots=True)
class _CachedClient:
    """A live client plus what it was built for.

    Mutable on purpose: this is cache bookkeeping, not domain data. The
    identity attributes recorded here are compared on every request so a client
    is never reused for a different proxy or profile than it was built with.

    `in_flight` exists so that eviction can never close a client out from under
    a live request. Capacity eviction is driven by *other* identities arriving,
    so without it a burst of new identities silently kills requests that were
    already on the wire - and those deaths would be counted as NETWORK_ERROR
    against identities that did nothing wrong.
    """

    client: WreqClient
    options: ClientOptions
    created_at: float
    last_used_at: float
    requests: int = 0
    in_flight: int = 0
    detached: bool = False
    closed: bool = False


@dataclass(frozen=True, slots=True)
class TransportStats:
    """Snapshot for the console's system information page."""

    clients: int
    max_clients: int
    requests: int = 0
    evictions: int = 0
    profiles: Mapping[str, str] = field(default_factory=dict)


class WreqTransport:
    """`Transport` over wreq, with one cached client per identity."""

    def __init__(
        self,
        *,
        cookie_sink: CookieSink | None = None,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        pool_idle_timeout: float = DEFAULT_POOL_IDLE_SECONDS,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        client_factory: ClientFactory | None = None,
        classifier: Classifier | None = None,
    ) -> None:
        if max_clients < 1:
            raise InvalidParam("max_clients must be at least 1")
        self._cookie_sink = cookie_sink
        self._default_timeout = default_timeout
        self._connect_timeout = connect_timeout
        self._pool_idle_timeout = pool_idle_timeout
        self._max_clients = max_clients
        self._factory: ClientFactory = client_factory or default_client_factory
        self._classifier = classifier or Classifier()
        self._clients: OrderedDict[str, _CachedClient] = OrderedDict()
        self._lock = asyncio.Lock()
        self._requests = 0
        self._evictions = 0

    # ---------------------------------------------------------------- clients

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def has_client(self, identity_id: str) -> bool:
        return identity_id in self._clients

    def stats(self) -> TransportStats:
        return TransportStats(
            clients=len(self._clients),
            max_clients=self._max_clients,
            requests=self._requests,
            evictions=self._evictions,
            profiles={
                identity_id: entry.options.emulation_name
                for identity_id, entry in self._clients.items()
            },
        )

    def _options_for(self, identity: IdentityLike) -> ClientOptions:
        match = profile_for(identity.fingerprint)
        return ClientOptions(
            identity_id=identity.id,
            emulation=match.profile.emulation,
            emulation_name=match.profile.name,
            proxy_url=identity.proxy_url,
            connect_timeout=self._connect_timeout,
            pool_idle_timeout=self._pool_idle_timeout,
        )

    async def _acquire(self, identity: IdentityLike) -> _CachedClient:
        options = self._options_for(identity)
        async with self._lock:
            entry = self._clients.get(identity.id)
            if entry is not None:
                if entry.options == options:
                    self._clients.move_to_end(identity.id)
                    entry.last_used_at = time.monotonic()
                    return entry
                # The four bound attributes of an identity are immutable by
                # design; if one changed, the connections built for the old one
                # must not carry the new one.
                log.warning(
                    "transport.client.rebuilt",
                    identity_id=identity.id,
                    emulation=options.emulation_name,
                    previous_emulation=entry.options.emulation_name,
                    proxy_changed=entry.options.proxy_url != options.proxy_url,
                )
                self._discard(identity.id)

            entry = _CachedClient(
                client=self._factory(options),
                options=options,
                created_at=time.monotonic(),
                last_used_at=time.monotonic(),
            )
            self._clients[identity.id] = entry
            log.info(
                "transport.client.created",
                identity_id=identity.id,
                emulation=options.emulation_name,
                proxied=options.proxy_url is not None,
                clients=len(self._clients),
            )
            self._evict_overflow()
            return entry

    def _discard(self, identity_id: str) -> _CachedClient | None:
        """Unlink an identity's client and close it once it is idle."""
        entry = self._clients.pop(identity_id, None)
        if entry is None:
            return None
        entry.detached = True
        self._evictions += 1
        self._close_entry(identity_id, entry)
        return entry

    def _close_entry(self, identity_id: str, entry: _CachedClient) -> None:
        """Close a detached client, unless a request is still using it.

        The request that finishes last calls this again, so the client is closed
        exactly once and never while it is carrying traffic.
        """
        if entry.closed or entry.in_flight > 0:
            return
        entry.closed = True
        try:
            entry.client.close()
        except Exception as exc:
            # Closing is best effort: a client that cannot be closed must not
            # keep a retired identity in the cache.
            log.warning("transport.client.close_failed", identity_id=identity_id, error=str(exc))

    def _evict_overflow(self) -> None:
        while len(self._clients) > self._max_clients:
            identity_id, _ = next(iter(self._clients.items()))
            self._discard(identity_id)
            log.info(
                "transport.client.evicted",
                identity_id=identity_id,
                reason="capacity",
                clients=len(self._clients),
            )

    async def evict(self, identity_id: str) -> None:
        """Drop an identity's client: retirement, cooling, or a proxy change."""
        async with self._lock:
            if self._discard(identity_id) is not None:
                log.info(
                    "transport.client.evicted",
                    identity_id=identity_id,
                    reason="explicit",
                    clients=len(self._clients),
                )

    async def close(self) -> None:
        """Close every client. Safe to call twice."""
        async with self._lock:
            for identity_id in list(self._clients):
                self._discard(identity_id)
            log.info("transport.closed")

    # --------------------------------------------------------------- requests

    def classify(
        self,
        response: RawResponse | None = None,
        exception: BaseException | None = None,
        *,
        empty_body_is_normal: bool = False,
    ) -> Classification:
        """Classify a response or failure with this transport's ruleset."""
        return self._classifier.classify(
            response, exception, empty_body_is_normal=empty_body_is_normal
        )

    async def request(
        self,
        identity: IdentityLike,
        spec: RequestSpec,
        timeout: float | None = None,
    ) -> RawResponse:
        """Perform one request as this identity.

        Raises `TransportFailure` when no response arrived; an error *response*
        is returned normally and left for the caller to classify.
        """
        entry = await self._acquire(identity)
        entry.in_flight += 1
        try:
            return await self._request_on(entry, identity, spec, timeout)
        finally:
            entry.in_flight -= 1
            if entry.detached:
                self._close_entry(identity.id, entry)

    async def _request_on(
        self,
        entry: _CachedClient,
        identity: IdentityLike,
        spec: RequestSpec,
        timeout: float | None,
    ) -> RawResponse:
        method = _resolve_method(spec.method)
        headers = build_headers(identity.fingerprint, spec.headers)
        budget = _clamp_timeout(timeout if timeout is not None else self._default_timeout)

        kwargs: dict[str, Any] = {"headers": headers, "timeout": timedelta(seconds=budget)}
        if spec.params:
            kwargs["query"] = dict(spec.params)
        if identity.cookies:
            kwargs["cookies"] = dict(identity.cookies)
        if spec.json_body is not None:
            kwargs["json"] = spec.json_body
        elif spec.body is not None:
            kwargs["body"] = spec.body

        started = time.perf_counter()
        try:
            response = await entry.client.request(method, spec.url, **kwargs)
            try:
                body = await response.bytes()
            except BaseException:
                # The response exists but its body failed mid-stream. wreq wants
                # the response closed explicitly; skipping it holds the
                # connection until the object is collected, which on a dying
                # proxy is exactly when connections are scarcest.
                await _close_response(response)
                raise
        except Exception as exc:
            elapsed_ms = _elapsed_ms(started)
            classification = classify_exception(exc)
            log.warning(
                "transport.request.failed",
                identity_id=identity.id,
                endpoint=spec.endpoint,
                error=type(exc).__name__,
                rule=classification.rule,
                elapsed_ms=elapsed_ms,
                proxied=entry.options.proxy_url is not None,
            )
            raise TransportFailure(
                f"{spec.method} {spec.url} failed: {type(exc).__name__}",
                identity_id=identity.id,
                url=spec.url,
                elapsed_ms=elapsed_ms,
                cause=exc,
            ) from exc

        elapsed_ms = _elapsed_ms(started)
        entry.requests += 1
        entry.last_used_at = time.monotonic()
        self._requests += 1

        raw = RawResponse(
            status=_status_int(response.status),
            headers=_headers_dict(response.headers),
            body=body,
            final_url=str(getattr(response, "url", spec.url)),
            elapsed_ms=elapsed_ms,
        )
        await self._write_back_cookies(identity.id, response)
        log.info(
            "transport.request.done",
            identity_id=identity.id,
            endpoint=spec.endpoint,
            status=raw.status,
            bytes=len(body),
            elapsed_ms=elapsed_ms,
            emulation=entry.options.emulation_name,
        )
        return raw

    async def _write_back_cookies(self, identity_id: str, response: Any) -> None:
        """Hand rotated cookies to the pool.

        A failing sink is logged, not raised: the response has already cost this
        identity a slot in its rate budget, and throwing it away over a database
        hiccup is the more expensive mistake. Rotation is not invalidation - the
        previous jar keeps working - so the error only has to be visible.
        """
        if self._cookie_sink is None:
            return
        cookies = _response_cookies(response)
        if not cookies:
            return
        try:
            await self._cookie_sink(identity_id, cookies)
        except Exception as exc:
            log.error(
                "transport.cookie.write_failed",
                identity_id=identity_id,
                count=len(cookies),
                error=str(exc),
            )
            return
        log.info("transport.cookie.updated", identity_id=identity_id, count=len(cookies))


async def _close_response(response: Any) -> None:
    """Best effort close of a response whose body could not be read.

    Only ever called on the failure path: wreq's `close` drops the underlying
    connection whether or not pooling is on, so calling it after a successful
    read would defeat the per-identity connection reuse this module exists for.
    """
    closer = getattr(response, "close", None)
    if closer is None:
        return
    try:
        await closer()
    except Exception as exc:
        log.debug("transport.response.close_failed", error=str(exc))


def _clamp_timeout(timeout: float) -> float:
    if timeout <= 0:
        raise InvalidParam("timeout must be positive")
    return min(timeout, MAX_TIMEOUT_SECONDS)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _resolve_method(method: str) -> Any:
    """Map a method name onto `wreq.Method`, which rejects plain strings."""
    resolved = getattr(wreq.Method, method.upper(), None)
    if resolved is None:
        raise InvalidParam(f"unsupported HTTP method: {method}")
    return resolved


def _status_int(status: Any) -> int:
    """Read an int out of wreq's `StatusCode`, or anything str-like."""
    as_int = getattr(status, "as_int", None)
    if callable(as_int):
        return int(as_int())
    if isinstance(status, int):
        return status
    text = str(status).strip()
    digits = text.split(" ", 1)[0]
    if digits.isdigit():
        return int(digits)
    raise InvalidParam(f"unreadable response status: {text}")


def _headers_dict(headers: Any) -> dict[str, str]:
    """Flatten a header map to lowercased keys, repeats joined by ", ".

    Set-Cookie is dropped rather than folded in: joining two Set-Cookie lines
    with ", " loses the boundary between them whenever an `expires` attribute
    contains a comma, so the folded value is not something any caller could
    parse back. The cookies are taken from the parsed cookie list instead, and
    leaving the raw header out also keeps a credential out of a structure that
    gets cached and logged (docs/design/08-security.md).
    """
    result: dict[str, str] = {}
    if headers is None:
        return result
    pairs = headers.items() if hasattr(headers, "items") else headers
    for name, value in pairs:
        key = _as_text(name).lower()
        if key == "set-cookie":
            continue
        text = _as_text(value)
        existing = result.get(key)
        result[key] = f"{existing}, {text}" if existing is not None else text
    return result


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1", errors="replace")
    return str(value)


def _response_cookies(response: Any) -> dict[str, str]:
    """Cookies the platform set on this response, as a plain mapping."""
    raw = getattr(response, "cookies", None)
    cookies: dict[str, str] = {}
    if not raw:
        return cookies
    for cookie in raw:
        name = getattr(cookie, "name", None)
        value = getattr(cookie, "value", None)
        if name and value is not None:
            cookies[_as_text(name)] = _as_text(value)
    return cookies


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_CLIENTS",
    "DEFAULT_POOL_IDLE_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ClientFactory",
    "ClientOptions",
    "TransportStats",
    "WreqClient",
    "WreqTransport",
    "default_client_factory",
]
