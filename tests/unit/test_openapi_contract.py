"""What the published API document promises.

Two things were wrong and are now asserted. Every success response was an
untyped `{}`, so a generated client had no idea where the payload lived or what
an error looked like. And every operation with a body documented a 422 with
FastAPI's own validation schema - a status this API never sends, because
`RequestValidationError` is answered as a 400 in the uniform envelope. A
documented status the service cannot produce is worse than an undocumented one:
a client writes a branch for it.
"""

from __future__ import annotations

import pytest

from dtk.api.routes.openapi import build_schema
from dtk.core.types import Language


@pytest.fixture(scope="module")
def document(api_app_module):
    return build_schema(api_app_module, Language.EN)


@pytest.fixture(scope="module")
def api_app_module():
    from dtk.api.app import create_app
    from dtk.core.config import BootstrapSettings

    return create_app(
        BootstrapSettings(
            secret_key="test-secret-key-for-openapi-contract-0123456789",
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost:6379/0",
        )
    )


def _json_responses(document):
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            yield path, method, operation


def test_the_envelope_is_declared_once(document) -> None:
    schemas = document["components"]["schemas"]
    assert "DtkResponse" in schemas
    assert "DtkError" in schemas
    envelope = schemas["DtkResponse"]
    assert set(envelope["required"]) == {"success", "data", "error", "meta"}
    # `data` stays untyped on purpose: declaring every endpoint's payload a
    # second time is what dtk.api.routes.schemas argues against for requests,
    # and the same reasoning holds here.
    assert "type" not in envelope["properties"]["data"]


def test_the_error_code_is_declared_as_stable(document) -> None:
    error = document["components"]["schemas"]["DtkError"]
    assert set(error["required"]) == {"code", "message"}
    assert "retry_after" in error["properties"]


def test_no_operation_promises_a_422_this_api_never_sends(document) -> None:
    for path, method, operation in _json_responses(document):
        assert "422" not in operation.get("responses", {}), f"{method} {path}"
    assert "HTTPValidationError" not in document["components"]["schemas"]


def test_every_json_success_points_at_the_envelope(document) -> None:
    for path, method, operation in _json_responses(document):
        for status, response in operation.get("responses", {}).items():
            if not status.startswith("2"):
                continue
            content = response.get("content") or {}
            schema = content.get("application/json", {}).get("schema")
            if schema is None:
                continue
            assert schema == {"$ref": "#/components/schemas/DtkResponse"}, f"{method} {path}"


def test_the_streaming_export_keeps_its_own_media_type(document) -> None:
    """The one endpoint that does not answer in the envelope says so."""
    response = document["paths"]["/api/v1/archive/export"]["get"]["responses"]["200"]
    assert list(response["content"]) == ["application/x-ndjson"]


def test_the_common_error_statuses_are_documented(document) -> None:
    responses = document["paths"]["/api/v1/parse"]["post"]["responses"]
    for status in ("400", "401", "403", "404", "429", "503"):
        assert status in responses
        assert responses[status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/DtkResponse"
        }
