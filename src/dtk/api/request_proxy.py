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

What this module does NOT do is resolve DNS. A name that resolves to a private
address today can resolve elsewhere between this check and the connection, so a
lookup here would buy a false sense of safety at the cost of a network call in a
request path; :func:`dtk.urls.is_private_host` takes the same position for the
same reason. The literal forms that exist only to evade string matching -
decimal, hex, IPv4-mapped IPv6, trailing dots - are what it does catch, and it
catches them by refusing anything that is not globally routable rather than by
listing what is bad.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

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
    """Validate a caller-supplied proxy, or explain why it cannot be used.

    Returns the URL to dial, or None when the caller supplied nothing. Never
    returns a value the current mode does not permit.

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

    # Host and scheme only. The value can carry credentials, so nothing that
    # could hold the whole URL is emitted.
    log.info("api.request_proxy.accepted", scheme=scheme, mode=mode.value)
    return text


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_LENGTH",
    "SETTING_KEY",
    "RequestProxyMode",
    "normalize",
]
