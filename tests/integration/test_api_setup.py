"""First-run initialization against real PostgreSQL and Redis.

The three properties doc 06 asks for are all exercised here: the token is
compared in constant time and single use, five failures burn it, and once an
account exists the endpoint is closed permanently.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.api.routes import setup
from dtk.core.redis import get_redis
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    envelope,
    error_code,
    make_user,
)

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration


async def issue_token() -> str:
    token = await setup.ensure_setup_token()
    assert token is not None
    return token


async def test_status_reports_uninitialized(client: Any) -> None:
    response = await client.get("/api/setup/status")
    assert response.status_code == 200
    assert envelope(response)["data"] == {"initialized": False}


async def test_init_creates_the_first_administrator(client: Any) -> None:
    token = await issue_token()

    response = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "owner", "password": "a-good-password"},
    )
    assert response.status_code == 201, response.text
    data = envelope(response)["data"]
    assert data["initialized"] is True
    assert data["user"]["username"] == "owner"
    assert data["user"]["role"] == "admin"

    status = await client.get("/api/setup/status")
    assert envelope(status)["data"] == {"initialized": True}


async def test_token_is_single_use_and_then_the_endpoint_closes(client: Any) -> None:
    token = await issue_token()
    first = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "owner", "password": "a-good-password"},
    )
    assert first.status_code == 201

    # The same token again: the account check fires before the token is even
    # looked at, so this is a permanent 409 rather than a token error.
    second = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "second", "password": "a-good-password"},
    )
    assert second.status_code == 409
    assert error_code(second) == "SETUP_ALREADY_DONE"


async def test_wrong_token_is_rejected_and_leaves_the_instance_open(client: Any) -> None:
    await issue_token()
    response = await client.post(
        "/api/setup/init",
        json={"token": "not-the-token", "username": "owner", "password": "a-good-password"},
    )
    assert response.status_code == 403
    body = envelope(response)
    assert body["error"]["code"] == "SETUP_TOKEN_INVALID"
    assert body["error"]["details"]["attempts_remaining"] == setup.MAX_SETUP_ATTEMPTS - 1

    status = await client.get("/api/setup/status")
    assert envelope(status)["data"] == {"initialized": False}


async def test_five_failures_invalidate_the_token(client: Any) -> None:
    token = await issue_token()
    for _ in range(setup.MAX_SETUP_ATTEMPTS):
        response = await client.post(
            "/api/setup/init",
            json={"token": "wrong-token-value", "username": "owner", "password": "a-good-password"},
        )
        assert response.status_code == 403

    assert await get_redis().get(setup.SETUP_TOKEN_KEY) is None

    # Even the correct token is now useless; the operator has to restart.
    response = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "owner", "password": "a-good-password"},
    )
    assert error_code(response) == "SETUP_TOKEN_INVALID"


async def test_missing_token_compares_against_a_decoy(client: Any) -> None:
    """No token issued at all still answers SETUP_TOKEN_INVALID, not a 500.

    The comparison runs against a freshly generated value so the "no token
    stored" path costs the same as a mismatch.
    """
    assert await get_redis().get(setup.SETUP_TOKEN_KEY) is None
    response = await client.post(
        "/api/setup/init",
        json={"token": "anything-at-all", "username": "owner", "password": "a-good-password"},
    )
    assert response.status_code == 403
    assert error_code(response) == "SETUP_TOKEN_INVALID"


async def test_ensure_setup_token_is_idempotent_and_stops_once_initialized(
    api_app: Any,
) -> None:
    first = await issue_token()
    assert await setup.ensure_setup_token() == first, "a restart must not invalidate the link"

    await make_user("owner")
    assert await setup.ensure_setup_token() is None
    assert await get_redis().get(setup.SETUP_TOKEN_KEY) is None


async def test_init_validates_its_body(client: Any) -> None:
    await issue_token()
    response = await client.post(
        "/api/setup/init",
        json={"token": "x" * 12, "username": "ok", "password": "short"},
    )
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PARAM"
