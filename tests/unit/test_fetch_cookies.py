"""What the fetch pipeline does with the cookies a response sets.

The transport only reports them (``RawResponse.set_cookies``); keeping them is
the pipeline's job, in the request's own session, because a session of the
transport's own would wait on the row lock this request's transaction already
holds. These tests hold which responses are learned from and that keeping a
cookie can never cost the response it arrived on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from dtk.core.errors import IdentityLoginRequired, NotFound, UpstreamRiskControl
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

    async def test_a_refusal_teaches_nothing(self) -> None:
        """What a risk page plants describes a visitor being turned away."""
        pool = KeepingPool()
        with pytest.raises(UpstreamRiskControl):
            await run_fetch(service(answer(403, {}), pool=pool), FakeSession())

        assert pool.recorded[0] is Outcome.RISK_CONTROL
        assert pool.absorbed == []

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


class TestALoginWall:
    async def test_teaches_nothing_although_it_is_filed_as_a_business_error(self) -> None:
        """TikTok's 2483 is not the platform serving this visitor; what it sets
        describes one being turned away."""
        pool = KeepingPool()
        wall = answer(200, {"status_code": 2483, "status_msg": "Please login your account first"})
        with pytest.raises(IdentityLoginRequired):
            await run_fetch(service(wall, pool=pool), FakeSession())

        assert pool.recorded == [Outcome.BUSINESS_ERROR]
        assert pool.absorbed == []


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
