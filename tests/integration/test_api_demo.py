"""The public demo account, end to end against a real database.

The unit tests next door prove the rules in isolation - the role ranks below
viewer, the allowlist matches the routes. These prove the two things only a
real request can show: that turning the switch off actually locks somebody out
who is already inside, and that a demo caller's request does not leave rows
behind.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from dtk.core.db import session_scope
from dtk.core.types import Outcome, Platform, Scope, UserRole
from dtk.db.models import ApiKey, RequestLog, Task, User
from dtk.services import demo
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    anonymous_client,
    envelope,
    error_code,
    login,
    signed_in,
)

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

DEMO_PASSWORD = "demo-password-for-the-test"


async def _provision() -> demo.DemoCredentials:
    from dtk.api.routes.passwords import hash_password

    async with session_scope() as session:
        return await demo.provision(session, hash_password=hash_password)


def _set_demo(app: Any, enabled: bool) -> None:
    """Flip the switch, as the settings route would after it reloads.

    A whole new Config rather than a mutated one, because that is what the
    application does: `Config` is an immutable snapshot replaced on reload, so
    a test that poked at a field would be exercising a state the running system
    never reaches.
    """
    from dtk.core.config import RUNTIME_SETTINGS, Config

    values = {key: app.state.config.get(key) for key in RUNTIME_SETTINGS}
    values[demo.SETTING_KEY] = enabled
    app.state.config = Config(values, app.state.config.version + 1)


# --------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------


async def test_provisioning_creates_one_account_with_the_two_read_scopes(api_app: Any) -> None:
    credentials = await _provision()

    assert credentials.username == demo.DEMO_USERNAME
    assert credentials.password
    assert credentials.api_key and credentials.api_key.startswith("dtk_")
    assert set(credentials.scopes) == {Scope.DOUYIN_READ.value, Scope.TIKTOK_READ.value}

    async with session_scope() as session:
        users = await session.scalar(
            select(func.count()).select_from(User).where(User.role == UserRole.DEMO.value)
        )
        assert users == 1


async def test_provisioning_twice_leaves_one_live_key(api_app: Any) -> None:
    """Otherwise an instance cannot answer "which of these is published"."""
    first = await _provision()
    second = await _provision()
    assert first.api_key != second.api_key

    async with session_scope() as session:
        user = await demo.find_user(session)
        assert user is not None
        live = await session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
        )
        total = await session.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id)
        )
    assert live == 1
    # Revoked rather than deleted: task and request rows still point at it.
    assert total == 2


# --------------------------------------------------------------------------
# The switch
# --------------------------------------------------------------------------


async def test_the_demo_cannot_log_in_while_the_switch_is_off(api_app: Any, client: Any) -> None:
    credentials = await _provision()
    _set_demo(api_app, False)

    response = await login(client, demo.DEMO_USERNAME, credentials.password or "")
    assert response.status_code == 401
    # Told the same thing a wrong password is told: the demo password is
    # published, so a truthful "the account exists but is off" would confirm it.
    assert error_code(response) == "UNAUTHENTICATED"


async def test_the_demo_can_log_in_while_the_switch_is_on(api_app: Any, client: Any) -> None:
    credentials = await _provision()
    _set_demo(api_app, True)

    response = await login(client, demo.DEMO_USERNAME, credentials.password or "")
    assert response.status_code == 200
    assert envelope(response)["data"]["user"]["role"] == UserRole.DEMO.value


async def test_turning_the_switch_off_locks_out_a_session_already_open(
    api_app: Any, client: Any
) -> None:
    """The property a login-time check alone would not give.

    A session cookie is good until it expires, so a demo visitor who was
    already inside would stay inside. The check runs on every request instead.
    """
    credentials = await _provision()
    _set_demo(api_app, True)
    assert (await login(client, demo.DEMO_USERNAME, credentials.password or "")).status_code == 200
    assert (await client.get("/api/v1/system/status")).status_code == 200

    _set_demo(api_app, False)
    after = await client.get("/api/v1/system/status")
    assert after.status_code == 401


async def test_the_published_key_stops_working_when_the_switch_goes_off(api_app: Any) -> None:
    credentials = await _provision()
    _set_demo(api_app, True)
    headers = {"X-API-Key": credentials.api_key or ""}

    async with anonymous_client(api_app) as http:
        assert (await http.get("/api/v1/system/status", headers=headers)).status_code == 200

    _set_demo(api_app, False)
    async with anonymous_client(api_app) as http:
        assert (await http.get("/api/v1/system/status", headers=headers)).status_code == 401


# --------------------------------------------------------------------------
# What the account may reach
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/identities",
        "/api/v1/admin/proxies",
        "/api/v1/admin/api-keys",
        "/api/v1/admin/users",
        "/api/v1/admin/settings",
    ],
)
async def test_the_demo_console_cannot_read_the_credential_pages(
    api_app: Any, client: Any, path: str
) -> None:
    """The reason the role ranks below viewer rather than beside it."""
    credentials = await _provision()
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.get(path)
    assert response.status_code == 403, f"{path} answered {response.status_code}"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/system/status", "/api/v1/admin/logs/requests", "/api/v1/admin/endpoints/health"],
)
async def test_the_demo_console_can_read_the_pages_it_is_meant_to(
    api_app: Any, client: Any, path: str
) -> None:
    credentials = await _provision()
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}: {response.text}"


async def test_the_published_key_cannot_read_the_logs_even_though_the_console_can(
    api_app: Any,
) -> None:
    """The scope check is what separates the two demo credentials.

    Same role, same instance; the session is unscoped and the key holds two
    platform read scopes, so this is refused without a second rule saying so.
    """
    credentials = await _provision()
    _set_demo(api_app, True)
    async with anonymous_client(api_app) as http:
        response = await http.get(
            "/api/v1/admin/logs/requests", headers={"X-API-Key": credentials.api_key or ""}
        )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/downloads"),
        ("POST", "/api/v1/auth/password"),
        ("POST", "/api/v1/tools/identity"),
        ("POST", "/api/v1/archive/recheck"),
        ("DELETE", "/api/v1/tasks/00000000-0000-0000-0000-000000000000"),
    ],
)
async def test_the_demo_console_cannot_write(
    api_app: Any, client: Any, method: str, path: str
) -> None:
    """Read-only means read-only, including for the unscoped console session."""
    credentials = await _provision()
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.request(method, path, json={})
    assert response.status_code == 403, f"{method} {path} answered {response.status_code}"
    assert error_code(response) == "FORBIDDEN_SCOPE"


async def test_an_administrator_is_unaffected_by_the_read_only_rule(
    api_app: Any, client: Any
) -> None:
    """The rule is a no-op for every other role, asserted rather than assumed."""
    await signed_in(client)
    _set_demo(api_app, True)
    response = await client.post("/api/v1/tools/parse-batch", json={"text": "nothing here"})
    assert response.status_code != 403


# --------------------------------------------------------------------------
# What it leaves behind, which is as little as possible
# --------------------------------------------------------------------------


async def test_a_demo_task_is_marked_and_a_real_one_is_not(api_app: Any, client: Any) -> None:
    """The mark is what the worker and the sweep both read later."""
    credentials = await _provision()
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.post(
        "/api/v1/parse", json={"url": "https://www.douyin.com/video/7126745726494821640"}
    )
    assert response.status_code in (200, 202), response.text

    async with session_scope() as session:
        marked = await session.scalar(
            select(func.count()).select_from(Task).where(Task.is_demo.is_(True))
        )
        unmarked = await session.scalar(
            select(func.count()).select_from(Task).where(Task.is_demo.is_(False))
        )
    assert marked == 1
    assert unmarked == 0


async def test_the_demo_sweep_removes_only_demo_rows(api_app: Any) -> None:
    from datetime import UTC, datetime, timedelta

    from dtk.core.types import TaskState
    from dtk.ops.retention import delete_expired_demo_tasks

    old = datetime.now(UTC) - timedelta(hours=2)
    async with session_scope() as session:
        for is_demo in (True, False):
            session.add(
                Task(
                    id=uuid.uuid4(),
                    endpoint="douyin.content_detail",
                    params={},
                    state=TaskState.DONE.value,
                    created_at=old,
                    is_demo=is_demo,
                )
            )

    async with session_scope() as session:
        removed = await delete_expired_demo_tasks(session, minutes=30)
    assert removed == 1

    async with session_scope() as session:
        left = await session.scalar(select(func.count()).select_from(Task))
    assert left == 1


async def test_a_demo_request_writes_no_request_log_row(api_app: Any) -> None:
    """Asserted at the writer, because it is the only one the table has."""
    from dtk.services.fetch import FetchContext, FetchService

    async with session_scope() as session:
        before = await session.scalar(select(func.count()).select_from(RequestLog))

    service = FetchService.__new__(FetchService)
    async with session_scope() as session:
        await FetchService._log_request(
            service,
            session,
            ctx=FetchContext(is_demo=True),
            platform=Platform.DOUYIN,
            endpoint="douyin.content_detail",
            identity_id=None,
            outcome=Outcome.OK,
            status=200,
            duration_ms=1,
            error_code=None,
        )

    async with session_scope() as session:
        after = await session.scalar(select(func.count()).select_from(RequestLog))
    assert after == before


async def test_the_demo_role_cannot_be_assigned_by_hand(api_app: Any, client: Any) -> None:
    """It is provisioned by the switch or not at all.

    A second demo account would have a password an administrator chose, no key,
    and would make "the demo account" ambiguous for everything that looks one up.
    """
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/users",
        json={"username": "demo2", "password": "a long enough password", "role": "demo"},
    )
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PARAM"
