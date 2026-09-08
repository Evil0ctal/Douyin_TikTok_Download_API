"""Fixtures and helpers shared by the API integration tests.

Named ``test_api_support`` so it sits inside the file set this module owns;
pytest collects it and finds no tests, which is fine. Other API test modules
import the fixtures they need from here, and importing a fixture into a test
module's namespace is enough for pytest to see it.

The application is built with :func:`dtk.api.app.create_app` but its lifespan
is not run: the ``db_engine`` and ``redis_client`` fixtures already opened both
connections, and running the lifespan would close them again on the way out,
underneath the fixtures that own them. Everything the lifespan would otherwise
set up - the config snapshot and the cipher - is set here explicitly.

Two isolation choices, both because the PostgreSQL and Redis fixtures are a
shared instance that other suites use at the same time:

* these tests get their own Redis logical database. A neighbouring suite's
  ``FLUSHDB`` would otherwise delete a live session cookie mid-test, which
  fails as an unexplained 401.
* the tables are cleared with ``DELETE`` rather than ``TRUNCATE``. ``TRUNCATE``
  takes ACCESS EXCLUSIVE on every table it names, and two suites naming them in
  different orders deadlock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import text

from dtk.api.app import create_app
from dtk.api.routes.passwords import hash_password
from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher, new_api_key
from dtk.core.db import Base, session_scope
from dtk.core.redis import init_redis
from dtk.core.types import Scope, UserRole
from dtk.db.models import ApiKey, User
from tests.integration.conftest import TEST_DATABASE_URL, TEST_REDIS_URL

#: Long enough to satisfy the master-key length check; test data only.
TEST_SECRET_KEY = "test-secret-key-for-integration-tests-0123456789"

#: Logical Redis database reserved for the API suite, so a neighbouring suite's
#: FLUSHDB cannot delete a session this suite is in the middle of using.
API_TEST_REDIS_DB = 9

#: Cleared before every test, children first so foreign keys stay satisfied.
TABLES = (
    "media_downloads",
    "archived_contents",
    "archived_authors",
    "audit_log",
    "request_log",
    "identity_events",
    "content_snapshots",
    "tasks",
    "identities",
    "proxies",
    "api_keys",
    "settings",
    "settings_version",
    "users",
)


def api_redis_url() -> str:
    """The shared Redis instance, on this suite's own logical database."""
    base, _, _ = TEST_REDIS_URL.rpartition("/")
    return f"{base}/{API_TEST_REDIS_DB}"


def bootstrap_settings() -> BootstrapSettings:
    return BootstrapSettings(
        secret_key=TEST_SECRET_KEY,
        database_url=TEST_DATABASE_URL,
        redis_url=api_redis_url(),
        bind_host="127.0.0.1",
        bind_port=8000,
    )


@pytest_asyncio.fixture
async def api_app(db_engine: Any, redis_client: Any) -> AsyncIterator[Any]:
    """A configured application on an empty schema and an empty key space."""
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # A short lock timeout turns contention with another suite into a
        # readable error instead of a hang.
        await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        for table in TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))

    settings = bootstrap_settings()
    # Repoints the process-wide client at this suite's database; the next test
    # in any other file gets db 0 back from the conftest fixture.
    redis = init_redis(settings.redis_url)
    await redis.flushdb()

    app = create_app(settings)
    app.state.config = Config.defaults()
    app.state.cipher = Cipher(settings.secret_key)
    yield app


@pytest_asyncio.fixture
async def client(api_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


def anonymous_client(app: Any) -> httpx.AsyncClient:
    """A second client with an empty cookie jar, for use with ``async with``.

    Needed whenever a test checks what an API key can do: the ``client``
    fixture usually holds a console session cookie as well, and
    :func:`dtk.api.deps.current_principal` falls back to the cookie when the
    key is rejected - so the request would succeed for the wrong reason.
    """
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def make_user(
    username: str = "admin",
    password: str = "correct horse battery",
    role: UserRole = UserRole.ADMIN,
) -> uuid.UUID:
    """Insert an account directly, skipping the setup flow."""
    user_id = uuid.uuid4()
    async with session_scope() as session:
        session.add(
            User(
                id=user_id,
                username=username,
                password_hash=hash_password(password),
                role=role.value,
            )
        )
    return user_id


async def make_api_key(
    user_id: uuid.UUID,
    *,
    scopes: tuple[Scope, ...] = (Scope.DOUYIN_READ,),
    name: str = "test key",
    rate_limit: int | None = None,
) -> str:
    """Create a key and return the full secret, as the caller would hold it."""
    full, prefix, digest = new_api_key()
    async with session_scope() as session:
        session.add(
            ApiKey(
                id=uuid.uuid4(),
                user_id=user_id,
                name=name,
                prefix=prefix,
                key_hash=digest,
                scopes=[s.value for s in scopes],
                rate_limit=rate_limit,
            )
        )
    return full


async def login(
    http: httpx.AsyncClient,
    username: str = "admin",
    password: str = "correct horse battery",
) -> httpx.Response:
    """Log in; the session cookie stays in the client's jar afterwards."""
    return await http.post("/api/v1/auth/login", json={"username": username, "password": password})


async def signed_in(
    http: httpx.AsyncClient,
    *,
    username: str = "admin",
    password: str = "correct horse battery",
    role: UserRole = UserRole.ADMIN,
) -> uuid.UUID:
    """Create an account and open a session for it in one step."""
    user_id = await make_user(username, password, role)
    response = await login(http, username, password)
    assert response.status_code == 200, response.text
    return user_id


def envelope(response: httpx.Response) -> dict[str, Any]:
    """Assert the uniform envelope and return the parsed body."""
    body = response.json()
    assert set(body) == {"success", "data", "error", "meta"}, body
    assert "request_id" in body["meta"]
    if body["success"]:
        assert body["error"] is None
    else:
        assert body["data"] is None
        assert set(body["error"]) >= {"code", "message"}
    return body


def error_code(response: httpx.Response) -> str:
    body = envelope(response)
    assert body["success"] is False, body
    return str(body["error"]["code"])


__all__ = [
    "API_TEST_REDIS_DB",
    "TABLES",
    "TEST_SECRET_KEY",
    "anonymous_client",
    "api_app",
    "api_redis_url",
    "bootstrap_settings",
    "client",
    "envelope",
    "error_code",
    "login",
    "make_api_key",
    "make_user",
    "signed_in",
]
