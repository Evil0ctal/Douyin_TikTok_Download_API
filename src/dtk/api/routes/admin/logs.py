"""The request log, read by the console's Logs page and by the playground.

Kept out of ``maintenance.py`` because it is a different kind of read. The
audit trail there is a small relational table that answers "who changed what";
this reads ``request_log``, the hypertable every derived number in the system
comes from, which grows with traffic and is trimmed by a retention policy
rather than by anyone's judgement (doc 05).

That difference is the whole design of this module. An unbounded read of
``request_log`` is the one query on this API that can stall an instance, so the
window and the row count are both mandatory here and neither has an "all"
setting. The response is the same bare-list envelope ``GET /admin/audit``
returns, so the page's two tabs render from one shape.

Nothing a row carries is a credential. ``dtk.services.fetch`` logs the logical
endpoint name it scheduled on, never the signed URL it built from it - the two
are kept apart at ``_to_transport_spec`` precisely because the signature
carries tokens (doc 08) - and the identity and proxy appear only as ids.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from dtk.api.deps import Principal
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import (
    DEFAULT_ADMIN_PAGE_SIZE,
    MAX_ADMIN_PAGE_SIZE,
    iso,
    ok,
    read_admin_demo,
)
from dtk.core.types import Outcome
from dtk.db.models import RequestLog
from dtk.db.timeseries import RequestLogRepository

# Tagged by the aggregate router in __init__.py; repeating it here is what
# put ["admin", "admin"] on every one of these operations.
router = APIRouter()

#: Widest window the console offers (its 30-day option). Retention deletes
#: request_log rows after 14 days, so asking for more is allowed and simply
#: returns less: this is a ceiling on the scan, not a promise about history.
MAX_MINUTES = 60 * 24 * 30
#: The console's own default, and what the playground gets by not sending one -
#: it looks up the request it has just made.
DEFAULT_MINUTES = 60


def _row(row: RequestLog) -> dict[str, Any]:
    """One log line, in the shape the console's table and drawer expect.

    Every field is named rather than dumped from the model. The drawer prints
    this object verbatim, so a column added to the table later should reach the
    console through a deliberate edit here; ``api_key_id`` is stored but not
    rendered and stays out for that reason.
    """
    return {
        "ts": iso(row.ts),
        "request_id": str(row.request_id),
        "task_id": str(row.task_id) if row.task_id else None,
        "platform": row.platform,
        "endpoint": row.endpoint,
        "identity_id": str(row.identity_id) if row.identity_id else None,
        "proxy_id": str(row.proxy_id) if row.proxy_id else None,
        "outcome": row.outcome,
        "http_status": row.http_status,
        "duration_ms": row.duration_ms,
        "cache_hit": row.cache_hit,
        "signer": row.signer,
        "error_code": row.error_code,
        # The wire code, not a sentence: the console has a translation for each
        # RejectReason and shows an unknown one verbatim, which is what a user
        # should be quoting in an issue anyway.
        "reject_reason": row.reject_reason,
    }


@router.get(
    "/logs/requests", summary="Read the request log", openapi_extra={I18N_KEY: "request_log"}
)
async def list_request_log(
    request: Request,
    request_id: uuid.UUID | None = Query(
        default=None, description="Only the request with this id."
    ),
    endpoint: str | None = Query(
        default=None, max_length=128, description="Only requests to this endpoint."
    ),
    identity_id: uuid.UUID | None = Query(
        default=None, description="Only requests made with this identity."
    ),
    outcome: list[Outcome] | None = Query(
        default=None, description="Only these outcomes. Repeat the parameter to allow several."
    ),
    minutes: int = Query(
        default=DEFAULT_MINUTES,
        ge=1,
        le=MAX_MINUTES,
        description="How far back to look. Rows older than the log's retention are gone.",
    ),
    limit: int = Query(
        default=DEFAULT_ADMIN_PAGE_SIZE,
        ge=1,
        le=MAX_ADMIN_PAGE_SIZE,
        description="Maximum rows to return.",
    ),
    principal: Principal = Depends(read_admin_demo),
) -> Any:
    """Recent requests, newest first, filtered.

    ``minutes`` and ``limit`` are both bounded by FastAPI before the handler
    runs, so there is no combination of parameters that reaches the hypertable
    without a window: a caller asking for a million rows is rejected rather
    than served a million rows or silently given a hundred.

    The consequence worth knowing is that a ``request_id`` older than the
    window is not found. Widening the range is the answer; dropping the bound
    for one lookup would leave the expensive path one query string away.

    ``outcome`` repeats. Unknown values are rejected by the ``Outcome``
    annotation rather than ignored, because a filter that silently matches
    everything reads exactly like a filter that found everything.
    """
    rows = await RequestLogRepository(request.state.db).list_recent(
        since=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
        request_id=request_id,
        endpoint=endpoint,
        identity_id=identity_id,
        outcomes=outcome,
    )
    return ok(request, [_row(row) for row in rows])


__all__ = ["DEFAULT_MINUTES", "MAX_MINUTES", "router"]
