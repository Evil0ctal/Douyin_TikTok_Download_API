"""Administrative surface: identities, proxies, keys, settings, users, audit.

Two invariants get the most attention here because doc 08 states them as
absolutes: no response contains a cookie, and no response contains a proxy
password.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from dtk.core.db import session_scope
from dtk.core.types import IdentityState, Platform, Scope, UserRole
from dtk.db.models import ApiKey, AuditLog, Identity, Proxy
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


async def audit_actions() -> list[str]:
    async with session_scope() as session:
        rows = (await session.scalars(select(AuditLog).order_by(AuditLog.ts))).all()
        return [row.action for row in rows]


# --------------------------------------------------------------------------
# Identities
# --------------------------------------------------------------------------


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
    assert "cookie" not in listed.text.lower()
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
            "/api/v1/douyin/video", params={"aweme_id": "7123"}, headers=headers
        )
        assert allowed.status_code == 202

        assert (await client.delete(f"/api/v1/admin/api-keys/{created['id']}")).status_code == 200

        denied = await caller.get(
            "/api/v1/douyin/video", params={"aweme_id": "7124"}, headers=headers
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
        "/api/v1/admin/settings/security.enable_download_proxy", json={"value": True}
    )
    assert error_code(unconfirmed) == "INVALID_PARAM"
    assert envelope(unconfirmed)["error"]["details"]["sensitive"] is True

    confirmed = await client.put(
        "/api/v1/admin/settings/security.enable_download_proxy",
        json={"value": True, "confirm": True},
    )
    assert envelope(confirmed)["data"]["value"] is True
    assert "settings.updated_sensitive" in await audit_actions()


async def test_an_operator_cannot_change_a_sensitive_setting(client: Any) -> None:
    await signed_in(client, username="op", role=UserRole.OPERATOR)
    response = await client.put(
        "/api/v1/admin/settings/security.enable_download_proxy",
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
            "/api/v1/admin/settings/security.enable_download_proxy",
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
            "/api/v1/admin/settings/security.enable_download_proxy",
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
