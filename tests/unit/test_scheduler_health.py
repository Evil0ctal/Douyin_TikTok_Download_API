"""Health scoring and backoff. Pure functions, no services required."""

from __future__ import annotations

import pytest

from dtk.scheduler.health import (
    DEFAULT_PRIOR,
    HEALTH_BUCKETS,
    MIN_SAMPLES,
    HealthInput,
    bucket,
    cooldown_seconds,
    score,
)


def h(total_15m=0, ok_15m=0, total_60m=0, risk_60m=0, consecutive_fails=0):
    return HealthInput(total_15m, ok_15m, total_60m, risk_60m, consecutive_fails)


def test_cold_start_uses_prior_not_zero():
    """A fresh identity must be neither trusted nor buried."""
    assert score(h()) == pytest.approx(DEFAULT_PRIOR)
    assert score(h(total_15m=MIN_SAMPLES - 1, ok_15m=0)) == pytest.approx(DEFAULT_PRIOR)


def test_sample_threshold_switches_to_observed_rate():
    assert score(h(total_15m=MIN_SAMPLES, ok_15m=MIN_SAMPLES)) == pytest.approx(1.0)
    assert score(h(total_15m=MIN_SAMPLES, ok_15m=0)) == pytest.approx(0.0)


def test_failure_streak_dominates_a_perfect_history():
    """The reason the factors multiply instead of summing.

    An identity with a flawless success rate that just failed three times in a
    row must not be picked first. Under a weighted sum it still would be.
    """
    perfect = score(h(total_15m=20, ok_15m=20, total_60m=60))
    just_failed = score(h(total_15m=20, ok_15m=20, total_60m=60, consecutive_fails=3))
    assert perfect == pytest.approx(1.0)
    assert just_failed == pytest.approx(0.125)
    assert bucket(just_failed) == 0
    assert bucket(perfect) == HEALTH_BUCKETS - 1


def test_risk_rate_reduces_score_proportionally():
    assert score(h(total_15m=20, ok_15m=20, total_60m=100, risk_60m=50)) == pytest.approx(0.5)


def test_score_is_clamped():
    for case in [h(), h(20, 20, 60, 0), h(20, 0, 60, 60, 99)]:
        assert 0.0 <= score(case) <= 1.0


@pytest.mark.parametrize("streak,expected", [(1, 60), (2, 120), (3, 240), (8, 7680)])
def test_backoff_doubles(streak, expected):
    assert cooldown_seconds(streak, base=60, maximum=21600) == expected


def test_backoff_is_capped_and_cannot_overflow():
    assert cooldown_seconds(999, base=60, maximum=21600) == 21600
    assert cooldown_seconds(64, base=60, maximum=21600) == 21600


def test_risk_weight_lengthens_backoff():
    plain = cooldown_seconds(2, base=60, maximum=21600, risk_weight=1.0)
    weighted = cooldown_seconds(2, base=60, maximum=21600, risk_weight=1.8)
    assert weighted > plain


def test_bucket_boundaries():
    assert bucket(0.0) == 0
    assert bucket(1.0) == HEALTH_BUCKETS - 1
    assert bucket(0.999) == HEALTH_BUCKETS - 1
    assert 0 <= bucket(0.5) < HEALTH_BUCKETS
