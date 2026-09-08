"""The console's path table and the API's route table are one contract.

``web/src/lib/endpoints.ts`` is where every path the console calls is written
down; ``src/dtk/api/routes`` is where they are registered. Nothing joined the
two, and a mismatch raises on neither side: a path is only ever a string, so the
build is clean and ``tsc`` has nothing to check, and the API starts happily with
the route simply absent. The first thing that notices is a user opening the
page, and what they see is the generic failure every other error renders as - so
the report says "the API is broken", not "this path was never built".

It shipped exactly that way. Four declared paths the API never registered
(``/auth/session`` against an API that serves ``/auth/me``; a GET on
``/admin/backup``, which existed but only accepted POST; ``/admin/backup/restore``;
``/admin/logs/requests``) and four more declarations nothing called, which would
have 404ed the day someone wired them up. This file is the join, in both
directions, and at the method level where the call site says which verb it uses.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import pytest

from dtk.api.app import create_app
from dtk.core.config import BootstrapSettings

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web" / "src"
ENDPOINTS_TS = WEB / "lib" / "endpoints.ts"

#: Long enough for Cipher. The application is only built here, never started, so
#: the unreachable hosts are deliberate: anything that tried to connect fails
#: loudly instead of finding a real database.
TEST_SECRET = "test-secret-key-that-is-long-enough-0123456789"

#: Floors, not targets. A parser that quietly stops understanding the file it
#: reads checks nothing while still passing, which is worse than no guard at
#: all; these turn that into a failure. Raise them only alongside the file.
MINIMUM_DECLARATIONS = 35
MINIMUM_CALL_SITES = 30


# --------------------------------------------------------------------------
# Reading endpoints.ts
#
# Two forms carry a path there: a template literal (`${API_V1}/auth/login`) and
# an arrow returning one for the parameterised paths (`(id) => `.../${id}``).
# Anything inside the block that is neither raises rather than being skipped -
# a declaration this parser cannot see is a declaration nothing checks.
# --------------------------------------------------------------------------

_BLOCK_START = "export const paths = {"
_BLOCK_END = "} as const"
_API_V1 = re.compile(r"^export const API_V1 = '([^']*)'$", re.M)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_OPEN_GROUP = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*\{$")
_CLOSE_GROUP = re.compile(r"^\},?$")
_LEAF = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*):\s*"
    # The arrow's parameter list, for the paths that take an id.
    r"(?:\([^)]*\)\s*=>\s*)?"
    r"(?:'(?P<literal>[^']*)'|`(?P<template>[^`]*)`),?$"
)
_INTERPOLATION = re.compile(r"\$\{([^{}]*)\}")

#: FastAPI writes ``{identity_id}`` where the console writes
#: ``${encodeURIComponent(id)}``. The two name their parameters independently
#: and always will, so both sides are compared with the names elided.
_PARAMETER = re.compile(r"\{[^{}]*\}")
_PLACEHOLDER = "{}"


def _canonical(path: str) -> str:
    return _PARAMETER.sub(_PLACEHOLDER, path)


def _expand(template: str, api_v1: str) -> str:
    """A template literal with its interpolations resolved.

    ``${API_V1}`` is the prefix constant and is substituted; every other
    interpolation is a value the caller supplies at runtime, which is a path
    parameter by definition.
    """

    def resolve(match: re.Match[str]) -> str:
        return api_v1 if match.group(1).strip() == "API_V1" else _PLACEHOLDER

    return _INTERPOLATION.sub(resolve, template)


@functools.cache
def declarations() -> dict[str, str]:
    """Every path the console declares, keyed by its dotted accessor.

    ``{'auth.login': '/api/v1/auth/login', 'users.byId': '/api/v1/admin/users/{}'}``
    """
    source = ENDPOINTS_TS.read_text(encoding="utf-8")
    source = _BLOCK_COMMENT.sub("", source)

    prefix = _API_V1.search(source)
    if prefix is None:
        raise RuntimeError(f"{ENDPOINTS_TS.name}: no `export const API_V1 = '...'` to resolve")
    api_v1 = prefix.group(1)

    if _BLOCK_START not in source:
        raise RuntimeError(f"{ENDPOINTS_TS.name}: no `{_BLOCK_START}` block to read")
    lines = source.split(_BLOCK_START, 1)[1].splitlines()[1:]

    found: dict[str, str] = {}
    group: list[str] = []
    # An arrow whose parameter list does not fit on one line is wrapped by the
    # formatter, so the declaration arrives as two lines. Rejoined rather than
    # skipped: an unreadable line is a path this file stops checking, which is
    # the failure it exists to prevent.
    pending = ""
    for number, raw in enumerate(lines, 1):
        line = (pending + " " + raw.strip()).strip() if pending else raw.strip()
        pending = ""
        if not line or line.startswith("//"):
            continue
        if line.endswith("=>"):
            pending = line
            continue
        if line == _BLOCK_END:
            break
        if opened := _OPEN_GROUP.match(line):
            group.append(opened.group(1))
            continue
        if _CLOSE_GROUP.match(line):
            group.pop()
            continue
        if leaf := _LEAF.match(line):
            literal, template = leaf.group("literal"), leaf.group("template")
            path = literal if literal is not None else _expand(template or "", api_v1)
            if not path.startswith("/"):
                raise RuntimeError(f"{ENDPOINTS_TS.name}: {line!r} does not resolve to a path")
            found[".".join([*group, leaf.group(1)])] = _canonical(path)
            continue
        raise RuntimeError(
            f"{ENDPOINTS_TS.name}: cannot read declaration on line {number} of the block: "
            f"{line!r}. Teach this parser the new form - skipping it would leave that "
            "path unchecked, which is the failure this file exists to prevent."
        )
    else:
        raise RuntimeError(f"{ENDPOINTS_TS.name}: the paths block has no `{_BLOCK_END}`")

    return found


# --------------------------------------------------------------------------
# Reading the call sites
#
# Only the two unambiguous forms are read: a verb helper wrapped directly around
# a declaration, and useApiQuery's `path`, which is always a GET. The console
# has three forms this deliberately does not read - apiRequest(), which carries
# its method in an options object; Playground's catalogue, which pairs a `path`
# arrow with its own `method` field; and Diagnose's ternary, whose second branch
# is a placeholder for a query that is disabled. Guessing at those would attach
# methods nobody calls, so they contribute no method here and are still covered
# by the path-level check below.
# --------------------------------------------------------------------------

# The type argument is skipped non-greedily rather than by excluding angle
# brackets: `apiPost<TaskEnvelope<unknown>>(paths.parse, ...)` nests them, and
# the excluding form silently matched nothing there - so paths.parse carried no
# method observation and a GET-only /api/v1/parse would have gone unnoticed.
_VERB_CALL = re.compile(
    r"\bapi(Get|Post|Put|Patch|Delete)\s*(?:<.*?>)?\s*\(\s*paths\.([A-Za-z0-9_.]+)"
)
_QUERY_PATH = re.compile(r"\bpath:\s*paths\.([A-Za-z0-9_.]+)")


def _console_sources() -> list[Path]:
    return sorted(
        path
        for path in [*WEB.rglob("*.ts"), *WEB.rglob("*.tsx")]
        if path != ENDPOINTS_TS and "node_modules" not in path.parts
    )


@functools.cache
def call_sites() -> dict[str, frozenset[str]]:
    """The methods each declaration is actually requested with."""
    observed: dict[str, set[str]] = {}
    for path in _console_sources():
        text = path.read_text(encoding="utf-8")
        for verb, name in _VERB_CALL.findall(text):
            observed.setdefault(name, set()).add(verb.upper())
        for name in _QUERY_PATH.findall(text):
            observed.setdefault(name, set()).add("GET")
    return {name: frozenset(methods) for name, methods in observed.items()}


@functools.cache
def referenced() -> frozenset[str]:
    """Declarations mentioned anywhere in the console, called or not.

    Wider than :func:`call_sites` on purpose: query.ts holds a path to match
    cache entries against and ApiDocs interpolates one into a link, neither of
    which is a request. Both are still consumers, so neither is dead.
    """
    sources = [path.read_text(encoding="utf-8") for path in _console_sources()]
    return frozenset(
        name
        for name in declarations()
        # The trailing boundary keeps `backup.list` from matching `backup.listAll`.
        if any(re.search(rf"\bpaths\.{re.escape(name)}\b", text) for text in sources)
    )


# --------------------------------------------------------------------------
# Reading the route table
# --------------------------------------------------------------------------

#: Routes registered with ``include_in_schema=False`` and therefore absent from
#: the OpenAPI document: the two container probes and the documentation surface
#: itself, none of which belongs in the data API's contract. The console links
#: to all five, so reading only ``openapi()["paths"]`` would report them as
#: missing. They are resolved by route name instead, which raises if one is
#: renamed - this cannot decay into an allowlist that hides a real gap.
HIDDEN_GET_ROUTES = ("healthz", "readyz", "swagger_ui", "redoc", "openapi_schema")

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


@functools.cache
def _app() -> Any:
    """One app, built without touching a service. Cached: it is not cheap."""
    return create_app(
        BootstrapSettings(
            secret_key=TEST_SECRET,
            database_url="postgresql+asyncpg://dtk:dtk@nowhere:5432/dtk",
            redis_url="redis://nowhere:6379/0",
            browser_rpc_url="",
        )
    )


@functools.cache
def registered() -> dict[str, frozenset[str]]:
    """Every path the API serves, canonicalised, with the methods it accepts."""
    app = _app()

    routes: dict[str, set[str]] = {}
    for path, operations in app.openapi()["paths"].items():
        methods = {verb.upper() for verb in operations} & _HTTP_METHODS
        routes.setdefault(_canonical(path), set()).update(methods)
    for name in HIDDEN_GET_ROUTES:
        routes.setdefault(_canonical(app.url_path_for(name)), set()).add("GET")
    return {path: frozenset(methods) for path, methods in routes.items()}


#: Declarations that name a family of paths rather than one route.
#:
#: ``tasks.root`` is the only member: query.ts matches cache entries against the
#: prefix and no request is ever sent there. Named explicitly rather than
#: inferred from "is a prefix of something registered", which is what this used
#: to do - that shape also sheltered ``tasks.byId``, since /api/v1/tasks/{} is a
#: prefix of /api/v1/tasks/{}/events. Deleting GET /tasks/{id} - the route every
#: 202 in the system polls - then left the suite green.
FAMILY_PREFIXES: frozenset[str] = frozenset({"tasks.root"})


def _serves_a_family_below(name: str, path: str) -> bool:
    """Whether this declaration names a family rather than a route.

    Still conditioned on the path being a prefix of something registered, so a
    stale entry in the set cannot shelter a family that has stopped existing.
    """
    if name not in FAMILY_PREFIXES:
        return False
    return any(registered_path.startswith(f"{path}/") for registered_path in registered())


# --------------------------------------------------------------------------
# The parsers, before anything that depends on them
# --------------------------------------------------------------------------


def test_the_declaration_parser_reads_the_whole_table() -> None:
    found = declarations()
    assert len(found) >= MINIMUM_DECLARATIONS, (
        f"only {len(found)} declarations parsed out of {ENDPOINTS_TS.name}; the file "
        "is larger than that, so the parser has lost track of its structure and the "
        "checks below are passing on an empty set"
    )


def test_the_declaration_parser_resolves_both_declaration_forms() -> None:
    """A worked example of each form, so a silent regression in one is visible."""
    found = declarations()
    assert found["setup.status"] == "/api/setup/status", "plain string literal"
    assert found["auth.login"] == "/api/v1/auth/login", "template literal with ${API_V1}"
    assert found["users.byId"] == "/api/v1/admin/users/{}", "arrow returning a parameterised path"


def test_the_call_site_scan_still_sees_the_console_making_requests() -> None:
    sites = call_sites()
    observations = sum(len(methods) for methods in sites.values())
    assert observations >= MINIMUM_CALL_SITES, (
        f"only {observations} calls found across {len(_console_sources())} console "
        "sources; the helpers in lib/api.ts have probably been renamed or wrapped, "
        "and the method check below is now asserting almost nothing"
    )
    verbs = frozenset().union(*sites.values())
    assert {"GET", "POST", "PUT", "DELETE"} <= verbs, f"only these verbs were read: {sorted(verbs)}"


def test_every_call_site_names_a_declaration_that_exists() -> None:
    """Guards the join itself: a typo here reads as a path with no call sites."""
    unknown = sorted(set(call_sites()) - set(declarations()))
    assert not unknown, (
        f"the console calls paths.{unknown} but the parser found no such declaration; "
        "one of the two readers above is out of step with endpoints.ts"
    )


# --------------------------------------------------------------------------
# The console asks for something the API does not serve
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(declarations()))
def test_every_declared_path_is_registered(name: str) -> None:
    path = declarations()[name]
    if path in registered():
        return
    assert _serves_a_family_below(name, path) and name not in call_sites(), (
        f"paths.{name} is {path}, which the API does not register. Every request the "
        "console sends there returns NOT_FOUND, and the page renders that like any "
        "other failure - so add the route, or delete the declaration."
    )


@pytest.mark.parametrize("name", sorted(call_sites()))
def test_every_call_site_uses_a_method_the_route_accepts(name: str) -> None:
    """The half a path check cannot see.

    ``/admin/backup`` was registered for POST and the console listed archives
    with a GET. The path matched, so nothing here or in the router objected; the
    request came back 405 and the list stayed empty.
    """
    path = declarations().get(name)
    # Reported in full by test_every_call_site_names_a_declaration_that_exists.
    assert path is not None, f"no declaration named paths.{name}"
    accepted = registered().get(path, frozenset())
    used = call_sites()[name]
    assert used <= accepted, (
        f"the console sends {sorted(used - accepted)} to paths.{name} ({path}), which "
        f"accepts {sorted(accepted) or 'nothing - it is not registered at all'}"
    )


# --------------------------------------------------------------------------
# The API serves something the console declares and never asks for
#
# The other direction is quieter still: a declaration nothing calls costs
# nothing and breaks nothing, right up until someone wires it into a page and
# finds out it was never a real path.
# --------------------------------------------------------------------------

#: Declarations kept for the reader rather than for a call site. Both are the
#: container's probes: docker/compose.yml polls /readyz in its healthcheck and
#: docker/README.md documents /healthz, and this file is where an operator
#: expects to find them written down. Both are registered, so neither is the
#: 404-in-waiting the check below is about.
DECLARED_FOR_OPERATORS = frozenset({"system.health", "system.ready"})


def test_no_declaration_is_dead() -> None:
    dead = sorted(set(declarations()) - referenced() - DECLARED_FOR_OPERATORS)
    assert not dead, (
        f"nothing in web/src refers to {dead}. A declaration nothing calls is not "
        "checked by anything either, so it rots into a path that 404s the day it is "
        "wired up: call it, or delete it from endpoints.ts."
    )


@pytest.mark.parametrize("name", sorted(DECLARED_FOR_OPERATORS))
def test_the_operator_declarations_have_not_quietly_become_ordinary(name: str) -> None:
    """An exception that stops being one has to leave, or it shelters the next."""
    assert name in declarations(), f"{name} is no longer declared; drop it from this list"
    assert name not in referenced(), (
        f"paths.{name} now has a consumer in web/src, so it no longer needs an "
        "exception from test_no_declaration_is_dead; remove it from DECLARED_FOR_OPERATORS"
    )


# --------------------------------------------------------------------------
# The document has to say how to authenticate
#
# The spec declared no securitySchemes at all, so Swagger UI showed no
# Authorize button, there was nowhere to put an API key, and every "Try it out"
# against a real endpoint answered 401 - on a service whose whole purpose is to
# be called by someone else's program.
# --------------------------------------------------------------------------


def test_the_document_says_how_to_authenticate() -> None:
    from dtk.api.deps import API_KEY_HEADER, SESSION_COOKIE

    schema = _app().openapi()
    schemes = (schema.get("components") or {}).get("securitySchemes") or {}
    assert schemes, "no securitySchemes: the docs page cannot offer an Authorize button"

    by_name = {s.get("name") for s in schemes.values()}
    assert API_KEY_HEADER in by_name, (
        f"the document does not advertise {API_KEY_HEADER}, which is the header "
        "dtk.api.deps actually reads"
    )
    assert SESSION_COOKIE in by_name


def test_every_guarded_route_declares_its_security() -> None:
    """A route with no security reads as public, and most of these are not."""
    schema = _app().openapi()
    public = {"/api/setup/status", "/api/setup/init", "/api/v1/auth/login"}
    missing = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        if path not in public
        for method, operation in operations.items()
        if isinstance(operation, dict) and not operation.get("security")
    ]
    assert not missing, f"routes that document no way to authenticate: {missing[:6]}"


def test_the_scraping_endpoints_are_translated() -> None:
    """The reason they did not look like scraping endpoints in the console.

    All seven were in the document and six carried an English summary in the
    Chinese one, so a reader scanning the Chinese document for the scraping endpoints
    saw English labels among translated ones and read them as something else.
    """
    from dtk.api.routes.openapi import build_schema
    from dtk.core.types import Language

    schema = build_schema(_app(), Language.ZH)
    untranslated = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
        and "content" in (operation.get("tags") or [])
        and operation.get("summary", "").isascii()
    ]
    assert not untranslated, f"content endpoints still in English under zh: {untranslated}"
