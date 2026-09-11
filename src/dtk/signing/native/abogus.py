"""A-Bogus, the signature Douyin Web puts on every data endpoint.

Reversed from Douyin's own ``bdms.js`` v1.0.1.19-fix.01 on 2026-09-09, by this
project, for this project. Nothing here is ported from anybody else's
implementation: the previous revision of this module was a port of GPL-3.0 code
and could not be shipped under this repository's Apache-2.0 licence, and that is
the reason the work was done. The method is
``docs/design/17-signature-reversing.md``; the working notes, the disassembler
and the offline decoder are in ``docs/design/tools/douyin/``.

Where it comes from
-------------------
``a_bogus`` is not produced by the ``secsdk`` bundle that produces
``x-secsdk-web-signature`` (:mod:`dtk.signing.native.websign`), and not by
``webmssdk``, which produces ``msToken`` and ``_signature``. It comes from
``bdms.js``, which the page installs over ``XMLHttpRequest``, ``fetch`` and
``URL`` for paths under ``/aweme/v1/`` among others.

No file the page loads contains the string ``a_bogus``: the generator lives in a
bytecode VM. What made this tractable is that ``bdms.js`` is a plain minified
webpack bundle rather than an obfuscated one, so the VM's *interpreter* is
readable JavaScript and its program decodes offline - base64, then a byte-wise
XOR whose key comes from bytes 4..7, then raw DEFLATE - into 1,001 strings and
796 functions. ``fn150`` is the generator. It is reached only through ``fn103``,
which is reached only from the three request decorators, so the attribution is
structural rather than a guess at what a string constant is near.

Verification
------------
Doc 17 section 6 is explicit that a successful request proves nothing, because
the platform only samples. What proves it is taking the browser's own output
apart. Every live signature captured from ``browser-rpc`` decodes under the
constants below and yields, without any of these being inputs to the decoder:
the literal header ``[3, 82]``; the SDK version ``[1, 0, 1, 0]``; ``aid`` 6383;
``page_id`` 6241; a millisecond clock matching the sibling ``timestamp``
parameter; the page's own screen metrics byte-for-byte; and a self-consistent
internal checksum. :func:`structure_error` re-runs those checks, which is what
lets the shadow comparison in :mod:`dtk.signing.registry` actually compare
rather than skip.

Two notes for whoever does this next
------------------------------------
The cipher looks like RC4 and is not quite: the S-box is seeded descending and
the key schedule multiplies. A textbook RC4 key search finds nothing even though
the key is one byte, which is presumably the point.

The randomness is decoration, but only for *recovery*. ``Math.random`` never
reaches the checksum and never changes a field the format can decode, so two
signatures taken in the same millisecond differ in most of their bytes and mean
the same thing. It does not follow that the noise can be uniform: three of those
bytes are not noise at all. The SDK replaces them with narrow environment
reports - which browser family it thinks it is running in, and whether its own
tripwires are still in place - and anybody holding a signature can read them
back out. Writing uniform random there is a tell, and :data:`_HEADER_NOISE_BANDS`
and :func:`_tripwire_noise` are what stop it being one.
"""

# The most-read file in this repository, by a distance. If you got this far
# down a pure-Python a_bogus, the cat would like something for the trouble.
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

import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from dtk.signing.native.sm3 import sm3_to_array

#: The five alphabets ``bdms.js`` carries. Only two are reachable from here:
#: ``s4`` encodes the finished signature, ``s3`` encodes the User-Agent on its
#: way into the third digest.
ALPHABETS: Final[Mapping[str, str]] = {
    "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
    "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
}

#: Appended to the query and to the body before hashing. A salt in the sense
#: that it is constant and secret-ish; it is neither long nor keyed.
SALT: Final = "dhzx"

#: The two plaintext bytes the header carries, before noise dilution. They are
#: the closest thing this format has to a magic number, and the first thing
#: :func:`structure_error` checks.
HEADER_MAGIC: Final[tuple[int, int]] = (3, 82)

#: ``"1.0.1.19-fix.01"`` as the SDK parses it: ``split(".")`` then a JS ``~~``,
#: which truncates ``"19-fix"`` to 19 and ``"01"`` to 1 - but only the first four
#: are used, and the fourth is 19 -> the version block reads ``[1, 0, 1, 0]``
#: because ``mask2`` is called on pairs and the second pair is ``[1, 19]``... it
#: is not: the SDK passes ``[v0, v1]`` and ``[v2, v3]`` where the parse yields
#: ``[1, 0, 1, 0, 1]``. Live captures agree: the block decodes to ``[1, 0, 1, 0]``.
SDK_VERSION: Final[tuple[int, int, int, int]] = (1, 0, 1, 0)

#: One byte, and the whole key. See the module docstring on why searching for it
#: with a textbook RC4 does not find it.
PAYLOAD_KEY: Final = 0xD3

#: ``2024-07-24T16:00:00Z``. One field counts fortnights from here.
FORTNIGHT_EPOCH_MS: Final = 1_721_836_800_000

#: Douyin web's own identifiers, as the page configures ``bdms``. They are not
#: ours to choose: they travel in the signature and in the query, and a
#: disagreement between the two is the cheapest possible tell.
PAGE_ID: Final = 6241
AID: Final = 6383

#: The masks ``fn148`` uses to hide three body bytes among four carrier bytes.
#: They partition a byte - ``0x91 | 0x42 | 0x2C == 0xFF`` - which is what lets
#: the fourth byte carry exactly the bits the first three surrendered to noise,
#: and what makes the whole layer reversible.
NOISE_MASKS: Final[tuple[int, int, int]] = (0x91, 0x42, 0x2C)
DATA_MASKS: Final[tuple[int, int, int]] = (0x6E, 0xBD, 0xD3)

#: How the fifty scalar fields are shuffled into the body. Not a cipher, just a
#: fixed permutation; it is written out rather than computed because that is how
#: it appears in the bytecode and because a reader checking this against a live
#: capture wants to see the same order the decoder prints.
FIELD_ORDER: Final[tuple[str, ...]] = (
    "L34", "L44", "L56", "L61", "L73", "L29", "L70", "L45", "L35", "L49",
    "L38", "L66", "L51", "L68", "L28", "L48", "L64", "L47", "L30", "L71",
    "L26", "L55", "L31", "L69", "L59", "L40", "L62", "L63", "L27", "L72",
    "L41", "L74", "L57", "L52", "L42", "L39", "L33", "L67", "L53", "L43",
    "L65", "L46", "L36", "L24", "L60", "L32", "L79", "L80", "L84", "L85",
)  # fmt: skip

#: Sentinel/offset/fallback for the three digest canaries. Normally the field
#: holds the first digest byte at or after ``offset`` that is not ``sentinel``;
#: writing the sentinel itself is how the SDK reports that its own environment
#: probe flagged something. We always write the honest value.
CANARIES: Final[tuple[tuple[int, int, int], ...]] = ((3, 11, 12), (4, 8, 9), (5, 12, 13))


@dataclass(frozen=True, slots=True)
class DigestChain:
    """Where one hashed input leaves its three bytes in the fifty scalars.

    ``slots`` are names in :data:`FIELD_ORDER`, ``indices`` the two digest
    positions written verbatim, and ``canary`` the sentinel triple the third
    slot follows.
    """

    slots: tuple[str, str, str]
    indices: tuple[int, int]
    canary: tuple[int, int, int]


#: The three hashed inputs and where each one lands. Read by the generator and
#: by :mod:`dtk.signing.native.decoding` alike, so a decoder cannot drift from
#: the thing it decodes.
#:
#: Three bytes per input is the whole binding, and that asymmetry is the point:
#: enough to *prove* that a given query, body or User-Agent is the one a
#: signature sealed, nowhere near enough to run the hash backwards. A decoder
#: that reported these as "the query" would be inventing; one that checks a
#: candidate against them is telling the truth.
DIGEST_CHAINS: Final[Mapping[str, DigestChain]] = MappingProxyType(
    {
        "query": DigestChain(("L48", "L49", "L51"), (9, 18), CANARIES[0]),
        "body": DigestChain(("L52", "L53", "L55"), (10, 19), CANARIES[1]),
        "user_agent": DigestChain(("L56", "L57", "L59"), (11, 21), CANARIES[2]),
    }
)

#: What an untouched browser reports about itself. ``fn150`` reads six probes
#: and a bot-detection bitset; a real page that has not been instrumented
#: produces these, and they are what a pool identity should look like.
ENV_FLAGS: Final = 1
DETECT_FLAGS: Final = 14
NR_FLAGS: Final = 0x21
NR_TAG: Final[tuple[int, int, int, int]] = (0, 0, 0, 0)

#: ``window.onwheelx._Ax`` present and locked - the state the SDK installs on
#: itself and then freezes. 12 would mean somebody unlocked it, 11 that it is
#: missing; both are things Douyin can score, so neither is a good thing to be.
TRIPWIRE_LOCKED: Final = 3

#: ``fn149`` buckets a per-page signature counter. 6 is "this page has signed
#: fewer than 140 times", which is where a browser spends most of its life.
CALL_BUCKET: Final = 6

#: Chrome on a 1080p display: 1920x1080 screen, 1032 of usable height once the
#: OS taskbar is gone, 947 of viewport once the browser's own chrome is. Used
#: when a fingerprint does not carry a screen size, because a plausible constant
#: beats a random value - a geometry that changes per request is itself a tell.
DEFAULT_BROWSER_INFO: Final = "1920|947|1920|1032|1920|1032|1920|1080|Win32"

#: Rows of browser chrome to subtract from a screen height to guess a viewport.
_TASKBAR_PX: Final = 48
_CHROME_PX: Final = 85


def build_browser_info(
    *,
    inner_width: int,
    inner_height: int,
    outer_width: int,
    outer_height: int,
    avail_width: int,
    avail_height: int,
    screen_width: int,
    screen_height: int,
    platform: str,
) -> str:
    """The ``navigator``/``screen`` string the signature carries verbatim.

    Nine fields joined with ``|``, in the order the SDK's own object literal
    lists them. It is the one part of the payload that survives as readable text
    all the way to the checksum, so the field order is not negotiable.
    """
    return "|".join(
        str(value)
        for value in (
            inner_width,
            inner_height,
            outer_width,
            outer_height,
            avail_width,
            avail_height,
            screen_width,
            screen_height,
            platform,
        )
    )


def browser_info_from_screen(width: int, height: int, platform: str) -> str:
    """A nine-field geometry consistent with one screen size.

    A browser reports four related rectangles and they are not independent: the
    window is at most the work area, the work area is the screen less the OS
    bars, and the viewport is the window less the browser's own. Deriving them
    from one number keeps them consistent, which a randomly assembled set would
    not be.
    """
    avail_height = max(height - _TASKBAR_PX, 1)
    return build_browser_info(
        inner_width=width,
        inner_height=max(avail_height - _CHROME_PX, 1),
        outer_width=width,
        outer_height=avail_height,
        avail_width=width,
        avail_height=avail_height,
        screen_width=width,
        screen_height=height,
        platform=platform,
    )


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def rc4(key: bytes, data: bytes) -> bytes:
    """The SDK's RC4, which is not RC4.

    Two deliberate deviations, both in the setup: the S-box starts as a
    *descending* identity, and the key schedule multiplies where the textbook
    one only adds. The keystream generator itself is unchanged.

    They matter more than they look. Together they mean that trying every
    one-byte key with a stock RC4 against a captured payload finds nothing -
    which is exactly what happened here before the bytecode was read, and is
    presumably what they are for.
    """
    box = [0] * 256
    for i in range(256):
        box[255 - i] = i
    j = 0
    for i in range(256):
        j = (j * box[i] + j + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]

    out = bytearray(len(data))
    i = j = 0
    for index, byte in enumerate(data):
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out[index] = byte ^ box[(box[i] + box[j]) % 256]
    return bytes(out)


def encode_base64(data: bytes, alphabet: str = "s4") -> str:
    """Base64 over one of the SDK's alphabets, with a literal ``=`` pad.

    Standard base64 in every respect except the digit set, so the padding rule
    and the six-bit grouping are the ordinary ones.
    """
    table = ALPHABETS[alphabet]
    out: list[str] = []
    for offset in range(0, len(data), 3):
        chunk = data[offset : offset + 3]
        block = int.from_bytes(chunk + b"\x00" * (3 - len(chunk)), "big")
        digits = [(block >> shift) & 0x3F for shift in (18, 12, 6, 0)]
        out.extend(table[digit] for digit in digits[: len(chunk) + 1])
    out.append("=" * ((4 - len(out) % 4) % 4))
    return "".join(out)


def decode_base64(text: str, alphabet: str = "s4") -> bytes:
    """Inverse of :func:`encode_base64`. Raises ``ValueError`` on a foreign digit."""
    table = ALPHABETS[alphabet]
    index = {char: value for value, char in enumerate(table)}
    body = text.rstrip("=")
    out = bytearray()
    for offset in range(0, len(body), 4):
        chunk = body[offset : offset + 4]
        block = 0
        for char in chunk:
            try:
                block = (block << 6) | index[char]
            except KeyError:
                raise ValueError(f"not a {alphabet} digit: {char!r}") from None
        block <<= 6 * (4 - len(chunk))
        out.extend(((block >> shift) & 0xFF) for shift in (16, 8, 0)[: len(chunk) - 1])
    return bytes(out)


#: Which browser family the SDK thinks it is in, as ``fn143`` reports it: a
#: 40-wide band per family, and the value it puts in the header's second noise
#: byte is ``floor(random * 40) + base``. Measured across 43 live Chrome
#: signatures, every observed value fell in 0..38.
#:
#: The bands do not cover 0..255. A value in a gap is one no browser can emit,
#: and a value in the wrong band contradicts the User-Agent sent beside it -
#: both free for Douyin to score. Uniform random lands in a gap 6% of the time
#: and in the wrong family 78% of the time.
_HEADER_NOISE_BANDS: Final[Mapping[str, int]] = {
    "chrome": 0,
    "firefox": 40,
    "safari": 81,
    "edge": 125,
    "huawei": 170,
    "other": 210,
}

#: ``fn145`` reports the SDK's own tripwires. ``fn152`` installs three of them -
#: ``window.onwheelx._Ax`` and two misspelled ``pemrissions`` properties - and
#: ``fn153`` freezes all three, so bits 1, 4, 5 and 7 set is the *healthy*
#: state, in the same way ``L66 == 3`` is. Live captures took exactly the 16
#: values this allows. Uniform random reports a stripped or unlocked page 94%
#: of the time.
_TRIPWIRE_SET: Final = 0xB2
_TRIPWIRE_FREE: Final = 0x4D


def _family_of(user_agent: str) -> str:
    """Which band ``fn143`` would draw from, given this User-Agent.

    Order matters: Edge and Huawei both also say "Chrome", and Chrome says
    "Safari". The SDK tests the specific names before the generic ones, so this
    does too.
    """
    ua = user_agent.lower()
    for name in ("edg", "huawei", "firefox", "chrome", "safari"):
        if name in ua:
            return {"edg": "edge"}.get(name, name)
    return "other"


def _header_noise(user_agent: str, rng: random.Random) -> int:
    """``fn143``: a browser-family report dressed as noise."""
    base = _HEADER_NOISE_BANDS.get(_family_of(user_agent), _HEADER_NOISE_BANDS["other"])
    return (base + int(rng.random() * 40)) & 0xFF


def _probe_noise(rng: random.Random) -> int:
    """``fn144``: the arm taken when ``nr()[4] & 64`` is clear, which it is.

    Below 110 the value passes through; above it the SDK forces it odd, which
    leaves half of 110..240 unreachable. A uniform byte lands outside the
    reachable set 31% of the time.
    """
    value = int(rng.random() * 240)
    return value + value % 2 + 1 if value > 109 else value


def _tripwire_noise(rng: random.Random) -> int:
    """``fn145``: "my tripwires are all present and locked", plus real noise."""
    return (int(rng.random() * 255) & _TRIPWIRE_FREE) | _TRIPWIRE_SET


def _mask_pair(
    pair: tuple[int, int],
    rng: random.Random,
    *,
    low: int | None = None,
    high: int | None = None,
) -> list[int]:
    """Spread two bytes over four, filling the other half of each with noise.

    Each output byte takes half its bits from the payload and half from a noise
    byte, on the alternating masks ``0xAA``/``0x55``. Recovery needs only the
    payload halves - but the noise halves are just as recoverable, and the SDK
    uses two of the three sites to say something about itself. ``low`` and
    ``high`` are how a caller supplies those reports; left alone, the byte is
    the plain draw the SDK makes when it has nothing to report.
    """
    noise = int(rng.random() * 65535)
    low = noise & 0xFF if low is None else low & 0xFF
    high = (noise >> 8) & 0xFF if high is None else high & 0xFF
    return [
        (low & 0xAA) | (pair[0] & 0x55),
        (low & 0x55) | (pair[0] & 0xAA),
        (high & 0xAA) | (pair[1] & 0x55),
        (high & 0x55) | (pair[1] & 0xAA),
    ]


def _unmask_pair(carrier: bytes) -> tuple[int, int]:
    """Inverse of :func:`_mask_pair`: take each byte's payload half back."""
    return (
        (carrier[0] & 0x55) | (carrier[1] & 0xAA),
        (carrier[2] & 0x55) | (carrier[3] & 0xAA),
    )


def _expand_noise(body: bytes, rng: random.Random) -> bytes:
    """Three body bytes and one noise byte become four carrier bytes.

    Each carrier keeps part of its own byte and takes the rest from the noise;
    the fourth byte collects precisely the bits the first three gave up, at the
    bit positions they came from. Nothing moves within a byte, which is why the
    layer is reversible and why the result nonetheless looks like ciphertext.
    """
    out = bytearray()
    for offset in range(0, len(body), 3):
        group = body[offset : offset + 3]
        if len(group) < 3:
            # The SDK's tail branch. Unreachable for the bodies this builds -
            # they are always a multiple of three - and reproduced so that a
            # future field change does not silently diverge here.
            out.append(group[0])
            if len(group) > 1 and group[1]:
                out.append(group[1])
            continue
        noise = int(rng.random() * 1000) & 0xFF
        out.extend(
            (noise & mask) | (byte & data)
            for mask, data, byte in zip(NOISE_MASKS, DATA_MASKS, group, strict=True)
        )
        out.append(
            (group[0] & NOISE_MASKS[0]) | (group[1] & NOISE_MASKS[1]) | (group[2] & NOISE_MASKS[2])
        )
    return bytes(out)


def _collapse_noise(frame: bytes) -> bytes:
    """Inverse of :func:`_expand_noise`."""
    out = bytearray()
    for offset in range(0, len(frame), 4):
        group = frame[offset : offset + 4]
        if len(group) < 4:
            out.extend(group)
            continue
        out.extend(
            (group[index] & data) | (group[3] & mask)
            for index, (mask, data) in enumerate(zip(NOISE_MASKS, DATA_MASKS, strict=True))
        )
    return bytes(out)


def js_bytes(text: str) -> bytes:
    """A JavaScript string as the SDK turns it into bytes.

    Not UTF-8. ``fn139`` walks UTF-16 code units and emits one byte per unit
    below U+0100 and two big-endian bytes above it, so a surrogate pair becomes
    four bytes and a Chinese character becomes two - where UTF-8 would give
    three. Measured against 43 live signatures: this rule reproduces the
    declared geometry length 43 times, UTF-8 reproduces it 41.

    It only diverges above U+00FF, which is why the difference stayed invisible
    until a ``navigator.platform`` with Chinese in it was signed. The geometry
    length is covered by the checksum, so getting it wrong shifts the frame and
    invalidates the whole signature rather than corrupting one field.
    """
    out = bytearray()
    units = text.encode("utf-16-le")
    for index in range(0, len(units), 2):
        code = units[index] | (units[index + 1] << 8)
        if code & 0xFF00:
            out.append(code >> 8)
        out.append(code & 0xFF)
    return bytes(out)


def _le_bytes(value: int, count: int) -> list[int]:
    """``count`` little-endian bytes of ``value``."""
    return [(value >> (8 * index)) & 0xFF for index in range(count)]


def canary(digest: Sequence[int], offset: int, sentinel: int, fallback: int) -> int:
    """First digest byte at or after ``offset`` that is not ``sentinel``.

    The sentinel is reserved: writing it is how the SDK tells the server its own
    environment probe fired. We never write it, so this only ever reports the
    digest.
    """
    for byte in digest[offset:]:
        if byte != sentinel:
            return byte
    return fallback


def digest_of(text: str) -> list[int]:
    """``SM3(SM3(text + SALT))``, as 32 integers."""
    return sm3_to_array(sm3_to_array(text + SALT))


def user_agent_digest(user_agent: str) -> list[int]:
    """The third chain: RC4 the User-Agent, base64 it, hash it once.

    Single SM3, unlike the other two. The RC4 key is three bytes built from the
    environment probes, so it is constant for a given identity rather than per
    call.
    """
    key = bytes((ENV_FLAGS // 256, ENV_FLAGS % 256, DETECT_FLAGS % 256))
    # Code units, not UTF-8: the cipher reads `charCodeAt` unmasked and the
    # base64 after it masks to eight bits, so the keystream advances once per
    # code unit. UTF-8 would advance three times on a Chinese character and give
    # a different digest - measured, 43/43 against 40/43.
    sealed = rc4(key, bytes(byte & 0xFF for byte in js_bytes(user_agent.strip())))
    return sm3_to_array(encode_base64(sealed, "s3"))


def chain_bytes(chain: DigestChain, digest: Sequence[int]) -> tuple[int, int, int]:
    """The three bytes ``chain`` contributes, given that chain's own digest."""
    first, second = chain.indices
    return digest[first], digest[second], canary(digest, *chain.canary)


# --------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------


class ABogus:
    """Computes ``a_bogus`` for one browser identity.

    Cheap and stateless apart from its configuration, so one per identity is the
    natural lifetime. ``rng`` exists so a test can pin the noise; the value is
    correct whatever it produces.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        browser_info: str = DEFAULT_BROWSER_INFO,
        page_id: int = PAGE_ID,
        aid: int = AID,
        rng: random.Random | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.browser_info = browser_info
        self.page_id = page_id
        self.aid = aid
        self._rng = rng or random.Random()

    def _fields(self, query: str, body: str, now_ms: int) -> dict[str, int]:
        """The fifty scalars, by the names the offline decoder prints."""
        digests = {
            "query": digest_of(query),
            "body": digest_of(body),
            "user_agent": user_agent_digest(self.user_agent),
        }

        # `ink` is Date.now() - 1, planted on navigator's prototype by the SDK
        # one call earlier. It is a liveness check on itself: a payload whose
        # `ink` is not one millisecond behind its clock was not assembled by
        # code that ran through the whole entry point.
        ink = now_ms - 1
        fortnights = (now_ms - FORTNIGHT_EPOCH_MS) // (1000 * 60 * 60 * 24 * 14)
        info_bytes = js_bytes(self.browser_info)
        tail_bytes = js_bytes(f"{(now_ms + 3) & 0xFF},")

        fields: dict[str, int] = {
            "L24": 41,
            "L26": fortnights,
            "L27": CALL_BUCKET,
            # Milliseconds since the SDK initialised, plus three. A page that
            # has just loaded signs with a small number here; ours says the
            # entry point was reached promptly, which is the honest answer for
            # a process that does not keep a page open.
            "L28": 3,
            "L35": ENV_FLAGS & 0xFF,
            "L36": (ENV_FLAGS // 256) & 0xFF,
            "L38": NR_FLAGS & 0xFF,
            "L39": (NR_FLAGS >> 8) & 0xFF,
            "L66": TRIPWIRE_LOCKED,
            "L79": len(info_bytes) & 0xFF,
            "L80": (len(info_bytes) >> 8) & 0xFF,
            "L84": len(tail_bytes) & 0xFF,
            "L85": (len(tail_bytes) >> 8) & 0xFF,
        }
        for index, byte in enumerate(_le_bytes(now_ms, 6)):
            fields[f"L{29 + index}"] = byte
        for index, byte in enumerate(_le_bytes(DETECT_FLAGS, 4)):
            fields[f"L{44 + index}"] = byte
        for index, byte in enumerate(NR_TAG):
            fields[f"L{40 + index}"] = byte
        for index, byte in enumerate(_le_bytes(ink, 6)):
            fields[f"L{60 + index}"] = byte
        for index, byte in enumerate(_le_bytes(self.page_id, 4)):
            fields[f"L{67 + index}"] = byte
        for index, byte in enumerate(_le_bytes(self.aid, 4)):
            fields[f"L{71 + index}"] = byte
        # The three hashed inputs, written through the same table the decoder
        # reads. Nine slots that used to be spelled out one at a time here, and
        # were the one place a decoder could silently disagree with us.
        for name, chain in DIGEST_CHAINS.items():
            written = chain_bytes(chain, digests[name])
            for slot, value in zip(chain.slots, written, strict=True):
                fields[slot] = value
        return fields

    def get_value(
        self,
        query: str,
        *,
        body: str = "",
        content_type: str = "",
        now_ms: int | None = None,
    ) -> str:
        """The ``a_bogus`` value for one request.

        ``query`` is the query string as it will be sent, without ``a_bogus``
        itself. ``body`` is the request body for a POST and empty otherwise -
        it is hashed, so a body signed as empty is a wrong signature.

        ``content_type`` exists for one rule: the SDK blanks the body before
        hashing it when the request is ``multipart/form-data``, because at that
        point in the page the body is a ``FormData`` and not a string. Signing
        the parts as if they were text would disagree with what the platform
        hashes, so the same rule is applied here.

        The HTTP method is deliberately absent: this revision of the algorithm
        does not hash it. The previous one did, which is worth knowing if a
        capture from an older bundle ever has to be explained.
        """
        if "multipart/form-data" in content_type.lower():
            body = ""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if now < FORTNIGHT_EPOCH_MS:
            # One field counts fortnights from 2024-07-24 and has nowhere to put
            # a negative. Refusing is right: a clock before the epoch means the
            # caller passed seconds where milliseconds were wanted, and a
            # signature built from it would be quietly wrong rather than absent.
            raise ValueError(f"clock is before the a_bogus epoch: {now}")
        fields = self._fields(query, body, now)

        # Three of the four noise bytes below are environment reports rather
        # than noise; see the module docstring. The fourth, the version block's
        # low byte, really is a free draw.
        version = _mask_pair(SDK_VERSION[:2], self._rng) + _mask_pair(
            SDK_VERSION[2:],
            self._rng,
            low=_probe_noise(self._rng),
            high=_tripwire_noise(self._rng),
        )

        # The checksum covers the version block and the fifty scalars, and
        # nothing else - not the geometry, not the tail. It is what makes the
        # format internally redundant, and therefore what makes
        # `structure_error` able to tell a real signature from a plausible one.
        checksum = 0
        for byte in version:
            checksum ^= byte
        for name in FIELD_ORDER:
            checksum ^= fields[name]

        body_bytes = bytes(fields[name] for name in FIELD_ORDER)
        body_bytes += js_bytes(self.browser_info)
        body_bytes += js_bytes(f"{(now + 3) & 0xFF},")
        body_bytes += bytes((checksum,))

        header = bytes(
            _mask_pair(HEADER_MAGIC, self._rng, high=_header_noise(self.user_agent, self._rng))
        )
        frame = _expand_noise(body_bytes, self._rng)
        sealed = rc4(bytes((PAYLOAD_KEY,)), bytes(version) + frame)
        return encode_base64(header + sealed, "s4")


# --------------------------------------------------------------------------
# Structural checking, for the shadow comparison
# --------------------------------------------------------------------------

#: Problems that mean the string is not an ``a_bogus`` at all, as opposed to one
#: whose contents are unexpected. :mod:`dtk.signing.registry` treats the first
#: kind as a mismatch and the second as inconclusive.
_DECODE_PROBLEMS: Final[frozenset[str]] = frozenset(
    {"alphabet", "length not a multiple of four", "payload too short"}
)


def is_decode_problem(problem: str | None) -> bool:
    """Whether ``problem`` means "not an a_bogus" rather than "unexpected contents"."""
    return problem in _DECODE_PROBLEMS


def structure_error(value: str, *, alphabet: str = "s4") -> str | None:
    """The first way ``value`` fails to be a well-formed ``a_bogus``, or None.

    Every check here is fixed by the algorithm, so two correct implementations
    agree on all of them however different their noise. Deliberately not
    checked: the length, which follows the geometry string and therefore differs
    between two browsers that are both right.

    The last check is the one worth having. The checksum is computed over
    fields that are themselves in the payload, so a signature that passes it was
    assembled by something that agreed with us about the whole layout - the
    version block, the fifty-slot permutation, the noise expansion and the
    cipher. Nothing short of the real algorithm produces one by accident.
    """
    body = value.rstrip("=")
    if not body or "=" in body or not set(body) <= set(ALPHABETS[alphabet]):
        return "alphabet"
    if len(value) % 4:
        return "length not a multiple of four"

    payload = decode_base64(value, alphabet)
    # Header, version block, and enough frame for the fifty scalars: 4 + 8 + 68.
    if len(payload) < 80:
        return "payload too short"

    if _unmask_pair(payload[:4]) != HEADER_MAGIC:
        return "header magic"

    plain = rc4(bytes((PAYLOAD_KEY,)), payload[4:])
    version, frame = plain[:8], plain[8:]
    if _unmask_pair(version[:4]) != SDK_VERSION[:2]:
        return "sdk version"
    if _unmask_pair(version[4:8]) != SDK_VERSION[2:]:
        return "sdk version"

    unpacked = _collapse_noise(frame)
    if len(unpacked) < len(FIELD_ORDER) + 1:
        return "frame too short"
    fields = dict(zip(FIELD_ORDER, unpacked, strict=False))

    info_length = fields["L79"] | (fields["L80"] << 8)
    tail_length = fields["L84"] | (fields["L85"] << 8)
    end = len(FIELD_ORDER) + info_length + tail_length
    if end + 1 > len(unpacked):
        return "declared lengths overrun the frame"
    # And the other way. Only checking for underrun accepted a signature
    # concatenated with itself: the cipher is a keystream, so the first half
    # still decrypts and the trailing junk was simply never looked at. The
    # declared lengths have to account for the payload, give or take the two
    # bytes the noise expansion pads a final group with.
    if len(unpacked) - (end + 1) > 2:
        return "declared lengths leave a tail"

    checksum = 0
    for byte in version:
        checksum ^= byte
    for name in FIELD_ORDER:
        checksum ^= fields[name]
    if unpacked[end] != checksum:
        return "checksum"
    return None


def decode(value: str, *, alphabet: str = "s4") -> dict[str, Any]:
    """Take an ``a_bogus`` apart. For diagnosis and for tests, not for signing.

    Returns the recoverable fields. This is the function that proved the
    algorithm: run over a signature a browser produced, it recovers that
    browser's screen metrics, Douyin's own ``aid``, and a clock that agrees with
    the ``timestamp`` parameter sent beside it.
    """
    problem = structure_error(value, alphabet=alphabet)
    if problem is not None:
        raise ValueError(f"not a well-formed a_bogus: {problem}")

    payload = decode_base64(value, alphabet)
    plain = rc4(bytes((PAYLOAD_KEY,)), payload[4:])
    unpacked = _collapse_noise(plain[8:])
    fields = dict(zip(FIELD_ORDER, unpacked, strict=False))

    def little_endian(first: int, count: int) -> int:
        return sum(fields[f"L{first + index}"] << (8 * index) for index in range(count))

    info_length = fields["L79"] | (fields["L80"] << 8)
    tail_length = fields["L84"] | (fields["L85"] << 8)
    start = len(FIELD_ORDER)
    return {
        "header_magic": _unmask_pair(payload[:4]),
        "sdk_version": _unmask_pair(plain[:4]) + _unmask_pair(plain[4:8]),
        "now_ms": little_endian(29, 6),
        "ink_ms": little_endian(60, 6),
        "page_id": little_endian(67, 4),
        "aid": little_endian(71, 4),
        "env_flags": fields["L35"],
        "detect_flags": little_endian(44, 4),
        "call_bucket": fields["L27"],
        "tripwire": fields["L66"],
        "browser_info": unpacked[start : start + info_length].decode("utf-8", "replace"),
        "tail": unpacked[start + info_length : start + info_length + tail_length].decode(
            "utf-8", "replace"
        ),
        "fields": fields,
    }


__all__ = [
    "AID",
    "ALPHABETS",
    "CANARIES",
    "DEFAULT_BROWSER_INFO",
    "DIGEST_CHAINS",
    "FIELD_ORDER",
    "HEADER_MAGIC",
    "PAGE_ID",
    "PAYLOAD_KEY",
    "SALT",
    "SDK_VERSION",
    "ABogus",
    "DigestChain",
    "browser_info_from_screen",
    "build_browser_info",
    "canary",
    "chain_bytes",
    "decode",
    "decode_base64",
    "digest_of",
    "encode_base64",
    "is_decode_problem",
    "rc4",
    "structure_error",
    "user_agent_digest",
]
