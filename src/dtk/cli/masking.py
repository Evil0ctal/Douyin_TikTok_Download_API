"""Masking helpers for terminal output.

The CLI is the one place credentials are routinely within reach: it decrypts
proxy URLs to probe them, loads cookie jars to test an identity, and prints
whatever the database holds. Every one of those values passes through here
before it reaches a terminal, a log file, or a diagnostic report that a user
will paste into an issue.

Pure functions, no IO: the unit tests cover the masking rules directly rather
than by scraping rendered tables.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

#: What replaces a value that must never be shown at all.
MASK = "***"

#: Placeholder for an absent value. Absent is None everywhere in the codebase;
#: this is only how None is rendered.
ABSENT = "-"

#: Credentials embedded in a URL authority, for free text where the URL cannot
#: be parsed structurally (exception messages, driver errors, tracebacks).
_URL_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^/\s:@]+):[^/\s@]+@", re.IGNORECASE
)

#: Signature and session parameters that leak inside URLs and free text. Mirrors
#: the redaction list in dtk.core.logging so a value masked in the log is masked
#: on the terminal too.
_PARAM_RE = re.compile(
    r"\b(msToken|a_bogus|X-Bogus|_signature|sessionid|sid_guard|odin_tt|uid_tt|ttwid|passport_csrf_token)"
    r"=([^&\s;\"']{6,})",
    re.IGNORECASE,
)


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Show at most ``keep`` leading characters of a secret.

    A short value is masked whole: revealing three of four characters is not a
    mask. ``None`` renders as the absent marker rather than as an empty string.
    """
    if value is None:
        return ABSENT
    text = value.strip()
    if not text:
        return ABSENT
    if keep <= 0 or len(text) <= keep:
        return MASK
    return f"{text[:keep]}{MASK}"


def mask_url(url: str | None) -> str:
    """Render a proxy URL with its credentials removed.

    Host and port survive because they are what an operator needs to recognize
    the egress; the password never does, and the username keeps two characters
    at most so two accounts on the same host stay distinguishable.
    """
    if url is None:
        return ABSENT
    text = url.strip()
    if not text:
        return ABSENT
    try:
        parts = urlsplit(text)
    except ValueError:
        return MASK
    if not parts.scheme or not parts.netloc:
        # Not a URL we can take apart safely; a bare "user:pass@host" would be
        # parsed as scheme "user", so refuse rather than half-mask it.
        return _URL_CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:" + MASK + "@", text)
    if parts.username is None:
        return text
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    user = mask_secret(parts.username, keep=2)
    authority = f"{user}:{MASK}@{host}" if parts.password is not None else f"{user}@{host}"
    return urlunsplit((parts.scheme, authority, parts.path, parts.query, parts.fragment))


def mask_endpoint(url: str | None) -> str:
    """Render a webhook or bot URL as scheme, host and nothing else.

    A notification endpoint is itself a credential: the token sits in the path
    (``/bot<token>/sendMessage``) or the query, and anyone holding the URL can
    post to the channel. Only the host is kept, which is all an operator needs
    to recognize which channel a row describes.
    """
    if url is None:
        return ABSENT
    text = url.strip()
    if not text:
        return ABSENT
    try:
        parts = urlsplit(text)
    except ValueError:
        return MASK
    if not parts.scheme or not parts.hostname:
        return MASK
    host = parts.hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    tail = "/" + MASK if (parts.path.strip("/") or parts.query or parts.fragment) else ""
    return f"{parts.scheme}://{host}{tail}"


def mask_cookies(cookies: Mapping[str, str] | None) -> str:
    """Describe a cookie jar by its names only.

    The names answer the operational question - is ``sessionid`` present, did
    minting produce a ``ttwid`` - and the values are exactly the thing that
    must never be printed.
    """
    if not cookies:
        return ABSENT
    return ", ".join(sorted(cookies))


def mask_api_key(prefix: str | None) -> str:
    """Render an API key by its stored prefix; the key itself is never held."""
    if not prefix:
        return ABSENT
    return f"dtk_{prefix}_{MASK}"


def short_id(value: uuid.UUID | str | None, *, length: int = 8) -> str:
    """First ``length`` characters of an id, for tables that must stay narrow.

    Every command that accepts an id also accepts this short form, so what is
    printed can be pasted straight back in.
    """
    if value is None:
        return ABSENT
    text = str(value)
    return text[:length] if len(text) > length else text


def scrub(text: str) -> str:
    """Remove credentials from arbitrary text before it is printed.

    Applied to every error message the CLI shows. A failed database connection
    reports its DSN, and that DSN carries the password.
    """
    without_urls = _URL_CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:" + MASK + "@", text)
    return _PARAM_RE.sub(lambda m: f"{m.group(1)}={m.group(2)[:6]}...", without_urls)


__all__ = [
    "ABSENT",
    "MASK",
    "mask_api_key",
    "mask_cookies",
    "mask_endpoint",
    "mask_secret",
    "mask_url",
    "scrub",
    "short_id",
]
