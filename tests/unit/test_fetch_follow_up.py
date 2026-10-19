"""A second call an answer asks for: Bilibili's detail and its playurl.

The fetch layer makes it, so it is scheduled, signed and logged like any other
call - and it must never be what fails the request. A Bilibili detail without
its stream is still the post.

Unless the plan says it is required. The weekly selection's index without the
issue it names is no answer at all, and a part asked for by number without
its own file would download as nothing; those fail as the first call would.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import pytest

from dtk.core.errors import DtkError, IdentityPoolExhausted, NotFound, UpstreamRiskControl
from dtk.core.types import BrowserFamily, LoginNeed, Outcome, Platform
from dtk.identity.pool import LiveIdentity
from dtk.platforms.bilibili.parser import STREAMS_KEY
from dtk.scheduler.leases import Lease
from dtk.services.fetch import FetchContext, FetchService
from dtk.signing.base import SIGNER_BROWSER, SignedParams
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
WEEKLY = "bilibili.popular_weekly"
ISSUE = "bilibili.popular_weekly_issue"


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


async def keeps_the_query(platform: Platform, spec: Any, sender: Any) -> SignedParams:
    """A signer that sends the builder's parameters as they are, so a test can
    read which part or issue a follow-up asked for off the URL."""
    params = {key: str(value) for key, value in (spec.get("params") or {}).items()}
    return SignedParams(query=urlencode(params), params=params, signer=SIGNER_BROWSER)


def bilibili_service(
    transport: FakeTransport,
    *,
    scheduler: FakeScheduler | None = None,
    sign: Any = signer,
) -> FetchService:
    return FetchService(
        scheduler=scheduler or FakeScheduler(),  # type: ignore[arg-type]
        pool=FakePool(bilibili_identity()),  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        sign=sign,
        cooldown_base=60,
    )


def answer(payload: Any) -> RawResponse:
    return response(200, body=json.dumps(payload).encode())


async def fetch_detail(
    svc: FetchService,
    session: FakeSession,
    *,
    endpoint: str = DETAIL,
    params: dict[str, Any] | None = None,
    **ctx: Any,
) -> list[Any]:
    seen: list[Any] = []

    def parse(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return payload

    await svc.fetch(
        session,  # type: ignore[arg-type]
        Platform.BILIBILI,
        endpoint,
        {"bvid": "BV1xx411c7mD"} if params is None else params,
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


async def test_a_named_part_is_fetched_by_its_own_cid() -> None:
    detail = load("bilibili", "video_multipart")
    transport = Scripted(answer(detail), answer(load("bilibili", "content_streams")))

    seen = await fetch_detail(
        bilibili_service(transport, sign=keeps_the_query),
        FakeSession(),
        params={"bvid": "BV1xx411c7mD", "part": 3},
    )

    third = str(detail["data"]["pages"][2]["cid"])
    assert f"cid={third}" in transport.specs[1].url
    assert seen[0]["data"]["__dtk_part"] == 3


async def test_a_named_part_whose_file_is_refused_fails_the_request() -> None:
    """Parsed without it, part 3 would be the post with no stream, and its
    download would fail as "no downloadable media" instead of saying why."""
    transport = Scripted(
        answer(load("bilibili", "video_multipart")),
        answer({"code": -352, "message": "risk", "ttl": 1, "data": {"v_voucher": "x"}}),
    )

    with pytest.raises(UpstreamRiskControl):
        await fetch_detail(
            bilibili_service(transport),
            FakeSession(),
            params={"bvid": "BV1xx411c7mD", "part": 3},
        )


async def test_the_weekly_index_is_followed_by_its_newest_issue() -> None:
    index = load("bilibili", "popular_weekly_index")
    transport = Scripted(answer(index), answer(load("bilibili", "popular_weekly_issue")))
    session = FakeSession()

    seen = await fetch_detail(
        bilibili_service(transport, sign=keeps_the_query), session, endpoint=WEEKLY, params={}
    )

    assert "__dtk_issue" in seen[0]["data"]
    assert f"number={index['data']['list'][0]['number']}" in transport.specs[1].url
    assert [row.endpoint for row in rows(session)] == [WEEKLY, ISSUE]


async def test_an_issue_that_does_not_exist_is_not_found() -> None:
    """Not an empty issue: the index alone parsed as one would read as a
    week in which nothing was selected."""
    transport = Scripted(
        answer(load("bilibili", "popular_weekly_index")),
        answer(load("bilibili", "popular_weekly_missing")),
    )

    with pytest.raises(NotFound):
        await fetch_detail(
            bilibili_service(transport),
            FakeSession(),
            endpoint=WEEKLY,
            params={"cursor": "999"},
        )


async def test_a_refused_issue_fails_the_request() -> None:
    transport = Scripted(answer(load("bilibili", "popular_weekly_index")))

    class RefusesIssues(FakeScheduler):
        async def acquire(self, endpoint: str, platform: Platform, **kwargs: Any) -> Lease:
            if endpoint == ISSUE:
                raise IdentityPoolExhausted("no identity available for this endpoint")
            return await super().acquire(endpoint, platform, **kwargs)

    with pytest.raises(IdentityPoolExhausted):
        await fetch_detail(
            bilibili_service(transport, scheduler=RefusesIssues()),
            FakeSession(),
            endpoint=WEEKLY,
            params={},
        )
