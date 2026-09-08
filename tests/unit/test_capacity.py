"""The disk guard.

It exists so an unattended instance degrades instead of dying, which puts two
properties above everything else: it never deletes, and it never pauses the
interactive reads a person is waiting on.
"""

from __future__ import annotations

import pytest

from dtk.ops import capacity


def _volume(path: str, used_percent: float) -> capacity.VolumeUsage:
    total = 1_000_000_000
    used = int(total * used_percent / 100)
    return capacity.VolumeUsage(
        path=path, total_bytes=total, used_bytes=used, free_bytes=total - used
    )


@pytest.mark.parametrize(
    ("used_percent", "expected"),
    [
        (10.0, capacity.CapacityState.OK),
        (79.9, capacity.CapacityState.OK),
        (80.0, capacity.CapacityState.WARN),
        (91.9, capacity.CapacityState.WARN),
        (92.0, capacity.CapacityState.FULL),
        (99.9, capacity.CapacityState.FULL),
    ],
)
def test_thresholds_are_inclusive(
    used_percent: float, expected: capacity.CapacityState, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capacity, "measure", lambda paths=(): (_volume("/db", used_percent),))
    verdict = capacity.evaluate(warn_percent=80, hard_stop_percent=92)
    assert verdict.state is expected


def test_the_fullest_volume_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Averaging is how a guard reports healthy while Postgres cannot write."""
    monkeypatch.setattr(
        capacity, "measure", lambda paths=(): (_volume("/db", 95.0), _volume("/media", 5.0))
    )
    verdict = capacity.evaluate(warn_percent=80, hard_stop_percent=92)

    assert verdict.state is capacity.CapacityState.FULL
    assert verdict.worst_path == "/db"


def test_only_the_full_state_pauses_writers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capacity, "measure", lambda paths=(): (_volume("/db", 85.0),))
    assert capacity.evaluate(warn_percent=80, hard_stop_percent=92).paused is False

    monkeypatch.setattr(capacity, "measure", lambda paths=(): (_volume("/db", 95.0),))
    assert capacity.evaluate(warn_percent=80, hard_stop_percent=92).paused is True


def test_nothing_measurable_is_not_an_emergency(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment without these paths must not pause itself forever."""
    monkeypatch.setattr(capacity, "measure", lambda paths=(): ())
    verdict = capacity.evaluate(warn_percent=80, hard_stop_percent=92)

    assert verdict.state is capacity.CapacityState.OK
    assert verdict.paused is False
    assert "no measurable volume" in verdict.detail


def test_a_missing_path_is_skipped_rather_than_counted_as_full() -> None:
    """An instance without the downloader has no media volume; that is normal."""
    assert capacity.measure(("/definitely/not/here",)) == ()


def test_the_real_filesystem_is_readable() -> None:
    """Guards against measuring nothing forever because a path moved."""
    volumes = capacity.measure(("/",))
    assert len(volumes) == 1
    assert volumes[0].total_bytes > 0
    assert 0 <= volumes[0].used_percent <= 100


def test_one_filesystem_mounted_twice_is_counted_once() -> None:
    """Otherwise the worst-volume calculation double counts the same disk."""
    assert len(capacity.measure(("/", "/"))) == 1


def test_a_percentage_outside_the_range_is_refused() -> None:
    """0 would pause a fresh instance; 150 would silently never fire."""
    from dtk.core.config import RUNTIME_SETTINGS

    spec = RUNTIME_SETTINGS["capacity.hard_stop_percent"]
    assert spec.validate is not None
    assert spec.validate(92) == 92
    for bad in (0, 100, 150, -1):
        with pytest.raises(ValueError, match="between"):
            spec.validate(bad)
