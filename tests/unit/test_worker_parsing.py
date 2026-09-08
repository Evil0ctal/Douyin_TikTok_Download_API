"""Turning a pasted link into an endpoint call.

Both POST /api/v1/parse and the MCP parse_url tool queue the logical endpoint
"parse", which names only a URL. Without this resolution step every "just give
it a link" path fails with "unknown endpoint", which is the single most likely
thing a first-time user tries.

Expanding the short link is also the one request the service makes before it
has an identity, and therefore the one place the host's own address could reach
the platform. The tests from :class:`TestEgress` down pin it to the pool.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from dtk.core.config import Config
from dtk.core.crypto import Cipher
from dtk.core.errors import (
    IdentityPoolExhausted,
    Internal,
    InvalidUrl,
    NotConfigured,
    UnsupportedContent,
)
from dtk.db.models import Proxy
from dtk.urls import identify
from dtk.worker import parsing
from dtk.worker.main import TaskRun, TaskWorker
from dtk.worker.parsing import plan, to_call

DOUYIN_VIDEO = "https://www.douyin.com/video/7372484719365098803"
TIKTOK_VIDEO = "https://www.tiktok.com/@someone/video/7372484719365098803"
DOUYIN_USER = "https://www.douyin.com/user/MS4wLjABAAAA_synthetic_sec_uid"
SHORT_LINK = "https://v.douyin.com/abc123/"
#: Credentials in the authority, so a test can assert they never come back out.
PROXY_URL = "http://pool:s3cr3t@egress.example:8080"
SECRET_KEY = "unit-test-secret-key-0123456789abcdef"


async def no_redirect(_url: str) -> str | None:
    return None


class TestDispatch:
    @pytest.mark.parametrize(
        ("url", "endpoint", "param", "value"),
        [
            (DOUYIN_VIDEO, "douyin.content_detail", "content_id", "7372484719365098803"),
            (TIKTOK_VIDEO, "tiktok.content_detail", "content_id", "7372484719365098803"),
            (DOUYIN_USER, "douyin.author_profile", "author_id", "MS4wLjABAAAA_synthetic_sec_uid"),
        ],
    )
    async def test_maps_a_link_onto_a_registered_endpoint(self, url, endpoint, param, value):
        got_endpoint, params = await plan(url, no_redirect)
        assert got_endpoint == endpoint
        assert params == {param: value}

    async def test_every_produced_endpoint_actually_exists(self):
        """The mapping is only useful if the worker can run what it names."""
        from dtk.worker.registry import ENDPOINTS

        for url in (DOUYIN_VIDEO, TIKTOK_VIDEO, DOUYIN_USER):
            endpoint, _ = await plan(url, no_redirect)
            assert endpoint in ENDPOINTS, f"{endpoint} is not a registered endpoint"

    async def test_produced_params_satisfy_the_endpoint(self):
        from dtk.worker.registry import definition_for

        for url in (DOUYIN_VIDEO, DOUYIN_USER):
            endpoint, params = await plan(url, no_redirect)
            definition = definition_for(endpoint)
            assert set(definition.required) <= set(params), (
                f"{endpoint} needs {definition.required}, parse produced {sorted(params)}"
            )


class TestRefusals:
    async def test_an_unrecognised_host_is_refused(self):
        with pytest.raises(InvalidUrl):
            await plan("https://example.com/whatever", no_redirect)

    async def test_a_supported_host_with_no_identifier_is_refused(self):
        with pytest.raises((InvalidUrl, UnsupportedContent)):
            await plan("https://www.douyin.com/", no_redirect)

    async def test_an_unsupported_resource_kind_says_so(self):
        """A live room is a recognised link the project does not serve yet, and
        the caller should be told that rather than 'invalid URL'."""
        live = identify("https://live.douyin.com/123456789")
        if live.platform is None:
            pytest.skip("live links are not recognised by the URL layer")
        with pytest.raises((UnsupportedContent, InvalidUrl)):
            await plan("https://live.douyin.com/123456789", no_redirect)


class TestExpansion:
    async def test_a_short_link_is_followed_then_re_identified(self):
        # The fetcher returns the next hop, or None when there is no further
        # redirect. Returning the same target forever is a loop, and the URL
        # layer correctly refuses it.
        # Matched on the token rather than the exact string: the URL layer
        # normalizes before handing the link to the fetcher, so an equality
        # check here would silently test nothing.
        async def fetcher(url: str) -> str | None:
            return DOUYIN_VIDEO if "abc123" in url else None

        endpoint, params = await plan("https://v.douyin.com/abc123/", fetcher)
        assert endpoint == "douyin.content_detail"
        assert params == {"content_id": "7372484719365098803"}

    async def test_a_short_link_that_leaves_the_allowlist_is_refused(self):
        """A short link cannot be checked before it is followed, so the target
        is checked after every hop. This is the SSRF case that matters."""

        async def fetcher(url: str) -> str | None:
            return "http://169.254.169.254/latest/meta-data/" if "v.douyin" in url else None

        with pytest.raises(InvalidUrl):
            await plan("https://v.douyin.com/abc123/", fetcher)

    async def test_a_dead_short_link_is_refused_not_hung(self):
        async def fetcher(_url: str) -> str | None:
            return None

        with pytest.raises((InvalidUrl, UnsupportedContent)):
            await plan("https://v.douyin.com/abc123/", fetcher)


def test_to_call_is_pure():
    endpoint, params = to_call(identify(DOUYIN_VIDEO))
    assert endpoint == "douyin.content_detail"
    assert params == {"content_id": "7372484719365098803"}


# --------------------------------------------------------------------------
# expanding through the pool
# --------------------------------------------------------------------------


def fake_client(
    monkeypatch: Any,
    hops: dict[str, str] | None = None,
    *,
    error: Exception | None = None,
) -> list[dict[str, Any]]:
    """Stand in for ``httpx.AsyncClient``; collect how each one was built.

    Expansion is the only part of this module that opens a socket, so the
    arguments the client is built with are what say which address the hop would
    have left from. ``hops`` maps a token in the requested URL to the Location
    the response carries.
    """
    built: list[dict[str, Any]] = []
    routes = hops or {}

    class _Stream:
        def __init__(self, url: str) -> None:
            location = next((to for token, to in routes.items() if token in url), None)
            self.status_code = 302 if location else 200
            self.headers = {"location": location} if location else {}

        async def __aenter__(self) -> _Stream:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def stream(self, _method: str, url: str) -> _Stream:
            if error is not None:
                raise error
            return _Stream(url)

    monkeypatch.setattr(parsing.httpx, "AsyncClient", _Client)
    return built


class CountingEgress:
    """A ProxySource that also reports how often it was asked."""

    def __init__(self, url: str | None = PROXY_URL) -> None:
        self.url = url
        self.calls = 0

    async def __call__(self) -> str | None:
        self.calls += 1
        return self.url


class TestEgress:
    """Doc 08: the hop leaves through the pool, never through this host.

    It runs before an identity is chosen, so no layer below can make that
    guarantee on its behalf.
    """

    async def test_a_short_link_leaves_through_the_pool_proxy(self, monkeypatch):
        built = fake_client(monkeypatch, {"abc123": DOUYIN_VIDEO})

        endpoint, _ = await plan(SHORT_LINK, parsing.egress_fetcher(CountingEgress()))

        assert endpoint == "douyin.content_detail"
        assert built, "the expansion issued no request at all"
        assert [call["proxy"] for call in built] == [PROXY_URL] * len(built)

    async def test_the_hop_does_not_announce_the_http_library(self, monkeypatch):
        built = fake_client(monkeypatch, {"abc123": DOUYIN_VIDEO})

        await plan(SHORT_LINK, parsing.egress_fetcher(CountingEgress()))

        assert "Mozilla/" in built[0]["headers"]["User-Agent"]

    async def test_one_exit_serves_the_whole_chain(self, monkeypatch):
        """Two hops from two addresses would be a correlation of our own making."""
        built = fake_client(monkeypatch, {"abc123": DOUYIN_VIDEO})
        egress = CountingEgress()

        await plan(SHORT_LINK, parsing.egress_fetcher(egress))

        assert len(built) > 1, "the chain took a single hop; the test proves nothing"
        assert egress.calls == 1

    async def test_a_full_url_never_asks_for_an_exit(self, monkeypatch):
        """The lookup behind an egress is a database round trip. Only a hop pays it."""
        built = fake_client(monkeypatch)
        egress = CountingEgress()

        endpoint, _ = await plan(DOUYIN_VIDEO, parsing.egress_fetcher(egress))

        assert endpoint == "douyin.content_detail"
        assert egress.calls == 0
        assert built == []

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError(f"connection to {PROXY_URL} refused"),
            # httpx rejects a proxy row saved without a scheme like this, with
            # the password unmasked because it never parsed the authority.
            ValueError(f"Unknown scheme for proxy URL URL('{PROXY_URL}')"),
        ],
        ids=["transport", "malformed_proxy"],
    )
    async def test_a_failed_hop_does_not_repeat_the_proxy_password(self, monkeypatch, error):
        fake_client(monkeypatch, error=error)

        with pytest.raises(Internal) as raised:
            await plan(SHORT_LINK, parsing.egress_fetcher(CountingEgress()))

        assert "s3cr3t" not in str(raised.value)
        assert "s3cr3t" not in str(raised.value.details)


def a_worker(egress: parsing.ProxySource | None = None) -> TaskWorker:
    """A worker holding only the parts endpoint resolution touches."""
    return TaskWorker(
        fetch=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        config=Config.defaults(),
        egress=egress,
    )


def a_parse_run(url: str) -> TaskRun:
    return TaskRun(id=uuid.uuid4(), endpoint=TaskWorker.PARSE_ENDPOINT, params={"url": url})


class TestWorkerPath:
    """What the production worker actually does, since nothing injects a fetcher."""

    async def test_the_worker_expands_through_its_egress(self, monkeypatch):
        built = fake_client(monkeypatch, {"abc123": DOUYIN_VIDEO})

        endpoint, params = await a_worker(CountingEgress())._resolve_endpoint(
            a_parse_run(SHORT_LINK)
        )

        assert endpoint == "douyin.content_detail"
        assert params == {"content_id": "7372484719365098803"}
        assert [call["proxy"] for call in built] == [PROXY_URL] * len(built)

    async def test_a_worker_without_an_egress_refuses_to_expand(self, monkeypatch):
        """Fail closed: a wiring mistake must not become a request from this host."""
        built = fake_client(monkeypatch, {"abc123": DOUYIN_VIDEO})

        with pytest.raises(NotConfigured):
            await a_worker()._resolve_endpoint(a_parse_run(SHORT_LINK))

        assert built == []

    async def test_a_worker_without_an_egress_still_parses_a_full_url(self, monkeypatch):
        built = fake_client(monkeypatch)

        endpoint, _ = await a_worker()._resolve_endpoint(a_parse_run(DOUYIN_VIDEO))

        assert endpoint == "douyin.content_detail"
        assert built == []


# --------------------------------------------------------------------------
# choosing the exit
# --------------------------------------------------------------------------


def a_proxy(url: str, *, cipher: Cipher, healthy: bool = True) -> Proxy:
    row_id = uuid.uuid4()
    return Proxy(id=row_id, url_encrypted=cipher.encrypt(url, aad=str(row_id)), healthy=healthy)


class FakeSession:
    """Answers ``ProxyRepository.list_all`` with a fixed set of rows."""

    def __init__(self, rows: list[Proxy]) -> None:
        self.rows = rows

    async def scalars(self, _statement: Any) -> Any:
        return SimpleNamespace(all=lambda: list(self.rows))


def session_factory_for(session: FakeSession) -> Any:
    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


class TestPickEgress:
    cipher = Cipher(SECRET_KEY)

    async def test_it_rotates_over_the_healthy_proxies(self):
        """A single expander address would be a fingerprint of its own.

        Twenty draws over two proxies: all-same is a one-in-half-a-million
        coincidence, which is a fair price for catching a fixed choice.
        """
        rows = [a_proxy("http://a.example:1", cipher=self.cipher)]
        rows.append(a_proxy("http://b.example:2", cipher=self.cipher))
        session = FakeSession(rows)

        seen = {await parsing.pick_egress(session, self.cipher) for _ in range(20)}

        assert seen == {"http://a.example:1", "http://b.example:2"}

    async def test_an_unhealthy_proxy_is_never_chosen(self):
        session = FakeSession(
            [
                a_proxy("http://down.example:1", cipher=self.cipher, healthy=False),
                a_proxy("http://up.example:2", cipher=self.cipher),
            ]
        )

        for _ in range(20):
            assert await parsing.pick_egress(session, self.cipher) == "http://up.example:2"

    async def test_no_proxies_configured_expands_directly(self):
        """Nothing to hide behind and nothing to give away: the fetch is direct too."""
        assert await parsing.pick_egress(FakeSession([]), self.cipher) is None

    async def test_every_proxy_down_is_refused_rather_than_leaked(self):
        session = FakeSession([a_proxy(PROXY_URL, cipher=self.cipher, healthy=False)])

        with pytest.raises(IdentityPoolExhausted) as raised:
            await parsing.pick_egress(session, self.cipher)

        assert raised.value.retry_after
        assert "s3cr3t" not in str(raised.value)

    async def test_a_proxy_from_another_secret_key_is_skipped(self):
        stale = Cipher("a-different-secret-key-0123456789abcdef")
        session = FakeSession(
            [
                a_proxy("http://stale.example:1", cipher=stale),
                a_proxy(PROXY_URL, cipher=self.cipher),
            ]
        )

        for _ in range(20):
            assert await parsing.pick_egress(session, self.cipher) == PROXY_URL

    async def test_the_pool_egress_opens_its_own_session(self):
        session = FakeSession([a_proxy(PROXY_URL, cipher=self.cipher)])
        egress = parsing.PoolEgress(self.cipher, session_factory=session_factory_for(session))

        assert await egress() == PROXY_URL
