"""What `/tools/decode` accepts, and what it refuses to guess.

The arithmetic is checked in ``test_signature_decoding.py``. What is checked
here is the endpoint's own judgement: that it works out what was pasted without
being told, that it says so rather than inventing a reading when it cannot, and
that "you did not send me that input" never comes back looking like "your input
is wrong".

Called directly. The endpoint touches no database and no network, so a request
object with a correlation id on it is the whole fixture.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request

from dtk.api.deps import Principal
from dtk.api.routes import tools
from dtk.core.errors import InvalidParam
from dtk.core.types import Platform, Scope, UserRole
from dtk.signing.native import decoding, tiktok_sign

pytestmark = pytest.mark.anyio

DOUYIN_URL = (
    "https://www.douyin.com/aweme/v1/web/aweme/detail/?aid=6383&aweme_id=7300000000000000000"
)
TIKTOK_URL = "https://www.tiktok.com/api/user/detail/?aid=1988&uniqueId=someone"
DOUYIN_JAR = "UIFID_TEMP=uifid-from-a-mint; s_v_web_id=verify_fp_from_a_mint"

PRINCIPAL = Principal(
    user_id=uuid.uuid4(),
    role=UserRole.VIEWER,
    scopes=frozenset({Scope.DOUYIN_READ, Scope.TIKTOK_READ}),
    api_key_id=uuid.uuid4(),
    rate_limit_per_min=None,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request() -> Request:
    return cast(Request, SimpleNamespace(state=SimpleNamespace(request_id=uuid.uuid4())))


async def sign(platform: Platform, url: str, cookies: str | None = None) -> dict[str, Any]:
    response = await tools.sign(
        _request(), tools.SignRequest(platform=platform, url=url, cookies=cookies), PRINCIPAL
    )
    return cast(dict[str, Any], json.loads(bytes(response.body))["data"])


async def decode(value: str, **extra: Any) -> dict[str, Any]:
    response = await tools.decode(_request(), tools.DecodeRequest(value=value, **extra), PRINCIPAL)
    return cast(dict[str, Any], json.loads(bytes(response.body))["data"])


def named(data: dict[str, Any], parameter: str) -> dict[str, Any]:
    matched = [item for item in data["parameters"] if item["parameter"] == parameter]
    assert len(matched) == 1, f"expected one {parameter}, got {len(matched)}"
    return cast(dict[str, Any], matched[0])


def statuses(entry: dict[str, Any]) -> dict[str, str]:
    return {check["name"]: check["status"] for check in entry["checks"]}


# --------------------------------------------------------------------------
# It works out what it was handed
# --------------------------------------------------------------------------


async def test_a_signed_douyin_url_round_trips_through_both_endpoints() -> None:
    """The pair that matters: sign, paste the result back, get every check green."""
    signed = await sign(Platform.DOUYIN, DOUYIN_URL, DOUYIN_JAR)
    data = await decode(signed["signed_url"], user_agent=signed["user_agent"])

    assert data["source"] == tools.SOURCE_URL
    assert data["platform"] == "douyin"
    a_bogus = named(data, "a_bogus")
    assert a_bogus["recovered"]
    assert statuses(a_bogus)["query"] == decoding.CHECK_MATCH
    assert statuses(a_bogus)["user_agent"] == decoding.CHECK_MATCH
    assert statuses(named(data, "x-secsdk-web-signature"))["query"] == decoding.CHECK_MATCH


async def test_a_signed_tiktok_url_decodes_both_halves_of_the_envelope() -> None:
    signed = await sign(Platform.TIKTOK, TIKTOK_URL)
    data = await decode(signed["signed_url"], user_agent=signed["user_agent"])

    assert data["platform"] == "tiktok"
    for name in (tiktok_sign.DYNOSAUR_PARAM, tiktok_sign.GNARLY_PARAM):
        entry = named(data, name)
        assert entry["recovered"], name
        assert statuses(entry)["query"] == decoding.CHECK_MATCH
        assert statuses(entry)["user_agent"] == decoding.CHECK_MATCH


async def test_the_x_bogus_tiktok_sends_is_reported_as_a_constant() -> None:
    """A parameter that seals nothing is not a decode failure."""
    signed = await sign(Platform.TIKTOK, TIKTOK_URL)
    entry = named(await decode(signed["signed_url"]), tiktok_sign.BOGUS_PARAM)
    assert entry["recovered"]
    assert entry["reason"] is None
    assert "constant_placeholder" in entry["notes"]


async def test_a_bare_value_is_identified_without_being_named() -> None:
    signed = await sign(Platform.DOUYIN, DOUYIN_URL)
    data = await decode(signed["params"]["a_bogus"])
    assert data["source"] == tools.SOURCE_PARAMETER
    assert named(data, "a_bogus")["recovered"]


async def test_a_name_equals_value_pair_is_accepted_as_pasted() -> None:
    signed = await sign(Platform.DOUYIN, DOUYIN_URL)
    data = await decode(f"a_bogus={signed['params']['a_bogus']}")
    assert data["source"] == tools.SOURCE_PARAMETER
    assert named(data, "a_bogus")["recovered"]


async def test_naming_the_parameter_overrides_the_guess() -> None:
    data = await decode("0" * 32, parameter="x-secsdk-web-signature")
    entry = named(data, "x-secsdk-web-signature")
    assert entry["reason"] == decoding.REASON_ONE_WAY


# --------------------------------------------------------------------------
# It does not guess
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["hello", "not a signature at all", "1234"])
async def test_an_unrecognised_value_is_refused_rather_than_read(value: str) -> None:
    with pytest.raises(InvalidParam) as raised:
        await decode(value)
    assert raised.value.details["field"] == "value"
    assert "a_bogus" in raised.value.details["known"]


async def test_an_unsigned_url_is_refused_rather_than_returned_empty() -> None:
    """An empty parameter list would read as "nothing wrong here"."""
    with pytest.raises(InvalidParam):
        await decode("https://www.douyin.com/video/7300000000000000000")


async def test_an_input_that_was_not_supplied_is_not_reported_as_wrong() -> None:
    """The distinction the panel rests on: silence is not a mismatch."""
    signed = await sign(Platform.DOUYIN, DOUYIN_URL)
    entry = named(await decode(signed["params"]["a_bogus"]), "a_bogus")
    assert set(statuses(entry).values()) == {decoding.CHECK_NOT_SUPPLIED}
    assert all(check["covered"] is None for check in entry["checks"])


async def test_a_wrong_user_agent_is_named_as_wrong() -> None:
    signed = await sign(Platform.DOUYIN, DOUYIN_URL)
    entry = named(
        await decode(signed["signed_url"], user_agent="Mozilla/5.0 (something else)"), "a_bogus"
    )
    assert statuses(entry)["user_agent"] == decoding.CHECK_DIFFERS
    assert statuses(entry)["query"] == decoding.CHECK_MATCH


async def test_a_covered_string_is_only_ever_published_when_it_verifies() -> None:
    """Same discipline as the websign preimage: a wrong one would be derived from."""
    signed = await sign(Platform.DOUYIN, DOUYIN_URL)
    entry = named(await decode(signed["signed_url"], user_agent="wrong"), "a_bogus")
    for check in entry["checks"]:
        assert (check["covered"] is None) == (check["status"] != decoding.CHECK_MATCH)


# --------------------------------------------------------------------------
# The shape callers read
# --------------------------------------------------------------------------


async def test_every_field_declares_which_kind_of_value_it_is() -> None:
    """A number with no kind beside it reads as recovered whether it is or not."""
    signed = await sign(Platform.DOUYIN, DOUYIN_URL, DOUYIN_JAR)
    kinds = {
        decoding.KIND_PLAIN,
        decoding.KIND_TIME,
        decoding.KIND_DIGEST,
        decoding.KIND_CHECKSUM,
        decoding.KIND_ENVIRONMENT,
        decoding.KIND_OPAQUE,
    }
    data = await decode(signed["signed_url"], user_agent=signed["user_agent"])
    seen = [field for entry in data["parameters"] for field in entry["fields"]]
    assert seen
    assert {field["kind"] for field in seen} <= kinds


async def test_the_response_keeps_its_published_shape() -> None:
    signed = await sign(Platform.DOUYIN, DOUYIN_URL)
    data = await decode(signed["signed_url"])
    assert set(data) == {"source", "platform", "user_agent", "parameters"}
    for entry in data["parameters"]:
        assert set(entry) == {
            "parameter",
            "platform",
            "algorithm",
            "recovered",
            "reason",
            "fields",
            "checks",
            "notes",
        }
