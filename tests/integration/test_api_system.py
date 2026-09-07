"""Health probes, the status page, the iOS endpoint and the bilingual docs."""

from __future__ import annotations

from typing import Any

import pytest

from dtk import __version__
from dtk.core.types import IdentityState, Platform
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------


async def test_healthz_is_unauthenticated_and_flat(client: Any) -> None:
    """Liveness answers a probe, not an API client: no envelope, no auth."""
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["uptime_seconds"] >= 0
    assert "success" not in body


async def test_healthz_survives_the_database_being_unreachable(client: Any) -> None:
    """The point of separating liveness from readiness (doc 15).

    Every pooled connection is dropped underneath the process; liveness must
    still answer 200, because a restart storm is what turns a database blip
    into an outage.
    """
    from dtk.core.db import get_engine

    await get_engine().dispose()
    assert (await client.get("/healthz")).status_code == 200


async def test_readyz_checks_both_dependencies(client: Any) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["components"]["postgres"]["ok"] is True
    assert body["components"]["redis"]["ok"] is True
    assert body["components"]["postgres"]["latency_ms"] >= 0


async def test_system_status_requires_authentication(client: Any) -> None:
    response = await client.get("/api/v1/system/status")
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_system_status_reports_version_pool_and_storage(client: Any) -> None:
    await signed_in(client)
    data = envelope(await client.get("/api/v1/system/status"))["data"]

    assert data["version"] == __version__
    assert data["uptime_seconds"] >= 0
    assert data["components"]["postgres"]["ok"] is True
    assert data["components"]["redis"]["ok"] is True
    # Not probed from a status page; the diagnostics run does that.
    assert data["components"]["browser_rpc"]["ok"] is None
    assert data["pool"]["total_active"] == 0
    assert set(data["pool"][Platform.DOUYIN.value]) == {s.value for s in IdentityState}
    assert data["storage"]["db_size_bytes"] > 0
    assert "request_log" in data["storage"]["rows"]


# --------------------------------------------------------------------------
# iOS Shortcut
# --------------------------------------------------------------------------


async def test_shortcut_defaults_to_english(client: Any) -> None:
    data = envelope(await client.get("/api/v1/ios/shortcut"))["data"]
    assert data["language"] == "en"
    assert data["version"]
    assert data["link"].startswith("https://")
    assert isinstance(data["notes"], list)
    assert data["notes"]


async def test_shortcut_returns_one_language_per_request(client: Any) -> None:
    """V4 shipped both languages in one response; v5 answers in one (doc 06)."""
    english = envelope(await client.get("/api/v1/ios/shortcut"))["data"]
    chinese = envelope(await client.get("/api/v1/ios/shortcut", params={"lang": "zh"}))["data"]

    assert chinese["language"] == "zh"
    assert chinese["notes"] != english["notes"]
    assert set(english) == set(chinese)
    assert "link_en" not in english
    assert "note_en" not in english


async def test_accept_language_selects_the_translation(client: Any) -> None:
    response = await client.get(
        "/api/v1/ios/shortcut", headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    )
    assert envelope(response)["data"]["language"] == "zh"


async def test_an_unsupported_language_falls_back_to_english(client: Any) -> None:
    response = await client.get("/api/v1/ios/shortcut", params={"lang": "fr"})
    assert envelope(response)["data"]["language"] == "en"


# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------


async def test_the_openapi_document_covers_the_documented_surface(client: Any) -> None:
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]

    for path in (
        "/api/setup/status",
        "/api/v1/parse",
        "/api/v1/tasks/batch",
        "/api/v1/tasks/{task_id}",
        "/api/v1/{platform}/video",
        "/api/v1/{platform}/video/comments",
        "/api/v1/{platform}/video/comments/replies",
        "/api/v1/{platform}/user",
        "/api/v1/{platform}/user/posts",
        "/api/v1/ios/shortcut",
        "/api/v1/admin/identities",
        "/api/v1/system/status",
    ):
        assert path in paths, path
    assert schema["info"]["x-language"] == "en"


async def test_the_document_is_rendered_in_the_requested_language(client: Any) -> None:
    english = (await client.get("/openapi.json")).json()
    chinese = (await client.get("/openapi.json", params={"lang": "zh"})).json()

    assert chinese["info"]["x-language"] == "zh"
    assert chinese["info"]["description"] != english["info"]["description"]

    en_parse = english["paths"]["/api/v1/parse"]["post"]
    zh_parse = chinese["paths"]["/api/v1/parse"]["post"]
    assert zh_parse["summary"] != en_parse["summary"]
    # The stable half never moves: same paths, same operation ids.
    assert english["paths"].keys() == chinese["paths"].keys()
    assert zh_parse["operationId"] == en_parse["operationId"]
    # And neither document mixes the two languages into one string.
    assert "/" not in zh_parse["summary"].strip("/")


async def test_every_operation_documents_the_lang_parameter(client: Any) -> None:
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/api/v1/ios/shortcut"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert "lang" in names


async def test_swagger_ui_loads_the_matching_document(client: Any) -> None:
    english = await client.get("/docs")
    assert english.status_code == 200
    assert "/openapi.json?lang=en" in english.text

    chinese = await client.get("/docs", params={"lang": "zh"})
    assert "/openapi.json?lang=zh" in chinese.text

    redoc = await client.get("/redoc", params={"lang": "zh"})
    assert redoc.status_code == 200
    assert "/openapi.json?lang=zh" in redoc.text


async def test_error_messages_are_localized_while_the_code_is_not(client: Any) -> None:
    english = await client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    chinese = await client.get(
        "/api/v1/tasks/00000000-0000-0000-0000-000000000000", params={"lang": "zh"}
    )
    assert error_code(english) == "UNAUTHENTICATED"
    assert error_code(chinese) == "UNAUTHENTICATED"
    assert english.json()["error"]["message"] != chinese.json()["error"]["message"]


async def test_security_headers_and_the_request_id_are_on_every_response(client: Any) -> None:
    response = await client.get("/api/v1/ios/shortcut")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-request-id"] == envelope(response)["meta"]["request_id"]
