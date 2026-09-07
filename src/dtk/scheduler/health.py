"""Identity health scoring.

The three factors multiply rather than sum. They are not commensurable: the
first two are rates in [0,1] while the failure streak is an unbounded count, so
in a weighted sum any non-trivial weight on the streak lets it dominate, and a
contradictory state ("100% success rate, but just failed three times in a row")
averages out instead of registering.

Multiplying means any one factor collapsing drags the whole score down, which
matches intuition: an identity that was just rate limited should not be picked
first no matter how good its history looks.

See docs/design/02-identity-pool.md.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Below this many observations the window is not informative, so a prior is
#: used instead. A freshly minted identity should be neither trusted like a
#: proven one nor buried at the bottom of the queue for having no history.
MIN_SAMPLES = 5
DEFAULT_PRIOR = 0.8

#: Each consecutive failure halves the score.
STREAK_BASE = 0.5

#: Number of health tiers used for ordering. Ordering by the exact score makes
#: the single healthiest identity win every time, creating a hot spot; bucketing
#: lets least-recently-used rotation operate inside a tier, which is what
#: "take turns" actually means.
HEALTH_BUCKETS = 5


@dataclass(frozen=True, slots=True)
class HealthInput:
    total_15m: int
    ok_15m: int
    total_60m: int
    risk_60m: int
    consecutive_fails: int


def score(data: HealthInput, *, prior: float = DEFAULT_PRIOR) -> float:
    """Return a health score in ``[0, 1]``."""
    success_rate = data.ok_15m / data.total_15m if data.total_15m >= MIN_SAMPLES else prior

    risk_rate = data.risk_60m / data.total_60m if data.total_60m else 0.0
    streak_penalty = STREAK_BASE ** max(0, data.consecutive_fails)

    return max(0.0, min(1.0, success_rate * (1.0 - risk_rate) * streak_penalty))


def bucket(health: float, buckets: int = HEALTH_BUCKETS) -> int:
    """Map a score onto a coarse tier; higher is healthier."""
    if health >= 1.0:
        return buckets - 1
    if health <= 0.0:
        return 0
    return min(buckets - 1, int(health * buckets))


def cooldown_seconds(
    consecutive_risk_hits: int,
    *,
    base: int,
    maximum: int,
    risk_weight: float = 1.0,
) -> int:
    """Exponential backoff after a risk-control hit."""
    exponent = max(0, consecutive_risk_hits - 1)
    # Cap the exponent before the shift so a long streak cannot overflow.
    exponent = min(exponent, 32)
    raw = base * (2**exponent) * risk_weight
    return int(min(raw, maximum))


__all__ = [
    "DEFAULT_PRIOR",
    "HEALTH_BUCKETS",
    "MIN_SAMPLES",
    "STREAK_BASE",
    "HealthInput",
    "bucket",
    "cooldown_seconds",
    "score",
]
