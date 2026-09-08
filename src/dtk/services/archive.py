"""Keeping parsed content after the task result expires.

Before this, a parsed post lived 24 hours in ``tasks.result`` and then was gone.
The instance could still say that a request happened (``request_log``) and how
many plays a video had at time T (``content_snapshots``), but not what the video
was called, who made it, or what tags it carried. This module is the current-state
half; ``content_snapshots`` remains the history half, and the two join on
``(platform, content_id)``.

Three rules shape everything here.

**Writing is never worth failing a task over.** The same discipline
``dtk.services.snapshots`` already follows: a failed archive write is logged and
swallowed, because the caller asked for data and got it, and losing the archive
row is a smaller harm than turning a successful fetch into an error.

**A cache hit archives nothing.** ``FetchService`` returns the cached dict without
invoking the parser, so there is no model to archive. That is mostly benign - a hit
means the same content was archived under half an hour ago - but it means
``last_seen_at`` undercounts, and the doc says so rather than pretending otherwise.

**Classification is derived, never inferred.** Every derived column is a pure
function of fields the parser already returned, so a rule change is a backfill
rather than a re-crawl, and nothing here reaches for a model.
docs/design/README.md records AI content analysis as a non-goal.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.logging import get_logger
from dtk.db.models import ArchivedAuthor, ArchivedContent
from dtk.models import Author, Content, Page

log = get_logger(__name__)

#: Upper bound in milliseconds for each duration bucket. The boundaries follow
#: what the platforms themselves treat as different formats rather than round
#: numbers: under a minute is the classic short, up to three is what both apps
#: allow by default, and beyond that is long-form.
DURATION_BUCKETS: Final[tuple[tuple[int, str], ...]] = (
    (60_000, "short"),
    (180_000, "medium"),
)
LONG_BUCKET: Final = "long"

#: Shorter-edge pixel thresholds, as exclusive upper bounds. Keyed on the shorter
#: edge because a portrait 1080x1920 and a landscape 1920x1080 are the same
#: quality, and keying on height would call one of them four times the other.
#:
#: The names follow what the numbers are called in the wild: a 720-pixel shorter
#: edge is "hd", not the tier below it. Writing them as the bound each tier stops
#: at, rather than the bound it starts from, is what got this off by one tier on
#: the first attempt.
RESOLUTION_CLASSES: Final[tuple[tuple[int, str], ...]] = (
    (720, "sd"),
    (1080, "hd"),
    (2160, "fhd"),
)
HIGHEST_RESOLUTION: Final = "uhd"

#: A script counts as present when it is at least a fifth of the letters.
MIXED_SCRIPT_RATIO: Final = 5

UNKNOWN: Final = "unknown"


def duration_bucket(duration_ms: int | None) -> str:
    """Bucket a duration. ``None`` stays unknown rather than becoming ``short``."""
    if duration_ms is None or duration_ms <= 0:
        return UNKNOWN
    for ceiling, name in DURATION_BUCKETS:
        if duration_ms < ceiling:
            return name
    return LONG_BUCKET


def orientation_of(width: int | None, height: int | None) -> str:
    """Portrait, landscape or square, from the video's own geometry."""
    if not width or not height or width <= 0 or height <= 0:
        return UNKNOWN
    if width == height:
        return "square"
    return "portrait" if height > width else "landscape"


def resolution_class(width: int | None, height: int | None) -> str:
    """Quality tier from the shorter edge."""
    if not width or not height or width <= 0 or height <= 0:
        return UNKNOWN
    shorter = min(width, height)
    for ceiling, name in RESOLUTION_CLASSES:
        if shorter < ceiling:
            return name
    return HIGHEST_RESOLUTION


def script_of(*texts: str | None) -> str:
    """Whether the text is CJK, Latin, both, or too short to tell.

    Decides which search path a query should take: Postgres' built-in text
    search parsers do not segment Chinese, so a CJK description has to be matched
    by trigram substring rather than by lexeme. Getting this wrong is silent -
    the search simply returns nothing - which is why it is stored rather than
    guessed at query time.

    Counted over letters only. Emoji, punctuation and digits say nothing about
    which language a caption is in, and a caption that is mostly emoji would
    otherwise flip class on one stray character.
    """
    cjk = latin = 0
    for text in texts:
        for char in text or "":
            if not char.isalpha():
                continue
            name = unicodedata.name(char, "")
            if name.startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
                cjk += 1
            elif "LATIN" in name:
                latin += 1
    if not cjk and not latin:
        return UNKNOWN
    if cjk and latin:
        # Both have to be substantial. A brand name or a hashtag inside an
        # otherwise Chinese caption is not a bilingual caption, and calling it
        # one would send the query down the wrong search path.
        #
        # The threshold is a proportion and nothing cleverer, which means it
        # cannot tell a long Chinese caption containing "iPhone" from a short
        # one that is genuinely half English - at equal proportions they are the
        # same string as far as this function can see. Twenty percent is where
        # the common case falls on the right side; a caption near the boundary
        # gets an arguable answer either way, and only chooses which index the
        # search uses first.
        total = cjk + latin
        if min(cjk, latin) * MIXED_SCRIPT_RATIO >= total:
            return "mixed"
        return "cjk" if cjk > latin else "latin"
    return "cjk" if cjk else "latin"


def _video_geometry(content: Content) -> tuple[int | None, int | None]:
    """Width and height of the best stream we know about."""
    video = content.media.video
    if video is not None and video.width and video.height:
        return video.width, video.height
    for stream in content.media.streams:
        if stream.width and stream.height:
            return stream.width, stream.height
    for image in content.media.images:
        if image.width and image.height:
            return image.width, image.height
    return None, None


def _first_url(images: Sequence[Any]) -> str | None:
    for image in images:
        if getattr(image, "url", None):
            return str(image.url)
    return None


def content_row(content: Content, *, now: datetime, store_raw: bool) -> dict[str, Any]:
    """One archived content row, classification included."""
    width, height = _video_geometry(content)
    return {
        "platform": content.platform.value,
        "content_id": content.content_id,
        "kind": content.kind.value,
        "web_url": content.web_url,
        "title": content.title or "",
        "description": content.description or "",
        "platform_created_at": content.created_at,
        "duration_ms": content.duration_ms,
        "author_uid": content.author.uid,
        "author_nickname": content.author.nickname,
        "music_id": content.music.music_id if content.music else None,
        "music_title": content.music.title if content.music else None,
        "tags": list(content.tags),
        "location": content.location,
        "cover_url": _first_url(content.media.covers),
        # The whole media block, mirror lists included, so a download can be
        # retried without re-parsing. The signed links inside expire; `web_url`
        # is what survives.
        "media": content.media.model_dump(mode="json"),
        "orientation": orientation_of(width, height),
        "duration_bucket": duration_bucket(content.duration_ms),
        "resolution_class": resolution_class(width, height),
        "script": script_of(content.title, content.description),
        "availability": "deleted"
        if content.is_deleted
        else "private"
        if content.is_private
        else "live",
        "raw": content.raw if store_raw else None,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def author_row(author: Author, *, now: datetime, store_raw: bool) -> dict[str, Any]:
    """One archived author row."""
    stats = author.stats
    return {
        "platform": author.platform.value,
        "uid": author.uid,
        "unique_id": author.unique_id,
        "nickname": author.nickname,
        "signature": author.signature,
        "avatar_url": str(author.avatar.url) if author.avatar and author.avatar.url else None,
        "web_url": author.web_url,
        "verified": author.verified,
        "follower_count": stats.follower_count if stats else None,
        "following_count": stats.following_count if stats else None,
        "content_count": stats.content_count if stats else None,
        "total_digg": stats.total_digg if stats else None,
        "raw": author.raw if store_raw else None,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def _upsert(model: Any, rows: list[dict[str, Any]], keys: tuple[str, ...]) -> Any:
    """Insert or refresh, keeping the earliest ``first_seen_at`` we ever saw.

    ``LEAST`` rather than "leave it alone": a backfill can legitimately archive a
    post whose real first sighting is older than the row already present, and
    taking the minimum is correct in both directions.

    A column is only overwritten when the new row actually carries a value, so a
    list endpoint's abbreviated author cannot blank a signature that a full
    profile fetch had already filled in.
    """
    statement = insert(model).values(rows)
    excluded = statement.excluded
    table = model.__table__

    updates: dict[str, Any] = {
        column.name: func.coalesce(excluded[column.name], table.c[column.name])
        for column in table.columns
        if column.name not in keys and column.name not in {"first_seen_at", "last_seen_at"}
    }
    updates["last_seen_at"] = excluded.last_seen_at
    updates["first_seen_at"] = func.least(excluded.first_seen_at, table.c.first_seen_at)
    return statement.on_conflict_do_update(index_elements=list(keys), set_=updates)


async def record(
    session: AsyncSession,
    parsed: Iterable[Any],
    *,
    store_raw: bool = False,
    now: datetime | None = None,
) -> int:
    """Archive everything archivable in one task's parsed models.

    Returns how many content rows were written, for the caller's log line. Author
    rows ride along: every Content carries one, and an author seen only as the
    byline of a post is still worth having.
    """
    stamp = now or datetime.now(UTC)
    contents: dict[tuple[str, str], dict[str, Any]] = {}
    authors: dict[tuple[str, str], dict[str, Any]] = {}

    for model in parsed:
        for item in _iter_models(model):
            if isinstance(item, Content):
                contents[(item.platform.value, item.content_id)] = content_row(
                    item, now=stamp, store_raw=store_raw
                )
                authors[(item.author.platform.value, item.author.uid)] = author_row(
                    item.author, now=stamp, store_raw=store_raw
                )
            elif isinstance(item, Author):
                # A full profile fetch beats the abbreviated author on a post, so
                # it is written last and wins the dictionary slot.
                authors[(item.platform.value, item.uid)] = author_row(
                    item, now=stamp, store_raw=store_raw
                )

    if authors:
        await session.execute(_upsert(ArchivedAuthor, list(authors.values()), ("platform", "uid")))
    if contents:
        await session.execute(
            _upsert(ArchivedContent, list(contents.values()), ("platform", "content_id"))
        )
    return len(contents)


def _iter_models(model: Any) -> Iterable[Any]:
    """A parsed result is one model or a page of them."""
    if isinstance(model, Page):
        yield from model.items
        return
    yield model


__all__ = [
    "DURATION_BUCKETS",
    "RESOLUTION_CLASSES",
    "author_row",
    "content_row",
    "duration_bucket",
    "orientation_of",
    "record",
    "resolution_class",
    "script_of",
]
