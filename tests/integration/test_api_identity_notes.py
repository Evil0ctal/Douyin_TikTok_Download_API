"""An identity's note, the endpoints it serves, and what each endpoint is served by.

Three imported logins used to be three UUIDs: nothing said whose account each
was, which endpoints rotation could send each to, or how many logins an
endpoint that needs one actually had. The scheduler's half of the per-platform
"guests first" setting is in tests/integration/test_scheduler_concurrency.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from dtk.core.db import session_scope
from dtk.core.types import IdentitySource, Platform, UserRole
from dtk.db.models import AuditLog, Identity
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
COOKIE_BLOB = "ttwid=1%7Cabcdefghijklmnop; odin_tt=0123456789abcdef"
#: A logged-in jar. The session value is SYNTHETIC, generated for this test.
SESSION_COOKIE_BLOB = f"{COOKIE_BLOB}; sessionid=deadbeefcafebabe0123"  # SYNTHETIC
POSTS = "douyin.author_posts"
DETAIL = "douyin.content_detail"


async def make_identity(
    *,
    state: str = "active",
    authenticated: bool = False,
    rotation: list[str] | None = None,
    note: str | None = None,
) -> uuid.UUID:
    identity_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            Identity(
                id=identity_id,
                platform=Platform.DOUYIN.value,
                state=state,
                cookies_encrypted=b"ciphertext",
                fingerprint={},
                source=IdentitySource.IMPORTED.value,
                authenticated=authenticated,
                rotation_endpoints=rotation,
                note=note,
            )
        )
    return identity_id


async def note_of(identity_id: uuid.UUID) -> str | None:
    async with session_scope() as session:
        row = await session.get(Identity, identity_id)
    assert row is not None
    return row.note


async def patch_note(client: Any, identity_id: uuid.UUID, note: str | None) -> Any:
    return await client.patch(f"/api/v1/admin/identities/{identity_id}", json={"note": note})


# --------------------------------------------------------------------------
# The note
# --------------------------------------------------------------------------


async def test_a_note_is_stored_trimmed_and_listed(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity()

    response = await patch_note(client, identity_id, "  work account, phone 1234  ")

    assert response.status_code == 200, response.text
    assert envelope(response)["data"]["note"] == "work account, phone 1234"
    assert await note_of(identity_id) == "work account, phone 1234"
    listed = envelope(await client.get("/api/v1/admin/identities"))["data"]
    assert [row["note"] for row in listed] == ["work account, phone 1234"]
    assert listed[0]["account_id"] is None
    assert listed[0]["account_seen_at"] is None


async def test_blank_and_null_both_clear_a_note(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity(note="old")

    assert (await patch_note(client, identity_id, "   ")).status_code == 200
    assert await note_of(identity_id) is None

    await patch_note(client, identity_id, "again")
    assert (await patch_note(client, identity_id, None)).status_code == 200
    assert await note_of(identity_id) is None


@pytest.mark.parametrize("note", ["first line\nsecond line", "x" * 201])
async def test_a_note_that_is_not_one_short_line_is_refused(client: Any, note: str) -> None:
    """A multi-line paste in the note box is the cookie jar meant for another."""
    await signed_in(client)
    identity_id = await make_identity(note="kept")

    response = await patch_note(client, identity_id, note)

    assert response.status_code in (400, 422), response.text
    assert error_code(response) == "INVALID_PARAM"
    assert await note_of(identity_id) == "kept"


async def test_an_empty_body_does_not_clear_a_note(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity(note="kept")

    response = await client.patch(f"/api/v1/admin/identities/{identity_id}", json={})

    assert response.status_code in (400, 422), response.text
    assert await note_of(identity_id) == "kept"


async def test_a_note_change_is_audited_without_its_text(client: Any) -> None:
    """People write the phone number a login belongs to into it."""
    await signed_in(client)
    identity_id = await make_identity()

    await patch_note(client, identity_id, "phone 13800000000")
    await patch_note(client, identity_id, "phone 13800000000")

    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "identity.note_changed")
            )
        ).all()
    assert [row.detail for row in rows] == [
        {"platform": "douyin", "had_note": False, "has_note": True}
    ]
    assert "13800000000" not in str(rows[0].detail)


async def test_a_viewer_cannot_change_a_note(client: Any) -> None:
    await signed_in(client, username="watcher", role=UserRole.VIEWER)
    identity_id = await make_identity(note="kept")

    response = await patch_note(client, identity_id, "changed")

    assert response.status_code == 403, response.text
    assert await note_of(identity_id) == "kept"


async def test_an_unknown_identity_is_not_found(client: Any) -> None:
    await signed_in(client)

    response = await patch_note(client, uuid.uuid4(), "x")

    assert response.status_code == 404


async def test_an_import_keeps_its_note(client: Any) -> None:
    await signed_in(client)

    response = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
            "note": " signed in as alice ",
        },
    )

    assert response.status_code == 201, response.text
    identity_id = uuid.UUID(envelope(response)["data"]["identity_id"])
    assert await note_of(identity_id) == "signed in as alice"


async def test_an_export_carries_the_note_and_a_restore_brings_it_back(client: Any) -> None:
    await signed_in(client)
    imported = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
            "note": "alice",
        },
    )
    identity_id = envelope(imported)["data"]["identity_id"]

    exported = envelope(
        await client.post("/api/v1/admin/identities/export", json={"identity_ids": [identity_id]})
    )["data"]
    assert [entry["note"] for entry in exported["identities"]] == ["alice"]

    restored = await client.post(
        "/api/v1/admin/identities/import/bundle",
        json={"version": exported["version"], "identities": exported["identities"]},
    )
    assert restored.status_code == 201, restored.text
    new_id = uuid.UUID(envelope(restored)["data"]["results"][0]["identity_id"])
    assert await note_of(new_id) == "alice"


async def test_a_restore_drops_a_note_this_build_would_refuse(client: Any) -> None:
    """A label is worth less than the identity it is on."""
    await signed_in(client)
    imported = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )
    identity_id = envelope(imported)["data"]["identity_id"]
    exported = envelope(
        await client.post("/api/v1/admin/identities/export", json={"identity_ids": [identity_id]})
    )["data"]
    entries = [{**entry, "note": "two\nlines"} for entry in exported["identities"]]

    restored = await client.post(
        "/api/v1/admin/identities/import/bundle",
        json={"version": exported["version"], "identities": entries},
    )

    assert restored.status_code == 201, restored.text
    new_id = uuid.UUID(envelope(restored)["data"]["results"][0]["identity_id"])
    assert await note_of(new_id) is None


# --------------------------------------------------------------------------
# Which identities serve an endpoint
# --------------------------------------------------------------------------


async def test_serves_lists_the_identities_rotation_may_send(client: Any) -> None:
    await signed_in(client)
    everywhere = await make_identity()
    posts_only = await make_identity(rotation=[POSTS])
    await make_identity(rotation=[])
    await make_identity(rotation=[DETAIL])

    listed = envelope(await client.get("/api/v1/admin/identities", params={"serves": POSTS}))

    assert {row["id"] for row in listed["data"]} == {str(everywhere), str(posts_only)}
    assert listed["meta"]["total"] == 2


async def test_coverage_counts_each_endpoint_the_way_the_scheduler_does(client: Any) -> None:
    await signed_in(client)
    await make_identity()
    await make_identity(authenticated=True, rotation=[POSTS])
    await make_identity(authenticated=True, rotation=[])
    await make_identity(authenticated=True, state="cooling")
    await make_identity(state="retired")

    data = envelope(await client.get("/api/v1/admin/identities/coverage"))["data"]

    by_platform = {entry["platform"]: entry for entry in data["platforms"]}
    assert set(by_platform) == {platform.value for platform in Platform}
    douyin = by_platform["douyin"]
    assert douyin["guests_first"] is True
    assert douyin["reserved"] == 1
    endpoints = {entry["endpoint"]: entry for entry in douyin["endpoints"]}
    posts = endpoints[POSTS]
    counted = ("login", "paging_login", "ready", "signed_in", "cooling", "dedicated")
    assert {key: posts[key] for key in counted} == {
        "login": "preferred",
        "paging_login": "required",
        "ready": 2,
        "signed_in": 1,
        "cooling": 1,
        "dedicated": 1,
    }
    assert (endpoints[DETAIL]["ready"], endpoints[DETAIL]["signed_in"]) == (1, 0)
    assert endpoints[DETAIL]["cooling"] == 1
    assert by_platform["tiktok"]["endpoints"][0]["ready"] == 0


async def test_coverage_reports_a_platform_with_guests_first_off(client: Any) -> None:
    await signed_in(client)
    response = await client.put(
        "/api/v1/admin/settings/pool.tiktok.guests_first", json={"value": False}
    )
    assert response.status_code == 200, response.text

    data = envelope(await client.get("/api/v1/admin/identities/coverage"))["data"]

    flags = {entry["platform"]: entry["guests_first"] for entry in data["platforms"]}
    assert flags == {"douyin": True, "tiktok": False, "bilibili": True}


async def test_capabilities_say_what_a_guest_loses_on_each_endpoint(client: Any) -> None:
    await signed_in(client)

    data = envelope(await client.get("/api/v1/system/capabilities"))["data"]

    douyin = {entry["endpoint"]: entry for entry in data["douyin"]}
    assert (douyin[POSTS]["login"], douyin[POSTS]["paging_login"]) == ("preferred", "required")
    assert (douyin[DETAIL]["login"], douyin[DETAIL]["paging_login"]) == ("none", None)
    assert {entry["login"] for entry in data["tiktok"]} == {"none"}
