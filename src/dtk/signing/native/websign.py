"""Douyin's ``x-secsdk-web-signature``, in pure Python.

Douyin sign-protects a subset of its API (see :mod:`dtk.signing.protection`).
On those endpoints a request without this parameter is refused with
``403 Blocked by ArgusSecurityPlugin Uifid Not Found`` or ``... Signature Not
Found``, whatever else it carries. This module is what lets the native signer
serve them, so no browser is needed for any Douyin endpoint.

Where it came from
------------------
The signature is produced by a function the SDK registers as ``webSignUrl``,
and reading it in a live page gives::

    function e(){var f=e._v;return(0,e._u)(f[0],arguments,f[1],f[2],this)}

- a trampoline into a bytecode VM, not something to read. But the VM's
*interpreter* ships as ordinary JavaScript in the same file, and the two
programs it runs are hex string literals beside it, so the string table decodes
without executing anything. The salt below is one of those strings.

Source: ``runtime_bundler_34.js`` from ``lf-security.bytegoofy.com``
(``@byted/secsdk-strategy`` v1.0.40; www.douyin.com loads it with
``project-id="34"``). The salt is per project - another ByteDance property
loading a different bundle may use a different one.

The algorithm
-------------
::

    query = URLSearchParams(everything except the signature).toString()
    sig   = md5(f"{uifid}_{timestamp}_{SALT}_{query}").hexdigest()

It is a pure function of those four things - no nonce, no session state. Two
calls in the same second produce byte-identical signatures; it appears to change
every time only because the timestamp is in whole seconds and a call takes
longer than that to set up.

Verified twice over, on 2026-09-08:

* Offline, against signatures a real browser produced: capture the URL Douyin's
  own SDK built, recompute this md5 over it, and the 32 hex characters match
  byte for byte.
* Live: a request carrying nothing but natively computed parameters returned the
  full payload 6 times out of 6 from ``/aweme/v1/web/aweme/detail/``, which is
  one of the endpoints Douyin does sign-protect.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import quote

#: String #39 of the VM's table. Per project; this one is douyin_web.
SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"

#: Where the SDK looks for the visitor id, in this order, first non-empty wins.
#: A Douyin mint yields ``UIFID_TEMP``; the others are the same value under the
#: spellings the SDK also accepts.
UIFID_COOKIE_NAMES: tuple[str, ...] = (
    "uifid",
    "uifid_temp",
    "uifidtemp",
    "UIFID",
    "UIFID_TEMP",
    "UIFIDTEMP",
)

#: The cookie that IS `verifyFp` and `fp`, verbatim.
VERIFY_FP_COOKIE = "s_v_web_id"

SIGNATURE_PARAM = "x-secsdk-web-signature"
UIFID_PARAM = "uifid"
TIMESTAMP_PARAM = "timestamp"
VERIFY_FP_PARAMS: tuple[str, ...] = ("verifyFp", "fp")

#: Sent alongside the query parameters. The platform accepts the request without
#: them, but its own pages send them, and looking like its own pages is the
#: point of the whole exercise.
EXPIRE_HEADER = "x-secsdk-web-expire"


def pick_uifid(cookies: Mapping[str, str] | None) -> str | None:
    """The visitor id the signature is bound to, from the identity's own jar."""
    for name in UIFID_COOKIE_NAMES:
        value = (cookies or {}).get(name)
        if value:
            return value
    return None


def encode_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """Serialize like the JavaScript ``URLSearchParams.toString()`` the SDK uses.

    The encoding has to match exactly, because the same string is both hashed
    and sent: a query that is escaped one way in the preimage and another way in
    the URL is a signature over bytes the platform never sees. ``*-._`` are the
    characters URLSearchParams leaves alone.
    """
    return "&".join(f"{quote(k, safe='*-._')}={quote(v, safe='*-._')}" for k, v in pairs)


def sign(
    pairs: Sequence[tuple[str, str]],
    uifid: str,
    *,
    timestamp: int | None = None,
) -> tuple[str, str, dict[str, str]]:
    """Return ``(query, signature, headers)`` for a request.

    ``pairs`` is everything that goes in the query before signing, in order -
    business parameters, then whatever the a_bogus layer added. ``uifid`` and
    ``timestamp`` are appended here, because the signature covers them.

    A ``uifid`` already present in ``pairs`` keeps its position and is not
    duplicated; the SDK does the same, and appending a second one produces a
    different preimage and so a signature the platform rejects.
    """
    stamp = str(int(time.time() if timestamp is None else timestamp))
    covered = list(pairs)
    if not any(name == UIFID_PARAM for name, _ in covered):
        covered.append((UIFID_PARAM, uifid))
    covered.append((TIMESTAMP_PARAM, stamp))
    query = encode_pairs(covered)
    signature = hashlib.md5(f"{uifid}_{stamp}_{SALT}_{query}".encode()).hexdigest()
    headers = {
        UIFID_PARAM: uifid,
        SIGNATURE_PARAM: signature,
        EXPIRE_HEADER: stamp,
    }
    return f"{query}&{SIGNATURE_PARAM}={signature}", signature, headers


__all__ = [
    "EXPIRE_HEADER",
    "SALT",
    "SIGNATURE_PARAM",
    "UIFID_COOKIE_NAMES",
    "VERIFY_FP_COOKIE",
    "VERIFY_FP_PARAMS",
    "encode_pairs",
    "pick_uifid",
    "sign",
]
