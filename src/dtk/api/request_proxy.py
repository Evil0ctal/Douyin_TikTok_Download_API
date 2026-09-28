"""Validation for an egress the *caller* supplies, rather than the operator.

Two kinds of proxy meet in this codebase and they carry opposite trust.

An operator-configured proxy is a row in the ``proxies`` table, typed by someone
with admin access. :mod:`dtk.api.routes.admin.proxy_urls` deliberately lets one
point at loopback or a LAN gateway, and says so: a local SOCKS listener is an
ordinary egress for a self-hosted install.

A caller-supplied ``?proxy=`` is a string from whoever holds an API key, and
"dial this address for me" is the request forgery primitive in its plainest
form. An instance on a public server sits inside a network the caller cannot
otherwise reach, so an unchecked value turns the deployment into a probe for its
own operator's intranet: cloud metadata on 169.254.169.254, a database on
10.x, an admin panel on 127.0.0.1.

So the default is to refuse the parameter entirely. An operator who wants the
feature opts in with ``security.request_proxy``, and the middle setting - the
one to actually use - keeps the caller on publicly routable addresses.

In that mode the host is resolved, every answer is checked, and the URL handed
on names the address that passed rather than the name. This module used to
refuse to resolve at all, on the grounds that a name can point elsewhere between
the check and the connection. The conclusion did not follow from the reason:
checking only the text left every name that simply points inside wide open, and
GHSA-q3h8-73xx-gwqx walked through with ``127-0-0-1.sslip.io`` - no race, just
a public DNS service that answers with the address the name spells. The race is
real, and pinning is what closes it: the worker, the browser service and anything
else downstream dial the literal that was checked and never look the name up
again. The literal forms that exist only to evade string matching - decimal,
hex, IPv4-mapped IPv6, trailing dots - are still refused before any lookup, by
refusing anything not globally routable rather than by listing what is bad.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Final
from urllib.parse import SplitResult, urlsplit, urlunsplit

from dtk.core.errors import DtkError, InvalidParam
from dtk.core.logging import get_logger
from dtk.urls import is_private_host

log = get_logger(__name__)

#: Schemes wreq can dial. Kept in step with `admin.proxy_urls.ALLOWED_SCHEMES`
#: but declared separately: that list answers "can we dial it", this one answers
#: "may a stranger ask us to", and they are free to diverge.
ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https", "socks5", "socks5h"})

#: Longest value accepted, before parsing. A proxy URL is short; anything else
#: is someone probing the parser.
MAX_LENGTH: Final = 512

#: What RFC 3986 allows in an authority: unreserved, sub-delims, percent
#: escapes, and the ":", "@" and brackets that give it structure.
AUTHORITY_RE: Final = re.compile(r"[A-Za-z0-9\-._~!$&'()*+,;=%:@\[\]]+")

#: How long the proxy's name gets to resolve. The lookup holds an API request
#: open, and a proxy whose own name does not answer is not going to carry
#: traffic either, so a slow one is refused rather than waited on.
RESOLVE_TIMEOUT_SECONDS: Final = 5.0

#: Looks a host up and returns every address it answers with. Injected so the
#: tests decide what a name resolves to without touching the network.
Resolver = Callable[[str], Awaitable[Sequence[str]]]


class RequestProxyMode(StrEnum):
    """What ``security.request_proxy`` may be set to.

    ``DENY`` is the default because the safe failure of this feature is not
    having it. The three values are ordered by how much the operator trusts
    whoever holds an API key.
    """

    #: Refuse the parameter. A caller that sends one is told so.
    DENY = "deny"
    #: Accept it, but only for a publicly routable destination.
    PUBLIC = "public"
    #: Accept whatever is given, loopback and private ranges included. Only
    #: coherent when every API key is held by someone already trusted with the
    #: network the instance runs in.
    ANY = "any"


SETTING_KEY: Final = "security.request_proxy"


def _reject(reason: str, detail: str) -> DtkError:
    """One shape of failure, so a caller can branch on `reason`.

    The offending value is never echoed back or logged: it can carry proxy
    credentials, and a rejected request is exactly when someone is most likely
    to have pasted a real one.
    """
    return InvalidParam(detail, details={"field": "proxy", "reason": reason})


def normalize(value: str | None, *, mode: RequestProxyMode) -> str | None:
    """Judge the text of a caller-supplied proxy, or explain why it cannot be used.

    Returns the URL as given, or None when the caller supplied nothing. This
    is the offline half: it cannot tell where a name leads, so in ``public``
    mode its answer is not yet safe to dial. Routes call :func:`vet`, which
    finishes the job.

    A disabled feature REFUSES rather than ignores. Silently dropping the
    parameter would send the request from the instance's own address while the
    caller believed it went through their proxy - which is worse than an error,
    because the caller only finds out from the other end.
    """
    if value is None or not value.strip():
        return None

    if mode is RequestProxyMode.DENY:
        raise _reject(
            "request_proxy_disabled",
            "this instance does not accept a caller-supplied proxy; "
            f"an administrator can enable it with {SETTING_KEY}",
        )

    text = value.strip()
    if len(text) > MAX_LENGTH:
        raise _reject("too_long", f"a proxy URL may be at most {MAX_LENGTH} characters")

    if "://" not in text:
        raise _reject(
            "scheme_missing",
            "give the proxy as a full URL, for example http://host:port",
        )

    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise _reject(
            "scheme_not_supported",
            f"proxy scheme must be one of {', '.join(sorted(ALLOWED_SCHEMES))}",
        )

    # Checked before asking urlsplit for the host, because the host it names
    # is only worth checking if the client that dials agrees with it. wreq and
    # the browser parse by WHATWG rules, which end the authority at a backslash
    # where Python reads on to the last "@"; any character RFC 3986 does not
    # allow in an authority is a place the two can disagree.
    if not AUTHORITY_RE.fullmatch(parts.netloc):
        raise _reject(
            "authority_invalid",
            "the proxy host, port or credentials contain characters that are "
            "not allowed there; percent-encode them",
        )

    try:
        host, port = parts.hostname, parts.port
    except ValueError as exc:  # a port that is not a number at all
        raise _reject("port_invalid", "the proxy port is not a number") from exc

    if not host:
        raise _reject("host_missing", "the proxy URL has no host")
    if port is not None and not (1 <= port <= 65535):
        raise _reject("port_invalid", "the proxy port is out of range")

    if mode is RequestProxyMode.PUBLIC and is_private_host(host):
        raise _reject(
            "host_not_public",
            "the proxy must be a publicly routable address; loopback, private, "
            "link-local and intranet names are refused",
        )

    return text


async def resolve_host(host: str) -> Sequence[str]:
    """Every address ``host`` answers with, from the event loop's resolver.

    The loop's resolver rather than ``socket.getaddrinfo``, which blocks: this
    runs inside an API request.
    """
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def vet(
    value: str | None,
    *,
    mode: RequestProxyMode,
    resolve: Resolver | None = None,
) -> str | None:
    """Validate a caller-supplied proxy and fix the address it will be dialled at.

    Returns the URL to dial, or None when the caller supplied nothing. Never
    returns a value the current mode does not permit: in ``public`` mode a
    name comes back replaced by the address that was checked, so nothing
    downstream resolves it a second time. ``any`` mode is handed back as given,
    because it permits every answer a lookup could give.
    """
    text = normalize(value, mode=mode)
    if text is None:
        return None

    dial = text
    if mode is RequestProxyMode.PUBLIC:
        dial = await _pin(text, resolve or resolve_host)

    # Scheme and mode only. The value can carry credentials, so nothing that
    # could hold the whole URL is emitted.
    log.info(
        "api.request_proxy.accepted",
        scheme=urlsplit(text).scheme.lower(),
        mode=mode.value,
        pinned=dial != text,
    )
    return dial


async def _pin(text: str, resolve: Resolver) -> str:
    """Resolve the proxy host, refuse any private answer, dial the one checked.

    Every answer is checked, not just the one that will be used: a record with
    a public and a private member is someone hedging, and refusing it costs a
    legitimate proxy nothing.
    """
    parts = urlsplit(text)
    host = parts.hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # A literal was judged as the address itself; there is nothing to look up.
        return text

    try:
        async with asyncio.timeout(RESOLVE_TIMEOUT_SECONDS):
            answers = list(await resolve(host))
    except (OSError, TimeoutError, ValueError) as exc:
        # ValueError covers what the IDNA codec raises for a label it cannot
        # encode, before any query is sent.
        raise _reject("host_unresolvable", "the proxy host does not resolve") from exc
    if not answers:
        raise _reject("host_unresolvable", "the proxy host does not resolve")

    if any(is_private_host(answer) for answer in answers):
        raise _reject(
            "host_not_public",
            "the proxy host resolves to a loopback, private, link-local or "
            "otherwise non-public address",
        )

    if parts.scheme.lower() == "https":
        # The TLS handshake with the proxy is verified against its name, and
        # that is the defence here: a name rebound to an internal address
        # cannot present a certificate for the caller's hostname. Pinning would
        # break exactly that check, so the name is kept.
        return text

    # IPv4 first when there is one. A container with no IPv6 route is the
    # common deployment, and a pinned address gets no happy-eyeballs fallback.
    ipv4 = [answer for answer in answers if ":" not in answer]
    return _with_host(parts, (ipv4 or answers)[0])


def _with_host(parts: SplitResult, address: str) -> str:
    """``parts`` with its host swapped for ``address``; userinfo and port kept."""
    userinfo, at, _ = parts.netloc.rpartition("@")
    host = f"[{address}]" if ":" in address else address
    port = f":{parts.port}" if parts.port is not None else ""
    return urlunsplit(parts._replace(netloc=f"{userinfo}{at}{host}{port}"))


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_LENGTH",
    "RESOLVE_TIMEOUT_SECONDS",
    "SETTING_KEY",
    "RequestProxyMode",
    "Resolver",
    "normalize",
    "resolve_host",
    "vet",
]
