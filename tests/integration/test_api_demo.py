"""The public demo account, end to end against a real database.

The unit tests next door prove the rules in isolation - the role ranks below
viewer, the allowlist matches the routes. These prove the two things only a
real request can show: that turning the switch off actually locks somebody out
who is already inside, and that a demo caller's request does not leave rows
behind.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from dtk.core.db import session_scope
from dtk.core.types import PLATFORM_READ_SCOPES, Outcome, Platform, Scope, UserRole
from dtk.db.models import ApiKey, Identity, RequestLog, Task, User
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


async def _provision(app: Any = None) -> demo.DemoCredentials:
    from dtk.api.routes.passwords import hash_password
    from dtk.core.crypto import Cipher

    cipher = app.state.cipher if app is not None else Cipher(_test_secret())
    async with session_scope() as session:
        return await demo.provision(session, hash_password=hash_password, cipher=cipher)


def _test_secret() -> str:
    from tests.integration.test_api_support import bootstrap_settings

    return bootstrap_settings().secret_key


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


async def test_provisioning_creates_one_account_with_the_platform_read_scopes(api_app: Any) -> None:
    credentials = await _provision(api_app)

    assert credentials.username == demo.DEMO_USERNAME
    assert credentials.password
    assert credentials.api_key and credentials.api_key.startswith("dtk_")
    assert set(credentials.scopes) == {scope.value for scope in PLATFORM_READ_SCOPES}

    async with session_scope() as session:
        users = await session.scalar(
            select(func.count()).select_from(User).where(User.role == UserRole.DEMO.value)
        )
        assert users == 1


async def test_provisioning_twice_leaves_one_live_key(api_app: Any) -> None:
    """Otherwise an instance cannot answer "which of these is published"."""
    first = await _provision(api_app)
    second = await _provision(api_app)
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
    credentials = await _provision(api_app)
    _set_demo(api_app, False)

    response = await login(client, demo.DEMO_USERNAME, credentials.password or "")
    assert response.status_code == 401
    # Told the same thing a wrong password is told: the demo password is
    # published, so a truthful "the account exists but is off" would confirm it.
    assert error_code(response) == "UNAUTHENTICATED"


async def test_the_demo_can_log_in_while_the_switch_is_on(api_app: Any, client: Any) -> None:
    credentials = await _provision(api_app)
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
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    assert (await login(client, demo.DEMO_USERNAME, credentials.password or "")).status_code == 200
    assert (await client.get("/api/v1/system/status")).status_code == 200

    _set_demo(api_app, False)
    after = await client.get("/api/v1/system/status")
    assert after.status_code == 401


async def test_the_published_key_stops_working_when_the_switch_goes_off(api_app: Any) -> None:
    credentials = await _provision(api_app)
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
        "/api/v1/admin/identities/coverage",
        "/api/v1/admin/proxies",
        "/api/v1/admin/users",
        "/api/v1/admin/settings",
    ],
)
async def test_the_demo_console_cannot_read_the_credential_pages(
    api_app: Any, client: Any, path: str
) -> None:
    """The reason the role ranks below viewer rather than beside it.

    `/admin/api-keys` is deliberately absent from this list: a visitor has to be
    able to copy the published key or the demo API is unusable. It is opened and
    then filtered to that one row - see
    :func:`test_the_demo_sees_only_its_own_key_on_the_keys_page`.
    """
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.get(path)
    assert response.status_code == 403, f"{path} answered {response.status_code}"


async def test_the_demo_cannot_read_transcriptions_even_with_a_known_id(
    api_app: Any, client: Any
) -> None:
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")
    transcription_id = uuid.uuid4()

    response = await client.get(f"/api/v1/transcriptions/{transcription_id}")
    assert response.status_code == 403

    await signed_in(client, username="transcription-viewer", role=UserRole.VIEWER)
    response = await client.get(f"/api/v1/transcriptions/{transcription_id}")
    assert response.status_code == 404


async def test_the_demo_can_read_only_demo_task_results(api_app: Any, client: Any) -> None:
    from datetime import UTC, datetime

    from dtk.core.types import TaskState

    real_task_id = uuid.uuid4()
    demo_task_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with session_scope() as session:
        for task_id, is_demo in ((real_task_id, False), (demo_task_id, True)):
            session.add(
                Task(
                    id=task_id,
                    endpoint="douyin.content_detail",
                    params={},
                    state=TaskState.DONE.value,
                    result={"secret": "result"},
                    created_at=now,
                    finished_at=now,
                    is_demo=is_demo,
                )
            )

    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    assert (await client.get(f"/api/v1/tasks/{real_task_id}")).status_code == 403
    assert (await client.get(f"/api/v1/tasks/{real_task_id}/events")).status_code == 403
    allowed = await client.get(f"/api/v1/tasks/{demo_task_id}")
    assert allowed.status_code == 200
    assert envelope(allowed)["data"]["data"] == {"secret": "result"}


async def test_the_demo_reads_the_request_log_without_identity_notes(
    api_app: Any, client: Any
) -> None:
    """A note is where somebody writes whose account a login is."""
    identity_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            Identity(
                id=identity_id,
                platform="douyin",
                state="active",
                cookies_encrypted=b"ciphertext",
                fingerprint={},
                source="imported",
                note="phone 13800000000",
            )
        )
        session.add(
            RequestLog(
                ts=datetime.now(UTC),
                request_id=uuid.uuid4(),
                platform="douyin",
                endpoint="douyin.content_detail",
                identity_id=identity_id,
                outcome="ok",
                duration_ms=100,
            )
        )
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.get("/api/v1/admin/logs/requests")

    assert response.status_code == 200, response.text
    assert [row["identity_note"] for row in envelope(response)["data"]] == [None]
    assert "13800000000" not in response.text


@pytest.mark.parametrize(
    "path",
    ["/api/v1/system/status", "/api/v1/admin/logs/requests", "/api/v1/admin/endpoints/health"],
)
async def test_the_demo_console_can_read_the_pages_it_is_meant_to(
    api_app: Any, client: Any, path: str
) -> None:
    credentials = await _provision(api_app)
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
    credentials = await _provision(api_app)
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
    credentials = await _provision(api_app)
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
    credentials = await _provision(api_app)
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


@pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
async def test_the_demo_account_cannot_be_promoted(api_app: Any, client: Any, role: str) -> None:
    """Only *assigning* the demo role was refused, so the reverse went through.

    The Users page saves a role the moment its picker changes, which made one
    stray click enough to turn the account whose password the login page prints
    for strangers into an administrator.
    """
    await _provision(api_app)
    await signed_in(client)
    async with session_scope() as session:
        account = await demo.find_user(session)
        assert account is not None
        account_id = account.id

    response = await client.put(f"/api/v1/admin/users/{account_id}", json={"role": role})
    assert error_code(response) == "INVALID_PARAM"
    async with session_scope() as session:
        stored = await session.get(User, account_id)
        assert stored is not None
        assert stored.role == UserRole.DEMO.value


async def test_the_demo_password_is_not_reset_by_hand(api_app: Any, client: Any) -> None:
    """The login page shows a stored copy of it, which a reset would leave
    behind: the published password would stop working while still printed."""
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    async with anonymous_client(api_app) as admin:
        await signed_in(admin)
        async with session_scope() as session:
            account = await demo.find_user(session)
            assert account is not None
        response = await admin.put(
            f"/api/v1/admin/users/{account.id}", json={"password": "chosen by an admin"}
        )
    assert error_code(response) == "INVALID_PARAM"
    assert (await login(client, demo.DEMO_USERNAME, credentials.password or "")).status_code == 200


async def test_the_demo_account_cannot_be_deleted(api_app: Any, client: Any) -> None:
    """Switching it off is how it is withdrawn; deleting it leaves the switch on
    with nothing behind it."""
    await _provision(api_app)
    await signed_in(client)
    async with session_scope() as session:
        account = await demo.find_user(session)
        assert account is not None

    response = await client.delete(f"/api/v1/admin/users/{account.id}")
    assert error_code(response) == "INVALID_PARAM"
    async with session_scope() as session:
        assert await demo.find_user(session) is not None


# --------------------------------------------------------------------------
# Who sees the demo's state
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.OPERATOR, UserRole.VIEWER])
async def test_every_console_reader_sees_whether_the_demo_runs(
    api_app: Any, client: Any, role: UserRole
) -> None:
    """The Settings card read a 403 here as "off" and showed a running demo as
    switched off to everyone below administrator."""
    await _provision(api_app)
    _set_demo(api_app, True)
    await signed_in(client, username="reader", role=role)

    response = await client.get("/api/v1/admin/demo")
    assert response.status_code == 200, response.text
    data = envelope(response)["data"]
    assert data["enabled"] is True
    assert data["provisioned"] is True
    assert data["password"] is None
    assert data["api_key"] is None


async def test_only_an_administrator_reissues_the_demo(api_app: Any, client: Any) -> None:
    await _provision(api_app)
    _set_demo(api_app, True)
    await signed_in(client, username="op", role=UserRole.OPERATOR)
    assert (await client.post("/api/v1/admin/demo/rotate")).status_code == 403


async def test_the_demo_console_still_cannot_read_the_demo_state(api_app: Any, client: Any) -> None:
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")
    assert (await client.get("/api/v1/admin/demo")).status_code == 403


async def test_a_key_minted_by_an_older_release_is_reported_as_missing_scopes(
    api_app: Any, client: Any
) -> None:
    """A demo published before 5.1.6 kept refusing Bilibili, and nothing on
    the console said a reissue would fix it."""
    await _provision(api_app)
    _set_demo(api_app, True)
    async with session_scope() as session:
        account = await demo.find_user(session)
        assert account is not None
        key = await demo.find_key(session, account.id)
        assert key is not None
        key.scopes = [Scope.DOUYIN_READ.value, Scope.TIKTOK_READ.value]
    await signed_in(client)

    stale = envelope(await client.get("/api/v1/admin/demo"))["data"]
    assert stale["missing_scopes"] == [Scope.BILIBILI_READ.value]

    rotated = envelope(await client.post("/api/v1/admin/demo/rotate"))["data"]
    assert rotated["missing_scopes"] == []
    fresh = envelope(await client.get("/api/v1/admin/demo"))["data"]
    assert fresh["missing_scopes"] == []


async def test_the_demo_key_cannot_be_widened_by_an_edit(api_app: Any, client: Any) -> None:
    """Its scopes are what the demo service publishes to strangers. An edit
    route that let an operator add `archive:export` to it would publish a copy
    of the database."""
    await _provision(api_app)
    _set_demo(api_app, True)
    await signed_in(client)
    rows = envelope(await client.get("/api/v1/admin/api-keys"))["data"]
    published = next(row for row in rows if row["name"] == demo.DEMO_KEY_NAME)

    response = await client.patch(
        f"/api/v1/admin/api-keys/{published['id']}",
        json={"scopes": [Scope.ARCHIVE_EXPORT.value]},
    )
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["setting"] == demo.SETTING_KEY


# --------------------------------------------------------------------------
# The published pair, which is readable on purpose
# --------------------------------------------------------------------------


async def test_the_login_page_is_told_the_demo_credentials(api_app: Any) -> None:
    """Unauthenticated, because the login page is."""
    credentials = await _provision(api_app)
    _set_demo(api_app, True)

    async with anonymous_client(api_app) as http:
        response = await http.get("/api/v1/auth/demo")
    assert response.status_code == 200
    data = envelope(response)["data"]
    assert data["enabled"] is True
    assert data["username"] == demo.DEMO_USERNAME
    assert data["password"] == credentials.password


async def test_the_login_page_is_told_nothing_when_the_demo_is_off(api_app: Any) -> None:
    """An instance that runs no demo must look like one that never could."""
    await _provision(api_app)
    _set_demo(api_app, False)

    async with anonymous_client(api_app) as http:
        response = await http.get("/api/v1/auth/demo")
    data = envelope(response)["data"]
    assert data == {"enabled": False}


async def test_the_demo_key_is_listed_in_full_and_others_are_not(api_app: Any, client: Any) -> None:
    """The asymmetry the API keys page explains.

    An operator's key has no plaintext anywhere in this system, so `secret` is
    null for it - not redacted, absent.
    """
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    admin_id = await signed_in(client)
    await support.make_api_key(admin_id, name="an operator's key")

    rows = envelope(await client.get("/api/v1/admin/api-keys"))["data"]
    by_name = {row["name"]: row for row in rows}

    published = by_name[demo.DEMO_KEY_NAME]
    assert published["demo"] is True
    assert published["secret"] == credentials.api_key

    ordinary = by_name["an operator's key"]
    assert ordinary["demo"] is False
    assert ordinary["secret"] is None
    assert ordinary["demo_owned"] is False


async def test_the_demo_key_stops_being_readable_when_the_demo_is_off(
    api_app: Any, client: Any
) -> None:
    await _provision(api_app)
    _set_demo(api_app, False)
    await signed_in(client)

    rows = envelope(await client.get("/api/v1/admin/api-keys"))["data"]
    published = next(row for row in rows if row["name"] == demo.DEMO_KEY_NAME)
    assert published["secret"] is None
    assert published["demo"] is False
    # Still the demo's, which is what keeps the console from offering an edit
    # the server refuses.
    assert published["demo_owned"] is True


async def test_a_rotation_logs_the_demo_out_and_nobody_else(api_app: Any) -> None:
    """Rotating answered 500 on any instance where anybody had logged in.

    Every session keeps a ``session:meta:{token}`` hash beside its
    ``session:{token}`` string, under the same prefix the demo's logout used to
    scan, and GET on a hash raises WRONGTYPE. So the reissue the 5.1.6 upgrade
    notes ask for failed and rolled back, and turning the switch off left the
    demo's sessions alive behind a logged error.
    """
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    async with anonymous_client(api_app) as admin, anonymous_client(api_app) as visitor:
        await signed_in(admin)
        assert (
            await login(visitor, demo.DEMO_USERNAME, credentials.password or "")
        ).status_code == 200
        assert (await visitor.get("/api/v1/system/status")).status_code == 200

        rotated = await admin.post("/api/v1/admin/demo/rotate")
        assert rotated.status_code == 200, rotated.text
        assert envelope(rotated)["data"]["password"] != credentials.password

        assert (await visitor.get("/api/v1/system/status")).status_code == 401
        assert (await admin.get("/api/v1/system/status")).status_code == 200


async def test_a_rotation_changes_what_the_login_page_offers(api_app: Any) -> None:
    """Otherwise a rotated demo would leave the login page prefilling a password
    that no longer works, which is worse than not prefilling at all."""
    first = await _provision(api_app)
    _set_demo(api_app, True)
    second = await _provision(api_app)

    async with anonymous_client(api_app) as http:
        data = envelope(await http.get("/api/v1/auth/demo"))["data"]
    assert data["password"] == second.password
    assert data["password"] != first.password


async def test_the_demo_sees_only_its_own_key_on_the_keys_page(api_app: Any, client: Any) -> None:
    """A visitor may copy the published key. The operator's inventory - names,
    scopes, rate limits, last used - is not a visitor's business, and is
    filtered in the query so those rows are never loaded."""
    credentials = await _provision(api_app)
    _set_demo(api_app, True)

    # An operator's key exists alongside the demo's.
    async with anonymous_client(api_app) as other:
        admin_id = await signed_in(other, username="owner", password="correct horse battery")
    await support.make_api_key(admin_id, name="private key")

    await login(client, demo.DEMO_USERNAME, credentials.password or "")
    rows = envelope(await client.get("/api/v1/admin/api-keys"))["data"]

    assert [row["name"] for row in rows] == [demo.DEMO_KEY_NAME]
    assert rows[0]["secret"] == credentials.api_key


async def test_the_demo_still_cannot_create_or_revoke_a_key(api_app: Any, client: Any) -> None:
    """Reading the page is not permission to change it."""
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    created = await client.post(
        "/api/v1/admin/api-keys", json={"name": "mine", "scopes": ["douyin:read"]}
    )
    assert created.status_code == 403


async def test_the_demo_console_can_read_its_own_principal(api_app: Any, client: Any) -> None:
    """`/auth/me` is how the console finds out it is a demo.

    It was behind `authenticated`, whose floor is viewer, so a demo session got
    403 here and the console never learned its own role. Nothing was protected
    by that: `/auth/demo` publishes the account and its scopes in plaintext to
    anonymous callers while the switch is on. What it cost was the trimming -
    `navItemsFor` in `web/src/lib/nav.ts` filters on this role, so with no role
    it rendered every page, including Identities, Users, Settings and Backup,
    each of which then answered 403 when pressed.
    """
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    user = response.json()["data"]["user"]
    assert user["role"] == "demo"
    assert user["username"] == demo.DEMO_USERNAME
    assert user["via"] == "session"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("explain", "true"),
        ("identity", "00000000-0000-0000-0000-000000000000"),
    ],
)
async def test_the_demo_cannot_ask_for_an_identitys_own_credentials(
    api_app: Any, client: Any, field: str, value: str
) -> None:
    """Both parameters hand back a jar, so both are operator-only.

    `explain` returns the signed upstream URL - which carries the identity's
    msToken - together with the cookie header itself; `identity` picks which
    account answers. A redacted `explain` was considered and is not offered:
    with the URL and the jar removed, what is left is the method, the endpoint
    and the identity id, all of which the demo console can already read off the
    logs page. The refusal names the field so the caller knows which one to drop.
    """
    credentials = await _provision(api_app)
    _set_demo(api_app, True)
    await login(client, demo.DEMO_USERNAME, credentials.password or "")

    response = await client.get(
        f"/api/v1/douyin/video?aweme_id=7300000000000000000&{field}={value}"
    )
    assert response.status_code == 403, response.text
    assert error_code(response) == "FORBIDDEN_SCOPE"
    details = response.json()["error"]["details"]
    assert details["required_roles"] == ["operator"]
    assert details["field"] == field
    # Both halves, in the one shape every 403 now uses: what the endpoint needs
    # and what this caller holds. A demo visitor told only "operator required"
    # still has to go and ask what they are.
    assert details["have_role"] == "demo"
    assert details["via"] == "session"
