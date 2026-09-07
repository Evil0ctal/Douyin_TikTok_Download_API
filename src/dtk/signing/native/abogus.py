"""A-Bogus, the signature Douyin Web requires on every data endpoint.

Ported from ``crawlers/douyin/web/abogus.py`` on the ``main`` branch (V4,
commit 8c98fb7, 635 lines). The algorithm is unchanged; only the surrounding API
was restructured and the comments were translated to English.

Original author and license
---------------------------
The algorithm comes from https://github.com/JoeanAmier/TikTokDownloader and is
licensed under the GNU General Public License v3.0. It reached V4 of this project
through https://github.com/Evil0ctal/Douyin_TikTok_Download_API with the original
author information kept, and the same notice is kept here.

What changed from V4
--------------------
* ``gmssl`` is gone: :mod:`dtk.signing.native.sm3` provides the same digest.
* ``user_agent`` drives the signature again. V4 hard-coded ``ua_code`` for one
  Chrome 90 User-Agent and commented the derivation out, which meant every
  identity in the pool signed as the same browser no matter what its fingerprint
  claimed - exactly the cross-layer inconsistency docs/design/04 warns about.
  :func:`generate_ua_code` reproduces V4's hard-coded array byte for byte for
  that User-Agent (asserted in tests/unit/test_signing.py).
* Randomness and the clock are injected, so a signature can be reproduced.
* Config file reads: there were none here, and none were added.
* Dead code dropped (never reachable from ``get_value`` in V4 either): the
  private SM3 (``write``/``fill``/``compress``/``generate_f``/``sum``/``de``/
  ``pe``/``he``/``ve``/``reg_to_array``), ``generate_args_code``,
  ``generate_result_unit``, ``generate_result_end``, ``decode_string``.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final
from urllib.parse import urlencode

from dtk.signing.native.sm3 import sm3_to_array

#: The User-Agent V4 pinned its ``ua_code`` to. Kept as the default so a caller
#: that supplies no fingerprint still reproduces V4's exact output.
DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
)

#: The array V4 carried inline for :data:`DEFAULT_USER_AGENT`. Kept so the
#: equivalence of the restored derivation stays testable, not because the code
#: needs it.
LEGACY_UA_CODE: Final[tuple[int, ...]] = (
    76, 98, 15, 131, 97, 245, 224, 133, 122, 199, 241, 166, 79, 34, 90, 191,
    128, 126, 122, 98, 66, 11, 14, 40, 49, 110, 110, 173, 67, 96, 138, 252,
)  # fmt: skip

#: ``navigator`` geometry string, laid out as
#: ``innerW|innerH|outerW|outerH|screenX|screenY|0|0|outerW|outerH|outerW|outerH|innerW|innerH|colorDepth|pixelDepth|platform``
DEFAULT_BROWSER_INFO: Final = "1536|742|1536|864|0|0|0|0|1536|864|1536|864|1536|742|24|24|MacIntel"

#: Base64 alphabets. ``s3`` encodes the User-Agent, ``s4`` encodes the result.
ALPHABETS: Final[Mapping[str, str]] = {
    "s0": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
    "s1": "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
    "s2": "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
    "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
    "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
}

#: RC4 key the site uses over the User-Agent before hashing it.
UA_KEY: Final = "\u0000\u0001\u000e"
#: RC4 key for the request payload.
PAYLOAD_KEY: Final = "y"
#: Salt appended before hashing the method and the query string.
END_STRING: Final = "cus"

#: Length of the structured prefix of the decoded payload, in bytes. The first
#: 12 bytes come from :meth:`ABogus.list_1` / ``list_2`` / ``list_3`` and carry
#: fixed bit patterns; the shadow comparator in dtk.signing.registry checks them.
STRUCTURED_PREFIX_LEN: Final = 12

#: Length of the frame :meth:`ABogus.list_4` builds, in bytes.
FRAME_LEN: Final = 44

#: Index within that frame of the slot carrying ``len(browser_info)``.
FRAME_BROWSER_LEN_INDEX: Final = 40

#: Fixed slots of the frame: index to value. Only the distinctive non-zero
#: constants are listed - the zero slots carry no information and are the ones
#: most likely to turn out to be fields the site fills for browsers V4 never saw.
FRAME_CONSTANTS: Final[Mapping[int, int]] = MappingProxyType(
    {0: 44, 6: 24, 17: 239, 29: 14, 35: 3, 37: 1, 39: 1}
)

#: ``(and_mask, or_mask)`` per structured prefix byte: ``byte == (noise & and) | or``.
#: Derived from the ``list_1`` / ``list_2`` / ``list_3`` arguments below.
PREFIX_MASKS: Final[tuple[tuple[int, int], ...]] = (
    (170, 1), (85, 2), (170, 5), (85, 45 & 170),
    (170, 1), (85, 0), (170, 0), (85, 0),
    (170, 1), (85, 0), (170, 5), (85, 0),
)  # fmt: skip


def rc4_encrypt(plaintext: str, key: str) -> str:
    """RC4 over code points, exactly as the site's JavaScript does it.

    Both arguments and the result are strings whose code points are all < 256;
    this is the JavaScript ``String.fromCharCode`` convention, not UTF-8.
    """
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + box[i] + ord(key[i % len(key)])) % 256
        box[i], box[j] = box[j], box[i]

    i = 0
    j = 0
    cipher: list[str] = []
    for position in range(len(plaintext)):
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        t = (box[i] + box[j]) % 256
        cipher.append(chr(box[t] ^ ord(plaintext[position])))
    return "".join(cipher)


def encode_base64(data: str, alphabet: str = "s4") -> str:
    """Base64 with one of the site's shuffled alphabets."""
    table = ALPHABETS[alphabet]
    out: list[str] = []
    for i in range(0, len(data), 3):
        if i + 2 < len(data):
            n = (ord(data[i]) << 16) | (ord(data[i + 1]) << 8) | ord(data[i + 2])
        elif i + 1 < len(data):
            n = (ord(data[i]) << 16) | (ord(data[i + 1]) << 8)
        else:
            n = ord(data[i]) << 16
        for shift, mask in zip((18, 12, 6, 0), (0xFC0000, 0x03F000, 0x0FC0, 0x3F), strict=True):
            if shift == 6 and i + 1 >= len(data):
                break
            if shift == 0 and i + 2 >= len(data):
                break
            out.append(table[(n & mask) >> shift])
    out.append("=" * ((4 - len(out) % 4) % 4))
    return "".join(out)


def decode_base64(data: str, alphabet: str = "s4") -> bytes:
    """Inverse of :func:`encode_base64`; used by the shadow comparator."""
    table = ALPHABETS[alphabet]
    index = {char: position for position, char in enumerate(table)}
    bits = 0
    width = 0
    out = bytearray()
    for char in data:
        if char == "=":
            continue
        position = index.get(char)
        if position is None:
            raise ValueError(f"character outside alphabet {alphabet}: {char!r}")
        bits = (bits << 6) | position
        width += 6
        if width >= 8:
            width -= 8
            out.append((bits >> width) & 0xFF)
    return bytes(out)


def generate_ua_code(user_agent: str, *, alphabet: str = "s3") -> list[int]:
    """Hash the User-Agent the way the site does: RC4, base64, SM3."""
    return sm3_to_array(encode_base64(rc4_encrypt(user_agent, UA_KEY), alphabet))


def build_browser_info(
    *,
    inner_width: int,
    inner_height: int,
    outer_width: int,
    outer_height: int,
    platform: str,
    screen_y: int = 0,
    color_depth: int = 24,
) -> str:
    """Assemble the ``navigator`` geometry string in the site's field order."""
    values: list[int | str] = [
        inner_width,
        inner_height,
        outer_width,
        outer_height,
        0,
        screen_y,
        0,
        0,
        outer_width,
        outer_height,
        outer_width,
        outer_height,
        inner_width,
        inner_height,
        color_depth,
        color_depth,
        platform,
    ]
    return "|".join(str(value) for value in values)


def structure_error(value: str, *, alphabet: str = "s4") -> str | None:
    """The first way ``value`` fails to be a well-formed ``a_bogus``, or None.

    Every invariant checked here is fixed by the algorithm itself, so two correct
    implementations always agree on it. In particular none of them depends on the
    window geometry, which differs between this process and the browser behind
    browser-rpc and therefore makes the *length* of two correct signatures
    differ. That is what makes this usable as the shadow comparison for A-Bogus,
    whose millisecond timings and random words rule out byte equality.

    Checked, in order: the base64 alphabet and padding; the twelve noise bytes
    against :data:`PREFIX_MASKS`; the frame constants of :meth:`ABogus.list_4`;
    the frame slot that repeats ``len(browser_info)``; and the frame checksum.
    The last two are the strong ones - they are internally redundant, so an
    algorithm that changed anything about the frame layout fails them.
    """
    body = value.rstrip("=")
    if not body or "=" in body or not set(body) <= set(ALPHABETS[alphabet]):
        return "alphabet"
    if len(value) % 4:
        return "length not a multiple of four"
    payload = decode_base64(value, alphabet)
    if len(payload) < STRUCTURED_PREFIX_LEN + FRAME_LEN + 1:
        return "payload too short"

    for index, (and_mask, or_mask) in enumerate(PREFIX_MASKS):
        byte = payload[index]
        if byte & ~(and_mask | or_mask) & 0xFF or byte & or_mask != or_mask:
            return f"noise byte {index}"

    # Everything past the noise prefix is RC4 with a one byte key, and RC4 is
    # its own inverse, so the same call decrypts it.
    frame = [
        ord(char)
        for char in rc4_encrypt(payload[STRUCTURED_PREFIX_LEN:].decode("latin-1"), PAYLOAD_KEY)
    ]
    for index, expected in FRAME_CONSTANTS.items():
        if frame[index] != expected:
            return f"frame byte {index}"
    if frame[FRAME_BROWSER_LEN_INDEX] != len(frame) - FRAME_LEN - 1:
        return "browser length slot"
    checksum = 0
    for byte in frame[:FRAME_LEN]:
        checksum ^= byte
    if frame[-1] != checksum:
        return "frame checksum"
    return None


class ABogus:
    """Computes the ``a_bogus`` query parameter for one browser identity.

    An instance is cheap and holds no request state, so one per identity is the
    natural lifetime.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        browser_info: str | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self._rng = rng or random.Random()
        self.ua_code = generate_ua_code(self.user_agent)
        self.browser = browser_info or DEFAULT_BROWSER_INFO
        self.browser_len = len(self.browser)
        self.browser_code = [ord(char) for char in self.browser]

    # -- randomised prefix ------------------------------------------------

    def random_list(
        self,
        seed: float | None = None,
        a: int = 170,
        b: int = 85,
        d: int = 0,
        e: int = 0,
        f: int = 0,
        g: int = 0,
    ) -> list[int]:
        """Four masked bytes derived from one random 16 bit value.

        ``seed`` of 0 draws a fresh value, exactly as V4's ``a or random()`` did.
        """
        noise = int(seed or (self._rng.random() * 10000))
        low = noise & 255
        high = noise >> 8
        return [low & a | d, low & b | e, high & a | f, high & b | g]

    def list_1(
        self, seed: float | None = None, a: int = 170, b: int = 85, c: int = 45
    ) -> list[int]:
        return self.random_list(seed, a, b, 1, 2, 5, c & a)

    def list_2(self, seed: float | None = None, a: int = 170, b: int = 85) -> list[int]:
        return self.random_list(seed, a, b, 1, 0, 0, 0)

    def list_3(self, seed: float | None = None, a: int = 170, b: int = 85) -> list[int]:
        return self.random_list(seed, a, b, 1, 0, 5, 0)

    def generate_string_1(
        self,
        seed_1: float | None = None,
        seed_2: float | None = None,
        seed_3: float | None = None,
    ) -> str:
        codes = self.list_1(seed_1) + self.list_2(seed_2) + self.list_3(seed_3)
        return "".join(chr(code) for code in codes)

    # -- request payload --------------------------------------------------

    def generate_method_code(self, method: str = "GET") -> list[int]:
        return sm3_to_array(sm3_to_array(method + END_STRING))

    def generate_params_code(self, params: str) -> list[int]:
        return sm3_to_array(sm3_to_array(params + END_STRING))

    def generate_string_2_list(
        self,
        url_params: str,
        method: str = "GET",
        start_time: int = 0,
        end_time: int = 0,
    ) -> list[int]:
        params_array = self.generate_params_code(url_params)
        method_array = self.generate_method_code(method)
        return self.list_4(
            (end_time >> 24) & 255,
            params_array[21],
            self.ua_code[23],
            (end_time >> 16) & 255,
            params_array[22],
            self.ua_code[24],
            (end_time >> 8) & 255,
            (end_time >> 0) & 255,
            (start_time >> 24) & 255,
            (start_time >> 16) & 255,
            (start_time >> 8) & 255,
            (start_time >> 0) & 255,
            method_array[21],
            method_array[22],
            int(end_time / 256 / 256 / 256 / 256) >> 0,
            int(start_time / 256 / 256 / 256 / 256) >> 0,
            self.browser_len,
        )

    @staticmethod
    def list_4(
        a: int,
        b: int,
        c: int,
        d: int,
        e: int,
        f: int,
        g: int,
        h: int,
        i: int,
        j: int,
        k: int,
        m: int,
        n: int,
        o: int,
        p: int,
        q: int,
        r: int,
    ) -> list[int]:
        """The fixed 44 byte frame the site fills with timings and hashes."""
        # fmt: off
        return [
            44, a, 0, 0, 0, 0, 24, b, n, 0, c, d, 0, 0, 0, 1, 0, 239, e, o, f,
            g, 0, 0, 0, 0, h, 0, 0, 14, i, j, 0, k, m, 3, p, 1, q, 1, r, 0, 0, 0,
        ]
        # fmt: on

    @staticmethod
    def end_check_num(values: list[int]) -> int:
        checksum = 0
        for value in values:
            checksum ^= value
        return checksum

    def generate_string_2(
        self,
        url_params: str,
        method: str = "GET",
        start_time: int = 0,
        end_time: int = 0,
    ) -> str:
        frame = self.generate_string_2_list(url_params, method, start_time, end_time)
        checksum = self.end_check_num(frame)
        frame.extend(self.browser_code)
        frame.append(checksum)
        return rc4_encrypt("".join(chr(code) for code in frame), PAYLOAD_KEY)

    # -- public entry point -----------------------------------------------

    def get_value(
        self,
        url_params: Mapping[str, str] | str,
        method: str = "GET",
        start_time: int = 0,
        end_time: int = 0,
        random_num_1: float | None = None,
        random_num_2: float | None = None,
        random_num_3: float | None = None,
    ) -> str:
        """Return the ``a_bogus`` value for a query string.

        Args:
            url_params: The query string exactly as it will be sent, or a
                mapping that is form-encoded in iteration order.
            method: HTTP method of the request being signed.
            start_time: Client timestamp in milliseconds; 0 means "now".
            end_time: Client timestamp in milliseconds; 0 means "shortly after
                ``start_time``", the way a real browser measures it.
            random_num_1: Pin the first noise word instead of drawing one.
            random_num_2: Pin the second noise word.
            random_num_3: Pin the third noise word.

        The value still has to be percent-encoded before it goes into a URL: it
        can contain ``+`` and ``/``.
        """
        start = start_time or int(time.time() * 1000)
        end = end_time or (start + self._rng.randint(4, 8))
        query = urlencode(url_params) if isinstance(url_params, Mapping) else url_params
        string_1 = self.generate_string_1(random_num_1, random_num_2, random_num_3)
        string_2 = self.generate_string_2(query, method, start, end)
        return encode_base64(string_1 + string_2, "s4")


__all__ = [
    "ALPHABETS",
    "DEFAULT_BROWSER_INFO",
    "DEFAULT_USER_AGENT",
    "FRAME_BROWSER_LEN_INDEX",
    "FRAME_CONSTANTS",
    "FRAME_LEN",
    "LEGACY_UA_CODE",
    "PAYLOAD_KEY",
    "PREFIX_MASKS",
    "STRUCTURED_PREFIX_LEN",
    "ABogus",
    "build_browser_info",
    "decode_base64",
    "encode_base64",
    "generate_ua_code",
    "rc4_encrypt",
    "structure_error",
]
