"""Bilibili's two names for one video, and what an id may look like.

A video has an ``aid`` (the numeric "av" number) and a ``bvid`` (``BV1`` and
nine base-58 characters). They are the same number: the bvid is the aid
XOR-ed, re-based and two pairs of characters swapped, so converting needs no
request. This project keys a video by its bvid - it is what every link shows
and what the detail endpoint takes - and derives the aid where an endpoint
wants it, which the comment endpoints do (``oid``).

The constants are the 2024 revision of the scheme, which widened the aid
range to 51 bits when av numbers outgrew 32. Checked on 2026-10-18 against
``BV1xx411c7mD`` = av2, ``BV17x411w7KC`` = av170001, and the 15-digit aids
current uploads carry.
"""

from __future__ import annotations

import re
from typing import Final

from dtk.core.errors import InvalidParam

XOR_CODE: Final = 23442827791579
MASK_CODE: Final = 2251799813685247
MAX_AID: Final = 1 << 51
ALPHABET: Final = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
BASE: Final = len(ALPHABET)

#: ``BV1`` and nine characters of the alphabet above. Case matters: the
#: alphabet uses both, and ``bv1...`` is not a video.
BVID_PATTERN: Final = re.compile(rf"BV1[{ALPHABET}]{{9}}")
#: An av number written as a link writes it: ``av170001``, any case.
AVID_PATTERN: Final = re.compile(r"[aA][vV](\d{1,16})")
#: Numeric ids - an author's ``mid``, a comment's ``rpid``, a folder's id.
NUMERIC_ID: Final = re.compile(r"\d{1,20}")


def av_to_bv(aid: int) -> str:
    if not 0 < aid < MAX_AID:
        raise ValueError(f"aid {aid} is outside the range a bvid can encode")
    out = list("BV1000000000")
    index = len(out) - 1
    value = (MAX_AID | aid) ^ XOR_CODE
    while value > 0:
        out[index] = ALPHABET[value % BASE]
        value //= BASE
        index -= 1
    out[3], out[9] = out[9], out[3]
    out[4], out[7] = out[7], out[4]
    return "".join(out)


def bv_to_av(bvid: str) -> int:
    if not BVID_PATTERN.fullmatch(bvid):
        raise ValueError(f"{bvid!r} is not a bvid")
    chars = list(bvid)
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    value = 0
    for char in chars[3:]:
        value = value * BASE + ALPHABET.index(char)
    return (value & MASK_CODE) ^ XOR_CODE


def content_bvid(value: object, *, field: str = "content_id") -> str:
    """A caller's video id as a bvid: ``BV1...``, ``av170001`` or a bare aid.

    Refused here, naming the field, rather than sent upstream: Bilibili answers
    a malformed id with ``-400`` and a well-formed one that does not exist with
    ``-404``, and only the second is about the video.
    """
    text = str(value).strip()
    if BVID_PATTERN.fullmatch(text):
        return text
    avid = AVID_PATTERN.fullmatch(text)
    digits = avid.group(1) if avid else text if NUMERIC_ID.fullmatch(text) else None
    if digits is not None:
        try:
            return av_to_bv(int(digits))
        except ValueError:
            pass
    raise InvalidParam(
        f"{field} must be a Bilibili video id such as BV1xx411c7mD or av170001",
        details={"field": field, "reason": "malformed_id"},
    )


def numeric_id(value: object, *, field: str) -> str:
    """An author, comment or folder id: digits, as a string."""
    text = str(value).strip()
    if not NUMERIC_ID.fullmatch(text) or int(text) == 0:
        raise InvalidParam(
            f"{field} must be a numeric Bilibili id",
            details={"field": field, "reason": "malformed_id"},
        )
    return text


__all__ = [
    "BVID_PATTERN",
    "av_to_bv",
    "bv_to_av",
    "content_bvid",
    "numeric_id",
]
