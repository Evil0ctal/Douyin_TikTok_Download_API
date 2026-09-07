"""SM3 hash (GB/T 32905-2016), pure Python.

A-Bogus hashes its inputs with SM3. V4 imported this from the ``gmssl`` package
(``crawlers/douyin/web/abogus.py`` on the ``main`` branch, commit 8c98fb7); v5
implements it inline so the signing path carries no third-party dependency and so
the hash itself can be unit tested against the published test vectors.

The output is byte for byte identical to ``gmssl.sm3.sm3_hash``.

V4's ``ABogus`` also carried a second, private SM3 implementation
(``write``/``fill``/``compress``/``sum``) that was never called: its padding
handled only messages shorter than 56 bytes, so it would have produced wrong
digests for real query strings. It is not carried over.
"""

from __future__ import annotations

_MASK = 0xFFFFFFFF

#: SM3 initial vector.
IV: tuple[int, ...] = (
    0x7380166F,
    0x4914B2B9,
    0x172442D7,
    0xDA8A0600,
    0xA96F30BC,
    0x163138AA,
    0xE38DEE4D,
    0xB0FB0E4E,
)

_T0 = 0x79CC4519
_T1 = 0x7A879D8A


def _rotl(value: int, bits: int) -> int:
    bits %= 32
    return ((value << bits) & _MASK) | (value >> (32 - bits))


def _ff(index: int, x: int, y: int, z: int) -> int:
    if index < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _gg(index: int, x: int, y: int, z: int) -> int:
    if index < 16:
        return x ^ y ^ z
    return (x & y) | ((~x & _MASK) & z)


def _expand(block: bytes) -> tuple[list[int], list[int]]:
    w = [int.from_bytes(block[i * 4 : i * 4 + 4], "big") for i in range(16)]
    for i in range(16, 68):
        x = w[i - 16] ^ w[i - 9] ^ _rotl(w[i - 3], 15)
        x = x ^ _rotl(x, 15) ^ _rotl(x, 23)
        w.append((x ^ _rotl(w[i - 13], 7) ^ w[i - 6]) & _MASK)
    w1 = [(w[i] ^ w[i + 4]) & _MASK for i in range(64)]
    return w, w1


def _compress(state: tuple[int, ...], block: bytes) -> tuple[int, ...]:
    w, w1 = _expand(block)
    a, b, c, d, e, f, g, h = state
    for j in range(64):
        t = _T0 if j < 16 else _T1
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j)) & _MASK, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        tt1 = (_ff(j, a, b, c) + d + ss2 + w1[j]) & _MASK
        tt2 = (_gg(j, e, f, g) + h + ss1 + w[j]) & _MASK
        d = c
        c = _rotl(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl(f, 19)
        f = e
        e = (tt2 ^ _rotl(tt2, 9) ^ _rotl(tt2, 17)) & _MASK
    return tuple((x ^ y) & _MASK for x, y in zip(state, (a, b, c, d, e, f, g, h), strict=True))


def _pad(message: bytes) -> bytes:
    bit_length = len(message) * 8
    padded = message + b"\x80"
    padded += b"\x00" * ((56 - len(padded) % 64) % 64)
    return padded + bit_length.to_bytes(8, "big")


def sm3_hash(message: bytes) -> bytes:
    """Return the 32 byte SM3 digest of ``message``."""
    state = IV
    padded = _pad(message)
    for offset in range(0, len(padded), 64):
        state = _compress(state, padded[offset : offset + 64])
    return b"".join(word.to_bytes(4, "big") for word in state)


def sm3_hexdigest(message: bytes) -> str:
    """Hex form of :func:`sm3_hash`, matching ``gmssl.sm3.sm3_hash``."""
    return sm3_hash(message).hex()


def sm3_to_array(data: str | bytes | list[int]) -> list[int]:
    """SM3 digest as a list of 32 integers.

    Mirrors V4's ``ABogus.sm3_to_array``: strings are hashed as UTF-8, integer
    lists are hashed as raw bytes.
    """
    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, bytes):
        raw = data
    else:
        raw = bytes(data)
    return list(sm3_hash(raw))


__all__ = ["IV", "sm3_hash", "sm3_hexdigest", "sm3_to_array"]
