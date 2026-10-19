"""`/api/v1/system/capabilities` through the mounted application.

The table itself is checked against the content routes in
``tests/unit/test_capabilities_route.py``. What only the mounted app can show is
who gets it: every signed-in role and every key must get the same answer, or
the console greys out different endpoints for a viewer than for an operator on
the same instance.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.core.types import Platform, Scope, UserRole
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

PATH = "/api/v1/system/capabilities"


async def test_it_requires_a_credential(client: Any) -> None:
    response = await client.get(PATH)
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_every_platform_is_listed_with_its_operations(client: Any) -> None:
    await signed_in(client)
    data = envelope(await client.get(PATH))["data"]

    assert set(data) == {platform.value for platform in Platform}
    for platform, rows in data.items():
        assert rows, f"{platform} lists nothing"
        for row in rows:
            assert set(row) == {
                "operation",
                "endpoint",
                "session_bound",
                "login",
                "paging_login",
            }
            assert row["endpoint"] == f"{platform}.{row['operation']}"
            assert row["login"] in {"none", "preferred", "required"}
            assert row["paging_login"] in {None, "none", "preferred", "required"}
    # The core five are served everywhere; a platform without them is not
    # registered at all (`missing_capabilities`).
    for rows in data.values():
        assert {"content_detail", "author_profile", "author_posts"} <= {
            row["operation"] for row in rows
        }


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.OPERATOR])
async def test_a_lower_role_sees_what_an_administrator_sees(
    api_app: Any, client: Any, role: UserRole
) -> None:
    await signed_in(client)
    expected = envelope(await client.get(PATH))["data"]

    async with anonymous_client(api_app) as other:
        await signed_in(other, username=f"user-{role.value}", role=role)
        assert envelope(await other.get(PATH))["data"] == expected


async def test_the_narrowest_key_reads_it(api_app: Any) -> None:
    """A single platform read scope - what an integration is typically given -
    is enough. The table says nothing a key could not learn from the API
    reference."""
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    async with anonymous_client(api_app) as http:
        response = await http.get(PATH, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200, response.text
    assert Platform.BILIBILI.value in envelope(response)["data"]
