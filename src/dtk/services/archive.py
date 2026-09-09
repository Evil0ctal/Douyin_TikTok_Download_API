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

import base64
import binascii
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.logging import get_logger
from dtk.db.base import affected
from dtk.db.models import ArchivedAuthor, ArchivedContent, CollectionItem
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
    "DEFAULT_PAGE",
    "DURATION_BUCKETS",
    "MAX_PAGE",
    "RESOLUTION_CLASSES",
    "ArchiveFilter",
    "author_row",
    "content_row",
    "count",
    "duration_bucket",
    "get",
    "orientation_of",
    "record",
    "remove",
    "resolution_class",
    "script_of",
    "search",
    "stats",
]


# --------------------------------------------------------------------------
# Reading it back
#
# content_snapshots has been written to since install and has no console reader
# at all - a write-only table is the mistake this half exists to avoid making
# twice.
# --------------------------------------------------------------------------


#: Rows per page, and the ceiling a caller can ask for. Bounded because the row
#: carries the whole media block, so a page of 500 is megabytes.
DEFAULT_PAGE: Final = 50
MAX_PAGE: Final = 200


@dataclass(frozen=True, slots=True)
class ArchiveFilter:
    """What a caller may narrow the archive by."""

    platform: str | None = None
    author_uid: str | None = None
    tag: str | None = None
    kind: str | None = None
    duration_bucket: str | None = None
    availability: str | None = None
    query: str | None = None
    seen_after: datetime | None = None
    seen_before: datetime | None = None
    #: Only posts in this collection. A hand-made set, so it is the one filter
    #: here that is not a property of the post.
    collection_id: uuid.UUID | None = None


def _cursor_encode(row: ArchivedContent) -> str:
    """Keyset cursor: the sort key of the last row handed out.

    Never OFFSET. The archive is written to while a client walks it, so an
    offset silently skips and repeats rows - the failure mode where a caller
    believes they have everything and does not.
    """
    stamp = row.last_seen_at.astimezone(UTC).isoformat()
    return base64.urlsafe_b64encode(f"{stamp}|{row.platform}|{row.content_id}".encode()).decode(
        "ascii"
    )


def _cursor_decode(cursor: str) -> tuple[datetime, str, str] | None:
    try:
        stamp, platform, content_id = (
            base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8").split("|", 2)
        )
        return datetime.fromisoformat(stamp), platform, content_id
    except (ValueError, UnicodeDecodeError, binascii.Error):
        # A cursor the caller invented or truncated. Refusing beats starting
        # over from the top, which would look like an infinite feed.
        return None


#: The escape character for a LIKE pattern built from caller input. Backslash
#: rather than anything exotic so the SQL reads the way anyone would expect.
LIKE_ESCAPE: Final = "\\"


def escape_like(term: str) -> str:
    """Neutralize the wildcards in a substring a caller typed.

    The escape character has to go first, or escaping `%` would produce a
    pattern whose own escape is then escaped.
    """
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )


def _search_clause(term: str) -> Any:
    """Match a search term against title and description.

    Trigram ILIKE, not full-text search. Postgres' built-in parsers do not
    segment Chinese: `to_tsvector` over a Douyin description yields a handful of
    giant lexemes, and a search for a two-character word matches nothing - in
    silence, on the platform most of this archive comes from. A substring match
    backed by the GIN trigram indexes is the honest thing available without
    adding a component README rules out, and it behaves the same in both scripts.
    """
    # Escaped, because the caller typed a substring and not a LIKE pattern.
    # Unescaped, `q=%` returned the entire archive and `q=L_D` matched "LED" -
    # and both characters are ordinary in captions and handles. Not an
    # injection (the value is still bound), just wrong answers, including on
    # the export, which would stream everything for `q=%`.
    pattern = f"%{escape_like(term)}%"
    return or_(
        ArchivedContent.title.ilike(pattern, escape=LIKE_ESCAPE),
        ArchivedContent.description.ilike(pattern, escape=LIKE_ESCAPE),
    )


def _apply(statement: Any, spec: ArchiveFilter) -> Any:
    if spec.platform:
        statement = statement.where(ArchivedContent.platform == spec.platform)
    if spec.author_uid:
        statement = statement.where(ArchivedContent.author_uid == spec.author_uid)
    if spec.tag:
        # `contains` on a Postgres array is the @> operator, which the GIN index
        # on `tags` serves; ANY(...) would not use it.
        statement = statement.where(ArchivedContent.tags.contains([spec.tag]))
    if spec.kind:
        statement = statement.where(ArchivedContent.kind == spec.kind)
    if spec.duration_bucket:
        statement = statement.where(ArchivedContent.duration_bucket == spec.duration_bucket)
    if spec.availability:
        statement = statement.where(ArchivedContent.availability == spec.availability)
    if spec.seen_after:
        statement = statement.where(ArchivedContent.last_seen_at >= spec.seen_after)
    if spec.seen_before:
        statement = statement.where(ArchivedContent.last_seen_at <= spec.seen_before)
    if spec.collection_id is not None:
        # EXISTS rather than a join: a post is in a collection at most once, but
        # the planner does not know that from the schema, and a join here would
        # be one more thing to remember if that ever stopped being true.
        statement = statement.where(
            select(literal(1))
            .where(
                CollectionItem.collection_id == spec.collection_id,
                CollectionItem.platform == ArchivedContent.platform,
                CollectionItem.content_id == ArchivedContent.content_id,
            )
            .exists()
        )
    if spec.query and spec.query.strip():
        statement = statement.where(_search_clause(spec.query.strip()))
    return statement


async def search(
    session: AsyncSession,
    spec: ArchiveFilter,
    *,
    limit: int = DEFAULT_PAGE,
    cursor: str | None = None,
) -> tuple[list[ArchivedContent], str | None]:
    """One page of the archive, newest sighting first.

    Returns the rows and the cursor for the next page, or None when this was the
    last one. An unreadable cursor yields an empty page rather than silently
    restarting from the top.
    """
    size = max(1, min(limit, MAX_PAGE))
    statement = _apply(select(ArchivedContent), spec)

    if cursor:
        decoded = _cursor_decode(cursor)
        if decoded is None:
            return [], None
        stamp, platform, content_id = decoded
        # Strict "less than" on the whole sort key, so a page boundary that
        # falls inside a group of rows sharing a timestamp neither repeats nor
        # skips one.
        statement = statement.where(
            tuple_(
                ArchivedContent.last_seen_at, ArchivedContent.platform, ArchivedContent.content_id
            )
            < tuple_(literal(stamp), literal(platform), literal(content_id))
        )

    statement = statement.order_by(
        ArchivedContent.last_seen_at.desc(),
        ArchivedContent.platform.desc(),
        ArchivedContent.content_id.desc(),
    ).limit(size + 1)

    rows = list((await session.scalars(statement)).all())
    if len(rows) > size:
        return rows[:size], _cursor_encode(rows[size - 1])
    return rows, None


async def remove(session: AsyncSession, keys: Sequence[tuple[str, str]]) -> int:
    """Delete archived posts by (platform, content_id), returning how many went.

    The only destructive operation this module has. Everything else here is a
    read, and the archive's whole reason for existing is to outlive the
    platform - so removing a row is something a person asks for explicitly,
    never something a sweep decides.

    Collection membership goes with the row, by the foreign key's cascade.
    Snapshots do not: `content_snapshots` is a time series about what the post's
    numbers were doing, its rows are true whether or not the post is still in
    the archive, and the hypertable has its own retention.
    """
    if not keys:
        return 0
    result = await session.execute(
        delete(ArchivedContent).where(
            tuple_(ArchivedContent.platform, ArchivedContent.content_id).in_(list(keys))
        )
    )
    await session.flush()
    return affected(result)


async def count(session: AsyncSession, spec: ArchiveFilter) -> int:
    """How many rows match, for the console's header."""
    statement = _apply(select(func.count()).select_from(ArchivedContent), spec)
    return int((await session.scalar(statement)) or 0)


async def get(session: AsyncSession, platform: str, content_id: str) -> ArchivedContent | None:
    return await session.get(ArchivedContent, (platform, content_id))


async def stale_availability(
    session: AsyncSession, *, older_than: datetime, limit: int
) -> list[ArchivedContent]:
    """Archived posts still believed live whose existence was checked longest ago.

    Rows already known to be gone are excluded, and deliberately: a deleted
    post is a settled fact, and re-asking the platform about it forever would
    spend the identity pool proving something already known. A post that comes
    back is a real case and a rarer one; it is left for an operator to re-parse.
    """
    rows = (
        (
            await session.execute(
                select(ArchivedContent)
                .where(
                    ArchivedContent.availability == "live",
                    or_(
                        ArchivedContent.availability_checked_at.is_(None),
                        ArchivedContent.availability_checked_at < older_than,
                    ),
                )
                .order_by(ArchivedContent.availability_checked_at.asc().nullsfirst())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def mark_availability(
    session: AsyncSession,
    platform: str,
    content_id: str,
    *,
    availability: str | None = None,
    now: datetime | None = None,
) -> None:
    """Stamp the check, and record the verdict when there is one.

    Called with no ``availability`` after a successful re-parse: the archive
    write has already refreshed the row from the observation, and overwriting
    it here from a second guess would be how the two disagree. All this adds is
    that the check happened.
    """
    values: dict[str, Any] = {"availability_checked_at": now or datetime.now(UTC)}
    if availability is not None:
        values["availability"] = availability
    await session.execute(
        update(ArchivedContent)
        .where(
            ArchivedContent.platform == platform,
            ArchivedContent.content_id == content_id,
        )
        .values(**values)
    )


async def stats(session: AsyncSession) -> dict[str, Any]:
    """Totals for the console, cheap enough to run on every page load."""
    contents = int((await session.scalar(select(func.count()).select_from(ArchivedContent))) or 0)
    authors = int((await session.scalar(select(func.count()).select_from(ArchivedAuthor))) or 0)
    by_platform = {
        str(platform): int(total)
        for platform, total in (
            await session.execute(
                select(ArchivedContent.platform, func.count()).group_by(ArchivedContent.platform)
            )
        ).all()
    }
    by_availability = {
        str(state): int(total)
        for state, total in (
            await session.execute(
                select(ArchivedContent.availability, func.count()).group_by(
                    ArchivedContent.availability
                )
            )
        ).all()
    }
    return {
        "contents": contents,
        "authors": authors,
        "by_platform": by_platform,
        "by_availability": by_availability,
    }
