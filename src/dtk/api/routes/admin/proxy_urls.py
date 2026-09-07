"""Proxy URL parsing for the console's paste-a-list import.

Users arrive with a text file from a provider, in whichever of five shapes that
provider chose. Doc 07 is explicit that asking them to reformat a hundred lines
by hand is not an option, so all five are accepted:

    host:port
    host:port:user:pass
    user:pass@host:port
    http://user:pass@host:port
    socks5://user:pass@host:port

Note what is *not* checked here: a proxy may point at a private or loopback
address. A local SOCKS listener or a LAN gateway is a perfectly ordinary egress
for a self-hosted deployment. The SSRF allowlist applies to the URLs a caller
asks the service to *fetch*, which is a different question entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from dtk.core.errors import DtkError, InvalidParam

#: Schemes wreq can actually dial. Anything else is a typo, not a feature.
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "socks5", "socks5h"})

DEFAULT_SCHEME = "http"

MAX_LINES = 500


@dataclass(frozen=True, slots=True)
class ProxySpec:
    """A parsed proxy line, ready to be encrypted and stored."""

    url: str
    host: str
    port: int
    scheme: str
    has_credentials: bool

    @property
    def masked(self) -> str:
        prefix = "***:***@" if self.has_credentials else ""
        return f"{self.scheme}://{prefix}{self.host}:{self.port}"


def sample_of(line: str) -> str:
    """A quotable fragment of a line, with any credential part removed.

    Two of the accepted shapes carry a password, in two different places:
    before an ``@`` in ``user:pass@host:port``, and after the second colon in
    ``host:port:user:pass``. A line is rejected precisely because it fits
    neither cleanly, so both are stripped rather than the one the line looks
    like it uses - a rejected paste has to be correctable without its password
    reaching a response, a log or a console screen.
    """
    text = line.strip()
    scheme, separator, rest = text.partition("://")
    if not separator:
        scheme, rest = "", text
    authority = rest.split("/", 1)[0].rsplit("@", 1)[-1]
    if authority.startswith("["):
        # Bracketed IPv6 literal: the colons inside the brackets are the
        # address, so the trailing port is the only field to keep.
        host, _, tail = authority.partition("]")
        port = tail.lstrip(":").split(":", 1)[0]
        authority = f"{host}]" + (f":{port}" if port else "")
    else:
        authority = ":".join(authority.split(":")[:2])
    prefix = f"{scheme}://" if scheme else ""
    return f"{prefix}{authority}"[:64]


def _fail(line: str, reason: str) -> DtkError:
    # The line is echoed back trimmed and without any credential part, so a
    # rejected paste can be corrected without the password ending up in a log.
    return InvalidParam(
        f"unrecognized proxy: {reason}",
        details={"reason": reason, "sample": sample_of(line)},
    )


def _port(raw: str, line: str) -> int:
    try:
        port = int(raw)
    except ValueError:
        raise _fail(line, "port_not_a_number") from None
    if not 1 <= port <= 65535:
        raise _fail(line, "port_out_of_range")
    return port


def parse_proxy(line: str, *, default_scheme: str = DEFAULT_SCHEME) -> ProxySpec:
    """Normalize one line into a dialable URL.

    Raises:
        InvalidParam: the line is not a proxy in any of the accepted shapes.
    """
    text = line.strip()
    if not text:
        raise _fail(text, "empty")

    if "://" not in text:
        # Colon-separated forms. Credentials may lead (user:pass@host:port) or
        # trail (host:port:user:pass); providers use both.
        if "@" not in text and text.count(":") == 3:
            host, port_s, username, password = text.split(":")
            text = f"{default_scheme}://{username}:{password}@{host}:{port_s}"
        else:
            text = f"{default_scheme}://{text}"

    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise _fail(line, "scheme_not_supported")
    try:
        hostname, raw_port = parts.hostname, parts.port
    except ValueError:
        raise _fail(line, "malformed_authority") from None
    if not hostname:
        raise _fail(line, "missing_host")
    if raw_port is None:
        raise _fail(line, "missing_port")
    port = _port(str(raw_port), line)

    credentials = ""
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials = f"{credentials}:{parts.password}"
        credentials += "@"
    return ProxySpec(
        url=f"{scheme}://{credentials}{hostname}:{port}",
        host=hostname,
        port=port,
        scheme=scheme,
        has_credentials=bool(parts.username),
    )


def parse_many(
    text: str, *, default_scheme: str = DEFAULT_SCHEME
) -> list[tuple[str, ProxySpec | DtkError]]:
    """Parse a pasted block, keeping the outcome of every line.

    Returns pairs of ``(line, spec-or-error)`` rather than raising: one bad row
    in a hundred should not reject the other ninety-nine, and the console shows
    exactly which ones failed (doc 07).
    """
    results: list[tuple[str, ProxySpec | DtkError]] = []
    for raw in text.splitlines()[:MAX_LINES]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            results.append((line, parse_proxy(line, default_scheme=default_scheme)))
        except InvalidParam as exc:
            results.append((line, exc))
    return results


__all__ = [
    "ALLOWED_SCHEMES",
    "DEFAULT_SCHEME",
    "MAX_LINES",
    "ProxySpec",
    "parse_many",
    "parse_proxy",
    "sample_of",
]
