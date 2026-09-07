"""Mapping tool arguments onto task endpoints and parameters.

The tools speak one vocabulary - ``platform``, ``content_id``, ``uid``,
``cursor``, ``count`` - because an agent picking a tool should not have to know
that a post is an ``aweme_id`` on Douyin and an ``item_id`` on TikTok.

That translation is not repeated here. :mod:`dtk.worker.registry` is the shared
endpoint table used by the worker, REST, the CLI and this package alike, so a
task submitted from a tool is validated against exactly the definition the
worker will execute it with. Duplicating the mapping is how V4's entry points
drifted apart; the only thing this module adds is the small amount of judgement
that is specific to being called by an agent - deciding whether a user
identifier is a stable id or a handle, and refusing a handle where it cannot
work with a sentence that says what to do instead.

The task ``endpoint`` written into the queue is the platform endpoint name
(``douyin.content_detail``), which is the stable operational key the scheduler,
the circuit breaker and the health board already use. The one exception is
:data:`PARSE_ENDPOINT`, the logical endpoint for "resolve this link, then fetch
whatever it turned out to be": a short link cannot be classified without
following it, and following it needs the identity's proxy.
"""

from __future__ import annotations

from typing import Any, Final

from dtk.core.errors import InvalidParam, UnsupportedContent
from dtk.core.types import Platform
from dtk.platforms.registry import available_platforms
from dtk.urls import ResourceKind, UrlKind
from dtk.worker.registry import (
    AUTHOR_ID,
    CONTENT_ID,
    COUNT,
    CURSOR,
    UNIQUE_ID,
    Capability,
    EndpointDefinition,
    definition_for,
    endpoint_name,
    endpoint_names,
)

#: Logical endpoint for the URL entry point, matching what ``POST /api/v1/parse``
#: submits. Its parameters are ``{"url": ...}`` and the worker decides the rest.
PARSE_ENDPOINT: Final = "parse"

#: Both platforms prefix their stable, non-rotating user key with this (Douyin
#: ``sec_user_id``, TikTok ``secUid``). Anything else a caller passes as a uid is
#: treated as a handle.
SEC_UID_PREFIX: Final = "MS4wLjAB"

#: Page size bounds. The platforms silently cap larger values, so accepting one
#: would return fewer items than the agent asked for without saying so.
MIN_COUNT: Final = 1
MAX_COUNT: Final = 50

#: Recognized resource kinds this tool set has no endpoint for. A short link is
#: absent on purpose: what it points at is unknown until it has been followed.
_UNFETCHABLE: Final[frozenset[ResourceKind]] = frozenset(
    {
        ResourceKind.LIVE,
        ResourceKind.LIVE_ROOM,
        ResourceKind.MIX,
        ResourceKind.MUSIC,
        ResourceKind.CHALLENGE,
        ResourceKind.SEARCH,
    }
)


def known_endpoints() -> tuple[str, ...]:
    """Every endpoint name the shared registry knows about."""
    return endpoint_names()


def coerce_platform(value: str) -> Platform:
    """Turn the caller's platform string into the enum, or explain the options."""
    text = (value or "").strip().lower()
    try:
        return Platform(text)
    except ValueError:
        raise InvalidParam(
            f"unknown platform {value!r}",
            details={"platform": value, "supported": list(available_platforms())},
        ) from None


def require_text(value: str | None, field: str) -> str:
    """Reject blank identifiers before they become an upstream round trip."""
    text = (value or "").strip()
    if not text:
        raise InvalidParam(f"{field} must not be empty", details={"field": field})
    return text


def coerce_count(value: int | None) -> int | None:
    """Validate the page size. ``None`` leaves the platform default in place."""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidParam("count must be an integer", details={"field": "count"})
    if value < MIN_COUNT or value > MAX_COUNT:
        raise InvalidParam(
            f"count must be between {MIN_COUNT} and {MAX_COUNT}",
            details={"field": "count", "min": MIN_COUNT, "max": MAX_COUNT},
        )
    return value


def looks_like_sec_uid(value: str) -> bool:
    return value.startswith(SEC_UID_PREFIX)


def definition(platform: Platform, capability: Capability) -> EndpointDefinition:
    """The registry entry for one capability on one platform.

    Raises :class:`UnsupportedContent` when a platform does not offer it, so a
    capability gap reads as a capability gap rather than as a crash.
    """
    name = endpoint_name(platform, capability)
    try:
        return definition_for(name)
    except InvalidParam:
        raise UnsupportedContent(
            f"{platform.value} does not support {capability.value.replace('_', ' ')}",
            details={"platform": platform.value, "capability": capability.value},
        ) from None


def _handle_advice(spec: EndpointDefinition) -> str:
    """What an agent should actually do next, given which call refused.

    The advice has to depend on the endpoint. Telling the caller of the profile
    endpoint to "call get_user first" is circular - get_user *is* that endpoint,
    and on Douyin no tool turns a handle into an id at all - so a profile call
    is pointed at the one entry point that does resolve a handle: a profile link
    through parse_url, where the worker follows the URL.
    """
    if spec.capability is Capability.AUTHOR_PROFILE:
        return "pass a profile link to parse_url instead - it resolves the link to that id"
    return "call get_user with the handle first and pass the uid from its result"


def _author_identifier(spec: EndpointDefinition, value: str) -> dict[str, Any]:
    """Decide whether a user identifier is the stable id or a handle.

    A secUid is unambiguous. Anything else is a handle, which only some
    endpoints accept: TikTok's user-detail call takes one, its post list does
    not. Where it cannot work, saying so - and naming a next step that exists -
    is worth more to an agent than an empty page.
    """
    if looks_like_sec_uid(value):
        return {AUTHOR_ID: value}
    if UNIQUE_ID in spec.accepts:
        return {UNIQUE_ID: value.lstrip("@")}
    raise InvalidParam(
        f"{spec.name} needs the author's stable id (it starts with '{SEC_UID_PREFIX}'), "
        f"not the @handle; {_handle_advice(spec)}",
        details={"field": "uid", "endpoint": spec.name},
    )


def build_task(
    platform: Platform,
    capability: Capability,
    identifier: str,
    *,
    cursor: str | None = None,
    count: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return ``(endpoint, params)`` ready to be queued as a task.

    The parameters are the registry's canonical names, validated against the
    same definition the worker resolves them with; translation into a platform's
    own argument spelling happens there, once.
    """
    spec = definition(platform, capability)

    params: dict[str, Any] = (
        _author_identifier(spec, identifier)
        if AUTHOR_ID in spec.accepts
        else {CONTENT_ID: identifier}
    )
    if cursor is not None and cursor.strip():
        params[CURSOR] = cursor.strip()
    checked = coerce_count(count)
    if checked is not None:
        params[COUNT] = checked

    # Raises INVALID_PARAM for a missing or unsupported argument, which costs a
    # sentence rather than an identity's quota.
    spec.platform_params(params)
    return spec.name, params


def route_url(kind: UrlKind) -> tuple[str, dict[str, Any]]:
    """Turn a classified URL into the task that answers it.

    Everything recognized goes to :data:`PARSE_ENDPOINT`, exactly as
    ``POST /api/v1/parse`` does: the worker owns short-link expansion and the
    choice of endpoint, so a link behaves identically whichever entry point it
    arrives through. What is refused here is only what no fetch could fix: a
    resource type this tool set has no endpoint for, and a URL on one of our
    hosts that points at no resource at all. A short link is neither - it is
    recognized, and what it points at is settled by following it.
    """
    if kind.url is None or kind.platform is None:
        raise InvalidParam("the URL was not recognized", details={"url": kind.original})

    if not kind.recognized:
        # An allowed host whose path matches no route: a feed, a search page, a
        # settings screen. Queueing it would spend an identity's quota to
        # discover that there is nothing to fetch, and a second attempt would
        # spend another one.
        raise UnsupportedContent(
            "that link is on a supported platform but does not point at a post or a "
            "user profile, which is all this tool set fetches",
            details={"resource": kind.resource.value, "url": kind.url},
        )

    if kind.resource in _UNFETCHABLE:
        raise UnsupportedContent(
            f"that link points at a {kind.resource.value.replace('_', ' ')}, which this "
            "tool set does not cover; it handles posts and user profiles",
            details={"resource": kind.resource.value, "url": kind.url},
        )
    return PARSE_ENDPOINT, {"url": kind.url}


__all__ = [
    "MAX_COUNT",
    "MIN_COUNT",
    "PARSE_ENDPOINT",
    "SEC_UID_PREFIX",
    "Capability",
    "build_task",
    "coerce_count",
    "coerce_platform",
    "definition",
    "known_endpoints",
    "looks_like_sec_uid",
    "require_text",
    "route_url",
]
