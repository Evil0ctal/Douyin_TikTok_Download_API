"""The platform page-size ceilings, and that every platform has one.

`dtk.platforms.paging` records what each platform will actually serve, which is
a different number from this project's own `MAX_PAGE_SIZE` and is not guessable
from it. A platform missing from the table would inherit a number measured
against somebody else's service, so the coverage check is the point of this
file rather than an extra in it.
"""

from __future__ import annotations

import pytest

from dtk.api.routes.support import MAX_PAGE_SIZE as PROJECT_CEILING
from dtk.api.routes.support import resolve_count
from dtk.core.errors import InvalidParam
from dtk.core.types import Platform
from dtk.platforms.paging import MAX_PAGE_SIZE, max_page_size


def test_every_platform_states_a_ceiling() -> None:
    assert set(MAX_PAGE_SIZE) == set(Platform), (
        "a platform has no measured page-size ceiling; it would silently use "
        "another platform's number"
    )


@pytest.mark.parametrize("platform", list(Platform))
def test_no_ceiling_exceeds_this_project_s_own(platform: Platform) -> None:
    """A platform ceiling above 50 would never be reachable anyway.

    `COUNT_QUERY` declares `le=MAX_PAGE_SIZE`, so FastAPI rejects more than 50
    before a route runs. A table entry above it would be dead and misleading.
    """
    assert max_page_size(platform) <= PROJECT_CEILING


def test_tiktok_refuses_more_than_thirty_five() -> None:
    """Measured, not assumed - see the module docstring for the readings.

    36 is the interesting number: it is accepted by `le=50` at the FastAPI
    layer, so without this check it reaches TikTok, which answers in a shape
    that reads as "this author has nothing".
    """
    assert max_page_size(Platform.TIKTOK) == 35

    ceiling = max_page_size(Platform.TIKTOK)
    assert resolve_count(35, maximum=ceiling) == 35
    with pytest.raises(InvalidParam) as excinfo:
        resolve_count(36, maximum=ceiling)
    assert excinfo.value.details["maximum"] == 35
    assert excinfo.value.details["value"] == 36


def test_a_page_size_over_the_ceiling_is_refused_not_shortened() -> None:
    """The same choice `wait` makes, for the same reason.

    Shortening looks harmless until the caller cannot tell fifty-asked-for,
    thirty-five-returned from an author who had thirty-five, and never asks for
    the page with the rest of it.
    """
    with pytest.raises(InvalidParam):
        resolve_count(40, maximum=35)


def test_an_omitted_count_never_exceeds_the_ceiling() -> None:
    """The default is 20 and both ceilings are higher, but a platform stricter
    than the default must still win - otherwise the endpoint fails for a caller
    who passed nothing at all."""
    assert resolve_count(None, default=20, maximum=10) == 10
    assert resolve_count(None, default=20, maximum=35) == 20
