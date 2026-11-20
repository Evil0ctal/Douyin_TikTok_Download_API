"""Error taxonomy for the RPC surface.

The callers in `dtk.identity.minting.client` and `dtk.signing.rpc` treat any
non-2xx reply as "browser-rpc is unavailable" and degrade: minting stops and the
pool falls back to imported cookies, signing falls back to the native algorithm.
The status code therefore does not change what the caller does - it changes what
the operator reads in the logs, which is why each failure mode gets its own code
instead of a blanket 500. One exception: `UserAgentMismatch` is not this service
failing, and `dtk.signing.registry` signs natively when it sees one.
"""

from __future__ import annotations

from enum import StrEnum


class Suspect(StrEnum):
    """What two pages that failed the same way have in common, by the failure's shape.

    The service opens a fresh page for its second attempt, so the same failure
    twice is evidence about something the two pages share. They share more than
    one thing - the exit they load through, the platform's bundle they load, the
    browser they run in - and which of those a failure implicates depends on
    the failure. Naming the exit for all of them sent an operator to the proxy
    when the platform had stopped signing (the review of #830's fix).
    """

    #: A navigation failed, or the page left the platform: what the page loaded
    #: through is in question.
    EXIT = "exit"
    #: The platform's SDK loaded and never signed, or never loaded: the bundle,
    #: or the exit it is fetched through.
    BUNDLE = "bundle"
    #: The renderer died: memory or /tmp in the browser container.
    BROWSER = "browser"


class RpcError(Exception):
    """Base for every failure that has a defined HTTP shape."""

    status_code: int = 500
    code: str = "internal"

    def __init__(
        self, message: str, *, detail: str | None = None, suspect: Suspect | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        #: Never sent on the wire; it only decides how the service words a
        #: failure that repeated. None when the shape points nowhere in particular.
        self.suspect = suspect

    def as_body(self) -> dict[str, object]:
        body: dict[str, object] = {"error": {"code": self.code, "message": self.message}}
        if self.detail:
            error = body["error"]
            assert isinstance(error, dict)
            error["detail"] = self.detail
        return body


class InvalidRequest(RpcError):
    """The request did not pass validation: unknown platform, bad URL, bad proxy."""

    status_code = 400
    code = "invalid_request"


class BackendUnavailable(RpcError):
    """The browser backend is not running, or not usable in this image."""

    status_code = 503
    code = "backend_unavailable"


class BackendFailure(RpcError):
    """The browser ran and failed: navigation error, no cookies, no signature."""

    status_code = 502
    code = "backend_failed"


class UserAgentMismatch(RpcError):
    """The caller's identity runs a User-Agent this browser's pages do not.

    A conflict between the caller and this browser, not a failure of either:
    the page signs under its own ``navigator.userAgent`` whatever it is asked,
    and TikTok refuses a request whose header names another one (#832,
    measured 2026-11-20). So it is refused before a page is spent on it, and
    never retried on a second page that would answer the same. On Douyin only
    a different operating system is refused, which is all Douyin was measured
    to refuse - see `browser_rpc.service.SYSTEM_BOUND_PLATFORMS`.
    """

    status_code = 409
    code = "user_agent_mismatch"


class OperationTimeout(RpcError):
    """The browser did not finish inside the service-side budget.

    Deliberately shorter than the client timeout, so the caller reads a reply
    that says what happened rather than hitting its own deadline.
    """

    status_code = 504
    code = "timeout"


class ConfigError(RuntimeError):
    """The process is misconfigured and must not start."""


__all__ = [
    "BackendFailure",
    "BackendUnavailable",
    "ConfigError",
    "InvalidRequest",
    "OperationTimeout",
    "RpcError",
    "Suspect",
    "UserAgentMismatch",
]
