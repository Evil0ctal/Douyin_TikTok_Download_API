"""Named sets of archived posts, made by hand.

The library already groups itself several ways and every one of them is derived
from the post: by author, by platform, by when it was collected. A collection is
the grouping that is not derived - "things I am keeping", "for the edit", "check
these later" - which is exactly why it needs storage of its own. Nothing here
infers membership and nothing may, or a collection stops meaning what the person
who made it meant.

Two decisions worth stating:

Names are unique case-insensitively, enforced by the index rather than by a
check-then-insert here. Two collections called "Keep" and "keep" are a mistake
every time, and a race between two console tabs would slip past any check this
module could do.

Membership is stored, deletion is cascaded. Removing a post from the archive
removes it from every collection it was in, because the alternative - a
collection that counts something no longer there - is a bug report waiting to
happen.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.errors import InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.db.base import affected
from dtk.db.models import ArchivedContent, Collection, CollectionItem

log = get_logger(__name__)

#: A name has to fit on a card and in a menu.
MAX_NAME = 80
MAX_NOTE = 500

#: Ceiling on one bulk call. High enough for "select everything on the page"
#: several times over, low enough that one request cannot hold a transaction
#: open across the whole archive.
MAX_ITEMS = 500

#: A (platform, content_id) pair, which is what the archive is keyed by.
ContentKey = tuple[str, str]


def clean_name(raw: str) -> str:
    """Collapse whitespace and refuse an empty or oversized name."""
    name = " ".join(raw.split())
    if not name:
        raise InvalidParam("a collection needs a name", details={"field": "name"})
    if len(name) > MAX_NAME:
        raise InvalidParam(
            f"a collection name is at most {MAX_NAME} characters",
            details={"field": "name", "max_length": MAX_NAME},
        )
    return name


def clean_note(raw: str | None) -> str | None:
    if raw is None:
        return None
    note = raw.strip()
    if not note:
        return None
    if len(note) > MAX_NOTE:
        raise InvalidParam(
            f"a collection note is at most {MAX_NOTE} characters",
            details={"field": "note", "max_length": MAX_NOTE},
        )
    return note


def clean_keys(items: Iterable[Any]) -> list[ContentKey]:
    """Read a caller's item list into deduplicated (platform, content_id) pairs.

    Order is preserved so an error names the first bad entry the caller wrote,
    not an arbitrary one.
    """
    keys: list[ContentKey] = []
    seen: set[ContentKey] = set()
    for entry in items:
        platform = str(getattr(entry, "platform", "") or "")
        content_id = str(getattr(entry, "content_id", "") or "")
        if not platform or not content_id:
            raise InvalidParam(
                "each item needs a platform and a content_id", details={"field": "items"}
            )
        key = (platform, content_id)
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        raise InvalidParam("no items given", details={"field": "items"})
    if len(keys) > MAX_ITEMS:
        raise InvalidParam(
            f"at most {MAX_ITEMS} items in one call",
            details={"field": "items", "max_items": MAX_ITEMS},
        )
    return keys


async def get(session: AsyncSession, collection_id: uuid.UUID) -> Collection:
    row = await session.get(Collection, collection_id)
    if row is None:
        raise NotFound("no such collection", details={"collection_id": str(collection_id)})
    return row


async def create(
    session: AsyncSession,
    *,
    name: str,
    note: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Collection:
    row = Collection(name=clean_name(name), note=clean_note(note), created_by=created_by)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The unique index on lower(name) is the only constraint that can fire
        # here, and a duplicate name is a caller mistake rather than a fault.
        log.info("collections.create_conflict", detail=str(exc.orig)[:300])
        await session.rollback()
        raise InvalidParam(
            "a collection with that name already exists", details={"field": "name"}
        ) from exc
    return row


async def rename(
    session: AsyncSession,
    collection_id: uuid.UUID,
    *,
    name: str | None = None,
    note: str | None = None,
    note_given: bool = False,
) -> Collection:
    """Change the name, the note, or both.

    ``note_given`` distinguishes "leave the note alone" from "clear it": both
    arrive as None, and a PATCH that silently erased a note the caller did not
    mention would be the wrong one to guess at.
    """
    row = await get(session, collection_id)
    if name is not None:
        row.name = clean_name(name)
    if note_given:
        row.note = clean_note(note)
    # A Python timestamp rather than func.now(): assigning a SQL function
    # leaves the attribute unreadable until a refresh, and the handler reads it
    # straight back to build the response.
    row.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise InvalidParam(
            "a collection with that name already exists", details={"field": "name"}
        ) from exc
    return row


async def remove(session: AsyncSession, collection_id: uuid.UUID) -> None:
    """Delete the collection. Its membership goes with it; the posts do not."""
    row = await get(session, collection_id)
    await session.delete(row)
    await session.flush()


async def add_items(
    session: AsyncSession, collection_id: uuid.UUID, keys: Sequence[ContentKey]
) -> int:
    """Put posts in a collection, returning how many were not already in it.

    Only posts that are actually in the archive go in: the foreign key would
    refuse the rest anyway, and refusing the whole call because one card was
    stale is worse than adding the nineteen that were not.
    """
    await get(session, collection_id)
    known = await _archived(session, keys)
    if not known:
        return 0
    statement = (
        pg_insert(CollectionItem)
        .values(
            [
                {"collection_id": collection_id, "platform": platform, "content_id": content_id}
                for platform, content_id in known
            ]
        )
        # Adding the same post twice is a no-op, not an error. The console sends
        # whatever is selected, and part of a selection is routinely already in.
        .on_conflict_do_nothing(index_elements=["collection_id", "platform", "content_id"])
    )
    result = await session.execute(statement)
    await session.flush()
    return affected(result)


async def remove_items(
    session: AsyncSession, collection_id: uuid.UUID, keys: Sequence[ContentKey]
) -> int:
    await get(session, collection_id)
    result = await session.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == collection_id,
            tuple_(CollectionItem.platform, CollectionItem.content_id).in_(list(keys)),
        )
    )
    await session.flush()
    return affected(result)


async def _archived(session: AsyncSession, keys: Sequence[ContentKey]) -> list[ContentKey]:
    """The subset of ``keys`` the archive actually holds."""
    rows = await session.execute(
        select(ArchivedContent.platform, ArchivedContent.content_id).where(
            tuple_(ArchivedContent.platform, ArchivedContent.content_id).in_(list(keys))
        )
    )
    return [(str(platform), str(content_id)) for platform, content_id in rows.all()]


async def listing(session: AsyncSession) -> list[dict[str, Any]]:
    """Every collection with the number of posts in it, newest first.

    An outer join rather than a subquery per row: the console renders this as a
    filter menu and asks for all of them at once.
    """
    # Labelled `item_count`, not `items`: `subquery().c` is a ColumnCollection
    # and `.items` on it resolves to the mapping method rather than to the
    # column, which reaches the driver as a bound method and fails there.
    counts = (
        select(CollectionItem.collection_id, func.count().label("item_count"))
        .group_by(CollectionItem.collection_id)
        .subquery()
    )
    rows = await session.execute(
        select(Collection, func.coalesce(counts.c.item_count, 0))
        .outerjoin(counts, counts.c.collection_id == Collection.id)
        .order_by(Collection.created_at.desc())
    )
    return [as_dict(row, items=int(items)) for row, items in rows.all()]


async def memberships(
    session: AsyncSession, keys: Sequence[ContentKey]
) -> dict[ContentKey, list[str]]:
    """Which collections each of these posts is in, keyed by (platform, id).

    One query for a page of cards rather than one per card: the library asks
    this of every post it renders.
    """
    if not keys:
        return {}
    rows = await session.execute(
        select(CollectionItem.platform, CollectionItem.content_id, CollectionItem.collection_id)
        .where(tuple_(CollectionItem.platform, CollectionItem.content_id).in_(list(keys)))
        .order_by(CollectionItem.added_at)
    )
    out: dict[ContentKey, list[str]] = {}
    for platform, content_id, collection_id in rows.all():
        out.setdefault((str(platform), str(content_id)), []).append(str(collection_id))
    return out


def as_dict(row: Collection, *, items: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if items is not None:
        payload["items"] = items
    return payload


__all__ = [
    "MAX_ITEMS",
    "MAX_NAME",
    "MAX_NOTE",
    "ContentKey",
    "add_items",
    "as_dict",
    "clean_keys",
    "clean_name",
    "clean_note",
    "create",
    "get",
    "listing",
    "memberships",
    "remove",
    "remove_items",
    "rename",
]
