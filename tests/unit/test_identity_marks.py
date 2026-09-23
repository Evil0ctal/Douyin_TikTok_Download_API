"""Per-platform refill thresholds, and how they fall back to the global pair."""

from __future__ import annotations

import pytest

from dtk.core.config import RUNTIME_SETTINGS, Config, coerce
from dtk.core.types import Platform
from dtk.identity.marks import INHERIT, pool_marks, strictest_min_size


def test_every_platform_has_both_overrides_defaulting_to_inherit() -> None:
    for platform in Platform:
        for field in ("min_size", "target_size"):
            assert RUNTIME_SETTINGS[f"pool.{platform.value}.{field}"].default == INHERIT


def test_with_no_override_a_platform_follows_the_global_pair() -> None:
    marks = pool_marks(Config({"pool.min_size": 4, "pool.target_size": 9}), Platform.TIKTOK)

    assert (marks.min_size, marks.target_size) == (4, 9)
    assert marks.min_inherited and marks.target_inherited
    assert marks.auto


def test_an_override_applies_to_its_own_platform_only() -> None:
    config = Config({"pool.tiktok.min_size": 5, "pool.tiktok.target_size": 10})

    tiktok = pool_marks(config, Platform.TIKTOK)
    douyin = pool_marks(config, Platform.DOUYIN)

    assert (tiktok.min_size, tiktok.target_size) == (5, 10)
    assert not tiktok.min_inherited and not tiktok.target_inherited
    assert (douyin.min_size, douyin.target_size) == (3, 8)


def test_each_number_inherits_separately() -> None:
    marks = pool_marks(Config({"pool.douyin.target_size": 12}), Platform.DOUYIN)

    assert (marks.min_size, marks.target_size) == (3, 12)
    assert marks.min_inherited and not marks.target_inherited


def test_a_zero_mark_turns_automatic_minting_off() -> None:
    marks = pool_marks(Config({"pool.tiktok.min_size": 0}), Platform.TIKTOK)

    assert marks.auto is False
    # The target is left alone: switching back on restores what was there.
    assert marks.target_size == 8


def test_a_target_under_the_mark_is_raised_to_it() -> None:
    marks = pool_marks(
        Config({"pool.douyin.min_size": 6, "pool.douyin.target_size": 2}), Platform.DOUYIN
    )

    assert marks.target_size == 6


@pytest.mark.parametrize("value", [-1, 0, 7])
def test_an_override_accepts_inherit_and_counts(value: int) -> None:
    assert coerce("pool.tiktok.min_size", value) == value


def test_an_override_below_inherit_is_refused_rather_than_read_as_inherit() -> None:
    with pytest.raises(ValueError, match="-1"):
        coerce("pool.tiktok.min_size", -3)


def test_the_diagnostic_is_held_to_the_strictest_platform() -> None:
    config = Config({"pool.tiktok.min_size": 0, "pool.douyin.min_size": 5})

    assert strictest_min_size(config) == 5
    assert strictest_min_size(Config({})) == 3
