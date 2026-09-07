"""Console authentication: cookies, sessions, lockout and password changes."""

from __future__ import annotations

from typing import Any

import pytest

from dtk.api.deps import SESSION_COOKIE, SESSION_KEY
from dtk.api.routes import auth, sessions
from dtk.core.redis import get_redis
from dtk.core.types import Scope, UserRole
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    envelope,
    error_code,
    login,
    make_api_key,
    make_user,
    signed_in,
)

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery"


async def test_login_sets_an_httponly_session_cookie(client: Any) -> None:
    await make_user("owner", PASSWORD)
    response = await login(client, "owner", PASSWORD)

    assert response.status_code == 200
    body = envelope(response)
    assert body["data"]["user"]["username"] == "owner"
    assert "password" not in response.text

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    # Plain HTTP in the test harness, so Secure is dropped rather than making
    # the cookie undeliverable; the downgrade is logged.
    assert "Secure" not in cookie
    assert client.cookies.get(SESSION_COOKIE)


async def test_wrong_password_is_unauthenticated_and_leaves_no_session(client: Any) -> None:
    await make_user("owner", PASSWORD)
    response = await login(client, "owner", "not the password")

    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"
    assert not client.cookies.get(SESSION_COOKIE)


async def test_unknown_user_reports_the_same_error(client: Any) -> None:
    response = await login(client, "nobody", PASSWORD)
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_repeated_failures_lock_the_account_out(client: Any) -> None:
    await make_user("owner", PASSWORD)
    for _ in range(auth.MAX_FAILURES_PER_USERNAME):
        assert (await login(client, "owner", "wrong")).status_code == 401

    locked = await login(client, "owner", PASSWORD)
    assert locked.status_code == 429
    body = envelope(locked)
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["retry_after"] > 0
    assert locked.headers["retry-after"]


async def test_me_requires_a_credential(client: Any) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_me_describes_the_session_principal(client: Any) -> None:
    await signed_in(client, username="owner", password=PASSWORD)
    response = await client.get("/api/v1/auth/me")

    body = envelope(response)
    assert body["data"]["user"]["username"] == "owner"
    assert body["data"]["user"]["via"] == "session"
    assert body["data"]["api_key_id"] is None


async def test_me_works_with_an_api_key_and_reports_the_rate_limit(client: Any) -> None:
    user_id = await make_user("owner", PASSWORD)
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,), rate_limit=42)

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {key}"})
    body = envelope(response)
    assert body["data"]["user"]["via"] == "api_key"
    assert body["data"]["rate_limit_per_min"] == 42
    assert response.headers["x-ratelimit-limit"] == "42"
    assert response.headers["x-ratelimit-remaining"] == "41"
    assert response.headers["x-ratelimit-reset"]


async def test_logout_revokes_the_session(client: Any) -> None:
    await signed_in(client, username="owner", password=PASSWORD)
    token = client.cookies.get(SESSION_COOKIE)

    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert await get_redis().get(SESSION_KEY.format(token=token)) is None

    client.cookies.set(SESSION_COOKIE, token)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_sessions_are_listed_without_exposing_the_token(client: Any) -> None:
    user_id = await signed_in(client, username="owner", password=PASSWORD)
    token = client.cookies.get(SESSION_COOKIE)
    await sessions.create(user_id, ip="10.0.0.9", user_agent="another device")

    response = await client.get("/api/v1/auth/sessions")
    listed = envelope(response)["data"]

    assert len(listed) == 2
    assert token not in response.text
    assert sum(1 for item in listed if item["current"]) == 1
    assert {item["id"] for item in listed} == {sessions.session_id(token)} | {
        item["id"] for item in listed if not item["current"]
    }


async def test_revoking_others_keeps_the_current_session(client: Any) -> None:
    user_id = await signed_in(client, username="owner", password=PASSWORD)
    other = await sessions.create(user_id, ip="10.0.0.9", user_agent="another device")

    response = await client.delete("/api/v1/auth/sessions")
    assert envelope(response)["data"]["revoked"] == 1
    assert await get_redis().get(SESSION_KEY.format(token=other)) is None
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_revoking_one_session_by_its_public_id(client: Any) -> None:
    user_id = await signed_in(client, username="owner", password=PASSWORD)
    other = await sessions.create(user_id)

    response = await client.delete(f"/api/v1/auth/sessions/{sessions.session_id(other)}")
    assert response.status_code == 200
    assert await get_redis().get(SESSION_KEY.format(token=other)) is None

    missing = await client.delete("/api/v1/auth/sessions/deadbeefdeadbeef")
    assert error_code(missing) == "INVALID_PARAM"


async def test_password_change_requires_the_current_one(client: Any) -> None:
    await signed_in(client, username="owner", password=PASSWORD)

    wrong = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "nope", "new_password": "a-brand-new-password"},
    )
    assert wrong.status_code == 401

    same = await client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
    )
    assert error_code(same) == "INVALID_PARAM"


async def test_password_change_logs_other_devices_out(client: Any) -> None:
    user_id = await signed_in(client, username="owner", password=PASSWORD)
    other = await sessions.create(user_id)

    response = await client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
    )
    body = envelope(response)
    assert body["data"]["changed"] is True
    assert body["data"]["revoked_sessions"] == 1
    assert await get_redis().get(SESSION_KEY.format(token=other)) is None

    # The current session survives, and the new password is the one that works.
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await login(client, "owner", "a-brand-new-password")).status_code == 200


async def test_an_api_key_cannot_change_its_owners_password(client: Any) -> None:
    user_id = await make_user("owner", PASSWORD)
    key = await make_api_key(user_id, scopes=(Scope.ADMIN,))

    response = await client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert error_code(response) == "INVALID_PARAM"


async def test_a_viewer_session_still_authenticates(client: Any) -> None:
    await signed_in(client, username="watcher", password=PASSWORD, role=UserRole.VIEWER)
    response = await client.get("/api/v1/auth/me")
    assert envelope(response)["data"]["user"]["role"] == UserRole.VIEWER.value
