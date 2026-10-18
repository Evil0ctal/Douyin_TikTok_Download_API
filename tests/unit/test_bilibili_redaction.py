"""Bilibili's credentials are masked by every redactor, not just the first one.

The project keeps three lists of names to mask in free text: the structured
log processor, the CLI's ``scrub`` and the diagnose report. Bilibili arrived
after all three were written, and none of them knew ``SESSDATA``, its session
cookie, until this test. The values below are SYNTHETIC.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.core import logging as dtk_logging
from dtk.ops import diagnose
from dtk.ops.masking import scrub

SECRETS = {
    "SESSDATA": "a1b2c3d4%2C1792300000%2Cabcd*42",
    "bili_jct": "0123456789abcdef0123456789abcdef",
    "buvid3": "SYNTHETIC-1234-5678-ABCDinfoc",
    "buvid4": "SYNTHETIC-B4-VALUE-QWERTY==",
    "w_rid": "fedcba9876543210fedcba9876543210",
}


def _log(text: str) -> str:
    event: dict[str, Any] = {"event": "transport.request.failed", "detail": text}
    return str(dtk_logging.redact(None, "info", event)["detail"])


REDACTORS = {"log": _log, "scrub": scrub, "diagnose": diagnose.redact}


@pytest.mark.parametrize("redactor", REDACTORS.values(), ids=REDACTORS.keys())
@pytest.mark.parametrize("name", SECRETS.keys())
def test_a_cookie_header_loses_the_value(redactor: Any, name: str) -> None:
    value = SECRETS[name]
    text = f"Cookie header was {name}={value}; b_nut=1792300000"
    assert value not in redactor(text)


@pytest.mark.parametrize("redactor", REDACTORS.values(), ids=REDACTORS.keys())
def test_a_signed_query_loses_its_signature(redactor: Any) -> None:
    rid = SECRETS["w_rid"]
    url = f"https://api.bilibili.com/x/web-interface/wbi/view?bvid=BV1xx411c7mD&w_rid={rid}&wts=1"
    redacted = redactor(f"GET {url} answered 412")
    assert rid not in redacted
    # What the request was for stays readable.
    assert "BV1xx411c7mD" in redacted
