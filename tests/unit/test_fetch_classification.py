"""Outcome classification in the fetch pipeline.

The split between BUSINESS_ERROR and RISK_CONTROL is the single most
consequential judgement this system makes. V4 treated every non-200 alike, so
looking up a deleted video could condemn a working cookie; conversely, mistaking
a risk-control page for a business error keeps sending a burnt identity back
into rotation.
"""

from __future__ import annotations

import json

import pytest

from dtk.core.types import Outcome, Platform
from dtk.platforms import get_adapter
from dtk.services.fetch import _classify, _decode, _dump
from dtk.transport.base import RawResponse


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


class TestClassification:
    def test_a_healthy_payload_is_ok(self, adapter):
        payload = {"status_code": 0, "aweme_detail": {"aweme_id": "1", "desc": "x"}}
        assert _classify(adapter, response(200, payload)) is Outcome.OK

    def test_rate_limit_status_is_risk_control(self, adapter):
        assert _classify(adapter, response(429, {})) is Outcome.RISK_CONTROL

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_errors_are_risk_control(self, adapter, status):
        assert _classify(adapter, response(status, {})) is Outcome.RISK_CONTROL

    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_client_errors_are_business_errors(self, adapter, status):
        """A 404 is a fact about the content. Counting it against the identity
        is exactly the V4 bug this project exists to avoid."""
        assert _classify(adapter, response(status, {})) is Outcome.BUSINESS_ERROR

    def test_unparseable_body_on_a_200_is_risk_control(self, adapter):
        """A 200 that is not JSON is usually an interstitial or a captcha page,
        not a valid response."""
        assert (
            _classify(adapter, response(200, body=b"<html>verify</html>")) is Outcome.RISK_CONTROL
        )

    def test_adapter_risk_marker_wins_over_a_200(self, adapter):
        """The platform's tell is a structurally valid 200 with the payload
        hollowed out, which is why status alone is not enough."""
        empty = {"status_code": 0, "aweme_detail": None}
        assert _classify(adapter, response(200, empty)) is Outcome.RISK_CONTROL


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

    def test_produces_the_transport_dataclass(self):
        from dtk.services.fetch import _to_transport_spec
        from dtk.transport.base import RequestSpec as TransportRequestSpec

        out = _to_transport_spec(self._spec(), {"aweme_id": "1"}, "douyin.content.detail")
        assert isinstance(out, TransportRequestSpec)
        assert out.method == "GET"
        assert out.url.endswith("/aweme/detail/")

    def test_carries_the_logical_endpoint_not_the_signed_url(self):
        """Signatures make every URL unique; logging them would make every log
        line uncorrelatable and leak the token."""
        from dtk.services.fetch import _to_transport_spec

        out = _to_transport_spec(
            self._spec(), {"aweme_id": "1", "a_bogus": "SECRET"}, "douyin.content.detail"
        )
        assert out.endpoint == "douyin.content.detail"
        assert "a_bogus" not in (out.endpoint or "")

    def test_signed_params_replace_the_originals(self):
        from dtk.services.fetch import _to_transport_spec

        out = _to_transport_spec(
            self._spec(), {"aweme_id": "1", "a_bogus": "AAA", "msToken": "BBB"}, "e"
        )
        assert out.params == {"aweme_id": "1", "a_bogus": "AAA", "msToken": "BBB"}

    def test_none_params_are_dropped_and_values_stringified(self):
        from dtk.services.fetch import _to_transport_spec

        out = _to_transport_spec(self._spec(), {"count": 20, "cursor": None}, "e")
        assert out.params == {"count": "20"}

    def test_empty_body_becomes_none_rather_than_an_empty_object(self):
        from dtk.services.fetch import _to_transport_spec

        assert _to_transport_spec(self._spec(body={}), {}, "e").json_body is None
        assert _to_transport_spec(self._spec(body={"a": 1}), {}, "e").json_body == {"a": 1}
