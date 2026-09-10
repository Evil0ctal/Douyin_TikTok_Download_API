"""Nothing in the document may be unlabelled, and nothing may be English-only.

Swagger is where most callers actually read this API, and a gap there is
invisible from the code: a field with no `description=` renders as a bare name
beside an input box, and a field the catalogue has not caught up with renders in
English inside a Chinese document. Neither shows up in a test that only asks
whether the route works.

Operations, tags and parameters were already covered by the catalogue. Request
body fields were not - `schemas.py` carried its knowledge in `#:` comments,
which readers of the source get and readers of Swagger do not.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from dtk.api.routes.openapi import FIELD_PREFIX, PARAM_PREFIX, build_schema
from dtk.core.types import Language

#: CJK ideographs, written as escapes so this file stays pure ASCII
#: (docs/design/14-i18n.md). Extension A first, then the main block.
CJK = re.compile("[\\u3400-\\u4dbf\\u4e00-\\u9fff]")


@pytest.fixture(scope="module")
def api_app() -> Any:
    from dtk.api.app import create_app
    from dtk.core.config import BootstrapSettings

    return create_app(
        BootstrapSettings(
            secret_key="test-secret-key-for-openapi-completeness-0123456",
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost:6379/0",
        )
    )


@pytest.fixture(scope="module")
def documents(api_app: Any) -> dict[Language, dict[str, Any]]:
    """One document per language, built the way the endpoint builds them."""
    return {language: build_schema(api_app, language) for language in Language}


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, methods in document.get("paths", {}).items()
        for method, operation in methods.items()
        if isinstance(operation, dict)
    ]


def _fields(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (name, field, spec)
        for name, schema in (document.get("components", {}).get("schemas") or {}).items()
        if isinstance(schema, dict)
        for field, spec in (schema.get("properties") or {}).items()
        if isinstance(spec, dict)
    ]


class TestNothingIsUnlabelled:
    def test_every_operation_has_a_summary_and_a_description(
        self, documents: dict[Language, dict[str, Any]]
    ) -> None:
        for path, method, operation in _operations(documents[Language.EN]):
            assert (operation.get("summary") or "").strip(), f"{method.upper()} {path}"
            assert (operation.get("description") or "").strip(), f"{method.upper()} {path}"

    def test_every_parameter_has_a_description(
        self, documents: dict[Language, dict[str, Any]]
    ) -> None:
        for path, method, operation in _operations(documents[Language.EN]):
            for parameter in operation.get("parameters", []) or []:
                if not isinstance(parameter, dict):
                    continue
                assert (parameter.get("description") or "").strip(), (
                    f"{method.upper()} {path} ?{parameter.get('name')}"
                )

    def test_every_request_body_field_has_a_description(
        self, documents: dict[Language, dict[str, Any]]
    ) -> None:
        """The gap this file was written for. A bare field name in Swagger is a
        question the reader has to answer by reading the source."""
        for name, field, spec in _fields(documents[Language.EN]):
            assert (spec.get("description") or "").strip(), f"{name}.{field}"

    def test_every_tag_is_described(self, documents: dict[Language, dict[str, Any]]) -> None:
        described = {
            tag["name"] for tag in documents[Language.EN].get("tags", []) if tag.get("description")
        }
        used = {
            tag for _, _, op in _operations(documents[Language.EN]) for tag in op.get("tags", [])
        }
        assert used <= described, sorted(used - described)


class TestNothingIsEnglishOnly:
    """Every sentence in the Chinese document has to actually be Chinese.

    Checked by looking for a CJK character rather than by comparing against the
    English, because the failure being guarded is exactly "the catalogue has no
    entry, so the English fell through" - and that produces two identical
    strings, which a difference test would pass.
    """

    def test_operations(self, documents: dict[Language, dict[str, Any]]) -> None:
        for path, method, operation in _operations(documents[Language.ZH]):
            for part in ("summary", "description"):
                text = (operation.get(part) or "").strip()
                assert CJK.search(text), f"{method.upper()} {path} .{part}"

    def test_parameters(self, documents: dict[Language, dict[str, Any]]) -> None:
        for path, method, operation in _operations(documents[Language.ZH]):
            for parameter in operation.get("parameters", []) or []:
                if not isinstance(parameter, dict):
                    continue
                text = (parameter.get("description") or "").strip()
                assert CJK.search(text), (
                    f"{method.upper()} {path} ?{parameter.get('name')} - add "
                    f"{PARAM_PREFIX}{parameter.get('name')} to the catalogue"
                )

    def test_request_body_fields(self, documents: dict[Language, dict[str, Any]]) -> None:
        for name, field, spec in _fields(documents[Language.ZH]):
            text = (spec.get("description") or "").strip()
            assert CJK.search(text), f"add {FIELD_PREFIX}{name}.{field} to the catalogue"

    def test_the_envelope_every_response_uses_is_translated_too(
        self, documents: dict[Language, dict[str, Any]]
    ) -> None:
        """It is added late, after the other localisation passes, and was the one
        thing left in English when this check first ran."""
        schemas = documents[Language.ZH].get("components", {}).get("schemas") or {}
        for name in ("DtkResponse", "DtkError"):
            assert name in schemas, name
            for field, spec in (schemas[name].get("properties") or {}).items():
                text = (spec.get("description") or "").strip()
                # `code` is the one field documented as never translated, and its
                # own sentence about that still is.
                assert CJK.search(text), f"{name}.{field}"


def test_a_parameter_may_be_worded_per_operation_where_the_name_is_ambiguous(
    documents: dict[Language, dict[str, Any]],
) -> None:
    """`identity_id` filters the log on one route and names a target on another.

    One catalogue entry cannot be both, and the English source is already two
    different sentences, so the Chinese has to be able to be too.
    """
    seen: dict[str, set[str]] = {}
    for _, _, operation in _operations(documents[Language.ZH]):
        for parameter in operation.get("parameters", []) or []:
            if isinstance(parameter, dict) and parameter.get("name") == "identity_id":
                seen.setdefault("identity_id", set()).add(parameter.get("description", ""))
    assert len(seen.get("identity_id", set())) > 1, "the per-operation override stopped working"


class TestTheFrontPage:
    """The description Swagger renders above everything else.

    It lives in two places by necessity - `_translate` declines on the default
    language, so `app.DESCRIPTION` is the English document's own text and the
    catalogue entry is its translation - which makes drift between them the
    obvious failure. Pinned rather than deduplicated, because collapsing them
    would mean the English text going through a translation lookup that is
    documented to decline.
    """

    def test_the_english_front_page_is_the_catalogue_entry(self) -> None:
        from dtk.api.app import DESCRIPTION
        from dtk.i18n import catalog

        assert catalog.t("openapi.description", Language.EN) == DESCRIPTION

    def test_it_says_how_to_make_a_call_synchronous(
        self, documents: dict[Language, dict[str, Any]]
    ) -> None:
        """The gap that prompted this: async-first is the default and nothing
        told the reader how to opt out of it, or what a 202 after a wait means."""
        for language in Language:
            text = documents[language]["info"]["description"]
            assert "?wait=" in text, language
            assert "202" in text and "200" in text, language
            assert "callback_url" in text, language

    def test_the_english_wait_wording_is_the_catalogue_entry(self) -> None:
        """Same arrangement as the front page, and the same failure mode.

        A parameter's English text comes from the route, not the catalogue, so
        `?wait=` had a one-line summary in English long after the Chinese
        explained the whole thing.
        """
        from dtk.api.routes.content import WAIT_QUERY
        from dtk.i18n import catalog

        assert WAIT_QUERY.description == catalog.t("openapi.param.wait", Language.EN)

    def test_the_wait_parameter_carries_this_instances_ceiling(
        self, documents: dict[Language, dict[str, Any]]
    ) -> None:
        """`api.max_wait_seconds` is editable at runtime, and the document
        describes this instance rather than the defaults it shipped with."""
        found = [
            parameter
            for _, _, operation in _operations(documents[Language.EN])
            for parameter in operation.get("parameters", []) or []
            if isinstance(parameter, dict) and parameter.get("name") == "wait"
        ]
        assert found, "no endpoint declares ?wait="
        for parameter in found:
            assert "maximum" in (parameter.get("schema") or {}), parameter
