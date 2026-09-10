"""Ask a platform whether an identity's login is still a login.

An imported jar is judged at import time on the cookies it contains: a
``sessionid`` is present, so the identity is marked authenticated. That is a
statement about the paste, not about the account - the cookie is there whether
or not the platform still honours it - and the difference only ever showed up
later, as logged-in-only data quietly going missing from otherwise successful
responses.

This asks. Both platforms publish an endpoint that answers about the cookies
that asked rather than about a user named in the request, which is what makes
it a login check and not a lookup:

* Douyin ``/aweme/v1/web/query/user/`` - signed like any other web API call,
  and answers with the account's ``user_uid``.
* TikTok ``/passport/token/beat/web/`` - the passport service, which takes the
  cookies and nothing else, so it is registered unsigned.

Neither uses an HTTP status to say it. A live session and a dead one both come
back 200, and :func:`dtk.ops.probes.read_session` is where the difference is
read.

Like the identity probe, this records nothing against the identity: a check
that cooled what it measured would move the state the operator is reading.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.ops import pipeline, probes
from dtk.ops.masking import mask_url

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Check one identity's session and report what the platform said."""
    identity_id = str(params.get("identity_id") or "").strip()
    if not identity_id:
        raise InvalidParam("identity_id is required", details={"param": "identity_id"})

    identity = await pipeline.pick_identity(
        session, deps.cipher, identity_id=identity_id, pool=deps.pool
    )
    endpoint = probes.SESSION_CHECKS.get(identity.platform)
    if endpoint is None:
        raise InvalidParam(
            "this platform has no session check",
            details={"platform": identity.platform.value},
        )

    # Built here rather than through the worker registry: that table is keyed
    # by capability and needs a non-empty argument map, and this endpoint takes
    # no arguments. See SessionCall for why inventing one would be worse.
    call = probes.SessionCall(platform=identity.platform, endpoint=endpoint)
    probe = await probes.probe_session(
        deps.transport,
        deps.signers,
        identity,
        call,
        timeout=pipeline.REQUEST_TIMEOUT_SECONDS,
    )
    log.info(
        "ops.identity_session",
        identity_id=identity.id,
        endpoint=endpoint,
        logged_in=probe.logged_in,
        reason=probe.reason,
    )

    return {
        "data": {
            "logged_in": probe.logged_in,
            # The account id is what makes the answer checkable: two identities
            # that both report a live session and the same account are one
            # login imported twice, which is worth knowing and invisible
            # otherwise.
            "account_id": probe.account_id,
            "reason": probe.reason,
            "status": probe.status,
            "latency_ms": probe.latency_ms,
            "detail": probe.detail,
        },
        "meta": {
            "identity_id": identity.id,
            "platform": identity.platform.value,
            "endpoint": endpoint,
            "proxy": mask_url(identity.proxy_url),
        },
    }


__all__ = ["run"]
