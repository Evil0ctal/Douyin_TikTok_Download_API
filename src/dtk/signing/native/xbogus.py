"""X-Bogus, the older Douyin / TikTok Web signature.

Ported from ``crawlers/douyin/web/xbogus.py`` on the ``main`` branch (V4,
commit 8c98fb7, 247 lines). The algorithm is unchanged; only the surrounding API
was restructured and the comments were translated to English.

Douyin has moved on to A-Bogus (see :mod:`dtk.signing.native.abogus`), but
TikTok Web still accepts X-Bogus, and some Douyin endpoints still do, so it stays
the native path for TikTok.

What changed from V4
--------------------
* The clock is injected, so a signature can be reproduced in a test.
* ``getXBogus`` returned a ``(url, signature, user_agent)`` tuple; it is now
  :meth:`XBogus.sign`, returning just the value, plus :meth:`XBogus.sign_query`
  for the ready-to-send query string.
* The hex lookup table is built from the alphabet instead of being spelled out
  as a 103 entry list with 87 ``None`` holes, and an unmapped character now
  raises ``ValueError`` instead of failing later on ``None << 4``.
* Config file reads: there were none here, and none were added.
"""

# The one everybody ports first, because it is the short one.
# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================

from __future__ import annotations

import base64
import hashlib
import time
from typing import Final

#: Default User-Agent, kept identical to V4 so an unconfigured call still
#: reproduces V4's output byte for byte.
DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
)

#: Output alphabet. Identical to A-Bogus alphabet ``s2``; kept separate because
#: the two algorithms are free to diverge.
CHARACTER: Final = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="

#: RC4 key applied to the User-Agent. Three control bytes.
UA_KEY: Final[bytes] = bytes((0x00, 0x01, 0x0C))

#: RC4 key applied to the assembled payload.
PAYLOAD_KEY: Final[bytes] = bytes((0xFF,))

#: Constant the site mixes in beside the timestamp.
CANVAS_CONSTANT: Final = 536919696

#: MD5 of the empty string, hashed a second time by the algorithm.
EMPTY_MD5: Final = "d41d8cd98f00b204e9800998ecf8427e"

#: Every X-Bogus value is this long: 21 payload bytes, three bytes per four
#: output characters.
X_BOGUS_LENGTH: Final = 28

_HEX_VALUES: Final[dict[str, int]] = {char: value for value, char in enumerate("0123456789abcdef")}


def _hex_pairs_to_bytes(text: str) -> list[int]:
    out: list[int] = []
    for index in range(0, len(text) - 1, 2):
        high = _HEX_VALUES.get(text[index])
        low = _HEX_VALUES.get(text[index + 1])
        if high is None or low is None:
            raise ValueError(f"expected lowercase hex, got {text[index : index + 2]!r}")
        out.append((high << 4) | low)
    return out


def rc4_encrypt(key: bytes, data: bytes) -> bytearray:
    """RC4 over bytes."""
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]

    i = 0
    j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) % 256])
    return out


class XBogus:
    """Computes the ``X-Bogus`` query parameter for one browser identity."""

    def __init__(self, user_agent: str | None = None) -> None:
        self.user_agent = user_agent or DEFAULT_USER_AGENT

    # -- hashing helpers ---------------------------------------------------

    @staticmethod
    def md5_str_to_array(value: str | list[int]) -> list[int]:
        """Hex-decode a digest, or fall back to code points for longer text.

        The length test is the site's, not ours: anything longer than a 32
        character MD5 digest is treated as raw text.
        """
        if isinstance(value, list):
            return list(value)
        if len(value) > 32:
            return [ord(char) for char in value]
        return _hex_pairs_to_bytes(value)

    @classmethod
    def md5(cls, data: str | list[int]) -> str:
        """MD5 hex digest of ``data``, decoded first when it is a string."""
        array = cls.md5_str_to_array(data) if isinstance(data, str) else list(data)
        return hashlib.md5(bytes(array)).hexdigest()

    @classmethod
    def md5_encrypt(cls, url_path: str) -> list[int]:
        """Two rounds of MD5 over the query string."""
        return cls.md5_str_to_array(cls.md5(cls.md5_str_to_array(cls.md5(url_path))))

    @classmethod
    def empty_digest(cls) -> list[int]:
        """The second chain, which hashes nothing and is therefore a constant.

        Its bytes 14 and 15 are in every X-Bogus ever produced. A decoder that
        finds something else there is not looking at an X-Bogus.
        """
        return cls.md5_str_to_array(cls.md5(cls.md5_str_to_array(EMPTY_MD5)))

    def user_agent_digest(self) -> list[int]:
        """The third chain: RC4 the User-Agent, base64 it, hash it twice-over.

        Split out of :meth:`sign` so that a decoder can recompute it for a
        candidate User-Agent and check it against the two bytes a captured
        signature carries. That check is the whole of what those two bytes
        support: they are a digest, and two bytes of one do not come back.
        """
        return self.md5_str_to_array(
            self.md5(
                base64.b64encode(rc4_encrypt(UA_KEY, self.user_agent.encode("ISO-8859-1"))).decode(
                    "ISO-8859-1"
                )
            )
        )

    # -- payload assembly --------------------------------------------------

    @staticmethod
    def split_even_odd(values: list[int | float]) -> list[int | float]:
        """Even-indexed items first, then odd-indexed ones."""
        return values[0::2] + values[1::2]

    @staticmethod
    def interleave(values: list[int | float]) -> list[int]:
        """Inverse of :meth:`split_even_odd` for a 19 item list.

        The site splits the payload and immediately reinterleaves it; the only
        thing the round trip really does is truncate the one fractional slot
        (``0.00390625``, which is ``1 / 256``) to an integer. The shape is kept
        because that is what the algorithm does.
        """
        head, tail = values[:10], values[10:]
        out: list[int] = []
        for index in range(len(head)):
            out.append(int(head[index]))
            if index < len(tail):
                out.append(int(tail[index]))
        return out

    def encode_group(self, first: int, second: int, third: int) -> str:
        """Three payload bytes to four output characters."""
        merged = ((first & 255) << 16) | ((second & 255) << 8) | third
        return (
            CHARACTER[(merged & 16515072) >> 18]
            + CHARACTER[(merged & 258048) >> 12]
            + CHARACTER[(merged & 4032) >> 6]
            + CHARACTER[merged & 63]
        )

    # -- public entry point ------------------------------------------------

    def sign(self, query: str, *, timestamp: int | None = None) -> str:
        """Return the ``X-Bogus`` value for a query string.

        Args:
            query: The query string exactly as it will be sent, without the
                leading ``?``.
            timestamp: Unix seconds; the current second when omitted. Two calls
                in the same second produce the same signature, which is what
                makes X-Bogus comparable against a browser's own output.
        """
        ua_digest = self.user_agent_digest()
        empty_digest = self.empty_digest()
        query_digest = self.md5_encrypt(query)

        timer = int(time.time()) if timestamp is None else timestamp
        constant = CANVAS_CONSTANT
        # fmt: off
        payload: list[int | float] = [
            64, 0.00390625, 1, 12,
            query_digest[14], query_digest[15],
            empty_digest[14], empty_digest[15],
            ua_digest[14], ua_digest[15],
            timer >> 24 & 255, timer >> 16 & 255, timer >> 8 & 255, timer & 255,
            constant >> 24 & 255, constant >> 16 & 255, constant >> 8 & 255, constant & 255,
        ]
        # fmt: on

        checksum = int(payload[0])
        for value in payload[1:]:
            checksum ^= int(value)
        payload.append(checksum)

        merged = self.interleave(self.split_even_odd(payload))
        encrypted = rc4_encrypt(PAYLOAD_KEY, bytes(merged)).decode("ISO-8859-1")
        garbled = chr(2) + chr(255) + encrypted

        signature = ""
        for index in range(0, len(garbled) - 2, 3):
            signature += self.encode_group(
                ord(garbled[index]),
                ord(garbled[index + 1]),
                ord(garbled[index + 2]),
            )
        return signature

    def sign_query(self, query: str, *, timestamp: int | None = None) -> str:
        """``query`` with ``&X-Bogus=`` appended, ready to send verbatim."""
        return f"{query}&X-Bogus={self.sign(query, timestamp=timestamp)}"


#: The payload slots that carry something a decoder can name, as
#: ``(first index, meaning)``. Written here because :meth:`XBogus.sign` builds
#: the list positionally and a reader taking a capture apart needs the map.
PAYLOAD_LEAD: Final[tuple[int, ...]] = (64, 0, 1, 12)
QUERY_DIGEST_SLOTS: Final[tuple[int, int]] = (4, 5)
EMPTY_DIGEST_SLOTS: Final[tuple[int, int]] = (6, 7)
UA_DIGEST_SLOTS: Final[tuple[int, int]] = (8, 9)
TIMER_SLOTS: Final[tuple[int, int, int, int]] = (10, 11, 12, 13)
CONSTANT_SLOTS: Final[tuple[int, int, int, int]] = (14, 15, 16, 17)
CHECKSUM_SLOT: Final = 18
#: The two bytes each digest chain contributes, as indices into that digest.
DIGEST_INDICES: Final[tuple[int, int]] = (14, 15)
#: The two plaintext bytes the envelope opens with, before the ciphertext.
ENVELOPE_LEAD: Final[tuple[int, int]] = (2, 255)


__all__ = [
    "CHARACTER",
    "CHECKSUM_SLOT",
    "CONSTANT_SLOTS",
    "DEFAULT_USER_AGENT",
    "DIGEST_INDICES",
    "EMPTY_DIGEST_SLOTS",
    "ENVELOPE_LEAD",
    "PAYLOAD_LEAD",
    "QUERY_DIGEST_SLOTS",
    "TIMER_SLOTS",
    "UA_DIGEST_SLOTS",
    "X_BOGUS_LENGTH",
    "XBogus",
    "rc4_encrypt",
]
