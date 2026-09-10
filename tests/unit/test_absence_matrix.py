"""What each platform answers when the thing asked for is not there.

Every shape below was captured on 2026-09-10 by asking Douyin and TikTok for a
post that was deleted, an id that never existed, an id of the wrong length, an
author nobody has, and a likes list that is private - then recording the reply
byte count, envelope and status fields verbatim.

The point of pinning them is one asymmetry. Reading a refusal as an answer
leaves a burnt identity in rotation, which is bad; reading an answer as a
refusal cools a healthy identity and counts toward the endpoint's risk rate,
which trips the circuit breaker for every identity at once. The second mistake
is the expensive one, and it is the one this table is here to stop coming back.

The rule for the genuine article stays simple and is the one the operator asked
for: a normal response carrying no data is a refusal. What the matrix adds is
the list of shapes that only LOOK like that.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest

from dtk.core.types import Outcome, Platform
from dtk.platforms.registry import get_adapter
from dtk.transport.base import RawResponse
from dtk.transport.classify import Classifier


def answer(payload: Any = None, *, status: int = 200, body: bytes | None = None) -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=body if body is not None else json.dumps(payload).encode(),
        elapsed_ms=200,
    )


def verdict(response: RawResponse, *, empty_body_is_normal: bool = False) -> Any:
    return Classifier().classify(response, empty_body_is_normal=empty_body_is_normal)


# --------------------------------------------------------------------------
# Douyin
# --------------------------------------------------------------------------


class TestDouyinAbsence:
    def test_a_post_that_was_deleted_is_an_answer(self) -> None:
        """200, 406 bytes: empty `aweme_detail` and a `filter_detail` saying why."""
        response = answer(
            {
                "status_code": 0,
                "aweme_detail": None,
                "filter_detail": {
                    "aweme_id": "7126745726494821640",
                    "filter_reason": "delete",
                    # "the video you want to watch does not exist", as Douyin words it.
                    "detail_msg": "\u4f60\u8981\u770b\u7684\u89c6\u9891\u4e0d\u5b58\u5728",
                    "notice": "",
                    "icon": "",
                },
            }
        )
        assert verdict(response).outcome is Outcome.BUSINESS_ERROR

    def test_an_id_that_never_existed_is_the_same_answer(self) -> None:
        """211 bytes, same shape. Douyin does not distinguish gone from never."""
        response = answer(
            {
                "status_code": 0,
                "aweme_detail": None,
                "filter_detail": {"aweme_id": "7999999999999999999", "filter_reason": "delete"},
            }
        )
        assert verdict(response).outcome is Outcome.BUSINESS_ERROR

    def test_an_author_nobody_has_is_an_answer(self) -> None:
        """200, 129 bytes, `status_code: 2` and the id named as invalid."""
        response = answer({"status_code": 2, "status_msg": "UserId not valid", "user": None})
        result = verdict(response)
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert "user id not valid" in (result.detail or "")

    def test_comments_of_a_post_that_is_not_there_are_simply_empty(self) -> None:
        """765 bytes, `comments: []`. An empty list is a page, not a refusal."""
        response = answer({"status_code": 0, "comments": [], "has_more": 0, "cursor": 0})
        assert verdict(response).outcome is Outcome.OK

    def test_a_private_likes_list_is_zero_bytes_and_still_an_answer(self) -> None:
        """The one endpoint whose "no" is silence.

        Measured against two different authors: zero bytes both times, while
        `author_posts` for the same author on the same identity returned 230KB
        and 439KB in the request immediately after. So the silence belongs to
        the endpoint, not to the identity.
        """
        response = answer(body=b"")
        result = verdict(response, empty_body_is_normal=True)
        # A business error, not OK: "the platform answered, the content is gone
        # or private" is this module's own definition of the category, and OK
        # would hand the parser an empty payload it correctly refuses.
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert result.rule == "body.silent_answer"

    def test_the_same_silence_anywhere_else_is_a_refusal(self) -> None:
        """Which is the operator's own rule, and stays the default."""
        assert verdict(answer(body=b"")).outcome is Outcome.RISK_CONTROL

    def test_only_the_likes_endpoint_claims_its_silence_is_normal(self) -> None:
        """A flag that spread would quietly disable the plainest rule there is."""
        specs = get_adapter(Platform.DOUYIN).endpoints.specs
        declared = {name for name, spec in specs.items() if spec.empty_body_is_normal}
        assert declared == {"douyin.author_likes"}
        assert not any(
            spec.empty_body_is_normal
            for spec in get_adapter(Platform.TIKTOK).endpoints.specs.values()
        )

    def test_an_envelope_with_a_status_and_nothing_else_is_a_refusal(self) -> None:
        """17 bytes, `{"status_code": 0}`, asked for posts from an old cursor -
        while the same author from cursor 0 returns hundreds of kilobytes."""
        response = answer({"status_code": 0})
        result = verdict(response)
        assert result.outcome is Outcome.RISK_CONTROL
        assert result.rule == "payload.bare_envelope"


# --------------------------------------------------------------------------
# TikTok
# --------------------------------------------------------------------------


#: The tracing crumbs TikTok attaches to everything. Present in every capture,
#: and meta as far as the classifier is concerned.
TRACE: Final[dict[str, Any]] = {"extra": {"now": 1789020280000}, "log_pb": {"impr_id": "x"}}


class TestTikTokAbsence:
    def test_a_post_that_is_gone_is_an_answer(self) -> None:
        response = answer({**TRACE, "statusCode": 10204, "status_code": 0, "status_msg": ""})
        result = verdict(response)
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert "item not found" in (result.detail or "")

    def test_a_handle_nobody_has_is_an_answer(self) -> None:
        """220 bytes, `statusCode: 100002`, empty `userInfo`. Before the status
        keys were read properly this was the emptiest possible risk signature."""
        response = answer({**TRACE, "statusCode": 100002, "status_code": 0, "userInfo": None})
        result = verdict(response)
        assert result.outcome is Outcome.BUSINESS_ERROR
        assert "user not found" in (result.detail or "")

    def test_posts_of_a_sec_uid_nobody_has_is_an_answer(self) -> None:
        """The only case in the matrix that is not a 200."""
        response = answer(
            {"log_pb": {"impr_id": "x"}, "statusCode": 10201, "status_code": 10201},
            status=400,
        )
        assert verdict(response).outcome is Outcome.BUSINESS_ERROR

    def test_a_private_likes_list_is_an_empty_page_not_a_silence(self) -> None:
        """241 bytes: TikTok says it differently to Douyin.

        `cursor` and `hasMore` are what make this a page rather than a bare
        envelope - the list is simply not in it. Douyin answers the same
        question with zero bytes, which is why only one of the two endpoints
        needs `empty_body_is_normal`.
        """
        response = answer(
            {
                **TRACE,
                "statusCode": 0,
                "status_code": 0,
                "status_msg": "",
                "cursor": "0",
                "hasMore": False,
            }
        )
        assert verdict(response).outcome is Outcome.OK

    def test_comments_of_a_post_that_is_gone_still_come_back(self) -> None:
        """5748 bytes of them. The post is gone; its comments are not."""
        response = answer(
            {"status_code": 0, "status_msg": "", "comments": [{"cid": "1"}, {"cid": "2"}]}
        )
        assert verdict(response).outcome is Outcome.OK


# --------------------------------------------------------------------------
# The rule that has to keep working
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("withheld post", {"status_code": 0, "aweme_detail": None}),
        ("withheld author", {"status_code": 0, "user": None}),
        ("withheld item", {"statusCode": 0, "status_code": 0, "itemInfo": None}),
        ("verification envelope", {"status_code": 10000}),
    ],
)
def test_a_payload_withheld_with_no_reason_is_still_a_refusal(
    label: str, payload: dict[str, Any]
) -> None:
    """The matrix names the exceptions; it must not have widened the rule.

    Each of these is an envelope that parsed, carried the shape of an answer,
    and had the answer taken out of it - with nothing saying why. That is what
    a platform withholding a payload looks like, and it is the signature the
    identity pool exists to react to.
    """
    assert verdict(answer(payload)).outcome is Outcome.RISK_CONTROL, label
