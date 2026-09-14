"""How large a page each platform will actually serve.

Separate from :data:`dtk.api.routes.support.MAX_PAGE_SIZE`, which is this
project's own ceiling - "a caller that wants everything at once is a caller to
slow down". This module records the *platform's* ceiling, which is a measured
fact about somebody else's service and is not the same number.

Measured 2026-09-13 against the live endpoints with a guest identity, on two
different authors, through ``tiktok.author_posts`` and ``tiktok.author_likes``:

    count=35  ->  a full page, `hasMore` true
    count=36  ->  refused
    count=50  ->  refused

Douyin served 50 from the same harness and was not measured above it, because
this project refuses more than 50 anyway.

Why this matters more than an off-by-one: TikTok's refusal is not an error
shaped like one. It classifies as an ordinary absence, so a caller who asks for
36 is told the author has no posts - which is the misreading
``tests/unit/test_absence_matrix.py`` exists to prevent, arrived at from the
request side instead of the response side. Refusing the parameter here means
the call is never made.

Found by @BennoCrafter while testing #753; the general case - that it is the
platform's limit rather than one endpoint's - was established afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from dtk.core.types import Platform

#: The largest ``count`` each platform answers rather than refuses.
MAX_PAGE_SIZE: Final[Mapping[Platform, int]] = MappingProxyType(
    {
        Platform.DOUYIN: 50,
        Platform.TIKTOK: 35,
    }
)


def max_page_size(platform: Platform) -> int:
    """The page-size ceiling for ``platform``.

    Every platform must appear in the table; a new one that forgets is caught by
    ``tests/unit/test_paging.py`` rather than silently inheriting a number
    measured against a different service.
    """
    return MAX_PAGE_SIZE[platform]


__all__ = ["MAX_PAGE_SIZE", "max_page_size"]
