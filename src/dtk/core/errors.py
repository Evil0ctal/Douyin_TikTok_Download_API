"""Error codes and the exception hierarchy.

Error codes are a stable, machine-readable contract: append only, never rename.
Messages are localized at the boundary; codes never are.
See docs/design/11-data-contracts.md and docs/design/14-i18n.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_URL = "INVALID_URL"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    INVALID_PARAM = "INVALID_PARAM"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN_SCOPE = "FORBIDDEN_SCOPE"
    NOT_FOUND = "NOT_FOUND"
    CONTENT_PRIVATE = "CONTENT_PRIVATE"
    RATE_LIMITED = "RATE_LIMITED"
    IDENTITY_POOL_EXHAUSTED = "IDENTITY_POOL_EXHAUSTED"
    ENDPOINT_CIRCUIT_OPEN = "ENDPOINT_CIRCUIT_OPEN"
    UPSTREAM_RISK_CONTROL = "UPSTREAM_RISK_CONTROL"
    UPSTREAM_CHANGED = "UPSTREAM_CHANGED"
    SIGNING_FAILED = "SIGNING_FAILED"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    SETUP_ALREADY_DONE = "SETUP_ALREADY_DONE"
    SETUP_TOKEN_INVALID = "SETUP_TOKEN_INVALID"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    QUEUE_FULL = "QUEUE_FULL"
    #: The work was given up on deliberately - a task cancelled by its caller.
    #: Its own code because it used to be reported as INVALID_PARAM, which told
    #: the caller their perfectly valid request was malformed, and which is in
    #: NON_RETRYABLE - so an agent reading the result learned never to try that
    #: URL again.
    CANCELLED = "CANCELLED"
    #: The method is not allowed on this path. Answered by the framework, and
    #: previously laundered into INTERNAL, whose own contract tells the caller
    #: to file a bug quoting a request id that is never logged.
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    #: The request body arrived in a media type this endpoint does not read.
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    INTERNAL = "INTERNAL"


#: HTTP status for each code. Kept beside the enum so the API layer never
#: invents its own mapping.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_URL: 400,
    ErrorCode.UNSUPPORTED_CONTENT: 400,
    ErrorCode.INVALID_PARAM: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN_SCOPE: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONTENT_PRIVATE: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.IDENTITY_POOL_EXHAUSTED: 503,
    ErrorCode.ENDPOINT_CIRCUIT_OPEN: 503,
    ErrorCode.UPSTREAM_RISK_CONTROL: 502,
    ErrorCode.UPSTREAM_CHANGED: 502,
    ErrorCode.SIGNING_FAILED: 502,
    ErrorCode.TASK_NOT_FOUND: 404,
    ErrorCode.SETUP_ALREADY_DONE: 409,
    ErrorCode.SETUP_TOKEN_INVALID: 403,
    ErrorCode.NOT_CONFIGURED: 501,
    ErrorCode.QUEUE_FULL: 503,
    ErrorCode.CANCELLED: 409,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.INTERNAL: 500,
}

#: Codes a caller should never retry. Surfaced in docs and in MCP tool errors so
#: an agent does not burn its budget looping on a permanent failure.
NON_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.INVALID_URL,
        ErrorCode.UNSUPPORTED_CONTENT,
        ErrorCode.INVALID_PARAM,
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN_SCOPE,
        ErrorCode.NOT_FOUND,
        ErrorCode.CONTENT_PRIVATE,
        ErrorCode.UPSTREAM_CHANGED,
        ErrorCode.SETUP_ALREADY_DONE,
        ErrorCode.SETUP_TOKEN_INVALID,
        # Somebody decided to stop this. Retrying is not a fix, it is ignoring
        # them.
        ErrorCode.CANCELLED,
        # A different method might work; this one never will.
        ErrorCode.METHOD_NOT_ALLOWED,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        # A capability this deployment simply does not have. Retrying is
        # never the answer: no amount of waiting installs a browser
        # container or writes an alert channel into the settings.
        ErrorCode.NOT_CONFIGURED,
    }
)


class DtkError(Exception):
    """Base for every error that maps onto the API envelope."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.code.value)
        self.raw_message = message
        self.retry_after = retry_after
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]

    @property
    def retryable(self) -> bool:
        return self.code not in NON_RETRYABLE

    def format_args(self) -> dict[str, Any]:
        """Values interpolated into the localized message template."""
        args: dict[str, Any] = dict(self.details)
        if self.retry_after is not None:
            args["retry_after"] = self.retry_after
        return args


def _err(name: str, code: ErrorCode) -> type[DtkError]:
    return type(name, (DtkError,), {"code": code})


InvalidUrl = _err("InvalidUrl", ErrorCode.INVALID_URL)
UnsupportedContent = _err("UnsupportedContent", ErrorCode.UNSUPPORTED_CONTENT)
InvalidParam = _err("InvalidParam", ErrorCode.INVALID_PARAM)
Unauthenticated = _err("Unauthenticated", ErrorCode.UNAUTHENTICATED)
ForbiddenScope = _err("ForbiddenScope", ErrorCode.FORBIDDEN_SCOPE)
NotFound = _err("NotFound", ErrorCode.NOT_FOUND)
ContentPrivate = _err("ContentPrivate", ErrorCode.CONTENT_PRIVATE)
RateLimited = _err("RateLimited", ErrorCode.RATE_LIMITED)
IdentityPoolExhausted = _err("IdentityPoolExhausted", ErrorCode.IDENTITY_POOL_EXHAUSTED)
EndpointCircuitOpen = _err("EndpointCircuitOpen", ErrorCode.ENDPOINT_CIRCUIT_OPEN)
UpstreamRiskControl = _err("UpstreamRiskControl", ErrorCode.UPSTREAM_RISK_CONTROL)
SigningFailed = _err("SigningFailed", ErrorCode.SIGNING_FAILED)
TaskNotFound = _err("TaskNotFound", ErrorCode.TASK_NOT_FOUND)
SetupAlreadyDone = _err("SetupAlreadyDone", ErrorCode.SETUP_ALREADY_DONE)
SetupTokenInvalid = _err("SetupTokenInvalid", ErrorCode.SETUP_TOKEN_INVALID)
Internal = _err("Internal", ErrorCode.INTERNAL)
#: An optional component was never set up. Distinct from Internal, which
#: promises the caller that trying again might work.
NotConfigured = _err("NotConfigured", ErrorCode.NOT_CONFIGURED)
#: The task queue is at its configured ceiling. Retryable by design: the
#: caller is told when to come back rather than being queued behind work
#: that will not finish in time to matter.
QueueFull = _err("QueueFull", ErrorCode.QUEUE_FULL)
#: Given up on deliberately, by whoever asked for it.
Cancelled = _err("Cancelled", ErrorCode.CANCELLED)


class UpstreamChanged(DtkError):
    """The platform response no longer matches what the parser expects.

    Carries the field path that went missing so the bug report is actionable.
    Parsers must raise this instead of silently returning a half-filled model.
    """

    code = ErrorCode.UPSTREAM_CHANGED

    def __init__(self, path: str, *, platform: str | None = None) -> None:
        super().__init__(f"missing or unexpected field: {path}", details={"path": path})
        self.path = path
        self.platform = platform


__all__ = [
    "HTTP_STATUS",
    "NON_RETRYABLE",
    "Cancelled",
    "ContentPrivate",
    "DtkError",
    "EndpointCircuitOpen",
    "ErrorCode",
    "ForbiddenScope",
    "IdentityPoolExhausted",
    "Internal",
    "InvalidParam",
    "InvalidUrl",
    "NotConfigured",
    "NotFound",
    "QueueFull",
    "RateLimited",
    "SetupAlreadyDone",
    "SetupTokenInvalid",
    "SigningFailed",
    "TaskNotFound",
    "Unauthenticated",
    "UnsupportedContent",
    "UpstreamChanged",
    "UpstreamRiskControl",
]
