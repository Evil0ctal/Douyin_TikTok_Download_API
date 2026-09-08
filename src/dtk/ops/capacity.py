"""Watching the disk, so an unattended instance degrades instead of dying.

Everything added around the content archive - archiving on every parse, and
later scheduled collection and downloaded media - shares one property the rest
of this system did not have: it writes unboundedly, without anyone asking it to,
for as long as the instance is running. That is exactly the thing a self-hoster
leaves overnight.

The guard is deliberately NOT a retention policy. Nothing here deletes anything.
It measures, it warns, and past a threshold it stops the writers that nobody is
waiting on - background collection and new download jobs - while leaving
interactive reads untouched. Turning a full disk into "my API is down" would be
a worse outage than the one being prevented, and deleting a user's archive to
free space would be worse than either: the archive exists precisely to outlive
the platform.

What "full" means is measured on the filesystem, not inferred from row counts.
``shutil.disk_usage`` on the database and media paths is the only number that
accounts for WAL, indexes, chunk overhead, other containers and whatever else
shares the volume - none of which a SUM over table sizes can see.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from dtk.core.logging import get_logger

log = get_logger(__name__)

#: Where to measure. Both are the container's own view: the database volume as
#: the API and worker see it, and the media volume the downloader writes to.
#: A path that does not exist is skipped rather than treated as full - an
#: instance without the downloader has no media volume, and that is not a fault.
DB_PATH: Final = "/var/lib/dtk"
MEDIA_PATH: Final = "/var/lib/dtk/media"


class CapacityState(StrEnum):
    """How much room is left, as three states rather than a percentage.

    Three, because there are exactly three behaviours: carry on, tell someone,
    and stop the writers nobody is waiting on.
    """

    OK = "ok"
    WARN = "warn"
    #: Background writers pause. Interactive reads never do.
    FULL = "full"


@dataclass(frozen=True, slots=True)
class VolumeUsage:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def used_percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return round(self.used_bytes / self.total_bytes * 100, 1)


@dataclass(frozen=True, slots=True)
class CapacityReport:
    state: CapacityState
    volumes: tuple[VolumeUsage, ...]
    #: Which volume drove the verdict, for the message an operator reads.
    worst_path: str | None = None
    worst_percent: float = 0.0
    detail: str = ""

    @property
    def paused(self) -> bool:
        """Whether background writers should stand down this tick."""
        return self.state is CapacityState.FULL

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "paused": self.paused,
            "worst_path": self.worst_path,
            "worst_percent": self.worst_percent,
            "detail": self.detail,
            "volumes": [
                {
                    "path": volume.path,
                    "total_bytes": volume.total_bytes,
                    "used_bytes": volume.used_bytes,
                    "free_bytes": volume.free_bytes,
                    "used_percent": volume.used_percent,
                }
                for volume in self.volumes
            ],
        }


def measure(paths: tuple[str, ...] = (DB_PATH, MEDIA_PATH)) -> tuple[VolumeUsage, ...]:
    """Free space per volume, skipping paths this deployment does not have.

    Two paths on the same filesystem report the same numbers, which is correct:
    they are competing for the same space, and pretending otherwise is how a
    guard passes while the disk fills.
    """
    seen: dict[tuple[int, int], VolumeUsage] = {}
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        try:
            usage = shutil.disk_usage(path)
            stat = path.stat()
        except OSError as exc:
            log.warning("ops.capacity.unreadable", path=raw, error=str(exc))
            continue
        # Deduplicate by device, so one filesystem mounted at two paths is not
        # counted twice in the worst-volume calculation.
        key = (stat.st_dev, usage.total)
        if key not in seen:
            seen[key] = VolumeUsage(
                path=raw, total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free
            )
    return tuple(seen.values())


def evaluate(
    *,
    warn_percent: float,
    hard_stop_percent: float,
    paths: tuple[str, ...] = (DB_PATH, MEDIA_PATH),
) -> CapacityReport:
    """Decide the state from the fullest volume this instance can see.

    The fullest, not the average: the disk that fills first is the one that
    breaks things, and averaging it against an empty second volume is how a
    guard reports healthy while Postgres is failing to write.
    """
    volumes = measure(paths)
    if not volumes:
        # Nothing measurable - a test environment, or a path layout this
        # deployment does not use. Not an emergency, and not a licence to claim
        # everything is fine either.
        return CapacityReport(state=CapacityState.OK, volumes=(), detail="no measurable volume")

    worst = max(volumes, key=lambda volume: volume.used_percent)
    if worst.used_percent >= hard_stop_percent:
        state = CapacityState.FULL
    elif worst.used_percent >= warn_percent:
        state = CapacityState.WARN
    else:
        state = CapacityState.OK
    return CapacityReport(
        state=state,
        volumes=volumes,
        worst_path=worst.path,
        worst_percent=worst.used_percent,
        detail=f"{worst.path} is {worst.used_percent}% full",
    )


__all__ = [
    "DB_PATH",
    "MEDIA_PATH",
    "CapacityReport",
    "CapacityState",
    "VolumeUsage",
    "evaluate",
    "measure",
]
