"""Naming an identity on a data request.

The case this exists for: a caller imported a jar from their own logged-in
browser and wants a post only that account can see. The API's job is to make
the pin either work or fail loudly - never to quietly answer from a different
session, because the platform's reply to the wrong session is a 200 with an
empty body, which a caller reads as "the post is gone".

The scheduler's half of the contract lives in
tests/integration/test_scheduler_concurrency.py. This module is about the
edge: who may ask, what a bad ask answers, and what reaches the task row.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.types import IdentitySource, Scope, UserRole
from dtk.db.models import Identity, Task
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    envelope,
    error_code,
    make_api_key,
    make_user,
    signed_in,
)

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

DOUYIN_VIDEO = "https://www.douyin.com/video/7123456789012345678"


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
                source=IdentitySource.MINTED.value,
            )
        )
    return identity_id


async def stored_params(response: Any) -> dict[str, Any]:
    task_id = uuid.UUID(envelope(response)["data"]["task_id"])
    async with session_scope() as session:
        row = await session.get(Task, task_id)
    assert row is not None
    return dict(row.params or {})


# --------------------------------------------------------------------------
# Who may ask
# --------------------------------------------------------------------------


async def test_a_plain_read_key_cannot_name_an_identity(client: Any) -> None:
    """The pool is instance-wide and its rows have no owner, so naming one is
    asking to send a request as whoever imported that jar. A key minted to read
    Douyin must not reach someone's logged-in session."""
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    identity_id = await make_identity()

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(identity_id)},
        headers={"X-API-Key": key},
    )

    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_a_key_holding_identity_manage_may_name_one(client: Any) -> None:
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ, Scope.IDENTITY_MANAGE))
    identity_id = await make_identity()

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(identity_id)},
        headers={"X-API-Key": key},
    )

    assert response.status_code == 202, response.text
    assert (await stored_params(response))["identity"] == str(identity_id)


async def test_a_viewer_session_cannot_name_an_identity(client: Any) -> None:
    """A console session is not scope-bounded, so the role is the only gate
    left. Reading the pool is a viewer's business; borrowing a session is not."""
    await signed_in(client, username="looker", role=UserRole.VIEWER)
    identity_id = await make_identity()

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(identity_id)},
    )

    assert error_code(response) == "FORBIDDEN_SCOPE"
    details = envelope(response)["error"]["details"]
    assert details["required_roles"] == ["operator"]
    assert details["have_role"] == "viewer"
    assert details["field"] == "identity"


# --------------------------------------------------------------------------
# What a bad ask answers
# --------------------------------------------------------------------------


async def test_a_malformed_identity_is_refused_before_anything_is_queued(
    client: Any,
) -> None:
    await signed_in(client)

    response = await client.get(
        "/api/v1/douyin/video", params={"url": DOUYIN_VIDEO, "identity": "not-a-uuid"}
    )

    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["field"] == "identity"


async def test_an_unknown_identity_is_a_404_not_a_dead_task(client: Any) -> None:
    """Checked at the edge on purpose. Left to the worker it would queue, run
    and fail seconds later with a pool-exhausted 503 - three wrong signals for
    one mistyped uuid."""
    await signed_in(client)

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(uuid.uuid4())},
    )

    assert error_code(response) == "NOT_FOUND"


async def test_a_retired_identity_says_so(client: Any) -> None:
    """Retirement wipes the ciphertext, so there is no session left to borrow."""
    await signed_in(client)
    identity_id = await make_identity(state="retired")

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(identity_id)},
    )

    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["reason"] == "retired"


async def test_an_identity_of_the_wrong_platform_is_refused(client: Any) -> None:
    """A TikTok jar cannot see a Douyin post, and the scheduler would refuse it
    anyway - but only after the task had queued and run."""
    await signed_in(client)
    identity_id = await make_identity(platform="tiktok")

    response = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(identity_id)},
    )

    assert error_code(response) == "INVALID_PARAM"
    details = envelope(response)["error"]["details"]
    assert details["identity_platform"] == "tiktok"
    assert details["endpoint_platform"] == "douyin"


# --------------------------------------------------------------------------
# What reaches the task
# --------------------------------------------------------------------------


async def test_a_pinned_request_does_not_join_an_unpinned_one(client: Any) -> None:
    """Coalescing is the quiet way a pin gets lost: the same post, already in
    flight for somebody else, answered from whichever identity that task drew."""
    await signed_in(client)
    identity_id = await make_identity()

    plain = await client.get("/api/v1/douyin/video", params={"url": DOUYIN_VIDEO})
    pinned = await client.get(
        "/api/v1/douyin/video",
        params={"url": DOUYIN_VIDEO, "identity": str(identity_id)},
    )

    assert envelope(plain)["data"]["task_id"] != envelope(pinned)["data"]["task_id"]


async def test_two_requests_pinned_to_the_same_identity_do_join(client: Any) -> None:
    """They are asking the same question of the same session."""
    await signed_in(client)
    identity_id = await make_identity()
    params = {"url": DOUYIN_VIDEO, "identity": str(identity_id)}

    first = await client.get("/api/v1/douyin/video", params=params)
    second = await client.get("/api/v1/douyin/video", params=params)

    assert envelope(first)["data"]["task_id"] == envelope(second)["data"]["task_id"]


async def test_omitting_the_parameter_stores_nothing(client: Any) -> None:
    await signed_in(client)

    response = await client.get("/api/v1/douyin/video", params={"url": DOUYIN_VIDEO})

    assert "identity" not in await stored_params(response)


# --------------------------------------------------------------------------
# include_raw on the page endpoints
# --------------------------------------------------------------------------


async def test_a_list_endpoint_now_accepts_include_raw(client: Any) -> None:
    """It used to answer 200-with-no-raw and the console rendered a button that
    re-sent the identical request."""
    await signed_in(client)

    response = await client.get(
        "/api/v1/douyin/user/posts",
        params={"sec_user_id": "MS4wLjABAAAAexample", "include_raw": "true"},
    )

    assert response.status_code == 202, response.text
    assert (await stored_params(response))["include_raw"] is True


async def test_include_raw_discriminates_a_list_endpoint_task(client: Any) -> None:
    """Two callers wanting different shapes of the same page are not asking the
    same question; joining them freezes whichever shape arrived first."""
    await signed_in(client)
    base = {"sec_user_id": "MS4wLjABAAAAexample"}

    plain = await client.get("/api/v1/douyin/user/posts", params=base)
    raw = await client.get("/api/v1/douyin/user/posts", params={**base, "include_raw": "true"})

    assert envelope(plain)["data"]["task_id"] != envelope(raw)["data"]["task_id"]
