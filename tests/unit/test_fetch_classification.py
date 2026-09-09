"""Outcome classification, and the row every request leaves behind.

The split between BUSINESS_ERROR and RISK_CONTROL is the single most
consequential judgement this system makes. V4 treated every non-200 alike, so
looking up a deleted video could condemn a working cookie; conversely, mistaking
a risk-control page for a business error keeps sending a burnt identity back
into rotation.

There used to be two classifiers, and the production path used the cruder one:
a 401, 403 or 444 - the platform refusing the identity - was filed as a business
error, which cools nothing, trips no circuit and tells the caller the content
does not exist. These tests go through :class:`FetchService` with the real
ruleset behind a fake socket, so the pipeline's verdict is the thing asserted
rather than a helper nothing calls.

``tests/unit/test_transport.py`` owns the ruleset itself; this file owns what
the pipeline does with it, and the two must not disagree about a status code.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from dtk.core.errors import (
    DtkError,
    EndpointCircuitOpen,
    IdentityPoolExhausted,
    NotFound,
    UpstreamChanged,
    UpstreamRiskControl,
)
from dtk.core.types import BrowserFamily, Outcome, Platform, RejectReason
from dtk.identity.pool import LiveIdentity
from dtk.platforms import get_adapter
from dtk.scheduler.leases import Lease
from dtk.services import cache
from dtk.services import fetch as fetch_module
from dtk.services.fetch import FetchContext, FetchService, _decode, _dump
from dtk.signing.base import SIGNER_BROWSER, SignedParams
from dtk.transport.base import (
    Fingerprint,
    RawResponse,
    TransportFailure,
    TransportIdentity,
)
from dtk.transport.classify import classify_detailed

ENDPOINT = "douyin.content_detail"
IDENTITY_ID = "6f1d7f4e-4c0a-4b3a-9f2e-1d0c8a5b7e31"
#: A second one, for the tests that have to tell "the identity the caller
#: named" apart from "whichever one the pool handed out".
PINNED_ID = "9a2c33b1-77de-4d55-8f10-2b6e4c9a0d18"
PROXY_ID = uuid.UUID("2b9c1e77-3c2a-4a55-8f10-9d6b4c2e0a13")
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0 Safari/537.36"


def response(status: int = 200, payload=None, body: bytes | None = None) -> RawResponse:
    if body is None:
        body = json.dumps(payload if payload is not None else {}).encode()
    return RawResponse(
        status=status,
        headers={"content-type": "application/json; charset=utf-8"},
        body=body,
        final_url="https://www.douyin.com/x",
        elapsed_ms=12,
    )


@pytest.fixture
def adapter():
    return get_adapter(Platform.DOUYIN)


class FakeScheduler:
    """Grants one lease, or refuses the way the real scheduler refuses."""

    def __init__(self, reject: DtkError | None = None) -> None:
        self.reject = reject
        self.released: list[Outcome] = []
        #: Every identity the service asked for, so a test can assert that a
        #: pinned request never widened to the pool.
        self.asked_for: list[str | None] = []

    async def acquire(
        self, endpoint: str, platform: Platform, *, identity_id: str | None = None
    ) -> Lease:
        self.asked_for.append(identity_id)
        if self.reject is not None:
            raise self.reject
        return Lease(
            identity_id=identity_id or IDENTITY_ID,
            endpoint=endpoint,
            lease_id="lease-1",
            tokens_left=3,
        )

    async def release(self, lease: Lease, outcome: Outcome) -> None:
        self.released.append(outcome)


class FakePool:
    """Remembers what the identity was charged with; the DB is out of scope."""

    def __init__(self, identity: LiveIdentity | None) -> None:
        self.identity = identity
        self.recorded: list[Outcome] = []

    async def load(self, session: Any, identity_id: str, *, proxy_url: str | None = None) -> Any:
        return self.identity

    async def record_outcome(self, session: Any, identity_id: str, outcome: Outcome, **_: Any):
        self.recorded.append(outcome)


class FakeTransport:
    """The real ruleset; only the socket is faked."""

    def __init__(self, answer: RawResponse | BaseException) -> None:
        self.answer = answer
        #: Every identity the transport was asked to send as.
        self.senders: list[Any] = []

    def classify(self, response=None, exception=None):
        return classify_detailed(response, exception)

    async def request(self, identity: Any, spec: Any, timeout: float | None = None) -> RawResponse:
        self.senders.append(identity)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    async def evict(self, identity_id: str) -> None: ...

    async def close(self) -> None: ...


class FakeSession:
    """Collects the rows written; ``get`` answers the proxy-id lookup."""

    def __init__(self, proxy_id: uuid.UUID | None = PROXY_ID) -> None:
        self.rows: list[Any] = []
        self.proxy_id = proxy_id

    def add(self, row: Any) -> None:
        self.rows.append(row)

    async def get(self, entity: Any, key: Any) -> Any:
        return SimpleNamespace(id=key, proxy_id=self.proxy_id)


def identity() -> LiveIdentity:
    return LiveIdentity(
        id=IDENTITY_ID,
        platform=Platform.DOUYIN,
        cookies={"ttwid": "x"},
        fingerprint=Fingerprint(
            browser_family=BrowserFamily.CHROME, browser_major=130, user_agent=CHROME_UA
        ),
        proxy_url="http://user:pass@exit.example:8080",
        authenticated=False,
    )


async def signer(platform: Platform, url: str, params: dict[str, Any], sender: TransportIdentity):
    """The real SignedParams, so the pipeline's signer seam is exercised."""
    return SignedParams(query="a_bogus=AAA", params={"a_bogus": "AAA"}, signer=SIGNER_BROWSER)


def service(
    answer: RawResponse | BaseException = None,  # type: ignore[assignment]
    *,
    scheduler: FakeScheduler | None = None,
    pool: FakePool | None = None,
) -> FetchService:
    return FetchService(
        scheduler=scheduler or FakeScheduler(),  # type: ignore[arg-type]
        pool=pool or FakePool(identity()),  # type: ignore[arg-type]
        transport=FakeTransport(answer if answer is not None else response(200, {})),  # type: ignore[arg-type]
        sign=signer,
        cooldown_base=60,
    )


async def run_fetch(
    svc: FetchService,
    session: FakeSession,
    *,
    parse=lambda payload: payload,
    cache_ttl: int = 0,
    ctx: FetchContext | None = None,
):
    return await svc.fetch(
        session,  # type: ignore[arg-type]
        Platform.DOUYIN,
        ENDPOINT,
        {"aweme_id": "7123"},
        parse=parse,
        cache_ttl=cache_ttl,
        ctx=ctx,
    )


def rows(session: FakeSession) -> list[Any]:
    return [row for row in session.rows if hasattr(row, "outcome")]


class TestClassification:
    """What the pipeline concludes, through the classifier it actually uses."""

    def test_a_healthy_payload_is_ok(self, adapter):
        payload = {"status_code": 0, "aweme_detail": {"aweme_id": "1", "desc": "x"}}
        classification, decoded = service()._classify(adapter, response(200, payload))
        assert classification.outcome is Outcome.OK
        assert decoded == payload

    @pytest.mark.parametrize("status", [401, 403, 405, 412, 429, 444])
    def test_refusal_statuses_are_risk_control(self, adapter, status):
        """The platform refusing this caller. Filing these as business errors is
        what left nothing to cool the identity and nothing to trip the circuit -
        the outage the console could not see."""
        classification, _ = service()._classify(adapter, response(status, {}))
        assert classification.outcome is Outcome.RISK_CONTROL

    @pytest.mark.parametrize("status", [400, 404, 410, 451])
    def test_content_statuses_are_business_errors(self, adapter, status):
        """A 404 is a fact about the content. Counting it against the identity
        is exactly the V4 bug this project exists to avoid."""
        classification, _ = service()._classify(adapter, response(status, {}))
        assert classification.outcome is Outcome.BUSINESS_ERROR

    @pytest.mark.parametrize("status", [407, 408, 500, 502, 503, 504])
    def test_unreachable_statuses_are_network_errors(self, adapter, status):
        """407 is the proxy, not the platform: a lapsed subscription has to reach
        the health probe rather than look like missing content."""
        classification, _ = service()._classify(adapter, response(status, {}))
        assert classification.outcome is Outcome.NETWORK_ERROR

    def test_a_challenge_page_is_risk_control(self, adapter):
        page = b"<html><script src='/verify_center/captcha.js'></script></html>"
        classification, decoded = service()._classify(adapter, response(200, body=page))
        assert classification.outcome is Outcome.RISK_CONTROL
        assert decoded is None

    def test_an_unreadable_200_is_not_evidence_against_the_identity(self, adapter):
        """No challenge marker, no envelope, just not JSON. The identity did get
        served, so the caller hears about a changed response and the pool is left
        alone; cooling on this would burn identities over a shape change."""
        classification, decoded = service()._classify(adapter, response(200, body=b"<html>hi"))
        assert classification.outcome is Outcome.OK
        assert decoded is None

    def test_adapter_risk_marker_wins_over_a_200(self, adapter):
        """The platform's tell is a structurally valid 200 with the payload
        hollowed out, which is why status alone is not enough."""
        empty = {"status_code": 0, "aweme_detail": None}
        classification, _ = service()._classify(adapter, response(200, empty))
        assert classification.outcome is Outcome.RISK_CONTROL


class TestPipelineOutcomes:
    """The verdict has to reach the identity, the caller and the row alike."""

    async def test_a_refusal_cools_the_identity_and_is_reported_as_one(self):
        pool = FakePool(identity())
        scheduler = FakeScheduler()
        svc = service(response(403, {}), scheduler=scheduler, pool=pool)
        session = FakeSession()

        with pytest.raises(UpstreamRiskControl):
            await run_fetch(svc, session)

        assert pool.recorded == [Outcome.RISK_CONTROL]
        assert scheduler.released == [Outcome.RISK_CONTROL]
        assert rows(session)[0].outcome == Outcome.RISK_CONTROL.value
        assert rows(session)[0].error_code == "http.risk_status"

    async def test_a_missing_video_still_leaves_the_identity_alone(self):
        pool = FakePool(identity())
        svc = service(response(404, {}), pool=pool)
        session = FakeSession()

        with pytest.raises(NotFound):
            await run_fetch(svc, session)

        assert pool.recorded == [Outcome.BUSINESS_ERROR]
        # The outcome and the 404 beside it say everything; a rule name here
        # would put a red badge on every deleted video in the console.
        assert rows(session)[0].error_code is None

    async def test_a_proxy_auth_failure_is_retried_on_another_identity(self):
        pool = FakePool(identity())
        svc = service(response(407, {}), pool=pool)
        session = FakeSession()

        with pytest.raises(DtkError):
            await run_fetch(svc, session)

        # Three egresses tried, three rows: the retry loop is what a network
        # error buys, and every attempt is accounted for.
        assert pool.recorded == [Outcome.NETWORK_ERROR] * 3
        assert len(rows(session)) == 3

    async def test_an_unreadable_body_is_reported_as_a_changed_upstream(self):
        svc = service(response(200, body=b"<html>hi"))
        session = FakeSession()

        with pytest.raises(UpstreamChanged):
            await run_fetch(svc, session)

        assert rows(session)[0].outcome == Outcome.OK.value
        assert rows(session)[0].error_code == "UPSTREAM_CHANGED"

    async def test_a_healthy_response_is_parsed_and_reports_its_signer(self):
        payload = {"status_code": 0, "aweme_detail": {"aweme_id": "1"}}
        session = FakeSession()
        result = await run_fetch(service(response(200, payload)), session)

        assert result.payload == payload
        assert result.signer == SIGNER_BROWSER
        assert result.identity_id == IDENTITY_ID


class TestRequestLogColumns:
    """Every column the Logs page reads, from the table's only writer."""

    async def test_the_row_carries_the_signer_and_the_proxy(self):
        session = FakeSession()
        await run_fetch(
            service(response(200, {"status_code": 0, "aweme_detail": {"a": 1}})), session
        )

        row = rows(session)[0]
        assert row.signer == SIGNER_BROWSER
        assert row.proxy_id == PROXY_ID
        assert row.identity_id == uuid.UUID(IDENTITY_ID)
        assert row.cache_hit is False
        assert row.reject_reason is None

    async def test_no_row_carries_the_proxy_url_or_a_cookie(self):
        """The id is the join key; the URL next to it carries credentials."""
        session = FakeSession()
        await run_fetch(
            service(response(200, {"status_code": 0, "aweme_detail": {"a": 1}})), session
        )

        printed = " ".join(f"{k}={v!r}" for k, v in vars(rows(session)[0]).items() if k[0] != "_")
        assert "exit.example" not in printed
        assert "ttwid" not in printed

    async def test_a_cache_hit_is_logged_as_one(self, monkeypatch):
        payload = {"cached": True}

        async def hit(digest: str):
            return payload

        monkeypatch.setattr(cache, "get", hit)
        session = FakeSession()
        result = await run_fetch(service(), session, cache_ttl=60)

        assert result.cached is True
        row = rows(session)[0]
        assert row.cache_hit is True
        assert row.outcome == Outcome.OK.value
        assert row.identity_id is None

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (
                IdentityPoolExhausted(
                    "every identity is out of quota",
                    details={"endpoint": ENDPOINT, "reject_reason": RejectReason.NO_TOKEN.value},
                ),
                RejectReason.NO_TOKEN.value,
            ),
            (
                EndpointCircuitOpen(
                    "circuit is open",
                    details={
                        "endpoint": ENDPOINT,
                        "reject_reason": RejectReason.CIRCUIT_OPEN.value,
                    },
                ),
                RejectReason.CIRCUIT_OPEN.value,
            ),
        ],
    )
    async def test_a_rejected_request_is_still_logged(self, error, reason):
        """The Logs page must not go quiet during the outage that caused the
        rejection: this service is the only writer request_log has."""
        svc = service(scheduler=FakeScheduler(error))
        session = FakeSession()

        with pytest.raises(DtkError):
            await run_fetch(svc, session)

        row = rows(session)[0]
        assert row.reject_reason == reason
        assert row.error_code == error.code.value
        assert row.identity_id is None
        assert row.endpoint == ENDPOINT

    async def test_a_rejection_writes_one_row_and_does_not_retry(self):
        """A refusal is not a network error: retrying it would queue three times
        against a pool that has already said no."""
        scheduler = FakeScheduler(
            IdentityPoolExhausted(
                "no identity",
                details={"reject_reason": RejectReason.NO_IDENTITY.value},
            )
        )
        session = FakeSession()

        with pytest.raises(IdentityPoolExhausted):
            await run_fetch(service(scheduler=scheduler), session)

        assert len(rows(session)) == 1

    async def test_a_transport_failure_is_logged_with_no_status(self):
        failure = TransportFailure(
            "connect timeout",
            identity_id=IDENTITY_ID,
            url="https://www.douyin.com/x?a_bogus=SECRET",
            elapsed_ms=1,
        )
        session = FakeSession()

        with pytest.raises(DtkError):
            await run_fetch(service(failure), session)

        row = rows(session)[0]
        assert row.http_status is None
        assert row.error_code == "transport_failure"
        assert row.outcome == Outcome.NETWORK_ERROR.value


class TestDecode:
    def test_empty_body_decodes_to_an_empty_mapping(self):
        assert _decode(RawResponse(status=200, body=b"")) == {}

    def test_utf8_content_survives(self):
        # Escaped rather than literal so this file stays ASCII; the hygiene
        # check keeps CJK out of every source file that is not a declared
        # input fixture.
        payload = {"desc": "\u6d4b\u8bd5 \U0001f600"}
        assert _decode(response(200, payload))["desc"] == payload["desc"]

    def test_invalid_bytes_do_not_raise(self):
        body = b'{"desc": "' + b"\xff\xfe" + b'"}'
        assert isinstance(_decode(RawResponse(status=200, body=body)), dict)


class TestDump:
    def test_raw_is_stripped_unless_requested(self):
        from datetime import UTC, datetime

        from dtk.core.types import ContentKind
        from dtk.models import Author, Content, ContentStats, Media

        content = Content(
            platform=Platform.DOUYIN,
            content_id="7372484719365098803",
            kind=ContentKind.VIDEO,
            web_url="https://www.douyin.com/video/7372484719365098803",
            title="t",
            description="d",
            author=Author(platform=Platform.DOUYIN, uid="MS4x", nickname="n"),
            stats=ContentStats(play_count=None),
            media=Media(),
            fetched_at=datetime.now(UTC),
            raw={"secret": "should not be exposed by default"},
        )
        assert "raw" not in _dump(content, include_raw=False)
        assert _dump(content, include_raw=True)["raw"] is not None

    def test_absent_metrics_stay_null_rather_than_zero(self):
        """A missing play count and a play count of zero are different facts."""
        from datetime import UTC, datetime

        from dtk.core.types import ContentKind
        from dtk.models import Author, Content, ContentStats, Media

        dumped = _dump(
            Content(
                platform=Platform.DOUYIN,
                content_id="1",
                kind=ContentKind.VIDEO,
                web_url="https://x",
                title="t",
                description="d",
                author=Author(platform=Platform.DOUYIN, uid="u", nickname="n"),
                stats=ContentStats(),
                media=Media(),
                fetched_at=datetime.now(UTC),
            ),
            include_raw=False,
        )
        assert dumped["stats"]["play_count"] is None
        assert dumped["content_id"] == "1"
        assert isinstance(dumped["content_id"], str)


class TestTransportSpecSeam:
    """The platform layer and the transport layer both define a RequestSpec.

    They are deliberately different shapes - a TypedDict so platform packages
    stay free of transport types, and a dataclass that also carries the logical
    endpoint name - and the orchestration layer converts between them. Letting
    either side learn the other's type is how two independent modules become one
    tangled one, so the conversion is pinned here.

    These tests used to assert the defect. `test_signed_params_replace_the
    _originals` checked that the signed parameters were merged into a dict and
    handed to the transport to re-encode - which is exactly what broke every
    platform read through the API on both platforms, because the signature is
    computed over an exact query string and Douyin's lives in three headers.
    The contract they now pin is the one `SignedRequest` always stated: send
    the query byte for byte, and carry the headers.
    """

    def _spec(self, **overrides):
        base = {
            "method": "GET",
            "url": "https://www.douyin.com/aweme/v1/web/aweme/detail/",
            "params": {"aweme_id": "1"},
            "headers": {"referer": "https://www.douyin.com/"},
            "body": None,
        }
        base.update(overrides)
        return base

    def _signed(self, query="aweme_id=1&a_bogus=AAA", headers=None, params=None):
        from dtk.signing.base import SignedParams

        return SignedParams(
            query=query,
            params=params or {"a_bogus": "AAA"},
            headers=headers or {},
        )

    def test_produces_the_transport_dataclass(self):
        from dtk.services.fetch import _to_transport_spec
        from dtk.transport.base import RequestSpec as TransportRequestSpec

        out = _to_transport_spec(self._spec(), self._signed(), "douyin.content_detail")
        assert isinstance(out, TransportRequestSpec)
        assert out.method == "GET"

    def test_carries_the_logical_endpoint_not_the_signed_url(self):
        """Signatures make every URL unique; logging them would make every log
        line uncorrelatable and leak the token."""
        from dtk.services.fetch import _to_transport_spec

        out = _to_transport_spec(
            self._spec(), self._signed(query="aweme_id=1&a_bogus=SECRET"), "douyin.content_detail"
        )
        assert out.endpoint == "douyin.content_detail"
        assert "a_bogus" not in (out.endpoint or "")

    def test_the_query_is_sent_byte_for_byte(self):
        """The regression. A base64 signature contains `/`, and re-encoding it
        as `%2F` changes the string the platform verifies - measured at 30 bytes
        of difference on one TikTok detail call, answered 200 with an empty
        payload."""
        from dtk.services.fetch import _to_transport_spec

        query = "aid=1988&itemId=7&X-Gnarly=Mky/8Brd+GN8aH=="
        out = _to_transport_spec(self._spec(), self._signed(query=query), "e")
        assert out.url.endswith("?" + query)
        # None, not the parameters: handing the transport both would let it
        # re-encode them and append a second copy.
        assert out.params is None

    def test_the_signed_headers_are_carried(self):
        """The other half of the regression. Douyin's web signature is three
        headers; dropping them is a 403 on every request."""
        from dtk.services.fetch import _to_transport_spec

        out = _to_transport_spec(
            self._spec(),
            self._signed(
                headers={
                    "uifid": "U",
                    "x-secsdk-web-signature": "S",
                    "x-secsdk-web-expire": "E",
                }
            ),
            "e",
        )
        assert out.headers["x-secsdk-web-signature"] == "S"
        assert out.headers["uifid"] == "U"
        # And the platform's own headers survive alongside them.
        assert out.headers["referer"] == "https://www.douyin.com/"

    def test_an_unsigned_request_still_sends_its_parameters(self):
        """Not every endpoint is signed. With no query, the platform's own
        parameters have to reach the transport."""
        from dtk.services.fetch import _to_transport_spec

        out = _to_transport_spec(self._spec(), self._signed(query="", params={}), "e")
        assert out.params == {"aweme_id": "1"}
        assert out.url.endswith("/aweme/detail/")

    def test_empty_body_becomes_none_rather_than_an_empty_object(self):
        from dtk.services.fetch import _to_transport_spec

        assert _to_transport_spec(self._spec(body={}), self._signed(), "e").json_body is None
        assert _to_transport_spec(self._spec(body={"a": 1}), self._signed(), "e").json_body == {
            "a": 1
        }


async def test_the_signature_and_the_request_describe_the_same_visitor() -> None:
    """One identity object reaches both seams, because the platform compares them.

    The signer and the transport used to be handed the identity separately -
    the signer got a bare fingerprint, the transport got the cookies - so
    nothing in the pipeline connected the two. That is how requests came to go
    out with a signature naming one session and a Cookie header naming another,
    which Douyin and TikTok answer by withholding the payload rather than by
    returning an error, so it read as rate limiting for weeks.
    """
    signed_as: list[Any] = []

    async def recording_signer(
        platform: Platform, url: str, params: dict[str, Any], sender: TransportIdentity
    ):
        signed_as.append(sender)
        return SignedParams(query="a_bogus=AAA", params={"a_bogus": "AAA"}, signer=SIGNER_BROWSER)

    transport = FakeTransport(response(200, {"status_code": 0, "aweme_detail": {"x": 1}}))
    svc = FetchService(
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        pool=FakePool(identity()),  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        sign=recording_signer,
        cooldown_base=60,
    )
    await run_fetch(svc, FakeSession())

    assert len(signed_as) == 1 and len(transport.senders) == 1
    assert signed_as[0] is transport.senders[0]
    # Named explicitly: the cookie jar is the half the signer needs and the
    # half a bare fingerprint would not have carried.
    assert signed_as[0].cookies == {"ttwid": "x"}
    assert signed_as[0].proxy_url == "http://user:pass@exit.example:8080"


class TestBareEnvelope:
    """A 200 carrying a status and no payload.

    Measured on 2026-09-08: Douyin answers author_posts with any non-zero
    max_cursor as exactly `{"status_code":0}` - 17 bytes - while max_cursor=0
    returns 612 KB. It is a refusal, not the end of the feed. It used to reach
    the parser and come back as a non-retryable UPSTREAM_CHANGED telling the
    operator to report a parser bug, and it was logged as outcome=ok, so the
    pool never learned anything from it.
    """

    def _classify(self, body: bytes, status: int = 200):
        return classify_detailed(response(status=status, body=body))

    def test_a_status_only_envelope_is_withheld_not_ok(self):
        result = self._classify(b'{"status_code":0}')
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "payload.bare_envelope"

    def test_tracing_crumbs_do_not_make_it_look_like_a_payload(self):
        result = self._classify(b'{"status_code":0,"status_msg":"","log_pb":{"impr_id":"x"}}')
        assert result.outcome is Outcome.RISK_CONTROL

    def test_a_real_answer_is_untouched(self):
        """The rule must key on 'nothing but metadata', not on a payload name -
        the payload key differs per endpoint."""
        result = self._classify(b'{"status_code":0,"aweme_list":[{"aweme_id":"1"}],"has_more":1}')
        assert result.outcome is Outcome.OK

    def test_an_empty_list_is_still_a_real_answer(self):
        """An author with no posts answers with the key present and empty; that
        is the end of a feed, not a refusal."""
        result = self._classify(b'{"status_code":0,"aweme_list":[],"has_more":0}')
        assert result.outcome is Outcome.OK

    def test_a_named_refusal_still_reports_its_own_reason(self):
        """payload.explained runs first, so a platform that says why keeps
        being reported as the business answer it is."""
        body = (
            b'{"status_code":0,"aweme_detail":null,'
            b'"filter_detail":{"filter_reason":"self_see","detail_msg":"only you"}}'
        )
        assert self._classify(body).outcome is Outcome.BUSINESS_ERROR


class TestPinnedIdentity:
    """What a request that names one identity must and must not do.

    The case is a caller reading content only their own account can see, with a
    jar they imported from their own browser. Every guarantee here follows from
    that one sentence: the answer belongs to a session, so serving it from
    another identity, or from a cache anyone else can read, does not degrade
    the answer - it changes what was asked and hides that it did.
    """

    async def test_the_pin_reaches_the_scheduler(self):
        scheduler = FakeScheduler()
        svc = service(
            response(200, {"status_code": 0, "aweme_detail": {"x": 1}}), scheduler=scheduler
        )

        await run_fetch(svc, FakeSession(), ctx=FetchContext(identity_id=PINNED_ID))

        assert scheduler.asked_for == [PINNED_ID]

    async def test_an_unpinned_call_asks_for_nothing_in_particular(self):
        scheduler = FakeScheduler()
        svc = service(
            response(200, {"status_code": 0, "aweme_detail": {"x": 1}}), scheduler=scheduler
        )

        await run_fetch(svc, FakeSession())

        assert scheduler.asked_for == [None]

    async def test_a_pinned_answer_is_never_written_to_the_shared_cache(self, monkeypatch):
        """The leak this prevents: a private post fetched on the caller's own
        session, cached, then served to the next anonymous caller who asks for
        the same post."""
        written: list[str] = []

        async def record(digest, payload, ttl):
            written.append(digest)

        monkeypatch.setattr(fetch_module.cache, "put", record)
        svc = service(response(200, {"status_code": 0, "aweme_detail": {"x": 1}}))

        await run_fetch(svc, FakeSession(), cache_ttl=600, ctx=FetchContext(identity_id=PINNED_ID))

        assert written == []

    async def test_an_unpinned_answer_is_still_cached(self, monkeypatch):
        """The bypass must be exactly as narrow as it claims to be."""
        written: list[str] = []

        async def record(digest, payload, ttl):
            written.append(digest)

        async def miss(digest):
            return None

        monkeypatch.setattr(fetch_module.cache, "get", miss)
        monkeypatch.setattr(fetch_module.cache, "put", record)
        svc = service(response(200, {"status_code": 0, "aweme_detail": {"x": 1}}))

        await run_fetch(svc, FakeSession(), cache_ttl=600)

        assert len(written) == 1

    async def test_a_pinned_call_never_reads_the_shared_cache(self, monkeypatch):
        """Reading is the other half. A pinned caller who was handed the public
        view of their own post would have no way to tell."""
        reads: list[str] = []

        async def record(digest):
            reads.append(digest)
            return {"data": "somebody else's answer"}

        monkeypatch.setattr(fetch_module.cache, "get", record)
        svc = service(response(200, {"status_code": 0, "aweme_detail": {"x": 1}}))

        result = await run_fetch(
            svc, FakeSession(), cache_ttl=600, ctx=FetchContext(identity_id=PINNED_ID)
        )

        assert reads == []
        assert result.cached is False

    async def test_a_pinned_call_does_not_retry_onto_the_same_bucket(self):
        """Retrying is only worth anything because the next attempt lands on a
        different identity. Pinned it does not, and three attempts would empty
        a token bucket that holds three to five."""
        scheduler = FakeScheduler()
        svc = service(response(407, {}), scheduler=scheduler)

        with pytest.raises(DtkError):
            await run_fetch(svc, FakeSession(), ctx=FetchContext(identity_id=PINNED_ID))

        assert scheduler.asked_for == [PINNED_ID]

    async def test_an_unpinned_call_still_retries_across_identities(self):
        scheduler = FakeScheduler()
        svc = service(response(407, {}), scheduler=scheduler)

        with pytest.raises(DtkError):
            await run_fetch(svc, FakeSession())

        assert len(scheduler.asked_for) == fetch_module.MAX_TRANSPORT_ATTEMPTS


class TestCacheKeyDiscriminators:
    """Two callers who will not receive the same answer must not share a key."""

    def test_a_different_egress_is_a_different_entry(self):
        plain = cache.cache_key(ENDPOINT, {"aweme_id": "7123"})
        proxied = cache.cache_key(
            ENDPOINT, {"aweme_id": "7123"}, egress="http://user:pw@example:8080"
        )
        assert plain != proxied

    def test_the_egress_value_never_appears_in_the_key(self):
        """It arrives as a URL that can carry credentials."""
        secret = "http://user:hunter2@example:8080"
        digest = cache.cache_key(ENDPOINT, {"aweme_id": "7123"}, egress=secret)
        assert "hunter2" not in digest
        assert "example" not in digest

    def test_the_same_egress_is_the_same_entry(self):
        first = cache.cache_key(ENDPOINT, {"aweme_id": "7123"}, egress="http://a:1")
        second = cache.cache_key(ENDPOINT, {"aweme_id": "7123"}, egress="http://a:1")
        assert first == second
