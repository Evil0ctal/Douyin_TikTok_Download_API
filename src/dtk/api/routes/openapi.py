"""Bilingual OpenAPI.

V4 concatenated both languages into every summary, separated by a slash, so
each reader skipped half of every line and a third language had nowhere to go.
Doc 14 asks for the opposite: one document per language, selected by ``?lang=``.

Docstrings and the ``summary=`` on each route stay English - they are source.
The translations live in the i18n catalogue under ``openapi.*`` keys, and this
module swaps them in when a Chinese document is requested. A key with no
translation keeps the English text rather than rendering as a key name: a
partially translated document is usable, a document full of
``openapi.op.parse.summary`` is not.

``/docs`` and ``/redoc`` forward their own ``?lang=`` to the schema URL, so the
Swagger page loads the matching document.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n import catalog
from dtk.i18n.negotiate import resolve_language

log = get_logger(__name__)

#: Extension key a route carries to name its catalogue entry. Set it with
#: ``openapi_extra={I18N_KEY: "parse"}`` and the summary and description are
#: taken from ``openapi.op.parse.summary`` / ``.description``.
I18N_KEY = "x-i18n"

TAG_PREFIX = "openapi.tag."
OP_PREFIX = "openapi.op."
PARAM_PREFIX = "openapi.param."
DESCRIPTION_KEY = "openapi.description"

#: Every endpoint honours ``?lang=``, but the middleware reads it rather than a
#: route signature, so it would otherwise be invisible in the document.
LANG_PARAMETER_KEY = f"{PARAM_PREFIX}lang"


def _translate(key: str, language: Language) -> str | None:
    """Catalogue lookup that declines rather than inventing text."""
    if language is DEFAULT_LANGUAGE:
        return None
    if not catalog.has(key, language):
        return None
    return catalog.t(key, language)


def _text(key: str, language: Language) -> str | None:
    """Catalogue lookup in any language, falling back to English."""
    if not catalog.has(key, language) and not catalog.has(key, DEFAULT_LANGUAGE):
        return None
    return catalog.t(key, language)


def _lang_parameter(language: Language) -> dict[str, Any]:
    return {
        "name": "lang",
        "in": "query",
        "required": False,
        "description": _text(LANG_PARAMETER_KEY, language) or "Response language.",
        "schema": {"type": "string", "enum": [lang.value for lang in Language]},
    }


def _localize_operation(operation: dict[str, Any], language: Language) -> None:
    """Swap one operation's prose, then its shared parameter descriptions."""
    key = operation.get(I18N_KEY)
    if isinstance(key, str) and key:
        summary = _translate(f"{OP_PREFIX}{key}.summary", language)
        if summary:
            operation["summary"] = summary
        description = _translate(f"{OP_PREFIX}{key}.description", language)
        if description:
            operation["description"] = description

    parameters = operation.setdefault("parameters", [])
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        text = _translate(f"{PARAM_PREFIX}{parameter.get('name')}", language)
        if text:
            parameter["description"] = text
    if not any(isinstance(p, dict) and p.get("name") == "lang" for p in parameters):
        parameters.append(_lang_parameter(language))


def _localize_tags(schema: dict[str, Any], language: Language) -> None:
    names = sorted(
        {
            tag
            for path in schema.get("paths", {}).values()
            for op in path.values()
            if isinstance(op, dict)
            for tag in op.get("tags", [])
        }
    )
    described = []
    for name in names:
        text = _translate(f"{TAG_PREFIX}{name}", language)
        described.append({"name": name, "description": text} if text else {"name": name})
    if described:
        schema["tags"] = described


def build_schema(app: FastAPI, language: Language) -> dict[str, Any]:
    """Render the document for one language.

    Built from scratch for each language rather than from a cached English
    document, because mutating one shared dictionary would leak the last
    request's language into the next one.
    """
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    translated_description = _translate(DESCRIPTION_KEY, language)
    if translated_description:
        schema["info"]["description"] = translated_description

    for path in schema.get("paths", {}).values():
        for operation in path.values():
            if isinstance(operation, dict):
                _localize_operation(operation, language)
    _localize_tags(schema, language)
    schema["info"]["x-language"] = language.value
    return schema


def install(app: FastAPI) -> None:
    """Replace the generated documentation routes with language-aware ones.

    FastAPI registers ``/openapi.json``, ``/docs`` and ``/redoc`` when the
    application is constructed, and the first matching route wins, so they are
    removed before the replacements are added rather than shadowed.
    """
    paths = {app.openapi_url, app.docs_url, app.redoc_url} - {None}
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "path", None) not in paths
    ]

    schema_url = app.openapi_url or "/openapi.json"

    def _language(request: Request) -> Language:
        return resolve_language(
            request.query_params.get("lang"),
            request.headers.get("accept-language"),
            app.state.config.get("api.default_language"),
        )

    @app.get(schema_url, include_in_schema=False)
    async def openapi_schema(request: Request) -> JSONResponse:
        return JSONResponse(build_schema(app, _language(request)))

    if app.docs_url:

        @app.get(app.docs_url, include_in_schema=False)
        async def swagger_ui(request: Request) -> HTMLResponse:
            language = _language(request)
            return get_swagger_ui_html(
                openapi_url=f"{schema_url}?lang={language.value}",
                title=f"{app.title} - API",
            )

    if app.redoc_url:

        @app.get(app.redoc_url, include_in_schema=False)
        async def redoc(request: Request) -> HTMLResponse:
            language = _language(request)
            return get_redoc_html(
                openapi_url=f"{schema_url}?lang={language.value}",
                title=f"{app.title} - API",
            )

    log.debug("openapi.localized_routes_installed", paths=sorted(str(p) for p in paths))


__all__ = [
    "DESCRIPTION_KEY",
    "I18N_KEY",
    "LANG_PARAMETER_KEY",
    "OP_PREFIX",
    "PARAM_PREFIX",
    "TAG_PREFIX",
    "build_schema",
    "install",
]
