"""The whole stack, from an empty database to a served request.

Everything below this file tests one layer. This one asserts that the layers
actually meet: migrations produce a usable schema, the bootstrap flow admits
exactly one administrator, a session works, and the contract a caller depends on
- stable error codes, a correlation id, a rejected SSRF attempt, a message in
their own language - holds through the real ASGI application.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest
import pytest_asyncio

from dtk.core.config import BootstrapSettings
from dtk.core.redis import get_redis

# The application is built once for the module, so its fixtures and the tests
# have to share one event loop; pytest-asyncio gives each function its own
# unless the scope is stated on both sides.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PASSWORD = "smoke-password-1234"


async def _reset_schema(url: str, attempts: int = 5) -> None:
    """Return the database to empty before a run.

    Uses its own pool-less engine and closes it immediately: DROP SCHEMA needs an
    exclusive lock, and a pooled connection left open by the previous test's
    application is enough to block it. Retried because that release is not
    instantaneous.

    asyncpg prepares every statement and a prepared statement cannot carry two
    commands, so each is issued separately.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    last: Exception | None = None
    for attempt in range(attempts):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
            return
        except Exception as exc:
            last = exc
            await asyncio.sleep(0.2 * (attempt + 1))
        finally:
            await engine.dispose()
    raise AssertionError(f"could not reset the test schema: {last}")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app():
    """The real application, wired to the test services.

    Module scoped on purpose. Dropping the schema and re-running the migrations
    for each of these tests meant seventeen resets fighting over an exclusive
    lock with connections the previous test had not finished releasing, which
    made the suite flaky for reasons that had nothing to do with the code. One
    reset, one application, and the tests below read in order as a single
    session: bootstrap first, then everything that needs an administrator.
    """

    from dtk.api.app import create_app
    from dtk.core.redis import close_redis, init_redis
    from dtk.db.migrate import upgrade

    redis_url = os.environ.get("DTK_TEST_REDIS_URL", "redis://127.0.0.1:56379/0")
    scratch = init_redis(redis_url)
    await scratch.flushdb()
    await close_redis()

    await _reset_schema(
        os.environ.get(
            "DTK_TEST_DATABASE_URL",
            "postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test",
        )
    )

    settings = BootstrapSettings(
        secret_key="end-to-end-secret-key-0123456789abcdefghij",
        database_url=os.environ.get(
            "DTK_TEST_DATABASE_URL",
            "postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test",
        ),
        redis_url=os.environ.get("DTK_TEST_REDIS_URL", "redis://127.0.0.1:56379/0"),
    )
    # `upgrade` drives Alembic synchronously and opens its own event loop, so
    # it cannot run on the one this fixture is already using.
    await asyncio.to_thread(upgrade, settings.database_url)

    application = create_app(settings)

    # The lifespan is entered and exited inside ONE task on purpose. The mounted
    # MCP server chains an anyio task group onto it, and an anyio cancel scope
    # may only be closed by the task that opened it; a module-scoped async
    # generator fixture does not guarantee that, and the teardown then raises
    # "Attempted to exit cancel scope in a different task".
    ready = asyncio.Event()
    finish = asyncio.Event()

    async def run_lifespan() -> None:
        async with application.router.lifespan_context(application):
            ready.set()
            await finish.wait()

    runner = asyncio.create_task(run_lifespan())
    await ready.wait()
    try:
        yield application
    finally:
        finish.set()
        await runner


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="module")
async def anonymous(app):
    """A client with no cookie jar of its own.

    The shared client accumulates a session once anything signs in, so a test
    about unauthenticated behaviour needs its own or it silently stops testing
    what it claims to.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="module")
async def signed_in(client):
    """A client carrying an administrator session.

    The administrator is created by the bootstrap test above; this tolerates it
    already existing so the fixture does not depend on which test ran first.
    """
    token = await get_redis().get("setup:token")
    if token:
        await client.post(
            "/api/setup/init",
            json={"token": token, "username": "admin", "password": PASSWORD},
        )
    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
    )
    assert login.status_code < 300, login.text
    return client


class TestLiveness:
    async def test_healthz_does_not_touch_the_database(self, client):
        """Liveness must answer even when the database is unhappy.

        A liveness probe that checks the database turns a brief outage into a
        restart of every API container at once, which makes recovery harder
        rather than easier (docs/design/15-operations.md).
        """
        assert (await client.get("/healthz")).status_code == 200

    async def test_readyz_reports_dependencies(self, client):
        assert (await client.get("/readyz")).status_code == 200


class TestBootstrap:
    async def test_the_full_first_run_sequence(self, client):
        status = await client.get("/api/setup/status")
        assert status.status_code == 200
        assert status.json()["data"]["initialized"] is False

        # The token is issued to the container log, never to an HTTP response:
        # under Docker's userland proxy a source-IP check sees the bridge
        # gateway for every caller and would admit the whole internet.
        token = await get_redis().get("setup:token")
        assert token, "no setup token was issued at startup"

        rejected = await client.post(
            "/api/setup/init",
            json={"token": "not-the-token", "username": "intruder", "password": PASSWORD},
        )
        assert rejected.status_code in (400, 403)

        created = await client.post(
            "/api/setup/init",
            json={"token": token, "username": "admin", "password": PASSWORD},
        )
        assert created.status_code < 300, created.text

        # Closed permanently, not merely until the token expires.
        again = await client.post(
            "/api/setup/init",
            json={"token": token, "username": "second", "password": PASSWORD},
        )
        assert again.status_code == 409

    async def test_login_establishes_a_session(self, signed_in):
        me = await signed_in.get("/api/v1/auth/me")
        assert me.status_code < 300
        assert "admin" in str(me.json()["data"])


class TestContract:
    async def test_authentication_is_checked_before_input_validation(self, anonymous):
        """An anonymous caller learns nothing about the parameters.

        Validating first would let someone probe which inputs are accepted
        without ever holding a credential.
        """
        body = (await anonymous.get("/api/v1/douyin/video?url=not-a-url")).json()
        assert body["success"] is False
        assert body["error"]["code"] == "UNAUTHENTICATED"

    async def test_failure_envelope_shape(self, signed_in):
        body = (await signed_in.get("/api/v1/douyin/video?url=not-a-url")).json()
        assert body["success"] is False
        assert body["data"] is None
        assert body["error"]["code"] == "INVALID_URL"
        assert body["meta"]["request_id"], "no id for the user to quote in a report"

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/api/v1/admin/users",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]:8000/x",
            "https://douyin.com.evil.example/video/1",
            "http://10.0.0.1/internal",
        ],
    )
    async def test_ssrf_shapes_are_rejected(self, signed_in, url):
        """The allowlist is the only defence, so every shape is asserted.

        The lookalike host matters as much as the metadata address: a naive
        substring check on 'douyin.com' accepts douyin.com.evil.example.
        """
        body = (await signed_in.post("/api/v1/parse", json={"url": url})).json()
        assert body["success"] is False, f"accepted {url}"

    async def test_messages_are_localized_but_codes_are_not(self, signed_in):
        en = (await signed_in.get("/api/v1/douyin/video?url=x&lang=en")).json()["error"]
        zh = (await signed_in.get("/api/v1/douyin/video?url=x&lang=zh")).json()["error"]
        assert en["code"] == zh["code"], "the code is a wire contract and must not translate"
        assert en["message"] != zh["message"], "the message is for humans and must translate"

    async def test_accept_language_header_is_honoured(self, signed_in):
        zh = await signed_in.get(
            "/api/v1/douyin/video?url=x", headers={"Accept-Language": "zh-CN,zh;q=0.9"}
        )
        en = await signed_in.get(
            "/api/v1/douyin/video?url=x", headers={"Accept-Language": "en-GB,en;q=0.9"}
        )
        assert zh.json()["error"]["message"] != en.json()["error"]["message"]

    async def test_unknown_language_falls_back_to_english(self, signed_in):
        fallback = await signed_in.get(
            "/api/v1/douyin/video?url=x", headers={"Accept-Language": "de-DE"}
        )
        english = await signed_in.get("/api/v1/douyin/video?url=x&lang=en")
        assert fallback.json()["error"]["message"] == english.json()["error"]["message"]


class TestDocumentation:
    async def test_openapi_is_served_in_both_languages(self, client):
        en = await client.get("/openapi.json?lang=en")
        zh = await client.get("/openapi.json?lang=zh")
        assert en.status_code == 200 and zh.status_code == 200
        assert en.json()["paths"], "the schema exposes no paths"

    async def test_every_designed_endpoint_is_exposed(self, app):
        paths = set(app.openapi()["paths"])
        for expected in (
            "/api/setup/status",
            "/api/v1/parse",
            "/api/v1/{platform}/video",
            "/api/v1/{platform}/user/posts",
            "/api/v1/tasks/{task_id}",
            "/api/v1/ios/shortcut",
            "/api/v1/admin/identities",
            "/api/v1/admin/settings",
        ):
            assert expected in paths, f"{expected} is missing from the API"

    async def test_swagger_and_redoc_render(self, client):
        assert (await client.get("/swagger")).status_code == 200
        assert (await client.get("/redoc")).status_code == 200
