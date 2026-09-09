"""Reading a post id, and deciding whether it can be one at all.

Both platforms mint post ids as Snowflakes: a 64-bit integer whose high 32 bits
are a Unix timestamp in seconds. That makes an id self-describing - it carries
the moment it was issued - and it makes garbage cheap to recognise, which is
the point of this module. A request for a post that cannot exist should cost
nothing: no identity, no upstream call, no entry in the endpoint's risk rate.

**Measured, not assumed.** Against this instance's own archive on 2026-09-09,
66 posts with a known publication time:

    tiktok  20 rows, all 20 within 5 minutes of `id >> 32`
    douyin  46 rows, 23 within 5 minutes; the rest up to 37 hours later

Every delta was in the same direction - the embedded time is at or before the
publication time - which is what you would expect if the id is issued when the
upload begins and Douyin lets people schedule the post. So the embedded value
is not the publication time and this module never claims it is. It is the
moment the id was minted, and that is enough to tell a real id from a typo.

What this catches: text that is not a number, a number too small or too large
to be a Snowflake, and anything whose embedded time lands outside the window in
which these platforms have existed. What it does not catch, and cannot: a
well-formed id for a post that never existed or has since been deleted. Only
the platform knows that, and it answers plainly when asked
(:mod:`dtk.transport.classify`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from dtk.core.errors import InvalidParam
from dtk.core.types import Platform

#: Where the timestamp sits. The low 32 bits are the sequence and shard fields,
#: which vary per platform and are of no use here.
TIMESTAMP_SHIFT: Final = 32

#: The earliest an id can plausibly have been minted. Douyin launched in
#: September 2016 and musical.ly, which became TikTok, in 2014 - but the id
#: scheme in use here is the post-merge one, and no id this system has ever seen
#: predates 2016. Deliberately loose: this is a sanity floor, not a history
#: lesson, and a floor set too tight rejects real archive material.
EPOCH_FLOOR: Final = datetime(2016, 1, 1, tzinfo=UTC)

#: How far into the future an id may be stamped. Not zero, because the clock
#: that minted it is not this one; not generous, because a year-2043 timestamp
#: is what a random 19-digit number decodes to and is exactly what should be
#: refused.
FUTURE_SKEW: Final = timedelta(minutes=10)

#: A Snowflake is unsigned 64-bit. Anything wider is not one.
MAX_ID: Final = (1 << 64) - 1

#: Ids are decimal text, never an integer, on the wire and in storage: a
#: 19-digit id exceeds the JavaScript safe range and a console that parsed it as
#: a number would round it. This module takes text for the same reason.
MAX_DIGITS: Final = 20


@dataclass(frozen=True, slots=True)
class ContentId:
    """A post id that survived parsing, with the time it carries."""

    value: str
    minted_at: datetime

    @property
    def as_int(self) -> int:
        return int(self.value)


def read_content_id(value: str) -> ContentId | None:
    """Parse a post id, or return None if it cannot be one.

    None rather than an exception, because two callers want opposite things: a
    validator wants to refuse, and a batch tool wants to report every line
    including the bad ones.
    """
    text = value.strip()
    if not text or not text.isdigit() or len(text) > MAX_DIGITS:
        # `isdigit` also rejects a leading sign, whitespace inside, and the
        # full-width digits a Chinese IME produces - all of which would parse
        # under `int()` and none of which the platform would accept.
        return None
    number = int(text)
    if number <= 0 or number > MAX_ID:
        return None
    seconds = number >> TIMESTAMP_SHIFT
    try:
        minted_at = datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if minted_at < EPOCH_FLOOR or minted_at > datetime.now(UTC) + FUTURE_SKEW:
        return None
    return ContentId(value=text, minted_at=minted_at)


def require_content_id(
    value: str, *, field: str = "aweme_id", platform: Platform | None = None
) -> str:
    """Return the id, or refuse the request before it costs anything.

    Raised at the boundary rather than discovered upstream: a malformed id is
    the caller's mistake, and spending a pooled identity to have the platform
    say so charges the operator for it - and, until the classifier was fixed,
    cooled that identity too.
    """
    parsed = read_content_id(value)
    if parsed is None:
        raise InvalidParam(
            f"{field} is not a valid post id",
            details={
                "field": field,
                **({"platform": platform.value} if platform else {}),
                # Named so the caller can tell "you typed a word" from "you
                # typed a number that cannot be an id".
                "expected": (
                    "a decimal post id whose embedded timestamp falls between "
                    f"{EPOCH_FLOOR.date().isoformat()} and now"
                ),
            },
        )
    return parsed.value


__all__ = [
    "EPOCH_FLOOR",
    "FUTURE_SKEW",
    "MAX_DIGITS",
    "MAX_ID",
    "TIMESTAMP_SHIFT",
    "ContentId",
    "read_content_id",
    "require_content_id",
]
