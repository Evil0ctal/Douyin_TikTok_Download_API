"""Fixtures for the browser-rpc tests.

browser-rpc ships as its own image with its own dependency set, so it is not
installed into the application's virtualenv. Putting `docker/` on the path is
what lets `import browser_rpc` work when the suite is run from the repository:

    uv run pytest docker/browser_rpc/tests -q
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browser_rpc.backends.fake import FakeBackend
from browser_rpc.main import create_app
from browser_rpc.service import BrowserRpcService
from browser_rpc.settings import Settings


class FakeClock:
    """A monotonic clock the tests move by hand, for warm-context expiry."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Offline settings: fake backend, no geo probe, no prewarm."""
    return Settings(
        backend="fake",
        profile_root=str(tmp_path / "profiles"),
        warm_contexts=1,
        warm_refresh_seconds=1800.0,
        prewarm=False,
        geo_probe_url=None,
        mint_timeout_seconds=5.0,
        sign_timeout_seconds=5.0,
        context_open_timeout_seconds=5.0,
        sdk_ready_timeout_seconds=2.0,
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
async def service(settings: Settings, backend: FakeBackend) -> AsyncIterator[BrowserRpcService]:
    instance = BrowserRpcService(settings, backend)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def app(settings: Settings, backend: FakeBackend) -> Iterator[object]:
    yield create_app(settings, backend)


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client wired straight to the ASGI app.

    The lifespan is driven by hand rather than by a test client, so a test can
    inspect the service before and after start.
    """
    service = app.state.service  # type: ignore[attr-defined]
    await service.start()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://browser-rpc") as http:
            yield http
    finally:
        await service.close()
