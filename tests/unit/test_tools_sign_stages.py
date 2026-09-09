"""What `/tools/sign` says about how it reached the signature.

The seven published fields describe the result; `stages` describes the pipeline
that produced it, and the two must not disagree. These check the three things a
reader would otherwise have to take on faith: that the stages account for every
parameter in `query` exactly once, that a layer which did not run says so and
names what was missing, and that the websign preimage handed out is the string
the signature was actually computed over rather than a plausible-looking guess.

The route is called directly. It touches no database and no network - the whole
endpoint is arithmetic over its own arguments - so a request object with a
correlation id on it is the entire fixture.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request

from dtk.api.deps import Principal
from dtk.api.routes import tools
from dtk.core.types import Platform, Scope, UserRole
from dtk.signing.native import websign

pytestmark = pytest.mark.anyio

DOUYIN_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aid=6383&aweme_id=7"
TIKTOK_URL = "https://www.tiktok.com/api/user/detail/?aid=1988&uniqueId=someone"

#: A jar shaped like the one `/tools/identity` mints: the visitor id Douyin's
#: own signature is computed over, and the cookie that IS `verifyFp` and `fp`.
DOUYIN_JAR = "UIFID_TEMP=uifid-from-a-mint; s_v_web_id=verify_fp_from_a_mint"

#: A read key, which is what a caller of this endpoint holds. Scoped rather than
#: a console session, so the scope check in the route is actually exercised.
PRINCIPAL = Principal(
    user_id=uuid.uuid4(),
    role=UserRole.VIEWER,
    scopes=frozenset({Scope.DOUYIN_READ, Scope.TIKTOK_READ}),
    api_key_id=uuid.uuid4(),
    rate_limit_per_min=None,
)

#: The fields this endpoint published before `stages` existed, in order. The
#: console and the documented contract both depend on them.
PUBLISHED_FIELDS = [
    "platform",
    "signed_url",
    "query",
    "params",
    "headers",
    "user_agent",
    "algorithm",
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def call(
    platform: Platform,
    url: str,
    *,
    cookies: str | None = None,
    ms_token: str | None = None,
) -> dict[str, Any]:
    """The endpoint's `data` payload for one request."""
    request = cast(Request, SimpleNamespace(state=SimpleNamespace(request_id=uuid.uuid4())))
    response = await tools.sign(
        request,
        tools.SignRequest(platform=platform, url=url, cookies=cookies, ms_token=ms_token),
        PRINCIPAL,
    )
    return cast(dict[str, Any], json.loads(bytes(response.body))["data"])


def stage(data: dict[str, Any], name: str) -> dict[str, Any]:
    matched = [entry for entry in data["stages"] if entry["name"] == name]
    assert len(matched) == 1, f"expected exactly one {name} stage, got {len(matched)}"
    return cast(dict[str, Any], matched[0])


def query_names(data: dict[str, Any]) -> list[str]:
    return [pair.partition("=")[0] for pair in data["query"].split("&") if pair]


# --------------------------------------------------------------------------
# The published fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "url", "cookies"),
    [
        (Platform.DOUYIN, DOUYIN_URL, DOUYIN_JAR),
        (Platform.DOUYIN, DOUYIN_URL, None),
        (Platform.TIKTOK, TIKTOK_URL, None),
    ],
)
async def test_stages_are_added_beside_the_seven_fields_rather_than_among_them(
    platform: Platform, url: str, cookies: str | None
) -> None:
    """Adding a field must not move or rename one that callers already read."""
    data = await call(platform, url, cookies=cookies)

    assert list(data)[: len(PUBLISHED_FIELDS)] == PUBLISHED_FIELDS
    assert set(data) == {*PUBLISHED_FIELDS, "stages"}
    assert data["signed_url"].endswith(data["query"])


# --------------------------------------------------------------------------
# The stages account for the query
# --------------------------------------------------------------------------


async def test_douyin_with_a_jar_runs_all_four_layers() -> None:
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)

    assert [entry["name"] for entry in data["stages"]] == [
        "business",
        "session",
        "signature",
        "websign",
    ]
    assert all(entry["ran"] for entry in data["stages"])
    assert all(entry["skipped_reason"] is None for entry in data["stages"])


@pytest.mark.parametrize(
    ("platform", "url", "cookies", "ms_token"),
    [
        (Platform.DOUYIN, DOUYIN_URL, DOUYIN_JAR, None),
        (Platform.TIKTOK, TIKTOK_URL, None, "a-real-token"),
    ],
)
async def test_every_parameter_in_the_query_belongs_to_exactly_one_stage(
    platform: Platform, url: str, cookies: str | None, ms_token: str | None
) -> None:
    """The property that makes the diagram readable as the query itself.

    A parameter listed under two stages would be drawn twice, and one listed
    under none would appear on the wire out of nowhere. Both cases here have
    every layer contributing, so the union is the whole query.
    """
    data = await call(platform, url, cookies=cookies, ms_token=ms_token)

    claimed: list[str] = []
    for entry in data["stages"]:
        claimed.extend(entry["params"])

    assert len(claimed) == len(set(claimed))
    assert sorted(claimed) == sorted(query_names(data))


async def test_the_business_stage_is_what_the_caller_put_in_the_url() -> None:
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)

    assert stage(data, "business")["params"] == {"aid": "6383", "aweme_id": "7"}


# --------------------------------------------------------------------------
# The session layer, and the asymmetry between the platforms
# --------------------------------------------------------------------------


async def test_douyin_says_the_session_token_was_invented() -> None:
    """Because it was: nothing in the request carried one."""
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)
    session = stage(data, "session")

    assert session["ran"] is True
    assert session["ms_token_source"] == "generated"
    assert session["params"]["msToken"] == data["params"]["msToken"]


async def test_tiktok_reports_an_absent_token_rather_than_inventing_one() -> None:
    """A fabricated TikTok token is verified and fails; a missing one is accepted.

    So the stage has to be able to say "there was none" - if it reported a value
    here the caller would send it, which is the one thing that cannot work.
    """
    data = await call(Platform.TIKTOK, TIKTOK_URL)
    session = stage(data, "session")

    assert session["ran"] is False
    assert session["skipped_reason"] == "no_ms_token_supplied"
    assert session["ms_token_source"] == "absent"
    assert session["params"] == {}
    assert data["params"]["msToken"] == ""


async def test_tiktok_reports_a_caller_supplied_token_as_the_callers() -> None:
    data = await call(Platform.TIKTOK, TIKTOK_URL, ms_token="a-real-token")
    session = stage(data, "session")

    assert session["ms_token_source"] == "cookies"
    assert session["params"] == {"msToken": "a-real-token"}


async def test_a_token_written_into_the_url_is_reported_as_coming_from_it() -> None:
    data = await call(Platform.TIKTOK, f"{TIKTOK_URL}&msToken=from-the-url")
    session = stage(data, "session")

    assert session["ms_token_source"] == "url"
    assert session["params"] == {"msToken": "from-the-url"}
    # It is a session value wherever it was written, so the business stage does
    # not claim it as well.
    assert "msToken" not in stage(data, "business")["params"]


# --------------------------------------------------------------------------
# The signature layer
# --------------------------------------------------------------------------


async def test_the_tiktok_signature_stage_names_the_parameter_that_actually_seals() -> None:
    """`algorithm` says X-Bogus, and X-Bogus is the constant "1".

    The field is a stable wire value and stays as it is; the stage is what keeps
    a diagram drawn from it from pointing at the wrong box.
    """
    data = await call(Platform.TIKTOK, TIKTOK_URL, ms_token="a-real-token")
    signature = stage(data, "signature")

    assert data["algorithm"] == "X-Bogus"
    assert signature["seal_param"] == "X-Gnarly"
    assert signature["roles"] == {
        "X-Dynosaur": "environment",
        "X-Bogus": "constant",
        "X-Gnarly": "seal",
    }
    assert data["params"]["X-Bogus"] == "1"


async def test_the_douyin_signature_stage_is_a_bogus_alone() -> None:
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)
    signature = stage(data, "signature")

    assert signature["seal_param"] == "a_bogus"
    assert signature["roles"] == {"a_bogus": "seal"}
    assert list(signature["params"]) == ["a_bogus"]


@pytest.mark.parametrize(
    ("platform", "url"),
    [(Platform.DOUYIN, DOUYIN_URL), (Platform.TIKTOK, TIKTOK_URL)],
)
async def test_the_seal_input_is_never_offered(platform: Platform, url: str) -> None:
    """The bytes this layer signed are not always the bytes finally sent.

    The seal is computed over one encoding of the query and the string sent is
    built by another, so a reconstruction would be right for most inputs and
    silently wrong for the rest. The stage says so rather than guessing.
    """
    signature = stage(await call(platform, url), "signature")

    assert signature["input_reconstructible"] is False
    assert "preimage" not in signature


# --------------------------------------------------------------------------
# The websign layer
# --------------------------------------------------------------------------


async def test_a_jar_without_a_visitor_id_leaves_a_labelled_hole() -> None:
    """The reason names `uifid`, because that is the word the platform uses.

    A caller who sends no jar gets no signature headers and is refused by the
    sign-protected endpoints with `Uifid Not Found`. A stage missing from the
    list would leave them to work that connection out themselves.
    """
    data = await call(Platform.DOUYIN, DOUYIN_URL)
    websign_stage = stage(data, "websign")

    assert websign_stage["ran"] is False
    assert websign_stage["skipped_reason"] == "no_uifid_cookie"
    assert "uifid" in websign_stage["skipped_reason"]
    assert websign_stage["params"] == {}
    assert websign_stage["headers"] == {}
    assert data["headers"] == {}


async def test_tiktok_has_no_websign_stage_at_all() -> None:
    """It is not a layer that could have run, so it is not drawn as one."""
    data = await call(Platform.TIKTOK, TIKTOK_URL)

    assert [entry["name"] for entry in data["stages"]] == ["business", "session", "signature"]


async def test_the_websign_stage_carries_the_visitor_parameters_and_the_headers() -> None:
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)
    websign_stage = stage(data, "websign")

    assert list(websign_stage["params"]) == [
        "verifyFp",
        "fp",
        "uifid",
        "timestamp",
        "x-secsdk-web-signature",
    ]
    assert websign_stage["params"]["verifyFp"] == "verify_fp_from_a_mint"
    assert websign_stage["params"]["fp"] == "verify_fp_from_a_mint"
    assert websign_stage["params"]["uifid"] == "uifid-from-a-mint"
    assert websign_stage["headers"] == data["headers"]
    assert set(websign_stage["headers"]) == {
        "uifid",
        "x-secsdk-web-signature",
        "x-secsdk-web-expire",
    }


async def test_the_preimage_reproduces_the_signature_that_was_returned() -> None:
    """The check the route makes before publishing it, made again from outside.

    A preimage is documentation of the algorithm - a reader will derive their own
    signatures from it - so it is worth nothing unless it hashes to the signature
    sent beside it.
    """
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)
    websign_stage = stage(data, "websign")
    preimage = websign_stage["preimage"]

    assert preimage is not None
    assert websign_stage["salt"] == websign.SALT
    assert (
        hashlib.md5(preimage.encode()).hexdigest()
        == websign_stage["params"]["x-secsdk-web-signature"]
    )


async def test_the_preimage_is_built_from_the_query_the_caller_is_told_to_send() -> None:
    """Its four parts are the visitor, the timestamp, the salt and that query.

    Recomputing it from the response alone is what makes the stage checkable by
    the reader rather than only by the route.
    """
    data = await call(Platform.DOUYIN, DOUYIN_URL, cookies=DOUYIN_JAR)
    websign_stage = stage(data, "websign")
    covered, _, signature = data["query"].rpartition("&x-secsdk-web-signature=")

    assert signature == websign_stage["params"]["x-secsdk-web-signature"]
    assert websign_stage["preimage"] == "_".join(
        [
            websign_stage["params"]["uifid"],
            websign_stage["params"]["timestamp"],
            websign.SALT,
            covered,
        ]
    )
