"""The uniform response envelope.

Every response, success or failure, has the same four top-level keys. Error
codes are a stable enum so a caller can branch on them; the human-readable
message beside the code is localized and must never be parsed.

See docs/design/06-api-auth-mcp.md and docs/design/11-data-contracts.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from dtk.core.errors import HTTP_STATUS, DtkError, ErrorCode
from dtk.core.types import Language
from dtk.i18n.messages import render


def _meta(
    request_id: uuid.UUID | str,
    *,
    cached: bool | None = None,
    duration_ms: int | None = None,
    cursor: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"request_id": str(request_id)}
    if cached is not None:
        meta["cached"] = cached
    if duration_ms is not None:
        meta["duration_ms"] = duration_ms
    if cursor is not None:
        meta["cursor"] = cursor
    if extra:
        meta.update(extra)
    return meta


def success(
    data: Any,
    request_id: uuid.UUID | str,
    *,
    cached: bool | None = None,
    duration_ms: int | None = None,
    cursor: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "data": data,
            "error": None,
            "meta": _meta(
                request_id, cached=cached, duration_ms=duration_ms, cursor=cursor, extra=extra
            ),
        },
    )


def failure(
    code: ErrorCode,
    request_id: uuid.UUID | str,
    *,
    language: Language = Language.EN,
    retry_after: int | None = None,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    args = dict(details or {})
    if retry_after is not None:
        args["retry_after"] = retry_after

    error: dict[str, Any] = {
        "code": code.value,
        "message": render(code, language, **args),
    }
    if retry_after is not None:
        error["retry_after"] = retry_after
    if details:
        error["details"] = details

    status = status_code or HTTP_STATUS.get(code, 500)
    # Whatever the framework attached, plus Retry-After when this error carries
    # one. `Allow` on a 405 is the case that made this matter: RFC 9110 makes it
    # a MUST, and rebuilding the response here dropped it.
    outgoing: dict[str, str] = dict(headers or {})
    if retry_after:
        outgoing["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "data": None,
            "error": error,
            "meta": _meta(request_id),
        },
        headers=outgoing or None,
    )


def from_exception(request: Request, exc: DtkError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid.uuid4())
    language = getattr(request.state, "language", Language.EN)
    return failure(
        exc.code,
        request_id,
        language=language,
        retry_after=exc.retry_after,
        details=exc.details or None,
    )


__all__ = ["failure", "from_exception", "success"]
