"""Error taxonomy for the RPC surface.

The callers in `dtk.identity.minting.client` and `dtk.signing.rpc` treat any
non-2xx reply as "browser-rpc is unavailable" and degrade: minting stops and the
pool falls back to imported cookies, signing falls back to the native algorithm.
The status code therefore does not change what the caller does - it changes what
the operator reads in the logs, which is why each failure mode gets its own code
instead of a blanket 500.
"""

from __future__ import annotations


class RpcError(Exception):
    """Base for every failure that has a defined HTTP shape."""

    status_code: int = 500
    code: str = "internal"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

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
]
