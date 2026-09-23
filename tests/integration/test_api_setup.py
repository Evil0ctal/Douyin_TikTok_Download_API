"""First-run initialization against real PostgreSQL and Redis.

The three properties doc 06 asks for are all exercised here: the token is
compared in constant time and single use, five failures burn it, and once an
account exists the endpoint is closed permanently.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dtk.api.routes import setup
from dtk.core.db import session_scope
from dtk.core.redis import get_redis
from dtk.db.repositories import UserRepository
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


class BothReadFirst:
    """Redis, except that no GET returns until two of them have been issued.

    Left to the event loop the two requests usually do not overlap at all - the
    first has deleted the token before the second reads it - so a test without
    this passes against the racy code too. This holds both at the one point
    where the race lives: after the read, before anything is written.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._barrier = asyncio.Barrier(2)

    async def get(self, key: str) -> Any:
        value = await self._redis.get(key)
        await self._barrier.wait()
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._redis, name)


async def test_racing_requests_with_the_real_token_create_one_administrator(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only one of two concurrent requests holding the token may win.

    The token used to be read, compared, and only then deleted, with awaits in
    between, so two requests could both read it before either deleted it and
    both create an administrator. Deleting it is now the claim: DEL reports
    whether it removed the key, and only one caller can be the one that did.
    """
    token = await issue_token()
    racing = BothReadFirst(get_redis())
    monkeypatch.setattr(setup, "get_redis", lambda: racing)

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/setup/init",
                json={"token": token, "username": name, "password": "a-good-password"},
            )
            for name in ("first", "second")
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    loser = next(r for r in responses if r.status_code == 409)
    assert error_code(loser) == "SETUP_ALREADY_DONE"
    async with session_scope() as session:
        assert await UserRepository(session).count() == 1


async def test_a_wrong_token_does_not_burn_the_real_one(client: Any) -> None:
    """A wrong guess costs an attempt, never the token itself.

    Consuming the token on every read (GETDEL) would close the race above too,
    but it would let anyone who can reach the port stop the owner from ever
    finishing setup, one bad request per restart.
    """
    token = await issue_token()
    wrong = await client.post(
        "/api/setup/init",
        json={"token": "not-the-token", "username": "owner", "password": "a-good-password"},
    )
    assert wrong.status_code == 403

    right = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "owner", "password": "a-good-password"},
    )
    assert right.status_code == 201, right.text


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
