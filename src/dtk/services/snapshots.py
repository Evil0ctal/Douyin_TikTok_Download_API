"""Metric snapshots for scraped content.

Every successful parse writes one row, at no extra request cost. It is the one
capability the project keeps beyond parsing itself, because it answers questions
that would otherwise need a fresh crawl: how a video's play count grew over the
last week, for instance.

A minimum interval is mandatory. A caller polling one video every ten seconds
would otherwise add 8640 near-identical rows a day. See docs/design/05-data-model.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import Platform
from dtk.db.models import ContentSnapshot
from dtk.models import Author, Content

log = get_logger(__name__)

DEDUP_KEY = "snapshot:{platform}:{content_id}"


async def _should_write(platform: Platform, content_id: str, min_interval: int) -> bool:
    if min_interval <= 0:
        return True
    return bool(
        await get_redis().set(
            DEDUP_KEY.format(platform=platform.value, content_id=content_id),
            "1",
            ex=min_interval,
            nx=True,
        )
    )


def _all_none(*values: Any) -> bool:
    return all(v is None for v in values)


async def record_content(session: AsyncSession, content: Content, *, min_interval: int) -> bool:
    """Write one metric snapshot. Returns whether a row was actually added."""
    s = content.stats
    # Writing a row where every metric is None adds nothing but noise, and
    # writing 0 instead would put a phantom cliff into the growth curve.
    if _all_none(s.play_count, s.digg_count, s.comment_count, s.share_count, s.collect_count):
        return False
    if not await _should_write(content.platform, content.content_id, min_interval):
        return False

    session.add(
        ContentSnapshot(
            ts=datetime.now(UTC),
            platform=content.platform.value,
            content_type="video",
            content_id=content.content_id,
            play_count=s.play_count,
            digg_count=s.digg_count,
            comment_count=s.comment_count,
            share_count=s.share_count,
            collect_count=s.collect_count,
            follower_count=None,
            raw=None,
        )
    )
    return True


async def record_author(session: AsyncSession, author: Author, *, min_interval: int) -> bool:
    if author.stats is None:
        return False
    st = author.stats
    if _all_none(st.follower_count, st.following_count, st.content_count, st.total_digg):
        return False
    if not await _should_write(author.platform, author.uid, min_interval):
        return False

    session.add(
        ContentSnapshot(
            ts=datetime.now(UTC),
            platform=author.platform.value,
            content_type="user",
            content_id=author.uid,
            play_count=None,
            digg_count=st.total_digg,
            comment_count=None,
            share_count=None,
            collect_count=None,
            follower_count=st.follower_count,
            raw=None,
        )
    )
    return True


__all__ = ["DEDUP_KEY", "record_author", "record_content"]
