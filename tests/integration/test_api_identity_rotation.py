"""Keeping an identity out of rotation (#789).

The scheduler's half - unpinned traffic never reaches a reserved identity, a
pin always does - is in tests/integration/test_scheduler_concurrency.py. This
module is the edge: who may change it, what a bad value answers, and the two
other places that pick identities without the scheduler.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from dtk.core.db import session_scope
from dtk.core.types import IdentitySource, Platform, UserRole
from dtk.db.models import AuditLog, Identity
from dtk.db.repositories import IdentityRepository
from dtk.identity.pool import IdentityPool
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration


async def make_identity(*, platform: str = "douyin", state: str = "active") -> uuid.UUID:
    identity_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            Identity(
                id=identity_id,
                platform=platform,
                state=state,
                cookies_encrypted=b"ciphertext",
                fingerprint={},
                source=IdentitySource.IMPORTED.value,
            )
        )
    return identity_id


async def set_rotation(client: Any, identity_id: uuid.UUID, body: dict[str, Any]) -> Any:
    return await client.post(f"/api/v1/admin/identities/{identity_id}/rotation", json=body)


async def stored(identity_id: uuid.UUID) -> list[str] | None:
    async with session_scope() as session:
        row = await session.get(Identity, identity_id)
    assert row is not None
    return row.rotation_endpoints


async def test_an_identity_can_be_kept_for_the_requests_that_name_it(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity()

    response = await set_rotation(client, identity_id, {"endpoints": []})

    assert response.status_code == 200, response.text
    assert envelope(response)["data"] == {"id": str(identity_id), "rotation_endpoints": []}
    assert await stored(identity_id) == []
    listed = envelope(await client.get("/api/v1/admin/identities"))["data"]
    assert [row["rotation_endpoints"] for row in listed] == [[]]
    async with session_scope() as session:
        audit = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "identity.rotation_changed")
            )
        ).all()
    assert [row.detail for row in audit] == [{"from": None, "to": [], "platform": "douyin"}]


async def test_null_puts_it_back_in_rotation(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity()
    await set_rotation(client, identity_id, {"endpoints": []})

    response = await set_rotation(client, identity_id, {"endpoints": None})

    assert response.status_code == 200, response.text
    assert await stored(identity_id) is None


async def test_endpoint_names_are_stored_once_and_in_order(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity()

    response = await set_rotation(
        client,
        identity_id,
        {"endpoints": ["douyin.author_posts", "douyin.content_detail", "douyin.author_posts"]},
    )

    assert response.status_code == 200, response.text
    assert await stored(identity_id) == ["douyin.author_posts", "douyin.content_detail"]


async def test_a_name_from_another_platform_is_refused_not_stored(client: Any) -> None:
    """A typo stored would read as "reserved for nothing" and say nothing why."""
    await signed_in(client)
    identity_id = await make_identity()

    response = await set_rotation(
        client, identity_id, {"endpoints": ["tiktok.content_detail", "douyin.author_post"]}
    )

    assert response.status_code == 400
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["unknown"] == [
        "douyin.author_post",
        "tiktok.content_detail",
    ]
    assert await stored(identity_id) is None


async def test_leaving_the_field_out_is_not_lifting_a_restriction(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity()
    await set_rotation(client, identity_id, {"endpoints": []})

    response = await set_rotation(client, identity_id, {})

    assert response.status_code == 400
    assert await stored(identity_id) == []


async def test_a_retired_identity_is_refused(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity(state="retired")

    response = await set_rotation(client, identity_id, {"endpoints": []})

    assert response.status_code == 400
    assert error_code(response) == "INVALID_PARAM"


async def test_a_viewer_cannot_change_it(client: Any) -> None:
    await signed_in(client, username="looker", role=UserRole.VIEWER)
    identity_id = await make_identity()

    response = await set_rotation(client, identity_id, {"endpoints": []})

    assert response.status_code == 403
    assert await stored(identity_id) is None


async def test_an_unknown_identity_is_a_404(client: Any) -> None:
    await signed_in(client)

    response = await set_rotation(client, uuid.uuid4(), {"endpoints": []})

    assert response.status_code == 404


async def test_a_reserved_identity_is_not_capacity_for_the_pool_level(client: Any) -> None:
    """Counted, it let one kept account hold the level at its target while
    general traffic had nothing to use - and the filler saw nothing to do."""
    await signed_in(client)
    kept = await make_identity()
    await make_identity()
    await set_rotation(client, kept, {"endpoints": []})

    async with session_scope() as session:
        usable = await IdentityPool(cipher=None).usable_count(  # type: ignore[arg-type]
            session, Platform.DOUYIN, max_fail_streak=5
        )
        in_rotation = await IdentityRepository(session).list_by_state(
            platform=Platform.DOUYIN, in_rotation=True
        )

    assert usable == 1
    assert kept not in {row.id for row in in_rotation}
