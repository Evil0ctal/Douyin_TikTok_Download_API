"""Administrative surface: identities, proxies, keys, settings, users, audit.

Two invariants get the most attention here because doc 08 states them as
absolutes: no response contains a cookie, and no response contains a proxy
password.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select

from dtk.core.db import session_scope
from dtk.core.types import IdentityState, Platform, Scope, UserRole
from dtk.db.models import ApiKey, AuditLog, Identity, IdentityEvent, Proxy, Setting
from dtk.ops import backup
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    anonymous_client,
    envelope,
    error_code,
    login,
    make_api_key,
    make_user,
    signed_in,
)

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
COOKIE_BLOB = "ttwid=1%7Cabcdefghijklmnop; odin_tt=0123456789abcdef"
#: A logged-in jar. The session value is SYNTHETIC, generated for this test.
SESSION_COOKIE_BLOB = f"{COOKIE_BLOB}; sessionid=deadbeefcafebabe0123"  # SYNTHETIC
PROXY_URL = "http://proxyuser:sup3rsecret@10.20.30.40:8080"
#: Well-formed post ids. Shape is checked at the boundary now, so a stand-in
#: like "7123" is refused before the thing under test is reached.
VIDEO_ID = "7123456789012345678"
OTHER_VIDEO_ID = "7123456789012345679"


async def audit_actions() -> list[str]:
    async with session_scope() as session:
        rows = (await session.scalars(select(AuditLog).order_by(AuditLog.ts))).all()
        return [row.action for row in rows]


# --------------------------------------------------------------------------
# Identities
# --------------------------------------------------------------------------


async def test_pool_level_reports_the_marks_the_refill_job_uses(client: Any) -> None:
    """The numbers the identities board shows for automatic minting.

    Both platforms are always listed, including one with nothing in it: an
    empty pool is the case an operator most needs to see, and a platform that
    only appears once it has identities is a platform whose refill looks
    switched off.
    """
    await signed_in(client)
    await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )

    data = envelope(await client.get("/api/v1/admin/identities/pool"))["data"]

    assert data["min_size"] >= 1
    assert data["target_size"] >= data["min_size"]
    by_platform = {row["platform"]: row for row in data["platforms"]}
    assert set(by_platform) == {platform.value for platform in Platform}
    assert by_platform[Platform.DOUYIN.value]["usable"] == 1
    assert by_platform[Platform.TIKTOK.value]["usable"] == 0
    assert by_platform[Platform.TIKTOK.value]["below_minimum"] is True


async def test_pool_level_counts_usable_not_rows(client: Any) -> None:
    """A dead identity is still a row. The mark is about what can serve."""
    await signed_in(client)
    await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )
    before = envelope(await client.get("/api/v1/admin/identities/pool"))["data"]
    streak = before["max_fail_streak"]

    async with session_scope() as session:
        identity = (await session.scalars(select(Identity))).one()
        identity.consecutive_fails = streak
        await session.commit()

    after = envelope(await client.get("/api/v1/admin/identities/pool"))["data"]
    row = next(r for r in after["platforms"] if r["platform"] == Platform.DOUYIN.value)
    assert row["live"] == 1  # the row is still there and still active
    assert row["usable"] == 0  # and it is not what the mark counts


async def test_pool_level_needs_admin(client: Any) -> None:
    """It names the marks and the shortfall, so it is not a public number."""
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    response = await client.get(
        "/api/v1/admin/identities/pool", headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code in (401, 403)


async def test_identity_import_dry_run_masks_and_stores_nothing(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": COOKIE_BLOB,
            "user_agent": CHROME_UA,
            "dry_run": True,
        },
    )
    body = envelope(response)
    assert body["data"]["stored"] is False
    report = body["data"]["report"]
    assert report["detected_format"] == "header"
    assert report["usable"] is True
    assert report["cookie_names"] == ["odin_tt", "ttwid"]
    # The values are masked and the raw cookie never appears anywhere.
    assert "abcdefghijklmnop" not in response.text
    assert all("*" in value for value in report["cookies_masked"].values())

    async with session_scope() as session:
        assert (await session.scalars(select(Identity))).all() == []


async def test_identity_import_stores_and_never_returns_the_cookie(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )
    assert response.status_code == 201
    data = envelope(response)["data"]
    assert data["stored"] is True
    assert data["report"]["authenticated"] is True
    assert "deadbeefcafebabe0123" not in response.text

    listed = await client.get("/api/v1/admin/identities")
    rows = envelope(listed)["data"]
    assert len(rows) == 1
    assert rows[0]["platform"] == Platform.DOUYIN.value
    assert rows[0]["authenticated"] is True
    # Every value from the imported jar, by value rather than by the word
    # "cookie": the listing legitimately names the session cookie it is looking
    # for ("uifid_temp") in its verdict, and a substring check on the word
    # cannot tell that apart from a leaked credential. What must never appear is
    # any part of the jar itself.
    body = listed.text.lower()
    for secret in ("deadbeefcafebabe0123", "abcdefghijklmnop", "0123456789abcdef"):
        assert secret not in body
    assert "cookies_encrypted" not in body
    assert "deadbeefcafebabe0123" not in listed.text

    assert "identity.imported" in await audit_actions()


async def test_identity_import_refuses_an_unusable_jar(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/identities/import",
        json={"platform": Platform.DOUYIN.value, "cookies": "ttwid=x"},
    )
    assert error_code(response) == "INVALID_PARAM"


async def test_retiring_an_identity_wipes_its_credential(client: Any) -> None:
    await signed_in(client)
    created = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )
    identity_id = envelope(created)["data"]["identity_id"]

    response = await client.request(
        "DELETE",
        f"/api/v1/admin/identities/{identity_id}",
        json={"reason": "rotated"},
    )
    assert envelope(response)["data"]["state"] == IdentityState.RETIRED.value

    async with session_scope() as session:
        row = await session.get(Identity, uuid.UUID(identity_id))
    assert row is not None
    assert row.state == IdentityState.RETIRED.value
    assert row.cookies_encrypted == b""
    assert "identity.retired" in await audit_actions()


async def test_minting_is_queued_rather_than_performed(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/identities/mint",
        json={"platform": Platform.TIKTOK.value, "count": 2},
    )
    assert response.status_code == 202
    assert len(envelope(response)["data"]["task_ids"]) == 2


# --------------------------------------------------------------------------
# Proxies
# --------------------------------------------------------------------------


async def test_proxy_create_masks_the_password_everywhere(client: Any) -> None:
    await signed_in(client)
    response = await client.post("/api/v1/admin/proxies", json={"url": PROXY_URL, "label": "home"})
    assert response.status_code == 201
    data = envelope(response)["data"]
    assert data["url_masked"] == "http://proxyuser:***@10.20.30.40:8080"
    assert "sup3rsecret" not in response.text

    listed = await client.get("/api/v1/admin/proxies")
    assert "sup3rsecret" not in listed.text
    assert envelope(listed)["data"][0]["decryptable"] is True

    async with session_scope() as session:
        stored = (await session.scalars(select(Proxy))).one()
    assert b"sup3rsecret" not in stored.url_encrypted


async def test_proxy_bulk_import_accepts_every_documented_shape(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/proxies/import",
        json={
            "text": "\n".join(
                [
                    "10.0.0.1:8000",
                    "10.0.0.2:8000:user:pass",
                    "user:pass@10.0.0.3:8000",
                    "http://user:pass@10.0.0.4:8000",
                    "socks5://user:pass@10.0.0.5:1080",
                    "# a comment",
                    "not a proxy at all",
                ]
            )
        },
    )
    assert response.status_code == 201
    data = envelope(response)["data"]
    assert data["counts"] == {"created": 5, "rejected": 1}
    assert "pass" not in str(data["created"])
    assert {row["url_masked"] for row in data["created"]} == {
        "http://10.0.0.1:8000",
        "http://user:***@10.0.0.2:8000",
        "http://user:***@10.0.0.3:8000",
        "http://user:***@10.0.0.4:8000",
        "socks5://user:***@10.0.0.5:1080",
    }


async def test_deleting_a_proxy_retires_the_identities_behind_it(client: Any) -> None:
    await signed_in(client)
    proxy = envelope(await client.post("/api/v1/admin/proxies", json={"url": PROXY_URL}))["data"]
    identity = envelope(
        await client.post(
            "/api/v1/admin/identities/import",
            json={
                "platform": Platform.DOUYIN.value,
                "cookies": COOKIE_BLOB,
                "user_agent": CHROME_UA,
                "proxy_id": proxy["id"],
            },
        )
    )["data"]

    response = await client.delete(f"/api/v1/admin/proxies/{proxy['id']}")
    assert envelope(response)["data"]["retired_identities"] == 1

    async with session_scope() as session:
        row = await session.get(Identity, uuid.UUID(identity["identity_id"]))
        assert row is not None
        assert row.state == IdentityState.RETIRED.value
        assert await session.get(Proxy, uuid.UUID(proxy["id"])) is None


async def test_updating_a_proxy_re_encrypts_under_its_own_id(client: Any) -> None:
    await signed_in(client)
    proxy = envelope(await client.post("/api/v1/admin/proxies", json={"url": PROXY_URL}))["data"]

    response = await client.put(
        f"/api/v1/admin/proxies/{proxy['id']}",
        json={"url": "socks5://other:secret@10.0.0.9:1080", "label": "renamed"},
    )
    data = envelope(response)["data"]
    assert data["url_masked"] == "socks5://other:***@10.0.0.9:1080"
    assert data["label"] == "renamed"
    assert data["decryptable"] is True
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "line",
    [
        # Credentials before an "@" ...
        "user:sup3rsecret@nowhere",
        # ... and after the second colon, which is the shape a provider list
        # uses and the one a stray colon in the password breaks.
        "10.0.0.1:8080:user:sup3r:secret",
        "socks5://10.0.0.1:notaport:user:sup3rsecret",
    ],
)
async def test_an_unparsable_proxy_is_rejected_without_echoing_credentials(
    client: Any,
    line: str,
) -> None:
    await signed_in(client)
    response = await client.post("/api/v1/admin/proxies", json={"url": line})
    assert error_code(response) == "INVALID_PARAM"
    assert "sup3r" not in response.text


async def test_a_rejected_import_line_never_echoes_its_password(client: Any) -> None:
    """A bad line has to be correctable from the console without leaking it."""
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/proxies/import",
        json={"text": "10.0.0.1:8080:user:sup3r:secret\n10.0.0.2:8000"},
    )
    data = envelope(response)["data"]
    assert data["counts"] == {"created": 1, "rejected": 1}
    assert data["rejected"][0]["line"] == "10.0.0.1:8080"
    assert "sup3r" not in response.text


async def test_import_rejection_codes_are_the_set_the_console_translates(client: Any) -> None:
    """The reason vocabulary is a contract with the console, so it is pinned here.

    Each code has a ``proxy.line.*`` entry in the console catalogue. A code
    added to the parser without one reaches an operator as raw snake_case,
    which is the whole reason these are asserted rather than left implicit.
    """
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/proxies/import",
        json={
            "text": "\n".join(
                [
                    "ftp://10.0.0.1:8080",
                    "http://:8080",
                    "10.0.0.2",
                    "http://10.0.0.3:0",
                    "http://10.0.0.4:99999",
                ]
            )
        },
    )
    assert response.status_code == 200
    data = envelope(response)["data"]
    assert [entry["error"] for entry in data["rejected"]] == [
        "scheme_not_supported",
        "missing_host",
        "missing_port",
        "port_out_of_range",
        "malformed_authority",
    ]


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------


async def test_a_new_key_is_shown_once_and_then_never_again(client: Any, api_app: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/api-keys",
        json={"name": "scripts", "scopes": [Scope.DOUYIN_READ.value], "rate_limit": 30},
    )
    assert response.status_code == 201
    created = envelope(response)["data"]
    full_key = created["key"]
    assert full_key.startswith("dtk_")
    assert created["warning"]

    listed = await client.get("/api/v1/admin/api-keys")
    rows = envelope(listed)["data"]
    assert len(rows) == 1
    assert rows[0]["prefix"] == created["prefix"]
    assert full_key not in listed.text
    assert "key_hash" not in listed.text

    # Checked on a client with no session cookie, so the key is the only
    # credential in play.
    async with anonymous_client(api_app) as caller:
        headers = {"Authorization": f"Bearer {full_key}"}
        allowed = await caller.get(
            "/api/v1/douyin/video", params={"aweme_id": VIDEO_ID}, headers=headers
        )
        assert allowed.status_code == 202

        assert (await client.delete(f"/api/v1/admin/api-keys/{created['id']}")).status_code == 200

        denied = await caller.get(
            "/api/v1/douyin/video", params={"aweme_id": OTHER_VIDEO_ID}, headers=headers
        )
    assert denied.status_code == 401
    assert {"api_key.created", "api_key.revoked"} <= set(await audit_actions())


async def test_key_creation_rejects_a_past_expiry(client: Any) -> None:
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/api-keys",
        json={"name": "stale", "expires_at": "2000-01-01T00:00:00Z"},
    )
    assert error_code(response) == "INVALID_PARAM"


async def test_an_expired_key_does_not_authenticate(client: Any) -> None:
    """No session cookie here, so the key really is the only credential."""
    user_id = await make_user()
    key = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    async with session_scope() as session:
        row = (await session.scalars(select(ApiKey))).one()
        row.expires_at = row.created_at
    response = await client.get(
        "/api/v1/douyin/video",
        params={"aweme_id": "7123"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


async def test_settings_report_where_each_value_comes_from(client: Any) -> None:
    await signed_in(client)
    body = envelope(await client.get("/api/v1/admin/settings"))["data"]
    by_key = {item["key"]: item for item in body["settings"]}

    assert by_key["cache.content_ttl"]["source"] == "default"
    assert by_key["cache.content_ttl"]["env_var"] == "DTK_CACHE_CONTENT_TTL"
    assert by_key["security.url_allowlist"]["sensitive"] is True
    assert by_key["cache.content_ttl"]["sensitive"] is False


async def test_a_constrained_setting_ships_its_options_and_refuses_the_rest(
    client: Any,
) -> None:
    """The signing mode is a choice, so the console gets the list to choose from.

    Without this the page renders a free-text box and an operator has to know
    that the three accepted spellings are rpc, native and auto. The rejection
    below is the other half: the server names the valid values rather than
    storing a typo that would fall back to a default at read time.
    """
    await signed_in(client)
    body = envelope(await client.get("/api/v1/admin/settings"))["data"]
    by_key = {item["key"]: item for item in body["settings"]}

    assert by_key["signing.mode"]["choices"] == ["rpc", "native", "auto"]
    # native is the shipped default now that the in-process signers match what
    # both platforms actually send; rpc is the fallback, not the norm.
    assert by_key["signing.mode"]["value"] == "native"
    # An ordinary setting has none, so the console keeps rendering a text box.
    assert by_key["cache.content_ttl"]["choices"] is None

    accepted = await client.put("/api/v1/admin/settings/signing.mode", json={"value": "rpc"})
    assert envelope(accepted)["data"]["value"] == "rpc"

    rejected = await client.put("/api/v1/admin/settings/signing.mode", json={"value": "browser"})
    assert error_code(rejected) == "INVALID_PARAM"
    # The message is translated, so the valid values travel in the details or
    # the operator is left guessing at a spelling they can see in a picker.
    assert envelope(rejected)["error"]["details"]["choices"] == ["rpc", "native", "auto"]


async def test_updating_a_runtime_setting_takes_effect_immediately(client: Any) -> None:
    await signed_in(client)
    response = await client.put("/api/v1/admin/settings/api.max_wait_seconds", json={"value": 5})
    assert envelope(response)["data"]["value"] == 5

    listed = envelope(await client.get("/api/v1/admin/settings"))["data"]
    by_key = {item["key"]: item for item in listed["settings"]}
    assert by_key["api.max_wait_seconds"]["value"] == 5
    assert by_key["api.max_wait_seconds"]["source"] == "database"

    # The new ceiling is enforced on the very next request.
    rejected = await client.post(
        "/api/v1/parse",
        params={"wait": 10},
        json={"url": "https://www.douyin.com/video/7123456789012345678"},
    )
    assert error_code(rejected) == "INVALID_PARAM"


async def test_a_sensitive_setting_needs_confirmation_and_writes_an_audit_row(
    client: Any,
) -> None:
    await signed_in(client)
    unconfirmed = await client.put(
        "/api/v1/admin/settings/security.enable_task_webhook", json={"value": True}
    )
    assert error_code(unconfirmed) == "INVALID_PARAM"
    assert envelope(unconfirmed)["error"]["details"]["sensitive"] is True

    confirmed = await client.put(
        "/api/v1/admin/settings/security.enable_task_webhook",
        json={"value": True, "confirm": True},
    )
    assert envelope(confirmed)["data"]["value"] is True
    assert "settings.updated_sensitive" in await audit_actions()


async def test_an_operator_cannot_change_a_sensitive_setting(client: Any) -> None:
    await signed_in(client, username="op", role=UserRole.OPERATOR)
    response = await client.put(
        "/api/v1/admin/settings/security.enable_task_webhook",
        json={"value": True, "confirm": True},
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN_SCOPE"

    # A routine setting is still theirs to tune.
    allowed = await client.put("/api/v1/admin/settings/cache.list_ttl", json={"value": 60})
    assert allowed.status_code == 200


async def test_an_identity_manage_key_cannot_change_a_sensitive_setting(
    client: Any, api_app: Any
) -> None:
    """Role alone is not enough: the key's own scope has to allow it.

    The key belongs to the administrator, as nearly every key on a self-hosted
    box does, and reaches this endpoint through ``identity:manage``. Widening
    the URL allowlist is the one thing standing between the service and being
    an open proxy, so it takes the ``admin`` scope as well.
    """
    user_id = await make_user(role=UserRole.ADMIN)
    key = await make_api_key(user_id, scopes=(Scope.IDENTITY_MANAGE,))
    async with anonymous_client(api_app) as caller:
        headers = {"Authorization": f"Bearer {key}"}
        denied = await caller.put(
            "/api/v1/admin/settings/security.enable_task_webhook",
            json={"value": True, "confirm": True},
            headers=headers,
        )
        assert denied.status_code == 403
        assert error_code(denied) == "FORBIDDEN_SCOPE"
        assert envelope(denied)["error"]["details"]["required"] == [Scope.ADMIN.value]

        # A routine setting is still within an identity:manage key's reach.
        allowed = await caller.put(
            "/api/v1/admin/settings/cache.list_ttl", json={"value": 60}, headers=headers
        )
        assert allowed.status_code == 200

    # And the admin scope does open it, so the check is on the scope and not on
    # something incidental about the key.
    admin_key = await make_api_key(user_id, scopes=(Scope.ADMIN,), name="admin key")
    async with anonymous_client(api_app) as caller:
        confirmed = await caller.put(
            "/api/v1/admin/settings/security.enable_task_webhook",
            json={"value": True, "confirm": True},
            headers={"Authorization": f"Bearer {admin_key}"},
        )
    assert confirmed.status_code == 200


async def test_a_viewer_can_read_but_not_write(client: Any) -> None:
    await signed_in(client, username="watcher", role=UserRole.VIEWER)
    assert (await client.get("/api/v1/admin/settings")).status_code == 200

    denied = await client.put("/api/v1/admin/settings/cache.list_ttl", json={"value": 60})
    assert denied.status_code == 403

    denied_proxy = await client.post("/api/v1/admin/proxies", json={"url": PROXY_URL})
    assert denied_proxy.status_code == 403
    assert error_code(denied_proxy) == "FORBIDDEN_SCOPE"


async def test_resetting_a_setting_falls_back_to_the_default(client: Any) -> None:
    await signed_in(client)
    await client.put("/api/v1/admin/settings/cache.list_ttl", json={"value": 60})
    response = await client.delete("/api/v1/admin/settings/cache.list_ttl")
    data = envelope(response)["data"]
    assert data["value"] == 300
    assert data["source"] == "default"


async def test_an_unknown_setting_is_a_404(client: Any) -> None:
    await signed_in(client)
    response = await client.put("/api/v1/admin/settings/not.a.setting", json={"value": 1})
    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


async def test_an_invalid_value_is_refused_before_it_is_stored(client: Any) -> None:
    await signed_in(client)
    response = await client.put(
        "/api/v1/admin/settings/api.max_wait_seconds", json={"value": "not a number"}
    )
    assert error_code(response) == "INVALID_PARAM"


# --------------------------------------------------------------------------
# Settings that hold credentials
#
# notify.channels is an ordinary RUNTIME setting whose descriptors carry a bot
# token, a signing secret and an SMTP password. This endpoint is reachable by a
# viewer session and by any identity:manage key, so the value is masked wherever
# it is read - and a masked value coming back in has to mean "keep the stored
# one" without becoming a way to move a credential somewhere it can be read.
# --------------------------------------------------------------------------

#: SYNTHETIC credentials, generated for these tests.
TELEGRAM_TOKEN = "7654321:AAFsyntheticBotTokenForTests00000000"
DINGTALK_SECRET = "SECsynthetic0123456789abcdefghij"
SMTP_PASSWORD = "synthetic-smtp-password"
DINGTALK_URL = "https://oapi.dingtalk.com/robot/send?access_token=syntheticaccesstoken"

CHANNEL_SECRETS = (TELEGRAM_TOKEN, DINGTALK_SECRET, SMTP_PASSWORD, "syntheticaccesstoken")


def channels() -> list[dict[str, Any]]:
    """One channel of each shape that carries a credential."""
    return [
        {
            "type": "telegram",
            "name": "tg",
            "enabled": True,
            "token": TELEGRAM_TOKEN,
            "chat_id": "-1001234567890",
        },
        {
            "type": "dingtalk",
            "name": "ding",
            "enabled": True,
            "url": DINGTALK_URL,
            "secret": DINGTALK_SECRET,
        },
        {
            "type": "smtp",
            "name": "mail",
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "sender": "dtk@example.com",
            "recipients": ["ops@example.com"],
            "username": "dtk",
            "password": SMTP_PASSWORD,
        },
    ]


async def stored_channels() -> list[dict[str, Any]]:
    """What the settings table actually holds, masking aside."""
    async with session_scope() as session:
        row = await session.get(Setting, "notify.channels")
        return list(row.value) if row is not None else []


async def put_channels(client: Any, value: list[dict[str, Any]]) -> Any:
    return await client.put("/api/v1/admin/settings/notify.channels", json={"value": value})


async def read_channels(client: Any) -> list[dict[str, Any]]:
    """The masked channel list, as the console reads it."""
    body = envelope(await client.get("/api/v1/admin/settings"))["data"]
    row = next(item for item in body["settings"] if item["key"] == "notify.channels")
    assert row["masked"] is True
    return list(row["value"])


async def test_channel_credentials_are_masked_wherever_a_setting_is_read(client: Any) -> None:
    await signed_in(client)
    written = await put_channels(client, channels())
    assert written.status_code == 200

    # The write's own answer, the listing, and the audit row the write left
    # behind. The audit table is never trimmed by retention, so a token written
    # there outlives the channel it belongs to.
    listing = await client.get("/api/v1/admin/settings")
    trail = await client.get("/api/v1/admin/audit")
    for surface, response in (("put", written), ("list", listing), ("audit", trail)):
        for secret in CHANNEL_SECRETS:
            assert secret not in response.text, surface

    detail = envelope(trail)["data"][0]["detail"]
    assert detail["to"][0]["token"].endswith("***")

    # A viewer reaches the same endpoint, and reads the same masked values.
    await signed_in(client, username="watcher", role=UserRole.VIEWER)
    assert TELEGRAM_TOKEN not in (await client.get("/api/v1/admin/settings")).text

    # Masking is a rendering, not a deletion: the channel still works.
    assert (await stored_channels())[0]["token"] == TELEGRAM_TOKEN


async def test_an_audit_row_that_predates_the_masking_is_masked_on_the_way_out(
    client: Any,
) -> None:
    """Nothing trims audit_log, so the rows written in clear are still there."""
    await signed_in(client)
    async with session_scope() as session:
        session.add(
            AuditLog(
                action="settings.updated",
                target_type="setting",
                target_id="notify.channels",
                detail={"from": [], "to": channels()},
            )
        )

    response = await client.get("/api/v1/admin/audit")
    assert TELEGRAM_TOKEN not in response.text
    assert envelope(response)["data"][0]["detail"]["to"][0]["token"].endswith("***")


async def test_a_masked_round_trip_keeps_the_stored_credentials(client: Any) -> None:
    """The console resubmits the whole list, masks and all, to change one field.

    Without the merge this is the write that replaces every credential in the
    instance with the rendering of itself.
    """
    await signed_in(client)
    await put_channels(client, channels())

    edited = await read_channels(client)
    edited[0]["enabled"] = False
    assert (await put_channels(client, edited)).status_code == 200

    stored = await stored_channels()
    assert [row.get("token") or row.get("secret") or row.get("password") for row in stored] == [
        TELEGRAM_TOKEN,
        DINGTALK_SECRET,
        SMTP_PASSWORD,
    ]
    assert stored[1]["url"] == DINGTALK_URL
    assert stored[0]["enabled"] is False


async def test_a_changed_credential_replaces_the_stored_one(client: Any) -> None:
    await signed_in(client)
    await put_channels(client, channels())

    edited = await read_channels(client)
    edited[0]["token"] = "1234567:AAFreplacementBotToken0000000000000"
    edited[2]["password"] = "a-new-password"
    assert (await put_channels(client, edited)).status_code == 200

    stored = await stored_channels()
    assert stored[0]["token"] == "1234567:AAFreplacementBotToken0000000000000"
    assert stored[2]["password"] == "a-new-password"
    # The one field that was left masked is still the original.
    assert stored[1]["secret"] == DINGTALK_SECRET


async def test_a_mask_cannot_carry_a_credential_to_a_new_target(client: Any) -> None:
    """The merge is not a way to read a credential back.

    An operator may edit notify.channels and must never learn the SMTP
    password. Pointing the channel at a host they control while leaving the
    password masked would have the server authenticate to that host with it.
    """
    await signed_in(client, username="op", role=UserRole.OPERATOR)
    await put_channels(client, channels())

    edited = await read_channels(client)
    edited[2]["host"] = "smtp.attacker.example"
    refused = await put_channels(client, edited)

    assert error_code(refused) == "INVALID_PARAM"
    assert envelope(refused)["error"]["details"] == {
        "field": "value",
        "record": "mail",
        "credential": "password",
    }
    assert (await stored_channels())[2]["host"] == "smtp.example.com"


async def test_a_mask_that_matches_nothing_is_refused(client: Any) -> None:
    """Nothing may be stored as ``***``, and no mask may be invented.

    A caller who reads the listing knows every mask in it. If a mask were
    resolved against whichever record it happened to land on, that knowledge
    would be enough to pull a stored token into a channel of their own.
    """
    await signed_in(client)
    await put_channels(client, channels())
    masked = (await read_channels(client))[0]["token"]

    invented = [*channels()[:1], {"type": "telegram", "name": "mine", "token": masked}]
    refused = await put_channels(client, invented)

    assert error_code(refused) == "INVALID_PARAM"
    assert envelope(refused)["error"]["details"]["record"] == "mine"
    assert len(await stored_channels()) == 3


async def test_channel_credentials_leave_a_backup_as_ciphertext(
    client: Any, tmp_path: Path
) -> None:
    """The create-backup response promises ciphertext; settings is JSONB.

    Encrypting on the way out rather than dropping the promise, because restore
    already refuses an archive taken under another DTK_SECRET_KEY - so this adds
    no failure an operator cannot see coming.
    """
    await signed_in(client)
    await put_channels(client, channels())

    async with session_scope() as session:
        info = await backup.create_backup(
            session, output=tmp_path, secret_key=support.TEST_SECRET_KEY
        )
    raw = backup.read_member(info.path, "settings")
    assert raw is not None
    for secret in CHANNEL_SECRETS:
        assert secret.encode() not in raw
    assert backup.SECRET_MARKER.encode() in raw

    # And it comes back: an archive nobody can read is not a backup.
    async with session_scope() as session:
        await session.execute(delete(Setting).where(Setting.key == "notify.channels"))
    async with session_scope() as session:
        await backup.restore_backup(session, info.path, secret_key=support.TEST_SECRET_KEY)
    assert (await stored_channels())[0]["token"] == TELEGRAM_TOKEN


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


async def test_user_management_is_administrator_only(client: Any) -> None:
    await signed_in(client, username="op", role=UserRole.OPERATOR)
    assert (await client.get("/api/v1/admin/users")).status_code == 403


async def test_creating_and_deleting_a_user(client: Any) -> None:
    await signed_in(client)
    created = await client.post(
        "/api/v1/admin/users",
        json={"username": "analyst", "password": "another-password", "role": "viewer"},
    )
    assert created.status_code == 201
    user = envelope(created)["data"]
    assert user["role"] == "viewer"
    assert "password" not in created.text

    duplicate = await client.post(
        "/api/v1/admin/users",
        json={"username": "analyst", "password": "another-password", "role": "viewer"},
    )
    assert error_code(duplicate) == "INVALID_PARAM"

    deleted = await client.delete(f"/api/v1/admin/users/{user['id']}")
    assert envelope(deleted)["data"]["deleted"] is True
    assert {"user.created", "user.deleted"} <= set(await audit_actions())


async def test_the_last_administrator_is_protected(client: Any) -> None:
    user_id = await signed_in(client)
    demote = await client.put(f"/api/v1/admin/users/{user_id}", json={"role": "viewer"})
    assert error_code(demote) == "INVALID_PARAM"

    delete_self = await client.delete(f"/api/v1/admin/users/{user_id}")
    assert error_code(delete_self) == "INVALID_PARAM"


async def test_an_administrator_can_reset_a_password_and_drop_its_sessions(
    client: Any, api_app: Any
) -> None:
    import httpx

    await signed_in(client)
    created = envelope(
        await client.post(
            "/api/v1/admin/users",
            json={"username": "analyst", "password": "another-password", "role": "operator"},
        )
    )["data"]

    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other:
        assert (await login(other, "analyst", "another-password")).status_code == 200
        assert (await other.get("/api/v1/auth/me")).status_code == 200

        response = await client.put(
            f"/api/v1/admin/users/{created['id']}", json={"password": "a-third-password"}
        )
        assert envelope(response)["data"]["revoked_sessions"] == 1
        assert (await other.get("/api/v1/auth/me")).status_code == 401
        assert (await login(other, "analyst", "a-third-password")).status_code == 200


# --------------------------------------------------------------------------
# Boards and the audit trail
# --------------------------------------------------------------------------


async def test_the_endpoint_board_lists_every_declared_endpoint(client: Any) -> None:
    await signed_in(client)
    rows = envelope(await client.get("/api/v1/admin/endpoints/health"))["data"]
    names = {row["endpoint"] for row in rows}

    assert "douyin.content_detail" in names
    assert "tiktok.content_detail" in names
    quiet = next(row for row in rows if row["endpoint"] == "douyin.content_detail")
    assert quiet["circuit_open"] is False
    assert quiet["samples"] == 0
    assert quiet["success_rate"] is None
    assert quiet["policy"]["capacity"] >= 1


async def test_the_timeseries_endpoint_answers_with_buckets(client: Any) -> None:
    await signed_in(client)
    response = await client.get(
        "/api/v1/admin/metrics/timeseries", params={"hours": 1, "step": 300}
    )
    data = envelope(response)["data"]
    assert data["window_hours"] == 1
    assert data["step_seconds"] == 300
    assert data["points"] == []


async def test_the_audit_trail_is_readable_and_filterable(client: Any) -> None:
    await signed_in(client)
    await client.post("/api/v1/admin/proxies", json={"url": PROXY_URL})

    rows = envelope(await client.get("/api/v1/admin/audit"))["data"]
    assert rows[0]["action"] == "proxy.created"
    assert rows[0]["ip"]
    assert "sup3rsecret" not in str(rows)

    filtered = envelope(
        await client.get("/api/v1/admin/audit", params={"action": "nothing.happened"})
    )["data"]
    assert filtered == []


async def test_maintenance_jobs_are_queued(client: Any) -> None:
    await signed_in(client)
    for path, body in (
        ("/api/v1/admin/diagnose", {"include_smoke_test": False}),
        ("/api/v1/admin/notifications/test", {"channel": "webhook"}),
        ("/api/v1/admin/backup", {"include_identities": False}),
    ):
        response = await client.post(path, json=body)
        assert response.status_code == 202, path
        assert envelope(response)["data"]["task_id"]


async def test_backup_is_administrator_only(client: Any) -> None:
    await signed_in(client, username="op", role=UserRole.OPERATOR)
    response = await client.post("/api/v1/admin/backup", json={"include_identities": True})
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Putting one back
# --------------------------------------------------------------------------


async def _degrade(streak: int = 5, state: str = IdentityState.DEGRADED.value) -> uuid.UUID:
    async with session_scope() as session:
        row = (await session.scalars(select(Identity))).one()
        row.state = state
        row.consecutive_fails = streak
        row.cooldown_until = datetime.now(UTC) + timedelta(hours=1)
        await session.commit()
        return row.id


async def _import_one(client: Any) -> None:
    await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": SESSION_COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )


async def test_reset_clears_the_cooldown_and_the_streak(client: Any) -> None:
    """The override for failures that were not the identity's fault.

    Recovery is deliberately slow, and slow is right only when the identity
    earned it. This instance's own classifier charged identities for looking up
    posts that did not exist until 2026-09-09, and fixing that repaired none of
    the damage already done.
    """
    await signed_in(client)
    await _import_one(client)
    identity_id = await _degrade()

    body = envelope(await client.post(f"/api/v1/admin/identities/{identity_id}/reset"))["data"]

    assert body["state"] == IdentityState.ACTIVE.value
    assert body["from_state"] == IdentityState.DEGRADED.value
    assert body["cleared_streak"] == 5
    async with session_scope() as session:
        row = await session.get(Identity, identity_id)
        assert row is not None
        assert row.state == IdentityState.ACTIVE.value
        assert row.consecutive_fails == 0
        assert row.cooldown_until is None


async def test_reset_brings_an_identity_back_into_the_pool_level(client: Any) -> None:
    """The number the refill job compares, which a streak keeps it out of."""
    await signed_in(client)
    await _import_one(client)
    identity_id = await _degrade()

    before = envelope(await client.get("/api/v1/admin/identities/pool"))["data"]
    douyin_before = next(p for p in before["platforms"] if p["platform"] == "douyin")
    assert douyin_before["usable"] == 0

    await client.post(f"/api/v1/admin/identities/{identity_id}/reset")

    after = envelope(await client.get("/api/v1/admin/identities/pool"))["data"]
    douyin_after = next(p for p in after["platforms"] if p["platform"] == "douyin")
    assert douyin_after["usable"] == 1


async def test_reset_is_written_to_the_identity_history(client: Any) -> None:
    """The record still shows it was in trouble, and that a person overrode it."""
    await signed_in(client)
    await _import_one(client)
    identity_id = await _degrade()

    await client.post(f"/api/v1/admin/identities/{identity_id}/reset")

    async with session_scope() as session:
        events = (
            await session.scalars(
                select(IdentityEvent).where(IdentityEvent.identity_id == identity_id)
            )
        ).all()
    reset = next(event for event in events if event.event == "reset")
    assert reset.detail == {"from_state": IdentityState.DEGRADED.value, "cleared_streak": 5}
    assert "identity.reset" in await audit_actions()


async def test_a_retired_identity_cannot_be_reset(client: Any) -> None:
    """Retiring wipes the jar. There is no session left to put back."""
    await signed_in(client)
    await _import_one(client)
    identity_id = await _degrade(state=IdentityState.RETIRED.value)

    refused = await client.post(f"/api/v1/admin/identities/{identity_id}/reset")

    assert error_code(refused) == "INVALID_PARAM"


async def test_resetting_something_that_is_not_there_is_a_404(client: Any) -> None:
    await signed_in(client)
    missing = uuid.uuid4()
    assert error_code(await client.post(f"/api/v1/admin/identities/{missing}/reset")) == "NOT_FOUND"


async def test_a_read_only_key_cannot_reset(api_app: Any, client: Any) -> None:
    """It returns an identity to service, which is a pool change.

    Through a client with no cookie: the shared one carries a console session,
    and `current_principal` falls back to it when a key is refused - so the
    request would succeed for the wrong reason.
    """
    await signed_in(client)
    await _import_one(client)
    identity_id = await _degrade()
    # `signed_in` already created "admin"; this one needs its own name.
    user_id = await make_user(username="reader", role=UserRole.VIEWER)
    key = await make_api_key(user_id, scopes=(Scope.ARCHIVE_READ,))

    async with anonymous_client(api_app) as caller:
        refused = await caller.post(
            f"/api/v1/admin/identities/{identity_id}/reset",
            headers={"Authorization": f"Bearer {key}"},
        )

    assert refused.status_code in (401, 403)
    async with session_scope() as session:
        row = await session.get(Identity, identity_id)
        assert row is not None
        assert row.state == IdentityState.DEGRADED.value
