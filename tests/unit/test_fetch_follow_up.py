"""A second call an answer asks for: Bilibili's detail and its playurl.

The fetch layer makes it, so it is scheduled, signed and logged like any other
call - and it must never be what fails the request. A Bilibili detail without
its stream is still the post.
"""

from __future__ import annotations

import json
from typing import Any

from dtk.core.errors import DtkError, IdentityPoolExhausted
from dtk.core.types import BrowserFamily, LoginNeed, Outcome, Platform
from dtk.identity.pool import LiveIdentity
from dtk.platforms.bilibili.parser import STREAMS_KEY
from dtk.scheduler.leases import Lease
from dtk.services.fetch import FetchContext, FetchService
from dtk.transport.base import Fingerprint, RawResponse
from tests.replay.support import load
from tests.unit.test_fetch_classification import (
    IDENTITY_ID,
    PINNED_ID,
    FakePool,
    FakeScheduler,
    FakeSession,
    FakeTransport,
    response,
    rows,
    signer,
)

DETAIL = "bilibili.content_detail"
STREAMS = "bilibili.content_streams"


class Scripted(FakeTransport):
    """Answers each request in turn."""

    def __init__(self, *answers: RawResponse) -> None:
        super().__init__(answers[0])
        self.answers = list(answers)

    async def request(self, identity: Any, spec: Any, timeout: float | None = None) -> RawResponse:
        self.senders.append(identity)
        self.specs.append(spec)
        return self.answers[min(len(self.specs), len(self.answers)) - 1]


class RefusesStreams(FakeScheduler):
    async def acquire(self, endpoint: str, platform: Platform, **kwargs: Any) -> Lease:
        if endpoint == STREAMS:
            raise IdentityPoolExhausted("no identity available for this endpoint")
        return await super().acquire(endpoint, platform, **kwargs)


def bilibili_identity() -> LiveIdentity:
    return LiveIdentity(
        id=IDENTITY_ID,
        platform=Platform.BILIBILI,
        cookies={"buvid3": "x"},
        fingerprint=Fingerprint(
            browser_family=BrowserFamily.CHROME,
            browser_major=146,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
        ),
        proxy_url=None,
        authenticated=False,
    )


def bilibili_service(
    transport: FakeTransport, *, scheduler: FakeScheduler | None = None
) -> FetchService:
    return FetchService(
        scheduler=scheduler or FakeScheduler(),  # type: ignore[arg-type]
        pool=FakePool(bilibili_identity()),  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        sign=signer,
        cooldown_base=60,
    )


def answer(payload: Any) -> RawResponse:
    return response(200, body=json.dumps(payload).encode())


async def fetch_detail(svc: FetchService, session: FakeSession, **ctx: Any) -> list[Any]:
    seen: list[Any] = []

    def parse(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return payload

    await svc.fetch(
        session,  # type: ignore[arg-type]
        Platform.BILIBILI,
        DETAIL,
        {"bvid": "BV1xx411c7mD"},
        parse=parse,
        ctx=FetchContext(**ctx),
    )
    return seen


async def test_the_detail_is_followed_by_its_stream_and_parsed_with_it() -> None:
    transport = Scripted(
        answer(load("bilibili", "video_normal")), answer(load("bilibili", "content_streams"))
    )
    session = FakeSession()

    seen = await fetch_detail(bilibili_service(transport), session)

    assert STREAMS_KEY in seen[0]["data"]
    assert [spec.endpoint for spec in transport.specs] == [DETAIL, STREAMS]
    assert "/x/player/wbi/playurl" in transport.specs[1].url
    # Its own request_log row, under its own name: not free, not invisible.
    assert [row.endpoint for row in rows(session)] == [DETAIL, STREAMS]


async def test_a_stream_the_platform_refuses_leaves_the_detail_standing() -> None:
    transport = Scripted(
        answer(load("bilibili", "video_normal")),
        answer({"code": -404, "message": "nothing here", "ttl": 1}),
    )
    session = FakeSession()

    seen = await fetch_detail(bilibili_service(transport), session)

    assert STREAMS_KEY not in seen[0]["data"]
    assert [row.outcome for row in rows(session)] == [Outcome.OK, Outcome.BUSINESS_ERROR]


async def test_a_stream_the_scheduler_refuses_leaves_the_detail_standing() -> None:
    transport = Scripted(answer(load("bilibili", "video_normal")))

    seen = await fetch_detail(
        bilibili_service(transport, scheduler=RefusesStreams()), FakeSession()
    )

    assert STREAMS_KEY not in seen[0]["data"]
    assert len(transport.specs) == 1


async def test_a_pinned_detail_asks_for_its_stream_as_the_same_identity() -> None:
    scheduler = FakeScheduler()
    transport = Scripted(
        answer(load("bilibili", "video_normal")), answer(load("bilibili", "content_streams"))
    )

    await fetch_detail(
        bilibili_service(transport, scheduler=scheduler), FakeSession(), identity_id=PINNED_ID
    )

    assert scheduler.asked_for == [PINNED_ID, PINNED_ID]
    assert scheduler.logins == [LoginNeed.NONE, LoginNeed.NONE]


async def test_an_answer_that_needs_nothing_more_is_one_request() -> None:
    transport = Scripted(answer({"code": 0, "data": {"card": {}}}))
    svc = bilibili_service(transport)

    await svc.fetch(
        FakeSession(),  # type: ignore[arg-type]
        Platform.BILIBILI,
        "bilibili.author_profile",
        {"mid": "2"},
        parse=lambda payload: payload,
    )

    assert len(transport.specs) == 1


def test_the_error_the_scheduler_refused_with_is_a_dtk_error() -> None:
    """What `_follow_up` catches; a plain exception would still fail the call."""
    assert issubclass(IdentityPoolExhausted, DtkError)
