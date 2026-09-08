"""Redaction, and the merge that makes a masked setting still editable.

Masking a credential on read is only half a feature: the console reads the whole
channel list and writes the whole list back, so every save carries masks for the
records nobody touched. What each mask stands for is decided here, and getting
it wrong is either silent credential loss or a way to aim a mask at a record of
the caller's choosing. Both happened; both are pinned below.
"""

from __future__ import annotations

import pytest

from dtk.core.errors import InvalidParam
from dtk.ops import masking

# --------------------------------------------------------------------------
# Which stored record a mask stands for
#
# The console reads the whole channel list and writes the whole list back, so
# every save carries masks for the records the operator did not touch. Deciding
# which stored record each mask means is the whole of this; getting it wrong is
# either silent credential loss or a way to aim a mask at a record of the
# caller's choosing.
# --------------------------------------------------------------------------


def _channels(*urls: tuple[str, str | None]) -> list[dict[str, object]]:
    return [
        {"type": "bark", "name": name, "url": f"https://api.day.app/{token}/"}
        for token, name in urls
    ]


def test_two_channels_with_the_same_name_keep_their_own_credentials() -> None:
    """Neither type nor name is unique, and pairing on them alone swapped these.

    Two DingTalk robots both called "ops" is an ordinary thing to have. Saved
    untouched, the second one used to end up pointing at the first one's
    webhook, with its own credential destroyed - no attacker, no warning.
    """
    stored = _channels(("AAAA", "ops"), ("BBBB", "ops"))

    merged = masking.unredact_setting(masking.redact_setting(stored), stored)

    assert [str(row["url"]).split("/")[-2] for row in merged] == ["AAAA", "BBBB"]


def test_a_channel_stored_without_a_name_can_still_be_saved() -> None:
    """build_channel treats name as optional, so stored rows may have none.

    Pairing that required a name refused the whole list, and because the page
    writes every channel on every save, one unnamed legacy row blocked every
    edit until its credential was retyped.
    """
    stored = _channels(("TOKEN1", None))

    merged = masking.unredact_setting(masking.redact_setting(stored), stored)

    assert merged[0]["url"] == stored[0]["url"]


def test_deleting_a_channel_does_not_wipe_the_ones_after_it() -> None:
    """Removing the first of three shifts the rest, so index alone is not enough."""
    stored = _channels(("AAA", "a"), ("BBB", "b"), ("CCC", "c"))
    masked = masking.redact_setting(stored)

    merged = masking.unredact_setting([masked[1], masked[2]], stored)

    assert [row["url"] for row in merged] == [stored[1]["url"], stored[2]["url"]]


def test_a_mask_cannot_be_aimed_at_another_record_by_renaming() -> None:
    """The reason the match-elsewhere rule needs both sides to be unique.

    A caller who can read the masked listing renames one record to another's
    name and submits both masks. Pairing on a stored-side unique match alone
    moved the first record's credential onto the second. The rename always
    leaves two records with one identity in what was submitted, which is what
    this refuses on.
    """
    stored = _channels(("AAA", "a"), ("BBB", "b"))
    masked = masking.redact_setting(stored)
    forged = [masked[0], {**masked[1], "name": "a"}]

    with pytest.raises(InvalidParam):
        masking.unredact_setting(forged, stored)
