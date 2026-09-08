"""Unit tests for the ``identity.mint`` maintenance job.

Two guarantees carry this job. The first is that a mint is a mint: the console's
button reaches the same :class:`PoolFiller` the periodic sweep uses, with its
lock and its proxy rules, rather than a second copy of the minting code. The
second is that the result of it never contains a cookie - it is stored in the
database and rendered in a browser, and the jar is the only thing an identity
is worth.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from dtk.core.config import Config
from dtk.core.errors import DtkError, ErrorCode
from dtk.core.types import BrowserFamily, IdentitySource, Platform
from dtk.identity.minting import BrowserRpcUnavailable
from dtk.transport import Fingerprint
from dtk.worker.ops import ENDPOINTS, OperationDeps
from dtk.worker.ops import identity_mint as job
from dtk.worker.pool_filler import FillerConfig, FillResult, PoolFiller

#: SYNTHETIC. Long enough to satisfy the bootstrap check, and never a
#: real deployment key.
TEST_SECRET_KEY = "t" * 48

COOKIE_VALUE = "ttwid-value-that-must-never-be-echoed"
PROXY_URL = "http://user:secret@eu-1.example:8080"


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeRpc:
    def __init__(self, *, configured: bool = True, error: Exception | None = None) -> None:
        self.configured = configured
        self.error = error
        self.mints = 0
        self.proxies: list[str | None] = []
        self.geo_hints: list[Any] = []
        #: A real mint suspends for seconds. Zero still yields, which is what
        #: lets a second caller observe the lock the first one holds.
        self.delay = 0.0

    async def mint(self, platform: Platform, *, proxy_url: str | None, geo_hint: Any) -> Any:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.mints += 1
        self.proxies.append(proxy_url)
        self.geo_hints.append(geo_hint)
        return SimpleNamespace(
            cookies={"ttwid": COOKIE_VALUE},
            fingerprint=Fingerprint(browser_family=BrowserFamily.CHROME, browser_major=131),
            exit_ip="203.0.113.7",
        )


class FakePool:
    def __init__(self, identity_id: uuid.UUID | None = None) -> None:
        self.identity_id = identity_id or uuid.uuid4()
        self.added: list[dict[str, Any]] = []
        self.counted: list[Platform] = []

    async def counts(self, session: Any, platform: Platform) -> dict[str, int]:
        self.counted.append(platform)
        return {}

    async def add(self, session: Any, **kwargs: Any) -> uuid.UUID:
        self.added.append(kwargs)
        return self.identity_id


class FakeSession:
    """Enough AsyncSession for the proxy lookups the filler makes."""

    def __init__(self, *, unbound: list[Any] | None = None, by_id: Any = None) -> None:
        self.unbound = list(unbound or [])
        self.by_id = by_id
        self.scalar_calls = 0
        self.get_calls: list[uuid.UUID] = []

    async def scalars(self, statement: Any) -> Any:
        self.scalar_calls += 1
        rows = list(self.unbound)
        return SimpleNamespace(all=lambda: rows)

    async def get(self, model: Any, key: uuid.UUID) -> Any:
        self.get_calls.append(key)
        return self.by_id


class FakeCipher:
    def decrypt(self, blob: bytes, *, aad: str) -> str:
        return PROXY_URL


def a_proxy(**kwargs: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "url_encrypted": b"blob",
        "country": "DE",
        "timezone": "Europe/Berlin",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def session_factory_for(session: Any) -> Any:
    @contextlib.asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


def make_filler(rpc: Any, pool: Any, session: FakeSession) -> PoolFiller:
    return PoolFiller(
        pool=pool,
        rpc=rpc,
        cipher=FakeCipher(),  # type: ignore[arg-type]
        config=Config.defaults(),
        options=FillerConfig(),
        session_factory=session_factory_for(session),
        distributed_lock=False,
    )


def make_deps(filler: Any) -> OperationDeps:
    return OperationDeps(
        config=Config.defaults,
        cipher=FakeCipher(),  # type: ignore[arg-type]
        secret_key=TEST_SECRET_KEY,
        pool=SimpleNamespace(),  # type: ignore[arg-type]
        transport=SimpleNamespace(),  # type: ignore[arg-type]
        signers=SimpleNamespace(),  # type: ignore[arg-type]
        filler=filler,
    )


async def mint(filler: Any, **params: Any) -> dict[str, Any]:
    session: Any = SimpleNamespace()
    return await job.run(make_deps(filler), session, params)


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------


async def test_the_job_is_one_of_the_operations_the_worker_dispatches() -> None:
    assert job.ENDPOINT in ENDPOINTS


async def test_minting_reports_the_identity_and_the_platform() -> None:
    rpc = FakeRpc()
    pool = FakePool()
    result = await mint(make_filler(rpc, pool, FakeSession()), platform="tiktok")

    assert result["data"] == {
        "minted": True,
        "identity_id": str(pool.identity_id),
        "platform": "tiktok",
        "proxy_id": None,
    }
    assert result["meta"]["endpoint"] == "identity.mint"
    # The worker logs off meta; a job that omits it fails a task that succeeded.
    assert "duration_ms" in result["meta"]


async def test_the_identity_is_minted_through_the_pool_filler() -> None:
    """The button and the sweep share one implementation, lock included."""
    rpc = FakeRpc()
    pool = FakePool()
    await mint(make_filler(rpc, pool, FakeSession()), platform="douyin")

    assert rpc.mints == 1
    assert pool.added[0]["platform"] is Platform.DOUYIN
    assert pool.added[0]["source"] is IdentitySource.MINTED


async def test_the_requested_platform_wins_over_the_neediest_one() -> None:
    """The sweep surveys for the platform to mint for; a person does not."""
    rpc = FakeRpc()
    pool = FakePool()
    await mint(make_filler(rpc, pool, FakeSession()), platform="tiktok")

    assert pool.added[0]["platform"] is Platform.TIKTOK
    # The sweep surveys every platform's level to choose one; this must not.
    assert pool.counted == []


# --------------------------------------------------------------------------
# cookies
# --------------------------------------------------------------------------


async def test_the_result_carries_no_cookie() -> None:
    rpc = FakeRpc()
    pool = FakePool()
    result = await mint(make_filler(rpc, pool, FakeSession()), platform="douyin")

    assert pool.added[0]["cookies"] == {"ttwid": COOKIE_VALUE}, "the mint did carry a jar"
    rendered = json.dumps(result)
    assert COOKIE_VALUE not in rendered
    assert "ttwid" not in rendered
    assert "cookie" not in rendered.lower()


async def test_a_named_proxy_never_reaches_the_result_as_a_url() -> None:
    proxy = a_proxy()
    rpc = FakeRpc()
    result = await mint(
        make_filler(rpc, FakePool(), FakeSession(by_id=proxy)),
        platform="douyin",
        proxy_id=str(proxy.id),
    )

    assert rpc.proxies == [PROXY_URL], "the mint did go through the proxy"
    rendered = json.dumps(result)
    assert "secret" not in rendered
    assert PROXY_URL not in rendered
    assert result["data"]["proxy_id"] == str(proxy.id)


# --------------------------------------------------------------------------
# proxies
# --------------------------------------------------------------------------


async def test_a_named_proxy_is_used_even_when_it_already_carries_an_identity() -> None:
    """The operator picked the exit; a one-proxy install must still mint."""
    proxy = a_proxy()
    rpc = FakeRpc()
    session = FakeSession(by_id=proxy)

    await mint(make_filler(rpc, FakePool(), session), platform="douyin", proxy_id=str(proxy.id))

    assert session.get_calls == [proxy.id]
    assert session.scalar_calls == 0, "the free-proxy search should have been skipped"
    assert rpc.geo_hints == [{"country": "DE", "timezone": "Europe/Berlin"}]


async def test_an_unknown_proxy_fails_the_task_rather_than_minting_elsewhere() -> None:
    rpc = FakeRpc()
    missing = uuid.uuid4()

    with pytest.raises(DtkError) as raised:
        await mint(
            make_filler(rpc, FakePool(), FakeSession(by_id=None)),
            platform="douyin",
            proxy_id=str(missing),
        )

    assert raised.value.code is ErrorCode.NOT_FOUND
    assert raised.value.details["proxy_id"] == str(missing)
    assert rpc.mints == 0


async def test_an_absent_proxy_id_lets_the_filler_choose() -> None:
    proxy = a_proxy()
    rpc = FakeRpc()
    session = FakeSession(unbound=[proxy])

    result = await mint(make_filler(rpc, FakePool(), session), platform="douyin", proxy_id=None)

    assert session.get_calls == []
    assert result["data"]["proxy_id"] == str(proxy.id)


# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------


@pytest.mark.parametrize("params", [{}, {"platform": None}, {"platform": "weibo"}])
async def test_an_unusable_platform_is_rejected_before_anything_is_minted(
    params: dict[str, Any],
) -> None:
    rpc = FakeRpc()
    with pytest.raises(DtkError) as raised:
        await mint(make_filler(rpc, FakePool(), FakeSession()), **params)

    assert raised.value.code is ErrorCode.INVALID_PARAM
    assert rpc.mints == 0


async def test_a_malformed_proxy_id_is_rejected() -> None:
    rpc = FakeRpc()
    with pytest.raises(DtkError) as raised:
        await mint(
            make_filler(rpc, FakePool(), FakeSession()),
            platform="douyin",
            proxy_id="not-a-uuid",
        )

    assert raised.value.code is ErrorCode.INVALID_PARAM
    assert rpc.mints == 0


async def test_an_empty_proxy_id_means_no_proxy_was_chosen() -> None:
    """MintRequest lets an empty string through, and it is not an id."""
    session = FakeSession()
    result = await mint(make_filler(FakeRpc(), FakePool(), session), platform="douyin", proxy_id="")

    assert session.get_calls == []
    assert result["data"]["proxy_id"] is None


# --------------------------------------------------------------------------
# failures
# --------------------------------------------------------------------------


async def test_an_install_without_browser_rpc_says_so_instead_of_crashing() -> None:
    """No browser container is a deployment fact; the operator has to read it."""
    filler = make_filler(FakeRpc(configured=False), FakePool(), FakeSession())

    with pytest.raises(DtkError) as raised:
        await mint(filler, platform="douyin")

    assert raised.value.details["reason"] == "browser_rpc_unconfigured"
    # NOT_CONFIGURED rather than INTERNAL, and the difference is not cosmetic:
    # INTERNAL is advertised as retryable, so an MCP agent or a retrying client
    # would loop forever on a fact no amount of waiting changes.
    assert raised.value.code is ErrorCode.NOT_CONFIGURED
    assert raised.value.retryable is False


async def test_a_worker_with_no_filler_at_all_fails_the_same_way() -> None:
    """OperationDeps.filler is optional, so the job cannot assume one."""
    with pytest.raises(DtkError) as raised:
        await mint(None, platform="douyin")

    assert raised.value.details["reason"] == "browser_rpc_unconfigured"


async def test_a_mint_that_produced_nothing_fails_the_task() -> None:
    """The loop shrugs at an empty tick; the person who pressed Mint cannot."""
    rpc = FakeRpc(error=BrowserRpcUnavailable("browser is down"))

    with pytest.raises(DtkError) as raised:
        await mint(make_filler(rpc, FakePool(), FakeSession()), platform="douyin")

    assert raised.value.details["reason"] == "rpc_unavailable"
    assert raised.value.retryable is True


async def test_a_mint_that_never_gets_its_turn_is_contention_not_breakage() -> None:
    """Only reachable once the wait has already expired.

    The job asks the filler to wait for the lock, so "busy" no longer means
    "someone else is minting right now" - it means nobody let go for the whole
    window. That is still contention rather than a broken mint, so the code and
    the retry advice stay; what changed is how rare it should be.
    """
    contended = SimpleNamespace(
        enabled=True,
        top_up_once=_returning(FillResult(platform=Platform.DOUYIN, reason="busy")),
    )

    with pytest.raises(DtkError) as raised:
        await mint(contended, platform="douyin")

    assert raised.value.code is ErrorCode.RATE_LIMITED
    assert raised.value.retry_after == job.BUSY_RETRY_SECONDS


async def test_the_job_asks_the_filler_to_wait_its_turn() -> None:
    """The fix for the console's own mint flow, asserted at the seam.

    MintRequest.count goes to 10 and the route submits that many independent
    tasks; the setup wizard asks for five. With a fail-fast lock that was one
    identity and four "too many requests" toasts on first run. The job must
    therefore ask to queue, and must keep its failures out of the sweep's
    shared backoff - ten presses against a down browser would otherwise push
    automatic refill to its one-hour ceiling.
    """
    seen: dict[str, Any] = {}

    async def record(platform: Platform, **kwargs: Any) -> FillResult:
        seen.update(kwargs)
        return FillResult(platform=platform, identity_id=uuid.uuid4(), minted=True)

    await mint(SimpleNamespace(enabled=True, top_up_once=record), platform="douyin")

    assert seen["wait_seconds"] == job.MINT_WAIT_SECONDS
    assert seen["wait_seconds"] > 0
    assert seen["shared_backoff"] is False


async def test_two_concurrent_mints_are_serialized_rather_than_one_being_refused() -> None:
    """Both halves of the rule, which used to be in tension.

    One mint at a time is doc 02's rule and the reason this job delegates to the
    filler rather than minting itself. But serializing is not the same as
    refusing: the console submits one task per identity, so the previous
    behaviour - first caller wins, everyone else gets RATE_LIMITED - turned a
    request for two identities into one identity and one red toast.

    So this asserts both: nobody is refused, and no two mints overlap.
    """
    filler = make_filler(FakeRpc(), FakePool(), FakeSession())
    deps = make_deps(filler)
    session: Any = SimpleNamespace()
    params = {"platform": "douyin"}

    overlap = _OverlapDetector(filler)

    outcomes = await asyncio.gather(
        job.run(deps, session, params),
        job.run(deps, session, params),
        return_exceptions=True,
    )

    failures = [item for item in outcomes if isinstance(item, BaseException)]
    assert not failures, f"a concurrent mint was refused: {failures}"
    assert all(item["data"]["minted"] for item in outcomes)
    assert overlap.peak == 1, "two mints ran at the same time; the lock is not holding"


class _OverlapDetector:
    """Counts how many mints are inside the filler's critical section at once."""

    def __init__(self, filler: Any) -> None:
        self.peak = 0
        self._current = 0
        inner = filler._mint

        async def counted(*args: Any, **kwargs: Any) -> Any:
            self._current += 1
            self.peak = max(self.peak, self._current)
            try:
                # Yield, so two callers would interleave here if they could.
                await asyncio.sleep(0)
                return await inner(*args, **kwargs)
            finally:
                self._current -= 1

        filler._mint = counted


def _returning(result: FillResult) -> Any:
    """A filler that always answers the same way.

    ``**_kwargs`` on purpose: the job passes wait_seconds and shared_backoff,
    and a stub that only accepted what the job used to send would fail with a
    TypeError rather than telling anyone the contract had moved.
    """

    async def call(platform: Platform, **_kwargs: Any) -> FillResult:
        return result

    return call
