"""Post ids, and telling a typo from a post that is merely gone.

The ids here are real ones from this instance's archive, kept because the
module's whole claim - that the high 32 bits are a Unix timestamp - is an
empirical one, and a synthetic id would let a wrong claim pass.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dtk.core.errors import DtkError, ErrorCode
from dtk.core.types import Platform
from dtk.urls.ids import EPOCH_FLOOR, MAX_ID, read_content_id, require_content_id

#: Real ids, with the publication time this instance archived for each. The
#: embedded time is at or before it - see the module docstring for why.
SAMPLES = (
    ("7681472434821696781", datetime(2026, 9, 4, 0, 36, 26, tzinfo=UTC)),
    ("7675184883987123469", datetime(2026, 8, 18, 1, 57, 40, tzinfo=UTC)),
    ("7516512304650964224", datetime(2025, 6, 16, 11, 46, 44, tzinfo=UTC)),
    ("7516101357724945699", datetime(2025, 6, 15, 9, 12, 0, tzinfo=UTC)),
    ("7667226065140221035", datetime(2026, 7, 27, 15, 13, 0, tzinfo=UTC)),
)


class TestRealIds:
    @pytest.mark.parametrize(("value", "published"), SAMPLES)
    def test_a_real_id_carries_a_time_at_or_before_publication(
        self, value: str, published: datetime
    ) -> None:
        parsed = read_content_id(value)
        assert parsed is not None
        assert parsed.minted_at <= published
        # Not the publication time and never claimed to be - but within two
        # days of it, which is what makes it a usable sanity check. The widest
        # gap measured across 66 archived posts was 37 hours, on Douyin.
        assert published - parsed.minted_at < timedelta(days=2)

    def test_surrounding_whitespace_is_ignored(self) -> None:
        """People paste ids out of spreadsheets and share sheets."""
        assert read_content_id("  7667226065140221035\n") is not None

    def test_the_value_is_returned_as_text(self) -> None:
        """A 19-digit id exceeds the JavaScript safe range; it never becomes a number."""
        parsed = read_content_id("7667226065140221035")
        assert parsed is not None
        assert parsed.value == "7667226065140221035"
        assert isinstance(parsed.value, str)


class TestRejections:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "abc",
            "not an id",
            "71234567890123456xx",
            # A leading sign parses under int() and is not an id.
            "-7667226065140221035",
            "+7667226065140221035",
            # Full-width digits, which a Chinese IME produces and int() accepts.
            # Written as escapes so the repository itself stays ASCII.
            "\uff17\uff11\uff12\uff13",
            # Too small: the embedded time is 1970.
            "123",
            "7123",
            # Too large: a random 19-digit number decodes to the 2040s.
            "9999999999999999999",
            # Wider than 64 bits.
            str(MAX_ID + 1),
            "0",
        ],
    )
    def test_what_cannot_be_an_id(self, value: str) -> None:
        assert read_content_id(value) is None

    def test_an_id_from_the_future_is_refused(self) -> None:
        """The clock that minted it is not this one, but it is not years ahead."""
        ahead = int((datetime.now(UTC) + timedelta(days=1)).timestamp()) << 32
        assert read_content_id(str(ahead)) is None

    def test_an_id_from_before_the_platforms_existed_is_refused(self) -> None:
        before = int((EPOCH_FLOOR - timedelta(days=1)).timestamp()) << 32
        assert read_content_id(str(before)) is None

    def test_a_few_minutes_of_clock_skew_is_allowed(self) -> None:
        """Refusing a post published one second ago would be worse than useless."""
        just_now = int((datetime.now(UTC) - timedelta(seconds=5)).timestamp()) << 32
        assert read_content_id(str(just_now)) is not None


class TestRequire:
    def test_a_bad_id_is_the_callers_mistake(self) -> None:
        with pytest.raises(DtkError) as caught:
            require_content_id("not an id")
        assert caught.value.code is ErrorCode.INVALID_PARAM
        assert caught.value.details["field"] == "aweme_id"

    def test_the_platform_is_named_when_known(self) -> None:
        with pytest.raises(DtkError) as caught:
            require_content_id("7123", platform=Platform.DOUYIN)
        assert caught.value.details["platform"] == "douyin"

    def test_a_good_id_comes_back_trimmed(self) -> None:
        assert require_content_id(" 7667226065140221035 ") == "7667226065140221035"


def test_a_well_formed_id_for_a_post_that_does_not_exist_still_passes() -> None:
    """The limit of what this can do, stated as a test.

    7123456789012345678 decodes to 2022-07-23 and is a perfectly plausible id.
    It is also the id that produced the bug report this module came from, and it
    gets through here - only the platform knows whether a post exists, and it
    says so plainly when asked. The two halves of that fix are independent:
    this refuses what cannot be an id, and dtk.transport.classify stops reading
    the platform's "no such post" as risk control.
    """
    assert read_content_id("7123456789012345678") is not None
