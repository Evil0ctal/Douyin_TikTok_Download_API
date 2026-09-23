"""The refill thresholds one platform's pool is held to.

``pool.min_size`` and ``pool.target_size`` used to be the whole story, shared by
every platform. That made a deployment that only serves one platform pay for
the other: an instance in mainland China that never touches TikTok still had
the filler minting TikTok identities every minute, failing every time, and
raising ``pool_empty`` for a pool nobody wanted (issue #763).

So each platform may override either number, and the global pair stays as the
default. The override is ``-1`` rather than absent because a runtime setting is
a typed value with a default, and "inherit" has to be a value of that type.
Setting a platform to ``0`` is how its automatic minting is turned off: nothing
is ever below a low-water mark of zero.

The worker, the admin API and the diagnostic all read the marks through
:func:`pool_marks`, so the clamping and the inheritance live in one place and
the console cannot show a threshold the filler is not using.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from dtk.core.config import Config
from dtk.core.types import Platform

#: A per-platform value that defers to the global setting.
INHERIT: Final = -1


def min_size_key(platform: Platform) -> str:
    return f"pool.{platform.value}.min_size"


def target_size_key(platform: Platform) -> str:
    return f"pool.{platform.value}.target_size"


@dataclass(frozen=True, slots=True)
class PoolMarks:
    """Where one platform's pool starts refilling and where it stops."""

    min_size: int
    target_size: int
    #: Whether each number came from the global setting rather than an override.
    min_inherited: bool
    target_inherited: bool

    @property
    def auto(self) -> bool:
        """False when this platform is never minted for automatically.

        Also the switch for its low-level alerts: an operator who set the mark
        to zero has said an empty pool is what they want, and paging them about
        it every hour is the noise the per-platform marks exist to remove.
        """
        return self.min_size > 0


def pool_marks(config: Config, platform: Platform) -> PoolMarks:
    """Resolve one platform's marks, falling back to the global pair."""
    own_min = int(config.get(min_size_key(platform)))
    own_target = int(config.get(target_size_key(platform)))
    min_size = max(0, own_min if own_min != INHERIT else int(config.get("pool.min_size")))
    target = own_target if own_target != INHERIT else int(config.get("pool.target_size"))
    return PoolMarks(
        min_size=min_size,
        # A target under the mark would top the pool up to less than the filler
        # just decided was too few, so it is clamped rather than obeyed.
        target_size=max(min_size, target),
        min_inherited=own_min == INHERIT,
        target_inherited=own_target == INHERIT,
    )


def strictest_min_size(config: Config) -> int:
    """The highest low-water mark any platform is held to.

    For the diagnostic, which counts the whole pool rather than one platform.
    Taking the global ``pool.min_size`` there would hold a Douyin-only
    deployment to a mark it had lowered, or miss one it had raised.
    """
    return max(pool_marks(config, platform).min_size for platform in Platform)


__all__ = [
    "INHERIT",
    "PoolMarks",
    "min_size_key",
    "pool_marks",
    "strictest_min_size",
    "target_size_key",
]
