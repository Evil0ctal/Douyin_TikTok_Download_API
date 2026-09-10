"""Reading a session check, per platform.

Both endpoints answer 200 whatever the session is doing, so everything worth
knowing is in the body - and only one of the two platforms actually says it.
The payloads below are real answers captured on 2026-09-09, which is the only
way this file is worth anything: the rule it pins was wrong the first time it
was written, and it was measurement that said so rather than reasoning.
"""

from __future__ import annotations

import pytest

from dtk.core.types import Platform
from dtk.ops.probes import read_session

# TikTok, logged in. From a browser with a live session.
TIKTOK_LIVE = {
    "data": {
        "error_code": 0,
        "error_name": "success",
        "description": "",
        "name": "success",
        "app_id": 1459,
        "user_id_str": "7472895737545360430",
        "session_expired_description": "",
    },
    "message": "success",
}

# TikTok, guest. From an identity this instance minted itself.
TIKTOK_GUEST = {
    "data": {
        "error_code": 401,
        "error_name": "session_expired",
        "description": "session expired, please sign in again",
        "name": "session_expired",
        "app_id": 1459,
        "user_id_str": "0",
        "session_expired_description": "",
    },
    "message": "error",
}

# Douyin, logged in and guest. The shapes are the same; only the values differ,
# and nothing in either says which is which.
DOUYIN_LOGGED_IN = {
    "id": "7515681134195344911",
    "create_time": "1749880894",
    "user_uid": "7673303621286872121",
    "user_uid_type": 0,
    "browser_name": "Firefox",
    "status_code": 0,
}
DOUYIN_GUEST = {
    "id": "7683329867132159503",
    "create_time": "1788914653",
    "user_uid": "4192893283341228",
    "user_uid_type": 0,
    "browser_name": "Chrome",
    "status_code": 0,
}


def test_tiktok_confirms_a_live_session_and_names_the_account() -> None:
    logged_in, account, reason, _detail = read_session(Platform.TIKTOK, TIKTOK_LIVE)
    assert logged_in is True
    assert account == "7472895737545360430"
    assert reason == "live"


def test_tiktok_reports_no_session_without_guessing_why() -> None:
    """A guest jar and a lapsed login produce the same answer.

    The passport service calls both "session_expired", so claiming to tell them
    apart would be inventing a distinction the platform does not make.
    """
    logged_in, account, reason, detail = read_session(Platform.TIKTOK, TIKTOK_GUEST)
    assert logged_in is False
    assert reason == "signed_out"
    # "0" is how the service spells nobody; it must not surface as an account.
    assert account is None
    assert detail == "session_expired"


@pytest.mark.parametrize(
    ("payload", "uid"),
    [
        pytest.param(DOUYIN_LOGGED_IN, "7673303621286872121", id="logged-in"),
        pytest.param(DOUYIN_GUEST, "4192893283341228", id="guest"),
    ],
)
def test_douyin_cannot_tell_a_login_from_a_guest(payload: dict[str, object], uid: str) -> None:
    """The endpoint that was proposed as a Douyin login check is not one.

    It answers a guest with the same fields and a device uid of its own. This
    test holds both real payloads side by side precisely so that a future
    attempt to read a verdict out of one of them fails here first.
    """
    logged_in, account, reason, _detail = read_session(Platform.DOUYIN, payload)
    assert logged_in is False
    assert reason == "indeterminate"
    # The uid is still worth returning: it says the platform recognised the
    # client, which is more than a failed request would.
    assert account == uid


def test_a_platform_that_refuses_is_not_a_platform_that_signed_you_out() -> None:
    assert read_session(Platform.DOUYIN, {"status_code": 8})[2] == "refused"
    assert read_session(Platform.TIKTOK, {"message": "error"})[2] == "refused"
    assert read_session(Platform.TIKTOK, "not json at all")[2] == "refused"
