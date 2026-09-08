"""One upstream call, assembled by hand.

``dtk fetch``, ``dtk identity test`` and the console's identity probe
deliberately do not go through the scheduler. The point of all three is to
answer "is the endpoint dead?" during an incident, and doc 15 spells out what
that means: no login, no authentication, no rate limiting. Borrowing a lease
would also make the probe change the very pool state the operator is trying to
read - a blocked probe would cool an identity that the console then reports as
cooling for reasons nobody can reconstruct.

So the identity is chosen explicitly, the request is signed and sent once, and
nothing is written back. What this path does share with the service layer is
everything that decides correctness: the same URL parser, the same endpoint
tables, the same signer registry, the same transport and the same classifier.

The signed query string is sent byte for byte on the URL rather than as a
parameter mapping. Signatures are computed over an exact byte sequence, so
handing the parameters back to an HTTP client that re-encodes them is how a
signature silently stops matching.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

import httpx

from dtk.core.config import Config, extra_url_hosts
from dtk.core.crypto import Cipher
from dtk.core.errors import (
    IdentityPoolExhausted,
    Internal,
    InvalidParam,
    NotFound,
    UnsupportedContent,
)
from dtk.core.types import IdentityState, Outcome, Platform
from dtk.db.models import Proxy
from dtk.db.repositories import IdentityRepository
from dtk.identity.pool import IdentityPool, LiveIdentity
from dtk.ops.masking import short_id
from dtk.ops.probes import probe_identity
from dtk.platforms import get_adapter
from dtk.signing import RequestSpec as SigningRequest
from dtk.signing import SignerRegistry, SigningSession, StaticFingerprint, native_signers
from dtk.transport.base import Fingerprint, RawResponse, RequestSpec, TransportIdentity
from dtk.urls import ResourceKind, UrlKind, resolve
from dtk.worker import registry
from dtk.worker.parsing import (
    EXPAND_TIMEOUT_SECONDS,
    ProxySource,
    egress_fetcher,
    pick_egress,
    redirect_fetcher,
)
from dtk.worker.registry import Capability, ResolvedCall

#: Timeout for one upstream call.
REQUEST_TIMEOUT_SECONDS: Final = 25.0


@dataclass(frozen=True, slots=True)
class Target:
    """A URL turned into one endpoint call.

    ``params`` are the canonical caller-facing names from
    :mod:`dtk.worker.registry` - ``content_id``, ``author_id``, ``unique_id`` -
    not a platform's own spelling. The registry translates them, so this module
    never has to know that Douyin says ``aweme_id`` and TikTok says ``item_id``.
    """

    platform: Platform
    #: Scheduler-visible endpoint name, for example "douyin.content_detail".
    endpoint: str
    capability: Capability
    params: dict[str, str]
    kind: UrlKind


def target_for(kind: UrlKind) -> Target:
    """Map a recognized URL onto the endpoint that answers it.

    Only the two resources a URL can identify on its own are routed. A live
    room or a music page is recognized by the URL parser but has no endpoint in
    the P0 set, and saying which resource was refused is more useful than a
    generic parse failure.
    """
    if kind.platform is None or (kind.resource_id is None and kind.handle is None):
        raise UnsupportedContent("the URL does not identify a fetchable resource")

    platform = kind.platform
    identifier = kind.resource_id or kind.handle or ""

    if kind.resource is ResourceKind.VIDEO:
        params = {registry.CONTENT_ID: identifier}
        capability = Capability.CONTENT_DETAIL
    elif kind.resource is ResourceKind.USER:
        # A TikTok user URL carries the @handle; Douyin carries the sec_user_id.
        key = registry.UNIQUE_ID if platform is Platform.TIKTOK else registry.AUTHOR_ID
        params = {key: identifier.lstrip("@")}
        capability = Capability.AUTHOR_PROFILE
    else:
        raise UnsupportedContent(
            f"{kind.resource.value} links have no endpoint yet",
            details={"resource": kind.resource.value, "platform": platform.value},
        )

    return Target(
        platform=platform,
        endpoint=registry.endpoint_name(platform, capability),
        capability=capability,
        params=params,
        kind=kind,
    )


def dump(parsed: Any, *, include_raw: bool) -> dict[str, Any]:
    """Normalized model to a JSON-ready dict, raw payload optional."""
    if hasattr(parsed, "model_dump"):
        return parsed.model_dump(mode="json", exclude=None if include_raw else {"raw"})
    if isinstance(parsed, dict):
        return dict(parsed)
    return {"value": parsed}


# --------------------------------------------------------------------------
# URL resolution
# --------------------------------------------------------------------------


async def resolve_target(
    text: str,
    *,
    proxy_url: str | None = None,
    egress: ProxySource | None = None,
    timeout: float = EXPAND_TIMEOUT_SECONDS,
    extra_hosts: frozenset[str] = frozenset(),
) -> Target:
    """Turn pasted share text into the endpoint call it stands for.

    ``extra_hosts`` is ``security.url_allowlist``: it widens which hosts a short
    link may redirect through, and the fetcher is built with the same list so
    both halves of the check agree.

    ``egress`` is the lazy form of ``proxy_url``, resolved only if a hop is
    actually taken. Most text is a full URL that takes none, and the lookup
    behind an egress is a database round trip - and one that raises when the
    pool has no healthy proxy, which would turn "your link needed no
    expansion" into "no proxy is available".
    """
    fetcher = (
        egress_fetcher(egress, timeout=timeout, extra_hosts=extra_hosts)
        if egress is not None
        else redirect_fetcher(proxy_url=proxy_url, timeout=timeout, extra_hosts=extra_hosts)
    )
    kind = await resolve(text, fetcher, extra_hosts=extra_hosts)
    return target_for(kind)


# --------------------------------------------------------------------------
# Identity selection
# --------------------------------------------------------------------------


async def proxy_url_for(session: Any, cipher: Cipher, proxy_id: uuid.UUID | None) -> str | None:
    """Decrypt one proxy URL. Returns None when the identity has no proxy."""
    if proxy_id is None:
        return None
    row = await session.get(Proxy, proxy_id)
    if row is None:
        return None
    return cipher.decrypt(row.url_encrypted, aad=str(row.id))


async def pick_identity(
    session: Any,
    cipher: Cipher,
    platform: Platform | None = None,
    *,
    identity_id: str | None = None,
    pool: IdentityPool | None = None,
) -> LiveIdentity:
    """Load a usable identity, either the one named or the least recently used.

    Least recently used rather than healthiest: a probe should reach for the
    identity the scheduler would have reached for next, not for the best one in
    the pool.

    ``platform`` only chooses which pool to draw from, so a caller that names an
    identity may omit it - the console's probe is handed an id and nothing else,
    and the row is what says which platform it belongs to. ``pool`` is likewise
    an accommodation for a caller that already owns one, so a job runs on the
    same collaborator as the loops around it.
    """
    repo = IdentityRepository(session)
    if identity_id is not None:
        row = await repo.get(_as_uuid(identity_id))
        if row is None:
            raise NotFound(f"no identity with id {identity_id}")
        if row.state == IdentityState.RETIRED.value:
            raise InvalidParam(f"identity {identity_id} is retired; its cookies were wiped")
    elif platform is None:
        raise InvalidParam("pick an identity by id or by platform; neither was given")
    else:
        rows = await repo.list_by_state(
            platform=platform,
            states=(IdentityState.ACTIVE, IdentityState.COOLING, IdentityState.DEGRADED),
            limit=1,
        )
        if not rows:
            raise IdentityPoolExhausted(
                f"no usable {platform.value} identity",
                details={"platform": platform.value},
            )
        row = rows[0]

    proxy_url = await proxy_url_for(session, cipher, row.proxy_id)
    identity = await (pool or IdentityPool(cipher)).load(session, str(row.id), proxy_url=proxy_url)
    if identity is None:
        raise NotFound(f"identity {row.id} could not be loaded")
    return identity


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise InvalidParam(f"not an identity id: {value}") from exc


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------


def signing_view(fingerprint: Fingerprint) -> StaticFingerprint:
    """The slice of a fingerprint the signers need."""
    width: int | None = None
    height: int | None = None
    if fingerprint.screen and "x" in fingerprint.screen:
        raw_width, _, raw_height = fingerprint.screen.partition("x")
        width = _as_int(raw_width)
        height = _as_int(raw_height)
    return StaticFingerprint(
        user_agent=fingerprint.user_agent or "",
        browser_platform=fingerprint.platform,
        screen_width=width,
        screen_height=height,
    )


def _as_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def build_registry(
    browser_rpc_url: str, *, client: httpx.AsyncClient | None = None
) -> SignerRegistry:
    """Native signers, with browser-rpc behind them when it is configured.

    ``RpcSigner`` borrows an ``httpx`` client rather than owning one, so a
    caller that configured browser-rpc must supply the client that will carry
    the signing calls. :func:`signing_stack` is that caller for every CLI
    command; passing a URL with no client would quietly build a registry with
    no fallback, so it is refused rather than degraded.
    """
    from dtk.signing import RpcSigner

    rpc = None
    if browser_rpc_url:
        if client is None:
            raise ValueError("browser-rpc is configured but no HTTP client was supplied")
        rpc = RpcSigner(client, browser_rpc_url)
    return SignerRegistry(native_signers(), rpc)


@asynccontextmanager
async def signing_stack(browser_rpc_url: str) -> AsyncIterator[tuple[Any, SignerRegistry]]:
    """The transport and the signer registry one probe needs, closed together.

    Every command that issues an upstream call goes through here, so browser-rpc
    is behind the native signers in all of them. Building the registry without
    the RPC client is what makes ``dtk fetch`` fail on a platform whose native
    signer is disabled, on a box where browser-rpc is running and would have
    answered.
    """
    from dtk.transport import WreqTransport

    transport = WreqTransport()
    client = (
        httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
        if browser_rpc_url
        else None
    )
    try:
        yield transport, build_registry(browser_rpc_url, client=client)
    finally:
        await transport.close()
        if client is not None:
            await client.aclose()


async def call_endpoint(
    transport: Any,
    signers: SignerRegistry,
    identity: LiveIdentity,
    call: ResolvedCall,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> RawResponse:
    """Sign and issue one request as ``identity``. No retry, no bookkeeping."""
    adapter = get_adapter(call.platform)
    spec = adapter.build_request(call.endpoint, **call.params)

    # Built before signing and reused for the request, because the two have to
    # describe the same visitor. Douyin's `verifyFp` is the `s_v_web_id` of
    # whichever browser computed the signature, so signing without this jar
    # produced a query naming one session and a Cookie header naming another -
    # which the platform answers by withholding the payload. That is what the
    # console's identity probe was reporting as risk control.
    sender = TransportIdentity(
        id=identity.id,
        platform=identity.platform,
        fingerprint=identity.fingerprint,
        proxy_url=identity.proxy_url,
        cookies=identity.cookies,
    )
    signed = await signers.sign(
        SigningRequest(method=spec["method"], url=spec["url"], params=spec["params"]),
        signing_view(identity.fingerprint),
        SigningSession(
            cookies=identity.cookies,
            proxy_url=identity.proxy_url,
            identity_id=str(identity.id),
        ),
        platform=call.platform,
        endpoint=call.endpoint,
    )
    url = signed.signed_url(spec["url"]) if signed.query else spec["url"]

    return await transport.request(
        sender,
        RequestSpec(
            url=url,
            method=spec["method"],
            params=None if signed.query else spec["params"],
            headers={**spec["headers"], **dict(signed.headers)},
            endpoint=call.endpoint,
        ),
        timeout,
    )


async def smoke(
    url: str,
    *,
    session: Any,
    cipher: Cipher,
    config: Config,
    transport: Any,
    signers: SignerRegistry,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """One public link through resolve, sign, fetch and classify.

    The last step of the setup wizard and of both diagnoses, so it lives beside
    the pieces it assembles rather than in either caller. ``dtk diagnose`` opens
    a signing stack for it and the worker passes the one it already runs on;
    that is the whole difference between the two.

    Raises rather than reporting. :func:`dtk.ops.diagnose.check_smoke` turns the
    exception into a failed step with the reason attached, and a result object
    saying "not ok" would put that decision back on every caller.
    """
    # The expansion hop is an outbound request like any other. Following the
    # link from this host while the probe itself leaves through a proxy shows
    # the platform both addresses and that they belong together, so the hop
    # borrows a pool egress too - see dtk.worker.parsing.pick_egress for why
    # that is a proxy rather than a lease.
    target = await resolve_target(
        url,
        # Lazily: SMOKE_URLS are full platform URLs that take no hop, and
        # pick_egress raises when proxies exist but none are healthy. Awaited
        # eagerly, a box with dead proxies failed the smoke step - and the last
        # step of the setup wizard - with "no healthy proxy is available to
        # expand the link", which is neither what failed nor what to do about it.
        egress=lambda: pick_egress(session, cipher),
        extra_hosts=extra_url_hosts(config),
    )
    call = registry.resolve(target.endpoint, target.params, config)
    identity = await pick_identity(session, cipher, target.platform)
    probe = await probe_identity(transport, signers, identity, call, timeout=timeout)
    if not probe.ok:
        # INTERNAL for the same reason `TransportFailure` carries it: an
        # unusable answer here is a fault in our own egress path, not in the
        # link the operator handed over. `detail` is already scrubbed.
        raise Internal(
            f"{probe.outcome.value if probe.outcome else 'no answer'}: "
            f"{probe.detail or probe.rule or 'no detail'}"
        )
    return {
        "endpoint": target.endpoint,
        "identity": short_id(identity.id),
        "outcome": (probe.outcome or Outcome.OK).value,
        "http_status": probe.status,
        "latency_ms": probe.latency_ms,
    }


__all__ = [
    "EXPAND_TIMEOUT_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "Target",
    "build_registry",
    "call_endpoint",
    "dump",
    "pick_identity",
    "proxy_url_for",
    "redirect_fetcher",
    "resolve_target",
    "signing_stack",
    "signing_view",
    "smoke",
    "target_for",
]
