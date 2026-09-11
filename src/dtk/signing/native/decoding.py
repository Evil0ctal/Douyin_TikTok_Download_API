"""Signature parameters, read back into plaintext.

This is the inverse of the four modules beside it, offered because the whole
point of owning the algorithms rather than vendoring them is that they can be
explained. Hand it an ``a_bogus``, an ``X-Gnarly`` or a whole signed URL and it
says what is inside: which clock the page was on, which ``aid`` and screen it
claimed, which SDK build produced it, and which of the values are hashes whose
inputs are gone.

What "decode" honestly means here
---------------------------------
Three different things, and conflating them is how a tool like this starts
lying:

* **Recovered.** The value is in the payload and comes back exactly. Clocks,
  ``aid``, ``page_id``, the screen geometry string, version strings, call
  counters, nonces. Nothing is inferred.
* **Bound but not recoverable.** The payload carries a hash *of* something -
  three SM3 bytes of the query in ``a_bogus``, a full md5 of it in ``X-Gnarly``.
  The input cannot be run backwards out of it, so this module never pretends to.
  What it does instead is *check*: give it a candidate query and it says whether
  that candidate is the one the signature sealed, and how many bits of evidence
  say so. That is the real inverse of a hash, and it is more useful than a
  guess would be - it is what turns "my request is refused" into "the signature
  I am sending was computed over a different URL".
* **Not computed at all.** ``msToken`` and the visitor tokens are issued by the
  platform or drawn from a random source. There is no plaintext under them to
  find, and saying so is the answer rather than a failure to produce one.

Every field carries which of the three it is, so a reader is never left to
assume that a number printed beside a label was derived rather than read.

Nothing here is used to sign anything. It runs against values the caller
already holds, touches no network and spends no identity.
"""

# Taking them apart again, which is how the rest of this directory got written.
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

import binascii
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import unquote

from dtk.signing.native import abogus, tiktok_sign, tokens, websign, xbogus

# --------------------------------------------------------------------------
# The vocabulary the console renders
# --------------------------------------------------------------------------

#: What a field is. Wire values: the console keys its colours and its footnotes
#: off them, and a caller may branch on them.
KIND_PLAIN = "plain"
"""Read straight out of the payload. Exactly what the signer put there."""
KIND_TIME = "time"
"""A clock, reported as both the raw number and an ISO-8601 instant."""
KIND_DIGEST = "digest"
"""A hash, or a byte of one. The input is not recoverable - see the checks."""
KIND_CHECKSUM = "checksum"
"""Internal redundancy. Recomputed here, which is what proves the decode."""
KIND_ENVIRONMENT = "environment"
"""What the SDK reported about the page it believed it was running in."""
KIND_OPAQUE = "opaque"
"""Present in every capture, meaning not established. Named, not invented."""

#: Why a parameter has no plaintext under it.
REASON_MALFORMED = "malformed"
REASON_NOT_COMPUTED = "not_computed"
REASON_ONE_WAY = "one_way"
REASON_UNKNOWN_PARAMETER = "unknown_parameter"

#: Whether a candidate input is the one a signature sealed.
CHECK_MATCH = "match"
CHECK_DIFFERS = "differs"
CHECK_NOT_SUPPLIED = "not_supplied"


@dataclass(frozen=True, slots=True)
class Field:
    """One value read out of a signature."""

    name: str
    """Stable slug. The console translates it; unknown slugs render as-is."""
    value: str
    kind: str
    detail: str | None = None
    """Where it came from, when that is not obvious from the name."""


@dataclass(frozen=True, slots=True)
class Check:
    """Whether a candidate input is the one this signature was computed over.

    ``bits`` is how much evidence there is. A full md5 is 128 and settles it; the
    three SM3 bytes ``a_bogus`` carries are 24, which is enough to be sure in
    practice and worth stating rather than hiding.
    """

    name: str
    status: str
    bits: int
    covered: str | None = None
    """The exact string checked - returned only when it matched, so that a
    preimage published here is always one that reproduces the signature beside
    it."""


@dataclass(frozen=True, slots=True)
class Decoded:
    """One parameter, taken apart."""

    parameter: str
    platform: str | None
    algorithm: str
    recovered: bool
    reason: str | None = None
    fields: tuple[Field, ...] = ()
    checks: tuple[Check, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The wire shape. Dataclasses stay the internal contract."""
        return {
            "parameter": self.parameter,
            "platform": self.platform,
            "algorithm": self.algorithm,
            "recovered": self.recovered,
            "reason": self.reason,
            "fields": [
                {"name": item.name, "value": item.value, "kind": item.kind, "detail": item.detail}
                for item in self.fields
            ],
            "checks": [
                {
                    "name": item.name,
                    "status": item.status,
                    "bits": item.bits,
                    "covered": item.covered,
                }
                for item in self.checks
            ],
            "notes": list(self.notes),
        }


def _instant(milliseconds: int) -> str:
    """A clock as an ISO-8601 UTC instant, or the raw number when it is absurd.

    A signature whose clock does not parse is still worth showing: it is usually
    the most interesting thing about it.
    """
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return str(milliseconds)


def _clock(name: str, milliseconds: int, detail: str | None = None) -> Field:
    return Field(name, f"{milliseconds} ({_instant(milliseconds)})", KIND_TIME, detail)


def _check(name: str, expected: object, actual: object, *, bits: int, covered: str) -> Check:
    if expected != actual:
        return Check(name, CHECK_DIFFERS, bits)
    return Check(name, CHECK_MATCH, bits, covered)


# --------------------------------------------------------------------------
# Douyin: a_bogus
# --------------------------------------------------------------------------

#: How many bits of the input each of the three SM3 chains pins down.
_ABOGUS_CHAIN_BITS: Final = 24
_FORTNIGHT_MS: Final = 1000 * 60 * 60 * 24 * 14


def decode_a_bogus(
    value: str,
    *,
    query: str | None = None,
    body: str | None = None,
    user_agent: str | None = None,
) -> Decoded:
    """Take an ``a_bogus`` apart, and check it against whatever was supplied."""
    problem = abogus.structure_error(value)
    if problem is not None:
        return Decoded(
            parameter="a_bogus",
            platform="douyin",
            algorithm="a_bogus",
            recovered=False,
            reason=f"{REASON_MALFORMED}:{problem}",
        )

    raw = abogus.decode(value)
    scalars: Mapping[str, int] = raw["fields"]
    fortnights = scalars["L26"]
    fields = [
        Field("header_magic", str(list(raw["header_magic"])), KIND_PLAIN),
        Field("sdk_version", ".".join(str(part) for part in raw["sdk_version"]), KIND_PLAIN),
        _clock("now_ms", raw["now_ms"]),
        _clock("ink_ms", raw["ink_ms"], detail="now_ms - 1; the SDK's own liveness check"),
        Field("aid", str(raw["aid"]), KIND_PLAIN),
        Field("page_id", str(raw["page_id"]), KIND_PLAIN),
        Field(
            "fortnights",
            f"{fortnights} ({_instant(abogus.FORTNIGHT_EPOCH_MS + fortnights * _FORTNIGHT_MS)})",
            KIND_PLAIN,
            detail="fortnights since the a_bogus epoch",
        ),
        Field("browser_info", raw["browser_info"], KIND_ENVIRONMENT, detail="screen|viewport|…"),
        Field("tail", raw["tail"], KIND_PLAIN, detail="(now_ms + 3) & 0xFF"),
        Field("env_flags", str(raw["env_flags"]), KIND_ENVIRONMENT),
        Field("detect_flags", str(raw["detect_flags"]), KIND_ENVIRONMENT),
        Field("call_bucket", str(raw["call_bucket"]), KIND_ENVIRONMENT),
        Field("tripwire", str(raw["tripwire"]), KIND_ENVIRONMENT),
    ]

    candidates = {"query": query, "body": body, "user_agent": user_agent}
    empty_body = abogus.chain_bytes(abogus.DIGEST_CHAINS["body"], abogus.digest_of(""))
    checks: list[Check] = []
    for name, chain in abogus.DIGEST_CHAINS.items():
        carried = tuple(scalars[slot] for slot in chain.slots)
        detail = f"3 bytes of the {name} chain, at {', '.join(chain.slots)}"
        if name == "body" and carried == empty_body:
            # Not a check - nobody asked about it. It is a fact read out of the
            # signature, the same way `aid` is, and it answers the question a
            # reader building a POST by hand actually has.
            detail = f"{detail}; consistent with an empty body, which a GET has"
        fields.append(
            Field(f"{name}_digest", " ".join(str(byte) for byte in carried), KIND_DIGEST, detail)
        )
        candidate = candidates[name]
        if candidate is None:
            checks.append(Check(name, CHECK_NOT_SUPPLIED, _ABOGUS_CHAIN_BITS))
            continue
        digest = (
            abogus.user_agent_digest(candidate)
            if name == "user_agent"
            else abogus.digest_of(candidate)
        )
        checks.append(
            _check(
                name,
                abogus.chain_bytes(chain, digest),
                carried,
                bits=_ABOGUS_CHAIN_BITS,
                covered=candidate,
            )
        )

    return Decoded(
        parameter="a_bogus",
        platform="douyin",
        algorithm="a_bogus",
        recovered=True,
        fields=tuple(fields),
        checks=tuple(checks),
        notes=("checksum_verified", "noise_not_recoverable"),
    )


# --------------------------------------------------------------------------
# Douyin: X-Bogus
# --------------------------------------------------------------------------


def _xbogus_bytes(value: str) -> bytes:
    """Undo :meth:`XBogus.encode_group` over the whole string."""
    positions = {char: index for index, char in enumerate(xbogus.CHARACTER)}
    out = bytearray()
    for start in range(0, len(value), 4):
        group = value[start : start + 4]
        if len(group) != 4:
            raise ValueError("length is not a multiple of four")
        merged = 0
        for char in group:
            if char not in positions:
                raise ValueError(f"character {char!r} is not in the alphabet")
            merged = (merged << 6) | positions[char]
        out += bytes(((merged >> 16) & 255, (merged >> 8) & 255, merged & 255))
    return bytes(out)


def _xbogus_agent_digest(user_agent: str) -> list[int]:
    """The X-Bogus User-Agent chain, as a plain function of the string."""
    return xbogus.XBogus(user_agent).user_agent_digest()


def decode_x_bogus(
    value: str,
    *,
    query: str | None = None,
    user_agent: str | None = None,
) -> Decoded:
    """Take an ``X-Bogus`` apart.

    The payload survives the round trip intact: the SDK splits it even/odd and
    immediately reinterleaves it, which is a no-op except that it truncates the
    one fractional slot. So everything the generator put in comes back, and the
    only irreversible parts are the six digest bytes.
    """
    if value == tiktok_sign.BOGUS_VALUE:
        # Not a malformed signature - a parameter that is not one. TikTok Web
        # sends the literal `1` here on every HTTP request and computes a real
        # X-Bogus only for websocket handshakes, so a decoder that called this
        # broken would be reporting a fault in the platform's own page.
        return Decoded(
            parameter=tiktok_sign.BOGUS_PARAM,
            platform="tiktok",
            algorithm="constant",
            recovered=True,
            fields=(
                Field(
                    "value",
                    value,
                    KIND_PLAIN,
                    detail="the literal TikTok Web sends; it seals nothing",
                ),
            ),
            notes=("constant_placeholder",),
        )

    unread = Decoded(parameter="X-Bogus", platform="douyin", algorithm="X-Bogus", recovered=False)
    try:
        raw = _xbogus_bytes(value)
    except ValueError as error:
        return replace(unread, reason=f"{REASON_MALFORMED}:{error}")
    if len(raw) < 3 or tuple(raw[:2]) != xbogus.ENVELOPE_LEAD:
        return replace(unread, reason=f"{REASON_MALFORMED}:envelope lead")

    payload = bytes(xbogus.rc4_encrypt(xbogus.PAYLOAD_KEY, raw[2:]))
    if len(payload) <= xbogus.CHECKSUM_SLOT:
        return replace(unread, reason=f"{REASON_MALFORMED}:payload too short")

    lead = tuple(payload[: len(xbogus.PAYLOAD_LEAD)])
    if lead != xbogus.PAYLOAD_LEAD:
        return replace(unread, reason=f"{REASON_MALFORMED}:constant lead")

    def word(slots: Sequence[int]) -> int:
        return int.from_bytes(bytes(payload[slot] for slot in slots), "big")

    timer = word(xbogus.TIMER_SLOTS)
    constant = word(xbogus.CONSTANT_SLOTS)
    folded = 0
    for slot in range(xbogus.CHECKSUM_SLOT):
        folded ^= payload[slot]
    carried_checksum = payload[xbogus.CHECKSUM_SLOT]

    query_bytes = tuple(payload[slot] for slot in xbogus.QUERY_DIGEST_SLOTS)
    empty_bytes = tuple(payload[slot] for slot in xbogus.EMPTY_DIGEST_SLOTS)
    agent_bytes = tuple(payload[slot] for slot in xbogus.UA_DIGEST_SLOTS)
    expected_empty = tuple(xbogus.XBogus.empty_digest()[index] for index in xbogus.DIGEST_INDICES)

    fields = [
        Field("constant_lead", str(list(lead)), KIND_PLAIN, detail="[64, 1/256, 1, 12]"),
        _clock("timestamp", timer * 1000, detail="unix seconds; X-Bogus has no millisecond"),
        Field(
            "canvas_constant",
            str(constant),
            KIND_PLAIN,
            detail="matches" if constant == xbogus.CANVAS_CONSTANT else "differs from the shipped",
        ),
        Field(
            "query_digest",
            " ".join(str(byte) for byte in query_bytes),
            KIND_DIGEST,
            detail="bytes 14 and 15 of md5(md5(query)) twice over",
        ),
        Field(
            "empty_digest",
            " ".join(str(byte) for byte in empty_bytes),
            KIND_DIGEST,
            detail="constant" if empty_bytes == expected_empty else "unexpected",
        ),
        Field(
            "user_agent_digest",
            " ".join(str(byte) for byte in agent_bytes),
            KIND_DIGEST,
            detail="bytes 14 and 15 of md5(base64(rc4(ua)))",
        ),
        Field(
            "checksum",
            str(carried_checksum),
            KIND_CHECKSUM,
            detail="verified" if carried_checksum == folded else f"recomputed as {folded}",
        ),
    ]

    checks: list[Check] = []
    for name, candidate, carried, chain in (
        ("query", query, query_bytes, xbogus.XBogus.md5_encrypt),
        ("user_agent", user_agent, agent_bytes, _xbogus_agent_digest),
    ):
        if candidate is None:
            checks.append(Check(name, CHECK_NOT_SUPPLIED, 16))
            continue
        try:
            digest = chain(candidate)
        except ValueError:
            # The site's own `md5_str_to_array` reads anything 32 characters or
            # shorter as hex, so a candidate too short to have come from a
            # browser cannot be hashed at all. It is therefore not the input
            # that produced this signature, which is what `differs` says.
            checks.append(Check(name, CHECK_DIFFERS, 16))
            continue
        expected = tuple(digest[index] for index in xbogus.DIGEST_INDICES)
        checks.append(_check(name, expected, carried, bits=16, covered=candidate))

    return Decoded(
        parameter="X-Bogus",
        platform="douyin",
        algorithm="X-Bogus",
        recovered=True,
        fields=tuple(fields),
        checks=tuple(checks),
        notes=("superseded_by_a_bogus",),
    )


# --------------------------------------------------------------------------
# TikTok: the shared envelope
# --------------------------------------------------------------------------

#: X-Dynosaur's 25 fields, by the key the SDK tags them with. The three
#: ``reserved`` entries are ``"0"`` in every capture taken and are named rather
#: than explained, which is the honest state of them.
_DYNOSAUR_FIELDS: Final[Mapping[int, tuple[str, str]]] = {
    0x20: ("checksum", KIND_CHECKSUM),
    0x21: ("flag_1", KIND_ENVIRONMENT),
    0x22: ("flag_2", KIND_ENVIRONMENT),
    0x23: ("reserved_23", KIND_OPAQUE),
    0x24: ("mixed", KIND_PLAIN),
    0x25: ("call_sequence", KIND_PLAIN),
    0x26: ("env_code", KIND_ENVIRONMENT),
    0x27: ("timestamp", KIND_TIME),
    0x28: ("webgl_hash", KIND_ENVIRONMENT),
    0x29: ("reserved_29", KIND_OPAQUE),
    0x2A: ("sdk_version", KIND_PLAIN),
    0x2B: ("body_hash", KIND_DIGEST),
    0x2C: ("canvas_hash", KIND_ENVIRONMENT),
    0x2D: ("reserved_2d", KIND_OPAQUE),
    0x2E: ("query_hash", KIND_DIGEST),
    0x2F: ("call_sequence_2", KIND_PLAIN),
    0x30: ("user_agent_hash", KIND_DIGEST),
    0x31: ("scm_version", KIND_PLAIN),
    0x32: ("component_version", KIND_ENVIRONMENT),
    0x33: ("device_hash", KIND_ENVIRONMENT),
    0x34: ("nonce", KIND_PLAIN),
    0x35: ("page", KIND_PLAIN),
    0x36: ("ub_code", KIND_ENVIRONMENT),
    0x37: ("reserved_37", KIND_OPAQUE),
    0x38: ("vm_state_hash", KIND_DIGEST),
}

#: The four X-Dynosaur fields that are a raw 32-bit hash rather than a scrambled
#: string, and the three that use the second encoder.
_DYNOSAUR_HASHED: Final[frozenset[int]] = frozenset({0x2B, 0x2E, 0x30, 0x38})
_DYNOSAUR_ENCODER_B: Final[frozenset[int]] = frozenset({0x20, 0x21, 0x22})

#: X-Gnarly's 16 fields: slug, kind, and the width of the big-endian integer -
#: zero meaning the value is ASCII text.
_GNARLY_FIELDS: Final[Mapping[int, tuple[str, str, int]]] = {
    0x00: ("checksum_outer", KIND_CHECKSUM, 4),
    0x01: ("env_code", KIND_ENVIRONMENT, 2),
    0x02: ("ub_code", KIND_ENVIRONMENT, 2),
    0x03: ("query_md5", KIND_DIGEST, 0),
    0x04: ("body_md5", KIND_DIGEST, 0),
    0x05: ("user_agent_md5", KIND_DIGEST, 0),
    0x06: ("timestamp", KIND_TIME, 4),
    0x08: ("nonce", KIND_PLAIN, 4),
    0x09: ("sdk_version", KIND_PLAIN, 0),
    0x0A: ("scm_version", KIND_PLAIN, 0),
    0x0B: ("call_sequence_start", KIND_PLAIN, 2),
    0x0C: ("call_sequence", KIND_PLAIN, 2),
    0x0D: ("call_sequence_2", KIND_PLAIN, 2),
    0x0E: ("mixed", KIND_PLAIN, 4),
    0x0F: ("nonce_2", KIND_PLAIN, 4),
    0x10: ("checksum_inner", KIND_CHECKSUM, 4),
}


def _open_envelope(value: str) -> tuple[bytes, tuple[int, ...]] | str:
    """The plaintext payload, or a slug saying why there is none."""
    try:
        return tiktok_sign.unseal(value)
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        return f"{REASON_MALFORMED}:{error}"


def _dynosaur_text(key: int, raw: bytes) -> str:
    """One X-Dynosaur field as the SDK wrote it."""
    if key in _DYNOSAUR_HASHED:
        return str(int.from_bytes(raw, "big"))
    encoder = tiktok_sign.ENCODER_B if key in _DYNOSAUR_ENCODER_B else tiktok_sign.ENCODER_A
    if not raw:
        return ""
    return tiktok_sign.decode_field(raw, encoder)


def decode_x_dynosaur(
    value: str,
    *,
    query: str | None = None,
    body: str | None = None,
    user_agent: str | None = None,
) -> Decoded:
    """Take an ``X-Dynosaur`` apart: the environment report, in plaintext.

    Almost all of it is plaintext - version strings, counters, the page the SDK
    thought it was on. Four fields are a 32-bit FNV variant over something, and
    two of those four are the only reason this parameter is bound to a request
    at all.
    """
    unread = Decoded(
        parameter=tiktok_sign.DYNOSAUR_PARAM,
        platform="tiktok",
        algorithm=tiktok_sign.DYNOSAUR_PARAM,
        recovered=False,
    )
    opened = _open_envelope(value)
    if isinstance(opened, str):
        return replace(unread, reason=opened)
    payload, _key = opened
    raw_fields = tiktok_sign.unpack_payload(payload)
    if not raw_fields or not set(raw_fields) & set(_DYNOSAUR_FIELDS):
        return replace(unread, reason=f"{REASON_MALFORMED}:no X-Dynosaur fields")

    fields: list[Field] = []
    for key in sorted(raw_fields):
        name, kind = _DYNOSAUR_FIELDS.get(key, (f"field_{key:#04x}", KIND_OPAQUE))
        text = _dynosaur_text(key, raw_fields[key])
        if name == "timestamp" and text.isdigit():
            fields.append(_clock(name, int(text) * 1000, detail=f"key {key:#04x}"))
            continue
        detail = f"key {key:#04x}"
        if name == "body_hash" and text == str(tiktok_sign.hash_state("")):
            detail = f"{detail}; the empty body, which a GET has"
        fields.append(Field(name, text, kind, detail))

    checks: list[Check] = []
    for name, key, candidate in (
        ("query", 0x2E, query),
        ("user_agent", 0x30, user_agent),
        ("body", 0x2B, body),
    ):
        if candidate is None or key not in raw_fields:
            checks.append(Check(name, CHECK_NOT_SUPPLIED, 32))
            continue
        carried = int.from_bytes(raw_fields[key], "big")
        checks.append(
            _check(name, tiktok_sign.hash_state(candidate), carried, bits=32, covered=candidate)
        )

    return Decoded(
        parameter=tiktok_sign.DYNOSAUR_PARAM,
        platform="tiktok",
        algorithm=tiktok_sign.DYNOSAUR_PARAM,
        recovered=True,
        fields=tuple(fields),
        checks=tuple(checks),
        notes=("key_travels_in_the_envelope",),
    )


def _gnarly_text(raw: bytes, width: int) -> str:
    if width:
        return str(int.from_bytes(raw, "big"))
    return raw.decode("ascii", "replace")


def _gnarly_checksums(values: Mapping[str, Any]) -> tuple[int, int] | None:
    """Recompute both folds from the recovered fields, or None if one is missing.

    This is the check that proves a decode rather than merely producing one:
    every input to the fold is itself a field of the payload, so agreement means
    all sixteen came back correctly.
    """
    try:
        covered: list[int | str] = [
            0,
            values["env_code"],
            values["ub_code"],
            values["query_md5"],
            values["body_md5"],
            values["user_agent_md5"],
            values["timestamp"],
            0,
            values["nonce"],
            values["sdk_version"],
            values["scm_version"],
            values["call_sequence_start"],
            values["call_sequence"],
            values["call_sequence_2"],
            values["mixed"],
            values["nonce_2"],
        ]
    except KeyError:
        return None
    inner = tiktok_sign.fold_checksum(covered, 2)
    return inner, tiktok_sign.fold_checksum([*covered, inner], 1)


def decode_x_gnarly(
    value: str,
    *,
    query: str | None = None,
    body: bytes | None = None,
    user_agent: str | None = None,
) -> Decoded:
    """Take an ``X-Gnarly`` apart: the seal over the whole rewritten URL.

    ``query`` is the query as it stands *after* ``X-Dynosaur`` and ``msToken``
    have been appended and *before* ``X-Bogus`` - that is what the md5 covers,
    and getting that boundary wrong is the usual reason a hand-built request is
    refused. :func:`decode_url` works it out from a whole URL.
    """
    unread = Decoded(
        parameter=tiktok_sign.GNARLY_PARAM,
        platform="tiktok",
        algorithm=tiktok_sign.GNARLY_PARAM,
        recovered=False,
    )
    opened = _open_envelope(value)
    if isinstance(opened, str):
        return replace(unread, reason=opened)
    payload, _key = opened
    raw_fields = tiktok_sign.unpack_payload(payload, lead_count=True)
    if not raw_fields or not set(raw_fields) & set(_GNARLY_FIELDS):
        return replace(unread, reason=f"{REASON_MALFORMED}:no X-Gnarly fields")

    values: dict[str, Any] = {}
    fields: list[Field] = []
    for key in sorted(raw_fields):
        name, kind, width = _GNARLY_FIELDS.get(key, (f"field_{key:#04x}", KIND_OPAQUE, 0))
        text = _gnarly_text(raw_fields[key], width)
        values[name] = int(text) if width else text
        if name == "timestamp":
            fields.append(_clock(name, int(text) * 1000, detail=f"key {key:#04x}"))
            continue
        detail = f"key {key:#04x}"
        if name == "body_md5" and text == tiktok_sign.EMPTY_BODY_MD5:
            detail = "md5 of the empty body, which is what a GET has"
        fields.append(Field(name, text, kind, detail=detail))

    notes: list[str] = []
    folds = _gnarly_checksums(values)
    if folds is not None:
        inner, outer = folds
        agreed = values.get("checksum_inner") == inner and values.get("checksum_outer") == outer
        notes.append("checksum_verified" if agreed else "checksum_disagrees")
    mixed = values.get("mixed")
    if mixed is not None and "timestamp" in values and "nonce" in values:
        recomputed = tiktok_sign.mix_state(
            values["timestamp"], values["nonce"], values.get("env_code", tiktok_sign.ENV_CODE)
        )
        notes.append("mixed_verified" if recomputed == mixed else "mixed_disagrees")

    checks: list[Check] = []
    for name, slot, candidate in (
        ("query", "query_md5", query),
        ("user_agent", "user_agent_md5", user_agent),
    ):
        if candidate is None or slot not in values:
            checks.append(Check(name, CHECK_NOT_SUPPLIED, 128))
            continue
        digest = hashlib.md5(candidate.encode("utf-8")).hexdigest()
        checks.append(_check(name, digest, values[slot], bits=128, covered=candidate))
    if body is None or "body_md5" not in values:
        checks.append(Check("body", CHECK_NOT_SUPPLIED, 128))
    else:
        checks.append(
            _check(
                "body",
                hashlib.md5(body).hexdigest(),
                values["body_md5"],
                bits=128,
                covered=body.decode("utf-8", "replace"),
            )
        )

    return Decoded(
        parameter=tiktok_sign.GNARLY_PARAM,
        platform="tiktok",
        algorithm=tiktok_sign.GNARLY_PARAM,
        recovered=True,
        fields=tuple(fields),
        checks=tuple(checks),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# The parameters that are not arithmetic
# --------------------------------------------------------------------------


def decode_websign(
    value: str,
    *,
    query: str | None = None,
    uifid: str | None = None,
    stamp: str | None = None,
) -> Decoded:
    """Douyin's ``x-secsdk-web-signature``: an md5, so read forwards only.

    There is nothing inside a 128-bit digest to recover. What there is, given
    the visitor id and the timestamp that travel beside it in the same query, is
    a preimage that can be rebuilt and confirmed - and a confirmed preimage is
    worth more than a decode would be, because it is the exact string the reader
    has to reproduce to sign one themselves.
    """
    fields = [Field("digest", value, KIND_DIGEST, detail="md5, 128 bits")]
    checks: list[Check] = []
    if query is None or not uifid or stamp is None:
        checks.append(Check("query", CHECK_NOT_SUPPLIED, 128))
    else:
        preimage = f"{uifid}_{stamp}_{websign.SALT}_{query}"
        checks.append(
            _check(
                "query",
                hashlib.md5(preimage.encode()).hexdigest(),
                value,
                bits=128,
                covered=preimage,
            )
        )
        fields.append(Field("salt", websign.SALT, KIND_PLAIN))
    return Decoded(
        parameter=websign.SIGNATURE_PARAM,
        platform="douyin",
        algorithm="md5",
        recovered=False,
        reason=REASON_ONE_WAY,
        fields=tuple(fields),
        checks=tuple(checks),
    )


#: Lengths an ``msToken`` is seen at, and whose it is. Douyin's real tokens come
#: back at 120 or 128 characters counting the ``==`` padding; the locally minted
#: fallbacks are the two length constants plus that same padding.
_MS_TOKEN_LENGTHS: Final[Mapping[int, str]] = {
    **dict.fromkeys(tokens.DOUYIN_MS_TOKEN_SIZES, "douyin"),
    tokens.DOUYIN_MS_TOKEN_LENGTH + 2: "douyin",
    tokens.TIKTOK_MS_TOKEN_LENGTH + 2: "tiktok",
}


def decode_ms_token(value: str, *, platform: str | None = None) -> Decoded:
    """``msToken``: issued, not computed, so there is no plaintext under it.

    Reporting the shape is still worth doing, because it is the only thing about
    a token that is checkable offline and the faults it catches are real ones. A
    Douyin token pasted into a TikTok request is 20 characters short and looks
    exactly like a signing bug until somebody counts them; a token whose body
    carries an ``=`` somewhere other than the padding was not produced by either
    platform.
    """
    body = value.rstrip("=")
    padding = len(value) - len(body)
    alien = sorted(set(body) - set(tokens.MS_TOKEN_ALPHABET))
    return Decoded(
        parameter=tiktok_sign.MS_TOKEN_PARAM,
        platform=platform or _MS_TOKEN_LENGTHS.get(len(value)),
        algorithm="session token",
        recovered=False,
        reason=REASON_NOT_COMPUTED,
        fields=(
            Field(
                "length",
                str(len(value)),
                KIND_PLAIN,
                detail=_MS_TOKEN_LENGTHS.get(len(value), "no platform sends this length"),
            ),
            Field("padding", "=" * padding if padding else "none", KIND_PLAIN),
            Field(
                "alphabet",
                "matches" if not alien else "".join(alien),
                KIND_PLAIN,
                detail="A-Za-z0-9+- with `=` padding",
            ),
        ),
        notes=("issued_by_the_platform",),
    )


def decode_verify_fp(value: str, *, parameter: str = "verifyFp") -> Decoded:
    """``verifyFp`` / ``fp`` / ``s_v_web_id``: a mint time and a UUID skeleton.

    The tail is drawn from a random source and means nothing. The head is base36
    milliseconds, which does mean something: it says when the visitor token was
    minted, and a token minted long before the request carrying it is the shape
    of a jar that has been sitting in a file.
    """
    head, _, tail = value.partition("_")[2].partition("_")
    fields = [Field("prefix", value.split("_", 1)[0], KIND_PLAIN)]
    recovered = False
    try:
        minted = int(head, 36)
    except ValueError:
        fields.append(Field("minted", head, KIND_OPAQUE, detail="not base36"))
    else:
        recovered = True
        fields.append(_clock("minted", minted, detail="base36 milliseconds"))
    fields.append(Field("tail", tail, KIND_PLAIN, detail="uuid4 skeleton, drawn at random"))
    return Decoded(
        parameter=parameter,
        platform="douyin",
        algorithm="verify_fp",
        recovered=recovered,
        reason=None if recovered else f"{REASON_MALFORMED}:timestamp",
        fields=tuple(fields),
    )


# --------------------------------------------------------------------------
# Working out what a value is, and what it seals
# --------------------------------------------------------------------------

#: Parameter names this module knows, lowercased, mapped to the canonical
#: spelling. Matching case-insensitively is deliberate: people paste these out
#: of DevTools, where the header pane lowercases them.
PARAMETERS: Final[Mapping[str, str]] = {
    "a_bogus": "a_bogus",
    "x-bogus": "X-Bogus",
    "x-gnarly": tiktok_sign.GNARLY_PARAM,
    "x-dynosaur": tiktok_sign.DYNOSAUR_PARAM,
    "mstoken": tiktok_sign.MS_TOKEN_PARAM,
    websign.SIGNATURE_PARAM: websign.SIGNATURE_PARAM,
    "verifyfp": "verifyFp",
    "fp": "fp",
    "s_v_web_id": "s_v_web_id",
}

#: For each sealing parameter, the parameter its covered string stops before.
#: The asymmetry is the platform's: X-Gnarly seals X-Dynosaur and msToken but
#: not the X-Bogus that sits between them on the wire.
_COVERAGE_ENDS: Final[Mapping[str, str]] = {
    "a_bogus": "a_bogus",
    "X-Bogus": "X-Bogus",
    tiktok_sign.GNARLY_PARAM: tiktok_sign.BOGUS_PARAM,
    tiktok_sign.DYNOSAUR_PARAM: tiktok_sign.DYNOSAUR_PARAM,
    websign.SIGNATURE_PARAM: websign.SIGNATURE_PARAM,
}


def identify(value: str) -> str | None:
    """Which parameter a bare value is, by shape alone.

    Cheap tests first and the expensive structural one last. Nothing here is a
    guess: each test is a property no other parameter in the set has.
    """
    text = value.strip()
    if not text:
        return None
    if text.startswith("verify_"):
        return "verifyFp"
    if len(text) == 32 and all(char in "0123456789abcdef" for char in text.lower()):
        return websign.SIGNATURE_PARAM
    if len(text) == xbogus.X_BOGUS_LENGTH and set(text) <= set(xbogus.CHARACTER):
        return "X-Bogus"
    if abogus.structure_error(text) is None:
        return "a_bogus"
    opened = _open_envelope(text)
    if not isinstance(opened, str):
        # Both TikTok parameters share one envelope, so they are told apart by
        # the field table inside it. Each test demands a non-empty key set: a
        # payload parsed at the wrong offset yields no fields at all, and an
        # empty set is a subset of everything.
        payload, _key = opened
        for keys, table, name in (
            (tiktok_sign.unpack_payload(payload), _DYNOSAUR_FIELDS, tiktok_sign.DYNOSAUR_PARAM),
            (
                tiktok_sign.unpack_payload(payload, lead_count=True),
                _GNARLY_FIELDS,
                tiktok_sign.GNARLY_PARAM,
            ),
        ):
            if keys and set(keys) <= set(table):
                return name
    # `=` only ever appears as padding, so it is stripped before the alphabet
    # test rather than widening the alphabet - a token carrying one in the
    # middle is not a token, and that is worth still being able to say.
    if set(text.rstrip("=")) <= set(tokens.MS_TOKEN_ALPHABET) and len(text) >= 100:
        return tiktok_sign.MS_TOKEN_PARAM
    return None


def _covered(query: str, parameter: str) -> str | None:
    """The part of ``query`` that ``parameter`` seals, or None if it is absent."""
    stop = _COVERAGE_ENDS.get(parameter)
    if stop is None:
        return None
    head, separator, _ = query.partition(f"&{stop}=")
    return head if separator else None


def decode_parameter(
    parameter: str,
    value: str,
    *,
    query: str | None = None,
    user_agent: str | None = None,
    body: bytes | None = None,
    pairs: Mapping[str, str] | None = None,
) -> Decoded:
    """Decode one named parameter, with whatever context is available.

    ``body`` is left unset rather than defaulted to empty on purpose. Assuming a
    GET would make every ``body`` check pass without the caller having said
    anything, which is the one thing this module must not do - what a signature
    says about its own body is reported as a field instead.
    """
    canonical = PARAMETERS.get(parameter.lower(), parameter)
    if canonical == "a_bogus":
        return decode_a_bogus(
            value,
            query=query,
            body=None if body is None else body.decode("utf-8", "replace"),
            user_agent=user_agent,
        )
    if canonical == "X-Bogus":
        return decode_x_bogus(value, query=query, user_agent=user_agent)
    if canonical == tiktok_sign.GNARLY_PARAM:
        return decode_x_gnarly(value, query=query, body=body, user_agent=user_agent)
    if canonical == tiktok_sign.DYNOSAUR_PARAM:
        return decode_x_dynosaur(
            value,
            query=query,
            body=None if body is None else body.decode("utf-8", "replace"),
            user_agent=user_agent,
        )
    if canonical == tiktok_sign.MS_TOKEN_PARAM:
        return decode_ms_token(value)
    if canonical == websign.SIGNATURE_PARAM:
        found = pairs or {}
        return decode_websign(
            value,
            query=query,
            uifid=found.get(websign.UIFID_PARAM),
            stamp=found.get(websign.TIMESTAMP_PARAM),
        )
    if canonical in {"verifyFp", "fp", "s_v_web_id"}:
        return decode_verify_fp(value, parameter=canonical)
    return Decoded(
        parameter=parameter,
        platform=None,
        algorithm="",
        recovered=False,
        reason=REASON_UNKNOWN_PARAMETER,
    )


def decode_url(
    url: str, *, user_agent: str | None = None, body: bytes | None = None
) -> list[Decoded]:
    """Every signature parameter in a signed URL, each against what it seals.

    This is the form worth using. A parameter decoded on its own can report its
    contents but not whether it belongs to the request carrying it; decoded out
    of the URL it came from, the covered string is known exactly and every check
    can actually run.
    """
    query = url.partition("#")[0].partition("?")[2] or url
    pairs: dict[str, str] = {}
    order: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            continue
        name, _, raw = part.partition("=")
        pairs[name] = raw
        order.append((name, raw))

    out: list[Decoded] = []
    for name, raw in order:
        canonical = PARAMETERS.get(name.lower())
        if canonical is None:
            continue
        # a_bogus travels percent-encoded because its alphabet contains `/` and
        # `-` and its padding is a literal `=`. The others are sent raw. Rather
        # than encode that per parameter, take whichever form parses.
        value = raw
        if abogus.structure_error(value) is not None and "%" in raw:
            value = unquote(raw)
        out.append(
            decode_parameter(
                canonical,
                value,
                query=_covered(query, canonical),
                user_agent=user_agent,
                body=body,
                pairs=pairs,
            )
        )
    return out


__all__ = [
    "CHECK_DIFFERS",
    "CHECK_MATCH",
    "CHECK_NOT_SUPPLIED",
    "KIND_CHECKSUM",
    "KIND_DIGEST",
    "KIND_ENVIRONMENT",
    "KIND_OPAQUE",
    "KIND_PLAIN",
    "KIND_TIME",
    "PARAMETERS",
    "REASON_MALFORMED",
    "REASON_NOT_COMPUTED",
    "REASON_ONE_WAY",
    "REASON_UNKNOWN_PARAMETER",
    "Check",
    "Decoded",
    "Field",
    "decode_a_bogus",
    "decode_ms_token",
    "decode_parameter",
    "decode_url",
    "decode_verify_fp",
    "decode_websign",
    "decode_x_bogus",
    "decode_x_dynosaur",
    "decode_x_gnarly",
    "identify",
]
