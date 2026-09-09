"""Collections, and the one archive call that destroys something.

A collection is the only grouping the library offers that is not derived from
the posts, which is what makes it worth a table and worth tests: everything
else here can be recomputed from the archive, and this cannot be recomputed
from anything.

The delete tests care about order. Files go first and rows second, so the worst
case is bytes with no record - which the storage panel already reports - rather
than a record offering a download it cannot deliver.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from dtk.core.db import session_scope
from dtk.core.types import Scope
from dtk.db.models import ArchivedContent, Collection, CollectionItem, MediaDownload
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    anonymous_client,
    envelope,
    error_code,
    make_api_key,
    make_user,
    signed_in,
)

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

AUTHOR_UID = "MS4wLjABAAAAexample"


async def archive_posts(*content_ids: str, platform: str = "douyin") -> None:
    now = datetime.now(UTC)
    async with session_scope() as session:
        for content_id in content_ids:
            session.add(
                ArchivedContent(
                    platform=platform,
                    content_id=content_id,
                    kind="video",
                    web_url=f"https://www.douyin.com/video/{content_id}",
                    title=f"post {content_id}",
                    description="",
                    author_uid=AUTHOR_UID,
                    author_nickname="someone",
                    tags=[],
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )


def refs(*content_ids: str, platform: str = "douyin") -> list[dict[str, str]]:
    return [{"platform": platform, "content_id": cid} for cid in content_ids]


async def make_collection(http: Any, name: str = "keep") -> str:
    response = await http.post("/api/v1/archive/collections", json={"name": name})
    assert response.status_code == 201, response.text
    return str(envelope(response)["data"]["id"])


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


async def test_a_collection_is_created_empty_and_counted(client: Any) -> None:
    await signed_in(client)
    collection_id = await make_collection(client, "for the edit")

    listed = envelope(await client.get("/api/v1/archive/collections"))["data"]["items"]

    assert [(row["id"], row["name"], row["items"]) for row in listed] == [
        (collection_id, "for the edit", 0)
    ]


async def test_two_names_that_differ_only_in_case_are_the_same_name(client: Any) -> None:
    """A mistake every time, so the schema refuses it rather than each caller."""
    await signed_in(client)
    await make_collection(client, "Keep")

    clash = await client.post("/api/v1/archive/collections", json={"name": "keep"})

    assert error_code(clash) == "INVALID_PARAM"


async def test_whitespace_in_a_name_is_collapsed(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/archive/collections", json={"name": "  for   the  edit \n"}
    )
    assert envelope(response)["data"]["name"] == "for the edit"


async def test_a_blank_name_is_refused(client: Any) -> None:
    await signed_in(client)
    response = await client.post("/api/v1/archive/collections", json={"name": "   "})
    assert error_code(response) in ("INVALID_PARAM", "VALIDATION_ERROR")


# --------------------------------------------------------------------------
# Renaming
# --------------------------------------------------------------------------


async def test_a_patch_that_does_not_mention_the_note_keeps_it(client: Any) -> None:
    """Sent-and-null and not-sent are the same value and different requests.

    Guessing at that would quietly erase text nobody mentioned, which is the
    kind of loss the person only discovers much later.
    """
    await signed_in(client)
    created = await client.post(
        "/api/v1/archive/collections", json={"name": "keep", "note": "the good ones"}
    )
    collection_id = envelope(created)["data"]["id"]

    renamed = await client.patch(
        f"/api/v1/archive/collections/{collection_id}", json={"name": "keeping"}
    )

    assert envelope(renamed)["data"]["name"] == "keeping"
    assert envelope(renamed)["data"]["note"] == "the good ones"


async def test_a_patch_that_sends_a_null_note_clears_it(client: Any) -> None:
    await signed_in(client)
    created = await client.post(
        "/api/v1/archive/collections", json={"name": "keep", "note": "the good ones"}
    )
    collection_id = envelope(created)["data"]["id"]

    cleared = await client.patch(
        f"/api/v1/archive/collections/{collection_id}", json={"note": None}
    )

    assert envelope(cleared)["data"]["note"] is None


# --------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------


async def test_posts_go_in_and_are_counted(client: Any) -> None:
    await signed_in(client)
    await archive_posts("7001", "7002")
    collection_id = await make_collection(client)

    added = await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001", "7002")}
    )

    assert envelope(added)["data"]["added"] == 2
    listed = envelope(await client.get("/api/v1/archive/collections"))["data"]["items"]
    assert listed[0]["items"] == 2


async def test_adding_the_same_post_twice_is_a_no_op(client: Any) -> None:
    """The console sends whatever is selected, and part of it is routinely in."""
    await signed_in(client)
    await archive_posts("7001")
    collection_id = await make_collection(client)
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001")}
    )

    again = await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001")}
    )

    assert again.status_code == 200
    assert envelope(again)["data"]["added"] == 0


async def test_a_post_that_is_not_archived_is_skipped_not_refused(client: Any) -> None:
    """One stale card must not refuse the nineteen beside it."""
    await signed_in(client)
    await archive_posts("7001")
    collection_id = await make_collection(client)

    added = await client.post(
        f"/api/v1/archive/collections/{collection_id}/items",
        json={"items": refs("7001", "7404-gone")},
    )

    assert envelope(added)["data"]["added"] == 1


async def test_posts_can_be_taken_back_out_and_stay_archived(client: Any) -> None:
    await signed_in(client)
    await archive_posts("7001")
    collection_id = await make_collection(client)
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001")}
    )

    removed = await client.post(
        f"/api/v1/archive/collections/{collection_id}/items/remove",
        json={"items": refs("7001")},
    )

    assert envelope(removed)["data"]["removed"] == 1
    async with session_scope() as session:
        assert (await session.scalars(select(ArchivedContent))).all() != []


async def test_the_list_can_be_filtered_to_one_collection(client: Any) -> None:
    await signed_in(client)
    await archive_posts("7001", "7002", "7003")
    collection_id = await make_collection(client)
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001", "7003")}
    )

    page = envelope(await client.get("/api/v1/archive", params={"collection": collection_id}))[
        "data"
    ]

    assert {row["content_id"] for row in page["items"]} == {"7001", "7003"}


async def test_every_row_says_which_collections_it_is_in(client: Any) -> None:
    """Asked once for the page, not once per card: the wall renders many."""
    await signed_in(client)
    await archive_posts("7001", "7002")
    collection_id = await make_collection(client)
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001")}
    )

    page = envelope(await client.get("/api/v1/archive"))["data"]

    by_id = {row["content_id"]: row for row in page["items"]}
    assert by_id["7001"]["collections"] == [collection_id]
    assert by_id["7002"]["collections"] == []


# --------------------------------------------------------------------------
# Deleting the label vs deleting the thing
# --------------------------------------------------------------------------


async def test_deleting_a_collection_keeps_the_posts(client: Any) -> None:
    await signed_in(client)
    await archive_posts("7001")
    collection_id = await make_collection(client)
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001")}
    )

    await client.delete(f"/api/v1/archive/collections/{collection_id}")

    async with session_scope() as session:
        assert len((await session.scalars(select(ArchivedContent))).all()) == 1
        assert (await session.scalars(select(Collection))).all() == []
        assert (await session.scalars(select(CollectionItem))).all() == []


async def test_deleting_a_post_takes_it_out_of_every_collection(client: Any) -> None:
    """Otherwise a collection keeps counting something that is gone."""
    await signed_in(client)
    await archive_posts("7001")
    collection_id = await make_collection(client)
    await client.post(
        f"/api/v1/archive/collections/{collection_id}/items", json={"items": refs("7001")}
    )

    deleted = await client.post("/api/v1/archive/delete", json={"items": refs("7001")})

    assert envelope(deleted)["data"]["deleted"] == 1
    async with session_scope() as session:
        assert (await session.scalars(select(CollectionItem))).all() == []
    listed = envelope(await client.get("/api/v1/archive/collections"))["data"]["items"]
    assert listed[0]["items"] == 0


async def test_delete_removes_the_download_record_too(client: Any) -> None:
    """A download record for a post nothing else knows about is an orphan.

    Eviction deliberately keeps the row - it is the record that something was
    collected and later reclaimed for space - but this is a person saying they
    do not want the post, which is a different thing.

    The row here is already evicted, which is what lets this test say something
    about the record without a sidecar: there are no bytes to remove, so the
    downloader is never called.
    """
    await signed_in(client)
    await archive_posts("7001")
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id="7001",
                author_uid=AUTHOR_UID,
                state="done",
                directory="douyin/MS4wLjABAAAAexample/7001",
                bytes_total=0,
                file_count=1,
                files_removed_at=datetime.now(UTC),
            )
        )

    body = envelope(await client.post("/api/v1/archive/delete", json={"items": refs("7001")}))

    assert body["data"]["downloads_removed"] == 1
    async with session_scope() as session:
        assert (await session.scalars(select(MediaDownload))).all() == []


async def test_delete_refuses_rather_than_orphan_the_bytes(client: Any) -> None:
    """A stored post whose files cannot be reached is not deleted at all.

    Removing the record while the bytes stay would put files on the volume that
    nothing can account for and nothing can remove. Refusing leaves everything
    as it was, which is the recoverable direction; `media=false` is the call
    that says to keep the files deliberately.
    """
    await signed_in(client)
    await archive_posts("7001")
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id="7001",
                author_uid=AUTHOR_UID,
                state="done",
                directory="douyin/MS4wLjABAAAAexample/7001",
                bytes_total=1024,
                file_count=1,
            )
        )

    refused = await client.post("/api/v1/archive/delete", json={"items": refs("7001")})

    assert error_code(refused) in ("NOT_CONFIGURED", "DOWNLOADER_UNAVAILABLE")
    async with session_scope() as session:
        assert len((await session.scalars(select(ArchivedContent))).all()) == 1
        assert len((await session.scalars(select(MediaDownload))).all()) == 1


async def test_delete_with_media_false_keeps_the_download_record(client: Any) -> None:
    await signed_in(client)
    await archive_posts("7001")
    async with session_scope() as session:
        session.add(
            MediaDownload(
                id=uuid.uuid4(),
                platform="douyin",
                content_id="7001",
                author_uid=AUTHOR_UID,
                state="done",
                directory="douyin/MS4wLjABAAAAexample/7001",
                bytes_total=1024,
                file_count=1,
            )
        )

    body = envelope(
        await client.post("/api/v1/archive/delete", json={"items": refs("7001"), "media": False})
    )

    assert body["data"]["deleted"] == 1
    assert body["data"]["downloads_removed"] == 0
    async with session_scope() as session:
        assert len((await session.scalars(select(MediaDownload))).all()) == 1


async def test_delete_ignores_posts_that_were_never_archived(client: Any) -> None:
    await signed_in(client)
    await archive_posts("7001")

    body = envelope(
        await client.post("/api/v1/archive/delete", json={"items": refs("7001", "7404-gone")})
    )

    assert body["data"]["deleted"] == 1


# --------------------------------------------------------------------------
# Scopes
# --------------------------------------------------------------------------


async def test_reading_collections_needs_only_the_archive_scope(
    api_app: Any, db_engine: Any, redis_client: Any
) -> None:
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.ARCHIVE_READ,))
    async with anonymous_client(api_app) as caller:
        response = await caller.get("/api/v1/archive/collections", headers={"X-API-Key": key})
    assert response.status_code == 200


async def test_an_archive_read_key_cannot_create_a_collection(
    api_app: Any, db_engine: Any, redis_client: Any
) -> None:
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.ARCHIVE_READ,))
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/archive/collections", json={"name": "keep"}, headers={"X-API-Key": key}
        )
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_an_archive_read_key_cannot_delete_the_archive(
    api_app: Any, db_engine: Any, redis_client: Any
) -> None:
    """Reading what was collected is not permission to destroy it."""
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.ARCHIVE_READ, Scope.MEDIA_READ))
    await archive_posts("7001")
    async with anonymous_client(api_app) as caller:
        response = await caller.post(
            "/api/v1/archive/delete",
            json={"items": refs("7001")},
            headers={"X-API-Key": key},
        )
    assert error_code(response) == "FORBIDDEN_SCOPE"
    async with session_scope() as session:
        assert len((await session.scalars(select(ArchivedContent))).all()) == 1


async def test_the_collections_path_is_not_read_as_a_platform(client: Any) -> None:
    """`/{platform}/{content_id}` would swallow `/collections/{id}` if it came first."""
    await signed_in(client)
    collection_id = await make_collection(client)

    response = await client.get("/api/v1/archive/collections")

    assert response.status_code == 200
    assert envelope(response)["data"]["items"][0]["id"] == collection_id
