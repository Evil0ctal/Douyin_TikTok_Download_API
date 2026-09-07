"""Long-lived public test subjects.

Kept in one place so replacing a deleted one is a single edit. Anything that can
disappear turns a platform-change alarm into a false alarm, and false alarms are
expensive here: the whole point of this suite is that a failure means something.
"""

from __future__ import annotations

from dataclasses import dataclass

from dtk.core.types import Platform


@dataclass(frozen=True, slots=True)
class Subject:
    platform: Platform
    kind: str
    url: str
    note: str


#: Deliberately empty. Populate with URLs chosen by a maintainer; the suite skips
#: entirely while this is empty, which is the correct default for a fresh clone
#: and for ordinary CI.
SUBJECTS: tuple[Subject, ...] = ()


def by_kind(kind: str) -> list[Subject]:
    return [s for s in SUBJECTS if s.kind == kind]
