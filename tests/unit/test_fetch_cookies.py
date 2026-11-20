"""What the fetch pipeline does with the cookies a response sets.

The transport only reports them (``RawResponse.set_cookies``); keeping them is
the pipeline's job, in the request's own session, because a session of the
transport's own would wait on the row lock this request's transaction already
holds. These tests hold which responses are learned from - every one the
platform answered, as a browser would - and that keeping a cookie can never
cost the response it arrived on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from dtk.core.errors import (
    IdentityLoginRequired,
    NotFound,
    UpstreamRiskControl,
    UpstreamUnreachable,
)
from dtk.core.types import Outcome
from dtk.services import fetch as fetch_module
from dtk.services.fetch import FetchService
from dtk.signing.base import SIGNER_BROWSER, SignedParams
from dtk.transport.base import RawResponse
from tests.replay.support import load
from tests.unit.test_fetch_classification import (
    IDENTITY_ID,
    FakePool,
    FakeScheduler,
    FakeSession,
    FakeTransport,
    identity,
    run_fetch,
    service,
)

ROTATED = {"odin_tt": "rotated", "ttwid": "fresh"}


def answer(status: int, payload: Any, cookies: Mapping[str, str] = ROTATED) -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "application/json; charset=utf-8"},
        body=json.dumps(payload).encode(),
        final_url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
        elapsed_ms=9,
        set_cookies=dict(cookies),
    )


class KeepingPool(FakePool):
    def __init__(self, *, fails: bool = False) -> None:
        super().__init__(identity())
        self.absorbed: list[tuple[str, dict[str, str]]] = []
        self.fails = fails

    async def absorb_cookies(
        self, session: Any, identity_id: str, offered: Mapping[str, str]
    ) -> tuple[str, ...]:
        if self.fails:
            raise RuntimeError("the cipher key was rotated under us")
        self.absorbed.append((identity_id, dict(offered)))
        return tuple(sorted(offered))


class TestWhichResponsesTeachTheJar:
    async def test_a_served_response_is_kept(self) -> None:
        pool = KeepingPool()
        await run_fetch(
            service(answer(200, load("douyin", "video_normal")), pool=pool), FakeSession()
        )

        assert pool.absorbed == [(IDENTITY_ID, ROTATED)]
        assert pool.recorded == [Outcome.OK]

    async def test_an_answer_about_missing_content_is_the_platform_too(self) -> None:
        pool = KeepingPool()
        with pytest.raises(NotFound):
            await run_fetch(service(answer(404, {}), pool=pool), FakeSession())

        assert pool.recorded == [Outcome.BUSINESS_ERROR]
        assert pool.absorbed == [(IDENTITY_ID, ROTATED)]

    async def test_a_refusal_s_cookies_are_kept_too(self) -> None:
        """Measured on TikTok: the refusal that spends an msToken's budget
        carries the fresh token that ends it; a jar that refused it stayed dead."""
        pool = KeepingPool()
        with pytest.raises(UpstreamRiskControl):
            await run_fetch(service(answer(403, {}), pool=pool), FakeSession())

        assert pool.recorded[0] is Outcome.RISK_CONTROL
        assert pool.absorbed == [(IDENTITY_ID, ROTATED)]

    async def test_no_answer_from_the_platform_keeps_nothing(self) -> None:
        """A 5xx is a gateway or an outage speaking, not the platform."""
        pool = KeepingPool()
        with pytest.raises(UpstreamUnreachable):
            await run_fetch(service(answer(503, {}), pool=pool), FakeSession())

        assert set(pool.recorded) == {Outcome.NETWORK_ERROR}
        assert pool.absorbed == []

    async def test_a_value_the_request_already_carried_is_not_offered(self) -> None:
        """TikTok's home page re-sends tt_chain_token on every load; writing it
        back would take a row lock for nothing."""
        pool = KeepingPool()
        resent = answer(200, load("douyin", "video_normal"), {"ttwid": "x", "odin_tt": "new"})
        await run_fetch(service(resent, pool=pool), FakeSession())

        assert pool.absorbed == [(IDENTITY_ID, {"odin_tt": "new"})]

    async def test_a_response_that_set_nothing_costs_no_call(self) -> None:
        pool = KeepingPool()
        await run_fetch(
            service(answer(200, load("douyin", "video_normal"), cookies={}), pool=pool),
            FakeSession(),
        )

        assert pool.absorbed == []


class Recorder:
    """Stands in for the module's logger and keeps the events it was given."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, level: str):
        def record(event: str, **fields: Any) -> None:
            self.events.append((f"{level}:{event}", fields))

        return record


class TestKeepingNeverCostsTheResponse:
    async def test_a_failure_to_keep_them_is_logged_not_raised(self, monkeypatch) -> None:
        recorder = Recorder()
        monkeypatch.setattr(fetch_module, "log", recorder)
        pool = KeepingPool(fails=True)
        result = await run_fetch(
            service(answer(200, load("douyin", "video_normal")), pool=pool), FakeSession()
        )

        assert result is not None
        assert pool.recorded == [Outcome.OK]
        failures = [
            fields
            for event, fields in recorder.events
            if event == "error:fetch.cookies.keep_failed"
        ]
        assert failures == [{"identity_id": IDENTITY_ID, "error": "RuntimeError"}]
        # The class name, not the message: an exception from the cipher or the
        # policy may quote the value it choked on.
        assert "rotated" not in repr(recorder.events)

    async def test_a_database_error_names_its_sqlstate(self, monkeypatch) -> None:
        """Issue #832: a deadlock here aborts the transaction, and the
        bookkeeping after it then fails as "current transaction is aborted".
        The SQLSTATE in this line is the only place the cause is still named."""

        class Deadlock(Exception):
            def __init__(self) -> None:
                super().__init__("deadlock detected")
                self.orig = type("Driver", (), {"sqlstate": "40P01"})()

        class DeadlockedPool(KeepingPool):
            async def absorb_cookies(self, *_: Any, **__: Any) -> tuple[str, ...]:
                raise Deadlock()

        recorder = Recorder()
        monkeypatch.setattr(fetch_module, "log", recorder)
        pool = DeadlockedPool()
        await run_fetch(
            service(answer(200, load("douyin", "video_normal")), pool=pool), FakeSession()
        )

        failures = [
            fields
            for event, fields in recorder.events
            if event == "error:fetch.cookies.keep_failed"
        ]
        assert failures == [{"identity_id": IDENTITY_ID, "error": "Deadlock", "sqlstate": "40P01"}]


class TestALoginWall:
    async def test_is_an_answer_like_any_other(self) -> None:
        """TikTok's 2483 is filed as a business error; a browser would keep
        what it sets, and so does the pool."""
        pool = KeepingPool()
        wall = answer(200, {"status_code": 2483, "status_msg": "Please login your account first"})
        with pytest.raises(IdentityLoginRequired):
            await run_fetch(service(wall, pool=pool), FakeSession())

        assert pool.recorded == [Outcome.BUSINESS_ERROR]
        assert pool.absorbed == [(IDENTITY_ID, ROTATED)]


class TestASignersSessionIsNotTheIdentitys:
    async def test_a_reply_to_a_sealed_cookie_is_not_kept(self) -> None:
        """A browser page signed this request with its own msToken; what the
        platform sets back for that name belongs to the page's session."""

        async def page_signer(platform, spec, sender):
            return SignedParams(
                query="X-Gnarly=g",
                params={"X-Gnarly": "g"},
                signer=SIGNER_BROWSER,
                cookies={"msToken": "the-page-s-own"},
            )

        pool = KeepingPool()
        svc = FetchService(
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            transport=FakeTransport(
                answer(200, load("douyin", "video_normal"), {"msToken": "m" * 150, "ttwid": "t"})
            ),  # type: ignore[arg-type]
            sign=page_signer,
        )
        await run_fetch(svc, FakeSession())

        assert pool.absorbed == [(IDENTITY_ID, {"ttwid": "t"})]

    async def test_a_reply_to_the_jars_own_token_is_kept(self) -> None:
        """A browser signature that sealed no token of its own overrides
        nothing: the request carried the jar's msToken, and the token the
        platform rotates in its answer is the identity's to keep.

        The pair with the test above is the whole rule for #832: under the
        page's override the jar's token is neither sent nor spent, and keeping
        the reply would change the jar - and so browser-rpc's binding - on
        every call.
        """

        async def empty_seal(platform, spec, sender):
            return SignedParams(
                query="msToken=&X-Gnarly=g",
                params={"msToken": "", "X-Gnarly": "g"},
                signer=SIGNER_BROWSER,
                cookies={},
            )

        pool = KeepingPool()
        svc = FetchService(
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            transport=FakeTransport(
                answer(200, load("douyin", "video_normal"), {"msToken": "m" * 150, "ttwid": "t"})
            ),  # type: ignore[arg-type]
            sign=empty_seal,
        )
        await run_fetch(svc, FakeSession())

        assert pool.absorbed == [(IDENTITY_ID, {"msToken": "m" * 150, "ttwid": "t"})]
