"""What the Identities page asks the listing and the pool level for.

The console fetched the newest 200 identities and hid the retired ones itself.
Retired rows are kept for 90 days, so a pool that retired a bad week's worth
pushed its older logins off the page without a word. The server now leaves
them out, says how many it left out, and pages the rest.

The pool level gained the login coverage the page warns from: a platform whose
paging needs a login answers IDENTITY_LOGIN_REQUIRED the moment its last login
in rotation is reserved, cooled or retired, and nothing on the board said so.

All cookie values are synthetic.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from dtk.core.db import session_scope
from dtk.core.types import Platform
from dtk.db.models import Identity
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DOUYIN_GUEST = "ttwid=1%7Cabcdefghijklmnop; odin_tt=0123456789abcdef"
DOUYIN_LOGIN = (
    "ttwid=1%7Cabcdefghijklmnop; sessionid=SYNTHETICsession0123; "
    "sid_guard=SYNTHETIC%7C1756134824%7C5184000%7Cx"
)
BILIBILI_LOGIN = (
    "buvid3=SYNTHETIC-1234-5678-ABCDinfoc; "
    "SESSDATA=SYNTHETICa1b2%2C1792300000%2Cabcd%2A42; bili_jct=SYNTHETICjct"
)

LIST = "/api/v1/admin/identities"


async def make_identity(
    http: httpx.AsyncClient,
    cookies: str = DOUYIN_GUEST,
    *,
    platform: Platform = Platform.DOUYIN,
    **extra: Any,
) -> str:
    response = await http.post(
        f"{LIST}/import",
        json={"platform": platform.value, "cookies": cookies, "user_agent": CHROME_UA, **extra},
    )
    assert response.status_code == 201, response.text
    return str(envelope(response)["data"]["identity_id"])


async def retire(http: httpx.AsyncClient, identity_id: str) -> None:
    response = await http.request("DELETE", f"{LIST}/{identity_id}", json={"reason": "test"})
    assert response.status_code == 200, response.text


async def test_retired_identities_are_left_out_and_counted(client: Any) -> None:
    await signed_in(client)
    kept = await make_identity(client)
    gone = await make_identity(client)
    await retire(client, gone)

    body = envelope(await client.get(LIST))

    assert [row["id"] for row in body["data"]] == [kept]
    assert body["meta"]["total"] == 1
    assert body["meta"]["retired"] == 1


async def test_retired_identities_come_back_when_asked_for(client: Any) -> None:
    await signed_in(client)
    await make_identity(client)
    gone = await make_identity(client)
    await retire(client, gone)

    included = envelope(await client.get(LIST, params={"include_retired": "true"}))
    by_state = envelope(await client.get(LIST, params={"state": "retired"}))
    by_id = envelope(await client.get(LIST, params={"identity_id": gone}))

    assert included["meta"]["total"] == 2
    assert [row["id"] for row in by_state["data"]] == [gone]
    assert [row["id"] for row in by_id["data"]] == [gone]


async def test_the_total_is_the_whole_match_and_offset_pages_it(client: Any) -> None:
    await signed_in(client)
    made = [await make_identity(client) for _ in range(3)]

    first = envelope(await client.get(LIST, params={"limit": 2}))
    second = envelope(await client.get(LIST, params={"limit": 2, "offset": 2}))

    assert first["meta"]["total"] == 3
    assert len(first["data"]) == 2
    seen = [row["id"] for row in first["data"]] + [row["id"] for row in second["data"]]
    assert sorted(seen) == sorted(made)


async def test_logins_guests_and_reserved_ones_filter_on_the_server(client: Any) -> None:
    await signed_in(client)
    guest = await make_identity(client)
    login = await make_identity(client, DOUYIN_LOGIN)
    reserved = await make_identity(client, DOUYIN_LOGIN, reserved=True)

    logins = envelope(await client.get(LIST, params={"authenticated": "true"}))
    guests = envelope(await client.get(LIST, params={"authenticated": "false"}))
    kept = envelope(await client.get(LIST, params={"rotation": "reserved"}))
    rotating = envelope(await client.get(LIST, params={"rotation": "rotating"}))

    assert sorted(row["id"] for row in logins["data"]) == sorted([login, reserved])
    assert [row["id"] for row in guests["data"]] == [guest]
    assert [row["id"] for row in kept["data"]] == [reserved]
    assert sorted(row["id"] for row in rotating["data"]) == sorted([guest, login])


async def test_an_import_can_be_kept_out_of_rotation_from_the_start(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity(client, DOUYIN_LOGIN, reserved=True)

    async with session_scope() as session:
        row = await session.get(Identity, uuid.UUID(identity_id))
    assert row is not None
    assert row.rotation_endpoints == []


async def test_a_login_says_when_it_expires_and_a_guest_does_not(client: Any) -> None:
    await signed_in(client)
    guest = await make_identity(client)
    login = await make_identity(client, BILIBILI_LOGIN, platform=Platform.BILIBILI)

    response = await client.get(LIST)
    rows = {row["id"]: row for row in envelope(response)["data"]}

    assert rows[guest]["expires_at"] is None
    assert rows[login]["expires_at"] == "2026-10-18T05:06:40+00:00"
    assert rows[login]["session"]["verdict"] == "ok"
    for secret in ("SYNTHETICa1b2", "SYNTHETICjct", "1234-5678-ABCD"):
        assert secret not in response.text


async def test_the_dry_run_reports_roles_and_the_platform_the_names_point_at(
    client: Any,
) -> None:
    await signed_in(client)
    response = await client.post(
        f"{LIST}/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": BILIBILI_LOGIN,
            "user_agent": CHROME_UA,
            "dry_run": True,
        },
    )
    report = envelope(response)["data"]["report"]

    assert report["detected_platform"] == "bilibili"
    assert report["cookie_roles"]["SESSDATA"] == "session"
    assert report["cookie_roles"]["buvid3"] == "other"


async def test_the_pool_level_counts_the_logins_rotation_can_reach(client: Any) -> None:
    await signed_in(client)
    await make_identity(client, DOUYIN_LOGIN)
    await make_identity(client, DOUYIN_LOGIN, reserved=True)

    level = envelope(await client.get(f"{LIST}/pool"))["data"]
    douyin = next(row for row in level["platforms"] if row["platform"] == "douyin")
    bilibili = next(row for row in level["platforms"] if row["platform"] == "bilibili")

    assert douyin["logins"] == {"rotating": 1, "limited": 0, "reserved": 1, "cooling": 0}
    assert "douyin.author_posts" in douyin["login_required_for"]
    assert bilibili["logins"]["rotating"] == 0
    assert "bilibili.comments" in bilibili["login_required_for"]
    assert "backoffs" in level["activity"]
