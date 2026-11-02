"""A body with two status fields, and the one that means something.

TikTok answers a post that does not exist with HTTP 200 and both
``status_code: 0`` and ``statusCode: 10204``. Reading the first key present
found the zero, so nothing in the envelope looked wrong, and the response fell
through to the rule that calls an envelope-with-no-payload risk control.

The cost of that is the one this module's docstring opens by warning about:
looking up a deleted video cooled the identity that asked for it and counted
toward the endpoint's risk rate, so a caller walking a list of old links could
trip the circuit breaker for the whole pool.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dtk.core.types import Outcome
from dtk.transport.base import RawResponse
from dtk.transport.classify import Classifier

#: Captured 2026-09-10 from `tiktok.content_detail`, 205 bytes, HTTP 200. The
#: same body comes back for a deleted post and for an id that never existed.
TIKTOK_MISSING: dict[str, Any] = {
    "extra": {"fatal_item_ids": [], "logid": "2026091006044", "now": 1789020280000},
    "log_pb": {"impr_id": "2026091006044"},
    "statusCode": 10204,
    "status_code": 0,
    "status_msg": "",
}


def classify(payload: dict[str, Any], status: int = 200) -> Any:
    return Classifier().classify(
        RawResponse(
            status=status,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode(),
            elapsed_ms=200,
        )
    )


def test_a_post_that_does_not_exist_is_a_business_error() -> None:
    verdict = classify(TIKTOK_MISSING)
    assert verdict.outcome is Outcome.BUSINESS_ERROR
    assert verdict.rule == "envelope.business_code"
    assert "10204" in (verdict.detail or "")


def test_it_is_not_read_as_the_identity_being_refused() -> None:
    """The regression: `payload.bare_envelope` used to claim this one."""
    assert classify(TIKTOK_MISSING).outcome is not Outcome.RISK_CONTROL


def test_a_zero_beside_a_nonzero_does_not_hide_it() -> None:
    assert classify({"status_code": 0, "statusCode": 2053}).outcome is Outcome.BUSINESS_ERROR


def test_a_zero_on_its_own_still_means_nothing_went_wrong() -> None:
    verdict = classify({"status_code": 0, "aweme_detail": {"aweme_id": "1"}})
    assert verdict.outcome is Outcome.OK


def test_an_envelope_with_no_status_at_all_is_unchanged() -> None:
    verdict = classify({"aweme_detail": {"aweme_id": "1"}})
    assert verdict.outcome is Outcome.OK


@pytest.mark.parametrize("code", [10000])
def test_a_real_risk_code_is_still_risk_control(code: int) -> None:
    """The split has to keep working in both directions."""
    verdict = classify({"status_code": 0, "statusCode": code})
    assert verdict.outcome is Outcome.RISK_CONTROL


def test_an_empty_envelope_with_no_payload_is_still_suspicious() -> None:
    """`payload.bare_envelope` earns its keep on a body that says nothing."""
    verdict = classify({"status_code": 0})
    assert verdict.outcome is Outcome.RISK_CONTROL
    assert verdict.rule == "payload.bare_envelope"


#: Captured 2026-11-02 from `tiktok.search_posts` on a guest identity: HTTP 200,
#: 125 bytes. Read as an unknown nonzero envelope it became NOT_FOUND - "the
#: requested resource does not exist" for a keyword search.
TIKTOK_LOGIN_FIRST: dict[str, Any] = {
    "status_code": 2483,
    "status_msg": "Please login your account first",
    "log_pb": {"impr_id": "20261102064430F6D47DC3F3C39100E64D"},
}


def test_a_platform_asking_for_a_login_is_named_as_such() -> None:
    verdict = classify(TIKTOK_LOGIN_FIRST)
    assert verdict.rule == "envelope.login_required"
    assert "2483" in (verdict.detail or "")


def test_a_login_wall_is_not_held_against_the_guest_that_met_it() -> None:
    """The guest did nothing wrong; it is the wrong kind of identity."""
    assert classify(TIKTOK_LOGIN_FIRST).outcome is Outcome.BUSINESS_ERROR
