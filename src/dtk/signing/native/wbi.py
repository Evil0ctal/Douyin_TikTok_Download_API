"""Bilibili's WBI signature: ``w_rid`` and ``wts`` over a sorted query.

Unlike the other algorithms here, nothing about it is secret or per visitor.
The two keys are published by ``/x/web-interface/nav`` to every caller - as the
file names of two images - and rotate about daily; the signature is an MD5 of
the sorted query with a key mixed from them. :mod:`dtk.signing.native.wbi_keys`
fetches and caches the keys; this module is the arithmetic, and has no IO.

The encoding is the platform's, not Python's. The page joins the pairs with
``encodeURIComponent`` after deleting ``!'()*`` from every value, which differs
from ``urllib.parse.urlencode`` in two places that matter: a space is ``%20``
rather than ``+``, and ``/`` is escaped. The query is sent exactly as it was
hashed, so a value containing either would otherwise be signed one way and
read by the platform another.

Measured 2026-10-18 from a guest identity: ``/x/web-interface/wbi/view``,
``/x/player/wbi/playurl`` and ``/x/v2/reply/wbi/main`` answer ``code: 0``
signed this way. The unsigned ``/x/web-interface/view`` answered the same
egress with an HTML 412 page.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote, urlsplit

#: Characters the page deletes from every value before signing.
_STRIPPED: Final = frozenset("!'()*")

#: The order the page reads the 64 key characters in, keeping the first 32.
MIXIN_KEY_ORDER: Final[tuple[int, ...]] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)  # fmt: skip

SIGNATURE_PARAM: Final = "w_rid"
TIMESTAMP_PARAM: Final = "wts"


@dataclass(frozen=True, slots=True)
class WbiKeys:
    """The two published keys, as ``nav`` names them."""

    img_key: str
    sub_key: str

    @property
    def mixin(self) -> str:
        raw = self.img_key + self.sub_key
        if len(raw) < 64:
            raise ValueError(f"WBI keys are {len(raw)} characters together, not 64")
        return "".join(raw[i] for i in MIXIN_KEY_ORDER)[:32]


def keys_from_nav(payload: Mapping[str, Any]) -> WbiKeys:
    """The keys in a ``/x/web-interface/nav`` answer.

    The endpoint answers a guest with ``code: -101`` ("not logged in") and the
    keys anyway, so the code is not checked: the keys are what is read.
    """
    data = payload.get("data")
    images = data.get("wbi_img") if isinstance(data, Mapping) else None
    if not isinstance(images, Mapping):
        raise ValueError("nav carries no wbi_img")
    return WbiKeys(_stem(images.get("img_url")), _stem(images.get("sub_url")))


def _stem(url: Any) -> str:
    """``https://i0.hdslb.com/bfs/wbi/7cd0...a2b.png`` -> ``7cd0...a2b``."""
    if not isinstance(url, str) or not url:
        raise ValueError("a WBI key url is missing")
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    stem = name.split(".", 1)[0]
    if len(stem) != 32:
        raise ValueError(f"a WBI key is {len(stem)} characters, not 32")
    return stem


def _component(value: Any) -> str:
    """One value as ``encodeURIComponent`` writes it, after the page's deletions."""
    text = "".join(ch for ch in str(value) if ch not in _STRIPPED)
    return quote(text, safe="")


def encode(params: Mapping[str, Any]) -> str:
    """The sorted, page-encoded query the signature covers and the request sends."""
    return "&".join(
        f"{quote(str(key), safe='')}={_component(value)}" for key, value in sorted(params.items())
    )


def sign(params: Mapping[str, Any], keys: WbiKeys, *, wts: int) -> tuple[str, str]:
    """``(query, w_rid)`` for ``params`` signed at ``wts``.

    ``wts`` joins the parameters before sorting, as the page adds it; the
    signature is appended last and is not itself sorted in.
    """
    signed = {**params, TIMESTAMP_PARAM: str(wts)}
    query = encode(signed)
    w_rid = hashlib.md5((query + keys.mixin).encode()).hexdigest()
    return f"{query}&{SIGNATURE_PARAM}={w_rid}", w_rid


__all__ = [
    "MIXIN_KEY_ORDER",
    "SIGNATURE_PARAM",
    "TIMESTAMP_PARAM",
    "WbiKeys",
    "encode",
    "keys_from_nav",
    "sign",
]
