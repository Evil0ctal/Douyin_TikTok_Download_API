"""TikTok Web's ``X-Dynosaur`` and ``X-Gnarly``, in pure Python.

TikTok's page loads a bytecode-VM SDK (``webmssdk``, imported as ``acrawler``)
that patches ``window.fetch`` and rewrites the URL of every API call, appending
four query parameters in a fixed order::

    <business query>&X-Dynosaur=<env report>&msToken=<session>&X-Bogus=1&X-Gnarly=<request seal>

This module produces all four without a browser, so the native signer serves
TikTok the way :mod:`dtk.signing.native.websign` serves Douyin.

Where it came from
------------------
Source: ``webmssdk 2.0.0.561`` from ``lf16-tiktok-web.tiktokcdn-us.com``.
Same shape as Douyin's protection - a bytecode VM whose *interpreter* is
ordinary JavaScript, so the string table decodes without executing anything -
but unlike Douyin there is no salt to recover: the signature is an encrypted
blob with a per-call key. Everything below was read out of that bundle's own
output. Both custom base64 alphabets and every algorithm constant used here
(:data:`CHACHA_INIT`, :data:`FNV_OFFSET`, :data:`FNV_PRIME`) appear verbatim in
the shipped bundle.

Two public implementations exist and NEITHER is used here. ``xvhuan/tiktok-web-params``
is MIT with an appended "learning and exchange purposes only" restriction, which
contradicts the grant it is attached to (GitHub classifies the repository as
NOASSERTION, not MIT). ``JoeanAmier/TikTokDownloader`` - which vendors the only
pure-Python version of this algorithm in public - is GPL-3.0, and copying from it
would relicense this project. This port is derived from the platform's own
artifact instead, which carries no third-party copyright at all. The xvhuan
implementation was consulted only as a cross-check, and it disagrees with the
genuine SDK on several points measured here (see :data:`CALL_SEQUENCE_START`).

The algorithm
-------------
Both parameters share one envelope::

    payload    = TLV entries, each [key][0x00][length][value...]
    key12      = 12 random uint32 drawn per call
    rounds     = 5 + (sum(word & 0xF for word in key12) & 0xF)
    ciphertext = ChaCha-like keystream XOR over the payload as LE uint32 words
    spliced    = ciphertext with the 48-byte key inserted at
                 (sum(key bytes) + sum(ciphertext bytes)) % (len(ciphertext) + 1)
    signature  = custom_base64(b"\\x4b" + spliced)

The signature carries its own key, which is what lets the server decrypt it -
and what lets :func:`dtk.signing.native.tiktok_sign` be verified at all, since
the per-call key makes byte equality between two independent signatures
impossible.

Verified on 2026-09-08, offline and byte for byte
-------------------------------------------------
The check that matters is not "a request succeeded" (docs/design/17 §6). It is
this: run the genuine SDK headlessly in Node - an oracle independent of this
code - take the ``X-Dynosaur`` it produced, decrypt it, read the timestamp and
nonce it happened to draw, feed those same values back into this module, and
compare.

* **X-Dynosaur**: the 249-byte plaintext payload is byte-identical, and
  re-encrypting it under the oracle's own recovered key reproduces the oracle's
  400-character signature string character for character.
* **X-Gnarly**: the 16-field payload is identical field for field (the SDK emits
  them in an order that varies between processes, so the server must parse by
  key; this module emits ascending). Re-encrypting the oracle's own payload
  reproduces its 324-character signature exactly, which proves the envelope.

20 vectors across 4 endpoints, 3 User-Agents, a percent-encoded CJK query, a
30-parameter query and call counters 1..3. See
``tests/unit/test_signing.py::TestTikTokSignature``.
"""

from __future__ import annotations

import base64
import hashlib
import random
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

MASK32: Final = 0xFFFFFFFF

#: Query parameters, in the order the SDK appends them. The order is the
#: platform's and is not ours to improve: ``X-Gnarly`` seals the query string
#: including ``X-Dynosaur`` and ``msToken``, so anything reordered after signing
#: is a seal over bytes the platform never receives.
DYNOSAUR_PARAM: Final = "X-Dynosaur"
MS_TOKEN_PARAM: Final = "msToken"
BOGUS_PARAM: Final = "X-Bogus"
GNARLY_PARAM: Final = "X-Gnarly"

#: ``X-Bogus`` is the literal one-character string ``1`` on HTTP requests. The
#: 16-character X-Bogus this project ships in ``xbogus.py`` is real, but it only
#: ever appears on websocket handshakes via the SDK's ``frontierSign()``. Sending
#: a computed one here would be a parameter set TikTok's own page never sends.
BOGUS_VALUE: Final = "1"

#: Custom base64 alphabet, positional against the standard one. Present verbatim
#: in the SDK's decoded string table (stored there with a trailing ``=`` pad
#: character, which passes through unchanged). Both parameters use this one; the
#: other alphabet in the table belongs to X-Bogus and is not needed.
ALPHABET: Final = "u09tbS3UvgDEe6r-ZVMXzLpsAohTn7mdINQlW412GqBjfYiyk8JORCF5/xKHwacP"
_STANDARD: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_TO_CUSTOM: Final = str.maketrans(_STANDARD, ALPHABET)

#: First byte of the envelope, before the spliced ciphertext.
ENVELOPE_TAG: Final = 0x4B

#: ChaCha state words 0..3. Not the textbook ``expand 32-byte k`` constants;
#: these four grep out of the live bundle.
CHACHA_INIT: Final = (1196819126, 600974999, 3863347763, 1451689750)

#: FNV-1a 32-bit, with a non-standard offset basis and an extra ``* 33`` per
#: byte. Both constants grep out of the live bundle.
FNV_OFFSET: Final = 2166136260
FNV_PRIME: Final = 16777619

#: Version strings the payload carries, from bundle 2.0.0.561. They are DATA,
#: not decoration: 2.0.0.514 carried "5.3.1"/"2.0.0.514" and a different field
#: set, so a bundle bump invalidates this port. The redo recipe is in
#: docs/design/17-signature-reversing.md - refetch the SDK, diff the string
#: table for the scmVersion literal, re-run the Node oracle, diff the decoded
#: field table.
SDK_VERSION: Final = "5.3.2"
SCM_VERSION: Final = "2.0.0.561"

#: Environment fingerprint the payload reports, as measured under the Node
#: harness that runs the genuine SDK with a minimal DOM.
#:
#: ``ENV_CODE`` (129 = 0x81) did not move when the stub was given a Chrome UA, a
#: different screen, a real ``window.chrome``, plugins, ``Notification``,
#: ``RTCPeerConnection`` or ``mediaDevices``, so what sets bit 7 was not found.
#: ``UB_CODE`` (14 = 0b1110) is user-behaviour: dispatching mousemove, click and
#: keydown at the SDK's own listeners drove it to 0. 14 is what any page reports
#: before the visitor touches it, which is when the feed request goes out.
ENV_CODE: Final = 129
UB_CODE: Final = 14

#: X-Dynosaur field 0x38. Constant across bundles 2.0.0.514 and 2.0.0.561 and
#: across every environment perturbation tried (UA, screen, location, canvas
#: present/absent), so it is a property of the SDK build rather than of the
#: machine. It is :func:`hash_state` of an md5 the SDK computes internally; the
#: md5's input never reaches ``TextEncoder`` or ``charCodeAt``, so it was not
#: recovered. Pinning the observed value is therefore what the port can honestly
#: do - and the value being environment-independent is exactly what makes that
#: safe.
VM_STATE_HASH: Final = 0xC46CE353

#: Canvas fingerprint. ``-1`` means "no canvas", which is what a page without a
#: 2d context reports. Giving the harness a working ``getContext('2d')`` made the
#: SDK emit a real hash here instead, so this field is the clearest bot tell in
#: the payload; it is a named constant so it can be replaced with a stable
#: per-identity value if TikTok ever starts reading it.
CANVAS_HASH: Final = "-1"

#: Three more environment fields the Node harness could not produce, so the port
#: pinned "0". Being placeholders is measurable: a real browser reports a 32-bit
#: hash at 0x28, a component version string at 0x32 and an md5 at 0x33.
WEBGL_HASH: Final = "0"
COMPONENT_VERSION: Final = "0"
DEVICE_HASH: Final = "0"

#: ``location.host + location.pathname`` of the page the SDK believes it is
#: running in. Confirmed by pointing the harness at a video URL, which made this
#: field follow. The home page is the honest answer for a client that has no page.
PAGE: Final = "www.tiktok.com/"

#: The SDK counts its own signing calls from 1 and puts the counter in four
#: fields. A fresh page signing its first request reports 1, which is what a
#: request-per-identity client is. (xvhuan hardcodes 4 in one of these and omits
#: another field entirely; measurement disagrees with it, so the oracle wins.)
CALL_SEQUENCE_START: Final = 1

#: md5 of the empty body, which every GET has. A literal in the SDK's own string
#: table, so the SDK special-cases it too.
EMPTY_BODY_MD5: Final = "d41d8cd98f00b204e9800998ecf8427e"

#: The SDK's two byte encoders, as ``(xor_base, add_base, pre_xor, rot, post_add)``.
#: A encodes every field but the checksum; B encodes the checksum and three flags.
_ENCODER_A: Final = (103, 1, None, 2, 1)
_ENCODER_B: Final = (102, 0, 165, 1, 0)


#: The only characters a browser escapes inside a query string. Chrome leaves
#: everything else - parentheses, slashes, colons, commas - literal, and the
#: `#` is here because it would otherwise start the fragment.
_MUST_ESCAPE: Final = {" ": "%20", '"': "%22", "<": "%3C", ">": "%3E", "`": "%60", "#": "%23"}


def encode_query(pairs: Iterable[tuple[str, str]]) -> str:
    """Serialize the business parameters exactly as a browser would send them.

    This is NOT ``urlencode``, and the difference is the whole reason
    ``/api/user/detail/`` used to fail while its neighbours worked.

    X-Dynosaur field 0x2E is ``hash_state`` of the query, and the SDK hashes the
    string the browser handed it - which is the URL after the browser's own
    normalisation, not after a library's. Chrome escapes a space to ``%20`` and
    leaves parentheses, slashes and colons alone, so the real page hashes
    ``browser_version=5.0%20(Windows)&root_referer=https://www.tiktok.com/``.
    Percent-encoding everything, as this function first did, produced
    ``5.0%20%28Windows%29`` and ``https%3A%2F%2F...`` - a different string, a
    different hash, and a signature bound to bytes the platform never sees.

    Recovered by capturing a browser-signed request, decoding its X-Dynosaur and
    searching for the input that reproduces 0x2E: only-escape-the-space matches
    byte for byte, and nothing else tried does. TikTok verifies that binding on
    ``/api/user/detail/`` and ignores it on ``/api/item/detail/`` and
    ``/api/comment/list/``, which is why the symptom was one endpoint silently
    returning nothing.

    The same string is hashed and sent, so this must stay the only encoder in
    the path.
    """
    return "&".join(f"{_escape(key)}={_escape(value)}" for key, value in pairs)


def _escape(text: str) -> str:
    """Percent-escape only what a browser has to, leaving the rest literal.

    Printable ASCII passes through untouched apart from the handful in
    :data:`_MUST_ESCAPE`. Everything else - control characters and anything
    non-ASCII - is percent-encoded from its UTF-8 bytes, which is what a browser
    puts on the wire for a query it was handed as text.
    """
    out: list[str] = []
    for ch in text:
        if ch in _MUST_ESCAPE:
            out.append(_MUST_ESCAPE[ch])
        elif " " < ch <= "~":
            out.append(ch)
        else:
            out.extend(f"%{byte:02X}" for byte in ch.encode("utf-8"))
    return "".join(out)


def hash_state(text: str) -> int:
    """The SDK's FNV-1a variant: the usual round, then ``* 33``."""
    value = FNV_OFFSET
    for byte in text.encode("utf-8"):
        step = ((value ^ byte) * FNV_PRIME) & MASK32
        value = (step + ((step * 32) & MASK32)) & MASK32
    return value


def _encode_bytes(text: str, config: tuple[int, int, int | None, int, int]) -> bytes:
    """One payload field: a per-position byte scramble, padded, length-tagged.

    The output is always at least 6 bytes, ends with ``0x00`` and the original
    length, and pads with ``(221 + index) & 255``. The padding matters: for a
    one-character string byte 1 is always ``0xDE``, which is what makes the
    X-Dynosaur checksum below stable no matter what it is a checksum of.
    """
    xor_base, add_base, pre_xor, rotate, post_add = config
    size = max(len(text) + 2, 6)
    out = bytearray(size)
    for index, char in enumerate(text):
        value = (ord(char) ^ (xor_base + index)) & MASK32
        value = (value + add_base + (170 & index)) % 256
        if pre_xor is not None:
            value ^= pre_xor
        value = ((value << rotate) | (value >> (8 - rotate))) & 0xFF
        out[index] = ((value ^ 187) + post_add) % 256
    for index in range(len(text), size - 2):
        out[index] = (221 + index) & 0xFF
    out[size - 2] = 0
    out[size - 1] = len(text)
    return bytes(out)


def pack_payload(
    fields: Mapping[int, bytes],
    order: Sequence[int] | None = None,
    *,
    lead_count: bool = False,
) -> bytes:
    """Lay the fields out as the TLV entries the platform parses.

    ``order`` exists because X-Gnarly's emission order is the one thing here
    that was inferred rather than measured: the SDK emits its 16 fields in an
    order that is stable within a process and different in the next one, which
    says the server parses by key and cannot be relying on position. Ascending is
    what this module ships; passing an order is how a captured signature can be
    reproduced byte for byte anyway, which is what the unit tests do.
    """
    keys = sorted(fields) if order is None else list(order)
    body = b"".join(bytes((key, 0, len(fields[key]))) + fields[key] for key in keys)
    return bytes((len(keys),)) + body if lead_count else body


def _be(value: int, size: int) -> bytes:
    return int(value).to_bytes(size, "big")


def _mix(timestamp: int, nonce: int, env_code: int) -> int:
    """Fold two 32-bit values into 16 bits and stamp the environment on top."""
    folded = ((timestamp >> 16) ^ (nonce >> 16) ^ timestamp ^ nonce) & 0xFFFF
    return folded | (env_code << 16)


def _checksum(values: Sequence[int | str], mode: int) -> int:
    """XOR fold of the X-Gnarly field values, seeded with all ones.

    Two modes, differing only in how a string contributes: mode 1 treats it as
    zero, mode 2 as its first four UTF-8 bytes big-endian. The seed is why the
    two checksums the SDK emits are complements of the plain folds.
    """
    accumulator = MASK32
    for value in values:
        if isinstance(value, str):
            number = 0 if mode == 1 else int.from_bytes(value.encode("utf-8")[:4], "big")
        else:
            number = value
        accumulator ^= number & MASK32
    return accumulator & MASK32


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & MASK32
    state[d] ^= state[a]
    state[d] = ((state[d] << 16) | (state[d] >> 16)) & MASK32
    state[c] = (state[c] + state[d]) & MASK32
    state[b] ^= state[c]
    state[b] = ((state[b] << 12) | (state[b] >> 20)) & MASK32
    state[a] = (state[a] + state[b]) & MASK32
    state[d] ^= state[a]
    state[d] = ((state[d] << 8) | (state[d] >> 24)) & MASK32
    state[c] = (state[c] + state[d]) & MASK32
    state[b] ^= state[c]
    state[b] = ((state[b] << 7) | (state[b] >> 25)) & MASK32


def _keystream(state: Sequence[int], rounds: int) -> list[int]:
    """One 64-byte block. This is NOT ChaCha20 and the differences are load bearing.

    ``rounds`` counts *single* rounds and is data-dependent (5..20), so an odd
    count exits after a column round; getting that exit wrong passes some calls
    and fails others, which looks like flaky rate limiting rather than a bug.
    The diagonal round's third quartet is (2, 7, 12, 13) where the textbook has
    (2, 7, 8, 13) - lane 12 appears twice, which is almost certainly a bug in the
    original and is reproduced deliberately.
    """
    working = list(state)
    done = 0
    while done < rounds:
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        done += 1
        if done >= rounds:
            break
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 12, 13)
        _quarter_round(working, 3, 4, 13, 14)
        done += 1
    return [(working[i] + state[i]) & MASK32 for i in range(16)]


def _crypt(key: Sequence[int], rounds: int, payload: bytes) -> bytes:
    """XOR the payload, read as little-endian uint32 words, with the keystream.

    The counter lives in state word 12 and is incremented only after a *full*
    16-word block, so the trailing partial block uses the already-incremented
    state. Payloads here are one block, but the loop is the SDK's.
    """
    state = [*CHACHA_INIT, *(word & MASK32 for word in key)]
    length = len(payload)
    word_count = (length + 3) // 4
    words = [
        int.from_bytes(payload[4 * i : 4 * i + 4].ljust(4, b"\0"), "little")
        for i in range(word_count)
    ]
    offset = 0
    while offset + 16 < word_count:
        block = _keystream(state, rounds)
        state[12] = (state[12] + 1) & MASK32
        for i in range(16):
            words[offset + i] ^= block[i]
        offset += 16
    block = _keystream(state, rounds)
    for i in range(word_count - offset):
        words[offset + i] ^= block[i]
    return b"".join(word.to_bytes(4, "little") for word in words)[:length]


def seal(payload: bytes, key: Sequence[int]) -> str:
    """Encrypt, splice the key back in, and encode. See the module docstring."""
    rounds = (sum(word & 0xF for word in key) & 0xF) + 5
    ciphertext = _crypt(key, rounds, payload)
    key_bytes = b"".join(int(word).to_bytes(4, "little") for word in key)
    position = (sum(key_bytes) + sum(ciphertext)) % (len(ciphertext) + 1)
    spliced = ciphertext[:position] + key_bytes + ciphertext[position:]
    raw = bytes((ENVELOPE_TAG,)) + spliced
    return base64.b64encode(raw).decode("ascii").translate(_TO_CUSTOM)


def dynosaur_payload(
    query: str,
    user_agent: str,
    *,
    timestamp: int,
    nonce: int,
    sequence: int = CALL_SEQUENCE_START,
) -> bytes:
    """The environment report, 25 TLV fields with keys 0x20..0x38 ascending.

    Three fields are :func:`hash_state` of something and are the only ones that
    bind the report to anything: 0x2B of the empty string (a GET has no body),
    0x2E of the query - the query alone, not the path - and 0x30 of the
    User-Agent, which is why the signature has to be computed with the same UA
    the request is sent with.
    """
    fields: dict[int, bytes] = {
        0x21: _encode_bytes("1", _ENCODER_B),
        0x22: _encode_bytes("1", _ENCODER_B),
        0x23: _encode_bytes("0", _ENCODER_A),
        0x24: _encode_bytes(str(_mix(timestamp, nonce, ENV_CODE)), _ENCODER_A),
        0x25: _encode_bytes(str(sequence), _ENCODER_A),
        0x26: _encode_bytes(str(ENV_CODE), _ENCODER_A),
        0x27: _encode_bytes(str(timestamp), _ENCODER_A),
        0x28: _encode_bytes(WEBGL_HASH, _ENCODER_A),
        0x29: _encode_bytes("0", _ENCODER_A),
        0x2A: _encode_bytes(SDK_VERSION, _ENCODER_A),
        0x2B: _be(hash_state(""), 4),
        0x2C: _encode_bytes(CANVAS_HASH, _ENCODER_A),
        0x2D: _encode_bytes("0", _ENCODER_A),
        0x2E: _be(hash_state(query), 4),
        0x2F: _encode_bytes(str(sequence), _ENCODER_A),
        0x30: _be(hash_state(user_agent), 4),
        0x31: _encode_bytes(SCM_VERSION, _ENCODER_A),
        0x32: _encode_bytes(COMPONENT_VERSION, _ENCODER_A),
        0x33: _encode_bytes(DEVICE_HASH, _ENCODER_A),
        0x34: _encode_bytes(str(nonce), _ENCODER_A),
        0x35: _encode_bytes(PAGE, _ENCODER_A),
        0x36: _encode_bytes(str(UB_CODE), _ENCODER_A),
        0x37: _encode_bytes("0", _ENCODER_A),
        0x38: _be(VM_STATE_HASH, 4),
        # Placeholder while the checksum below is computed over every field.
        # Any one-character string works, because byte 1 of a one-character
        # field is the constant pad byte; the SDK uses "0" and so do we.
        0x20: _encode_bytes("0", _ENCODER_A),
    }
    checksum = 0
    for key in sorted(fields):
        checksum ^= fields[key][1]
    fields[0x20] = _encode_bytes(str(checksum), _ENCODER_B)
    return pack_payload(fields)


def gnarly_fields(
    signed_query: str,
    user_agent: str,
    *,
    body: bytes = b"",
    timestamp: int,
    nonce: int,
    nonce2: int,
    sequence: int = CALL_SEQUENCE_START,
) -> dict[int, bytes]:
    """The request seal, as a field table. 16 entries; 0x07 is absent.

    ``signed_query`` is the business query with ``&X-Dynosaur=...&msToken=...``
    already appended - the suffix is required even when the token is empty, and
    is what makes this parameter a seal over the whole rewritten URL rather than
    over the caller's parameters alone.

    The two checksums are folded over a fixed logical order that is not the
    emission order. Order does not actually matter to an XOR fold, but keeping
    the SDK's list shape keeps the correspondence readable.
    """
    query_md5 = hashlib.md5(signed_query.encode("utf-8")).hexdigest()
    body_md5 = hashlib.md5(body).hexdigest()
    agent_md5 = hashlib.md5(user_agent.encode("utf-8")).hexdigest()
    mixed = _mix(timestamp, nonce, ENV_CODE)
    covered: list[int | str] = [
        0,
        ENV_CODE,
        UB_CODE,
        query_md5,
        body_md5,
        agent_md5,
        timestamp,
        0,
        nonce,
        SDK_VERSION,
        SCM_VERSION,
        CALL_SEQUENCE_START,
        sequence,
        sequence,
        mixed,
        nonce2,
    ]
    first = _checksum(covered, 2)
    second = _checksum([*covered, first], 1)
    return {
        0x00: _be(second, 4),
        0x01: _be(ENV_CODE, 2),
        0x02: _be(UB_CODE, 2),
        0x03: query_md5.encode("ascii"),
        0x04: body_md5.encode("ascii"),
        0x05: agent_md5.encode("ascii"),
        0x06: _be(timestamp, 4),
        0x08: _be(nonce, 4),
        0x09: SDK_VERSION.encode("ascii"),
        0x0A: SCM_VERSION.encode("ascii"),
        0x0B: _be(CALL_SEQUENCE_START, 2),
        0x0C: _be(sequence, 2),
        0x0D: _be(sequence, 2),
        0x0E: _be(mixed, 4),
        0x0F: _be(nonce2, 4),
        0x10: _be(first, 4),
    }


def gnarly_payload(
    signed_query: str,
    user_agent: str,
    *,
    body: bytes = b"",
    timestamp: int,
    nonce: int,
    nonce2: int,
    sequence: int = CALL_SEQUENCE_START,
    order: Sequence[int] | None = None,
) -> bytes:
    """:func:`gnarly_fields`, packed behind the one-byte field count."""
    fields = gnarly_fields(
        signed_query,
        user_agent,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        nonce2=nonce2,
        sequence=sequence,
    )
    return pack_payload(fields, order, lead_count=True)


def _clock_nonce() -> int:
    """A 32-bit value from the microsecond clock, which is what the SDK uses.

    Measured: freezing ``Date.now``, ``performance.now`` and ``Math.random`` made
    both nonces deterministic, and across runs they track wall time at roughly
    one unit per microsecond. Uniform randomness would work equally well as far
    as anything observable goes, but a clock-derived value is what the platform
    has always seen, and it costs nothing to keep.
    """
    return int(time.time() * 1_000_000) & MASK32


def _random_key(rng: random.Random) -> tuple[int, ...]:
    return tuple(rng.getrandbits(32) for _ in range(12))


def sign(
    pairs: Sequence[tuple[str, str]],
    user_agent: str,
    *,
    ms_token: str = "",
    body: bytes = b"",
    timestamp: int | None = None,
    nonce: int | None = None,
    nonce2: int | None = None,
    sequence: int = CALL_SEQUENCE_START,
    rng: random.Random | None = None,
    key: Sequence[int] | None = None,
    key2: Sequence[int] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(query, parameters)`` for one TikTok Web API request.

    ``pairs`` is the business query in the platform's own order; the four
    signature parameters are appended after it and never interleaved. The
    returned query is the exact byte sequence to send - re-encoding it breaks
    the seal.

    ``ms_token`` goes in verbatim, empty included. It is the identity's session
    token, and TikTok does not appear to gate on it (the same signed URL returned
    a full payload with the signing page's own jar, with an unrelated identity's
    jar, and with no msToken at all), but it is inside the sealed bytes, so what
    is signed and what is sent must be the same string.
    """
    source = rng or random.Random()
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    first_nonce = _clock_nonce() if nonce is None else int(nonce)
    second_nonce = (~_clock_nonce()) & MASK32 if nonce2 is None else int(nonce2)

    query = encode_query(pairs)
    dynosaur = seal(
        dynosaur_payload(query, user_agent, timestamp=stamp, nonce=first_nonce, sequence=sequence),
        key if key is not None else _random_key(source),
    )
    # The seal covers the query as it stands after X-Dynosaur and msToken have
    # been appended, so those two have to be spliced in before X-Gnarly is
    # computed - and X-Bogus, which comes between them on the wire, is not
    # covered. That asymmetry is the platform's.
    sealed_query = f"{query}&{DYNOSAUR_PARAM}={dynosaur}&{MS_TOKEN_PARAM}={ms_token}"
    gnarly = seal(
        gnarly_payload(
            sealed_query,
            user_agent,
            body=body,
            timestamp=stamp,
            nonce=first_nonce,
            nonce2=second_nonce,
            sequence=sequence,
        ),
        key2 if key2 is not None else _random_key(source),
    )
    parameters = {
        DYNOSAUR_PARAM: dynosaur,
        MS_TOKEN_PARAM: ms_token,
        BOGUS_PARAM: BOGUS_VALUE,
        GNARLY_PARAM: gnarly,
    }
    # Emitted raw, not percent-encoded: the alphabet contains `/` and the
    # padding is `=`, and TikTok's own page sends both unescaped.
    signed = f"{sealed_query}&{BOGUS_PARAM}={BOGUS_VALUE}&{GNARLY_PARAM}={gnarly}"
    return signed, parameters


def pick_ms_token(cookies: Mapping[str, str] | None) -> str:
    """The session token from the identity's own jar, or empty like the SDK's."""
    return (cookies or {}).get(MS_TOKEN_PARAM) or ""


__all__ = [
    "ALPHABET",
    "BOGUS_PARAM",
    "BOGUS_VALUE",
    "CALL_SEQUENCE_START",
    "DYNOSAUR_PARAM",
    "ENV_CODE",
    "GNARLY_PARAM",
    "MS_TOKEN_PARAM",
    "SCM_VERSION",
    "SDK_VERSION",
    "UB_CODE",
    "dynosaur_payload",
    "encode_query",
    "gnarly_fields",
    "gnarly_payload",
    "hash_state",
    "pack_payload",
    "pick_ms_token",
    "seal",
    "sign",
]
