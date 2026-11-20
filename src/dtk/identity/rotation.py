"""Which cookies a platform's response may change in an identity's jar.

A browser keeps whatever a response sets, and for most cookies so does the
pool. Until 2026-11 nothing was kept at all - the transport had a hook for it
that was never wired - on the strength of a 386-minute-old jar still answering
(docs/design/02-identity-pool.md). Age was the wrong axis. Measured on
2026-11-20, a TikTok msToken has a use budget: a guest jar replaying its minted
token answered about 22 author_profile calls and was then refused on almost
every one (signature.rejected, tt_orcas_res: 1), and still five minutes later.
The API sets a fresh 152-character token on most answers, refusals included;
a jar that kept them answered 40 of 40, and the spent one recovered on its very
next call once it adopted the token its own refusal carried. Older tokens stay
valid after a newer one is issued, so two requests racing on one identity lose
nothing by keeping different ones. That spent token is what issue 830's
rejections were.

What is refused, and why each one:

* **A value shorter than the session's own for a sealed token.** TikTok's home
  page plants a 128-character bootstrap ``msToken`` before the page's SDK swaps
  in the usable one of at least 144 (measured while minting), and a request
  carrying a wrong token is refused where one carrying none is not. dtk reads
  that page as the identity to learn its device id, so taking its cookies at
  face value could replace a working token with the bootstrap one on the
  identity's first use. On 2026-11-20 the page did not send one in Set-Cookie
  to a jar that already held a token - it re-sent ``tt_chain_token``
  unchanged - but the rule does not lean on that staying true.
* **A value that is not RFC 6265 cookie-octets, or is absurdly long.** The jar
  is stored as one ``name=value; ...`` header; a ``;`` or a space in a value
  would split it into cookies nobody set.
* **A new name once the jar is already large.** A platform that names a cookie
  per request would otherwise grow the jar until the Cookie header is refused
  for its size. Existing names still rotate.

Deletions never get this far: the transport leaves them out of
``RawResponse.set_cookies``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from dtk.core.types import Platform

#: The shortest value a response may write over these cookies, per platform.
#: TikTok's figure is the same one browser-rpc's mint waits for
#: (``SESSION_COOKIE_MINIMUMS`` in docker/browser_rpc/backends/cloak.py): every
#: token at or above it signed, every 128-character bootstrap token was refused.
SEALED_MINIMUMS: Final[Mapping[Platform, Mapping[str, int]]] = MappingProxyType(
    {Platform.TIKTOK: MappingProxyType({"msToken": 144})}
)

#: Browsers cap a cookie at about 4 KB; nothing the platforms set comes close.
MAX_VALUE_LENGTH: Final = 4096

#: The serialised jar size past which a response may rotate cookies but not add
#: them. Common front ends refuse request headers well above this; the minted
#: jars measured here are 1-3 KB.
MAX_JAR_BYTES: Final = 8192

_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]+")


@dataclass(frozen=True, slots=True)
class Rotation:
    """What a response changes in a jar, and what it tried to that was refused."""

    jar: dict[str, str]
    changed: tuple[str, ...]
    refused: tuple[str, ...]


def rotate(platform: Platform, jar: Mapping[str, str], offered: Mapping[str, str]) -> Rotation:
    """``jar`` with the cookies ``offered`` by a response applied, per the rules above.

    ``changed`` and ``refused`` are names only, sorted, so they can be logged.
    A value equal to the stored one is neither: the platform re-sent what the
    jar already holds, which is the common case and not worth a write.
    """
    result = dict(jar)
    changed: list[str] = []
    refused: list[str] = []
    minimums = SEALED_MINIMUMS.get(platform, {})
    size = _jar_bytes(result)
    for name, value in offered.items():
        if result.get(name) == value:
            continue
        if not _usable(name, value) or len(value) < minimums.get(name, 0):
            refused.append(name)
            continue
        if name not in result:
            grown = size + len(name) + len(value) + 1 + (2 if result else 0)
            if grown > MAX_JAR_BYTES:
                refused.append(name)
                continue
            size = grown
        else:
            size += len(value) - len(result[name])
        result[name] = value
        changed.append(name)
    return Rotation(jar=result, changed=tuple(sorted(changed)), refused=tuple(sorted(refused)))


def _usable(name: str, value: str) -> bool:
    return (
        _NAME.fullmatch(name) is not None
        and len(value) <= MAX_VALUE_LENGTH
        and _VALUE.fullmatch(value) is not None
    )


def _jar_bytes(jar: Mapping[str, str]) -> int:
    """The length of ``jar`` as the stored ``name=value; ...`` header."""
    if not jar:
        return 0
    return sum(len(name) + len(value) + 1 for name, value in jar.items()) + 2 * (len(jar) - 1)


__all__ = ["MAX_JAR_BYTES", "MAX_VALUE_LENGTH", "SEALED_MINIMUMS", "Rotation", "rotate"]
