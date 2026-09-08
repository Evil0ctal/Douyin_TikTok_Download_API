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

from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from dtk.api.deps import API_KEY_HEADER, SESSION_COOKIE
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
API_KEY_DESCRIPTION_KEY = "openapi.security.api_key"
SESSION_DESCRIPTION_KEY = "openapi.security.session"
OP_PREFIX = "openapi.op."
PARAM_PREFIX = "openapi.param."
DESCRIPTION_KEY = "openapi.description"
#: The one sentence that explains the uniform envelope to a reader of /docs.
ENVELOPE_DESCRIPTION_KEY = "openapi.envelope"

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


#: Paths every caller reaches without credentials. Everything else needs one,
#: which is what the scheme below lets Swagger UI actually send.
_PUBLIC_PATHS: Final[frozenset[str]] = frozenset(
    {"/api/setup/status", "/api/setup/init", "/api/v1/auth/login"}
)

_API_KEY_SCHEME = "ApiKeyAuth"
_SESSION_SCHEME = "SessionCookie"


def _declare_security(schema: dict[str, Any], language: Language) -> None:
    """Say how a caller authenticates, so the docs page can do it.

    Without this the document declares no security at all: Swagger UI shows no
    Authorize button, there is nowhere to put an API key, and every "Try it out"
    against a real endpoint answers 401 - on a service whose whole purpose is to
    be called by someone else's program.

    Both schemes are declared because both are real. A program sends the header;
    the console sends the cookie it already has, which is why opening /docs from
    a signed-in browser works without pasting anything.
    """
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        _API_KEY_SCHEME: {
            "type": "apiKey",
            "in": "header",
            "name": API_KEY_HEADER,
            "description": _translate(API_KEY_DESCRIPTION_KEY, language)
            or "Create one on the console's API keys page.",
        },
        _SESSION_SCHEME: {
            "type": "apiKey",
            "in": "cookie",
            "name": SESSION_COOKIE,
            "description": _translate(SESSION_DESCRIPTION_KEY, language)
            or "Set by signing in to the console; sent automatically by a browser.",
        },
    }
    # Per operation rather than one global default: a document that claims the
    # login endpoint needs a key is wrong in a way a reader has to un-learn.
    for path, operations in schema.get("paths", {}).items():
        if path in _PUBLIC_PATHS:
            continue
        for operation in operations.values():
            if isinstance(operation, dict) and "security" not in operation:
                operation["security"] = [{_API_KEY_SCHEME: []}, {_SESSION_SCHEME: []}]


#: The two component schemas every operation actually answers with.
#:
#: Only the envelope is declared, never each endpoint's ``data``. That is the
#: same decision :mod:`dtk.api.routes.schemas` makes for request bodies and
#: states outright: responses are assembled as dictionaries, and declaring them
#: a second time here would only let the two drift. What a generated client
#: genuinely needs is the part that never varies - is this a success, where is
#: the payload, what shape is an error - and that is what this types.
_ENVELOPE_SCHEMA: Final = "DtkResponse"
_ERROR_SCHEMA: Final = "DtkError"


def _envelope_components(language: Language) -> dict[str, Any]:
    return {
        _ERROR_SCHEMA: {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Stable machine-readable identifier. Never translated.",
                    "example": "IDENTITY_POOL_EXHAUSTED",
                },
                "message": {
                    "type": "string",
                    "description": "Human sentence, rendered in the caller's language.",
                },
                "details": {"type": "object", "additionalProperties": True},
                "retry_after": {
                    "type": "integer",
                    "nullable": True,
                    "description": "Seconds to wait, when the error is one that clears.",
                },
                "retryable": {"type": "boolean"},
            },
        },
        _ENVELOPE_SCHEMA: {
            "type": "object",
            "required": ["success", "data", "error", "meta"],
            "description": _text(ENVELOPE_DESCRIPTION_KEY, language)
            or (
                "Every response has this shape, including errors. `data` carries "
                "the endpoint's own payload and is null whenever `success` is false."
            ),
            "properties": {
                "success": {"type": "boolean"},
                "data": {"nullable": True, "description": "The endpoint's payload."},
                "error": {
                    "oneOf": [{"$ref": f"#/components/schemas/{_ERROR_SCHEMA}"}],
                    "nullable": True,
                },
                "meta": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {"request_id": {"type": "string", "format": "uuid"}},
                },
            },
        },
    }


#: Status codes any authenticated endpoint can answer, documented once.
#: Without these a generated client has no error type at all and a reader is
#: left to discover 429 by being rate limited.
_COMMON_ERRORS: Final[tuple[tuple[str, str], ...]] = (
    ("400", "The request was rejected. `error.code` says why."),
    ("401", "No API key or session, or it is not valid."),
    ("403", "Authenticated, but this credential lacks the scope."),
    ("404", "No such resource, or the platform says the content is gone."),
    ("429", "Rate limited. `error.retry_after` says when to come back."),
    ("503", "The identity pool, the queue or an upstream endpoint is unavailable."),
)


def _type_responses(schema: dict[str, Any], language: Language) -> None:
    """Point every response at the envelope, and drop the 422 that never happens.

    FastAPI documents a 422 with its own `HTTPValidationError` on any operation
    that has a body or a typed parameter. This API never sends one: the handler
    in :mod:`dtk.api.app` catches `RequestValidationError` and answers 400 in
    the envelope like everything else. A documented status the service cannot
    produce is worse than an undocumented one, because a client writes a branch
    for it.
    """
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(_envelope_components(language))
    components.pop("HTTPValidationError", None)
    components.pop("ValidationError", None)

    envelope_content = {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{_ENVELOPE_SCHEMA}"}}
    }
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            responses.pop("422", None)
            for status, response in list(responses.items()):
                if not isinstance(response, dict) or not status.startswith("2"):
                    continue
                # A streaming endpoint declares its own media type; the export
                # answers newline-delimited JSON, not the envelope, and saying
                # otherwise would be the same lie in the other direction.
                content = response.get("content") or {}
                if any(kind != "application/json" for kind in content):
                    continue
                response["content"] = envelope_content
            for status, description in _COMMON_ERRORS:
                responses.setdefault(
                    status, {"description": description, "content": envelope_content}
                )


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
    _declare_security(schema, language)
    _type_responses(schema, language)
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

    # One generator, not two. `app.openapi()` is what FastAPI's own tooling and
    # every test reaches for, and leaving it on the built-in generator meant the
    # served document and the introspected one disagreed - the security schemes
    # below existed on the wire and were invisible to anything that asked the
    # app. Pointed at the same builder, in the default language.
    app.openapi = lambda: build_schema(app, DEFAULT_LANGUAGE)  # type: ignore[method-assign]

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
