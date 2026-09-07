"""Turning failures into sentences an agent can act on.

A tool error is read by a model, not by a debugger. "endpoint
douyin.author_posts is currently tripped; last success was 2 hours ago" lets an
agent change strategy - wait, or ask for something else. A traceback lets it do
nothing but repeat itself, and most agents will repeat themselves until their
budget is gone (docs/design/06-api-auth-mcp.md).

Every message therefore says three things: what happened, whether retrying can
possibly help, and what state the system is in right now. Retryability is read
from :data:`dtk.core.errors.NON_RETRYABLE` so the prose can never drift from
what the REST layer tells its callers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from dtk.core.errors import NON_RETRYABLE, DtkError, ErrorCode
from dtk.core.types import Language
from dtk.i18n.format import format_relative_time
from dtk.i18n.messages import render
from dtk.mcp.context import EndpointHealth, PoolSnapshot

#: Agents read English. The bilingual requirement in docs/design/14-i18n.md
#: covers the console and the REST error messages, both of which have a human on
#: the other end who chose a language; an MCP client never sends one.
TOOL_LANGUAGE = Language.EN


#: A first word that is an identifier rather than prose: an endpoint name, a
#: parameter name, a platform key. Capitalizing one produces
#: "Douyin.author_profile", which no longer matches the endpoint the same
#: message names two sentences later, and which an agent may echo back.
_IDENTIFIER_HEAD = re.compile(r"^[a-z][a-z0-9]*[._][a-z0-9]")


def _sentence(text: str | None) -> str:
    """Normalize a raw message into one sentence, or nothing."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if not _IDENTIFIER_HEAD.match(stripped):
        stripped = stripped[0].upper() + stripped[1:]
    return stripped if stripped[-1] in ".!?" else stripped + "."


def _retry_sentence(code: ErrorCode, retry_after: int | None) -> str:
    if code is ErrorCode.UPSTREAM_CHANGED:
        return (
            "Retrying will not help: this is a parser bug in dtk, not something a "
            "different request can work around."
        )
    if code in NON_RETRYABLE:
        return "Retrying will not help; the request has to change."
    if retry_after:
        return f"This is retryable: back off for about {retry_after} seconds, then try again."
    return "This is retryable: back off briefly, then try again."


def describe_health(health: EndpointHealth | None, *, now: datetime | None = None) -> str:
    """One sentence about an endpoint's circuit state and its last success."""
    if health is None:
        return ""
    reference = now or datetime.now(UTC)
    if health.last_success_at is not None:
        ago = format_relative_time(health.last_success_at, TOOL_LANGUAGE, now=reference)
        last = f"last success was {ago}"
    else:
        last = "no successful call has been recorded for it yet"

    if not health.circuit_open:
        return f"Endpoint {health.endpoint} is not tripped; {last}."

    reason = f" Reason: {health.reason}." if health.reason else ""
    retry = (
        f" It reopens in about {health.retry_after_seconds} seconds."
        if health.retry_after_seconds
        else ""
    )
    return f"Endpoint {health.endpoint} is currently tripped; {last}.{reason}{retry}"


def describe(
    code: ErrorCode,
    message: str | None = None,
    *,
    retry_after: int | None = None,
    details: dict[str, Any] | None = None,
    endpoint: str | None = None,
    health: EndpointHealth | None = None,
    now: datetime | None = None,
) -> str:
    """Render one failure as prose, never as a traceback.

    The specific message leads when there is one: "listing TikTok posts needs
    the author's secUid" is worth more to an agent than the generic sentence for
    INVALID_PARAM. The localized catalogue fills in when the failure carried no
    message of its own.
    """
    args: dict[str, Any] = dict(details or {})
    if retry_after is not None:
        args["retry_after"] = retry_after

    parts = [_sentence(message) or render(code, TOOL_LANGUAGE, **args)]
    if endpoint and health is None:
        parts.append(f"This was the {endpoint} endpoint.")
    parts.append(describe_health(health, now=now))
    parts.append(_retry_sentence(code, retry_after))
    parts.append(f"(error code {code.value})")
    return " ".join(part for part in parts if part)


def describe_error(
    error: DtkError,
    *,
    endpoint: str | None = None,
    health: EndpointHealth | None = None,
    now: datetime | None = None,
) -> str:
    """Prose for a raised domain error."""
    return describe(
        error.code,
        error.raw_message,
        retry_after=error.retry_after,
        details=error.details or None,
        endpoint=endpoint,
        health=health,
        now=now,
    )


def describe_task_error(
    payload: dict[str, Any] | None,
    *,
    endpoint: str | None = None,
    health: EndpointHealth | None = None,
    now: datetime | None = None,
) -> str:
    """Prose for the ``error`` object a failed task recorded.

    The worker stores the envelope's error shape, so the code survives the trip
    through the queue and the wording stays identical to the direct path.
    """
    body = payload or {}
    try:
        code = ErrorCode(str(body.get("code")))
    except ValueError:
        code = ErrorCode.INTERNAL
    raw_retry = body.get("retry_after")
    retry_after = (
        raw_retry if isinstance(raw_retry, int) and not isinstance(raw_retry, bool) else None
    )
    message = body.get("message")
    details = body.get("details")
    return describe(
        code,
        str(message) if message else None,
        retry_after=retry_after,
        details=details if isinstance(details, dict) else None,
        endpoint=endpoint,
        health=health,
        now=now,
    )


def summarize_pool(snapshot: PoolSnapshot) -> str:
    """A compact, credential-free description of the current pool state."""
    if not snapshot.identities:
        parts = ["no identities are registered"]
    else:
        parts = []
        for platform in sorted(snapshot.identities):
            states = snapshot.identities[platform]
            if not states:
                parts.append(f"{platform}: none")
                continue
            detail = ", ".join(f"{count} {state}" for state, count in sorted(states.items()))
            parts.append(f"{platform}: {detail}")
    text = "Identity pool - " + "; ".join(parts) + "."

    tripped = snapshot.tripped()
    if tripped:
        names = ", ".join(sorted(entry.endpoint for entry in tripped))
        text += f" Tripped endpoints: {names}."
    elif snapshot.endpoints:
        text += " No endpoint is tripped."
    return text


def timeout_message(
    task_id: str,
    seconds: float,
    snapshot: PoolSnapshot | None,
    *,
    endpoint: str | None = None,
) -> str:
    """What a tool returns when the work outlived the tool call.

    It names the task id and the pool state instead of raising, because the call
    did not fail - the work is simply still running, and the agent can decide
    whether the answer is still worth collecting.
    """
    where = f" for {endpoint}" if endpoint else ""
    lines = [
        f"The request{where} is still running after {int(seconds)} seconds, so this tool "
        "call returned before it finished. Nothing was lost: the work continues in the "
        f"background as task {task_id}.",
        f"Call get_task_result with task_id={task_id} in a little while to collect it.",
    ]
    if snapshot is not None:
        lines.append(summarize_pool(snapshot))
        if snapshot.usable() == 0:
            lines.append(
                "No identity is currently active, which is the usual reason for this; "
                "the pool refills on its own."
            )
    return " ".join(lines)


def expired_result_message(task_id: str, endpoint: str | None = None) -> str:
    """A task that succeeded, but whose payload has already been evicted.

    Not routed through :func:`describe`: TASK_NOT_FOUND is retryable in general,
    and here it is not - the payload is gone for good, and waiting brings it
    back no more than repeating the lookup does. The one thing that works is
    asking for the data again, so that is what the sentence says.
    """
    where = f" ({endpoint})" if endpoint else ""
    return (
        f"Task {task_id}{where} finished successfully, but its result is past the "
        "retention window and is no longer stored. Waiting or asking again will not "
        "recover it: call the tool that produced this task id once more to fetch the "
        f"data fresh. (error code {ErrorCode.TASK_NOT_FOUND.value})"
    )


def unfinished_task_message(task_id: str, state: str, snapshot: PoolSnapshot | None) -> str:
    """Answer for ``get_task_result`` on a task that has not settled yet."""
    text = (
        f"Task {task_id} is {state}; it has not produced a result yet. Wait a little and ask again."
    )
    if snapshot is not None:
        text += " " + summarize_pool(snapshot)
    return text


__all__ = [
    "TOOL_LANGUAGE",
    "describe",
    "describe_error",
    "describe_health",
    "describe_task_error",
    "expired_result_message",
    "summarize_pool",
    "timeout_message",
    "unfinished_task_message",
]
