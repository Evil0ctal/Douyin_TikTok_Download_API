"""Delivering the task callback this API has been accepting and dropping.

``callback_url`` was declared in the request schema, validated carefully by
:func:`dtk.api.routes.support.validate_callback_url` - https only, private
ranges refused, gated behind a setting - and stored on the task. Then nothing
ever sent anything to it. Doc 06 promises webhooks; the repository contained no
line that made an outbound request to that address. An endpoint that accepts a
parameter and silently ignores it is the hardest kind of API defect to
diagnose, because everything the caller can see says it worked.

Two properties shape what is here.

**It is an outbound request to an address the caller chose**, which is an SSRF
primitive by construction. The URL is vetted at submission and vetted again
here - the setting can be turned off, and a stored task can outlive the moment
it was accepted - and the resolved address is checked at dial time, because a
hostname that passed validation an hour ago may resolve somewhere else now.

**Delivery must never affect the task.** The caller asked for data and got it;
a webhook endpoint that is down, slow or hostile cannot be allowed to turn a
successful fetch into a failure, or to hold a worker while it times out. So
every failure here is logged and swallowed, and the whole thing is bounded by a
short timeout and a small number of attempts.

The payload is deliberately small: what happened, not what was found. A result
can be megabytes, the receiving end usually wants to know it can now collect
it, and posting the whole thing to a third party is a data-flow decision no
caller made explicitly by writing a URL in a query string.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from dtk.core.logging import get_logger
from dtk.urls.parse import is_private_host

log = get_logger(__name__)

#: One delivery attempt. Short: nobody is waiting on it, but a worker slot is.
TIMEOUT_SECONDS: Final = 10.0

#: Attempts, including the first. Three is enough to ride out a restart on the
#: receiving end and few enough that a black hole costs half a minute, not more.
MAX_ATTEMPTS: Final = 3

#: Backoff between attempts.
RETRY_DELAYS: Final[tuple[float, ...]] = (2.0, 8.0)

#: Header carrying the HMAC of the body, when a signing secret is configured.
SIGNATURE_HEADER: Final = "X-Dtk-Signature"
#: Header naming the event, so a receiver can route without parsing the body.
EVENT_HEADER: Final = "X-Dtk-Event"


@dataclass(frozen=True, slots=True)
class Delivery:
    """What one attempt at delivery came to. Never raised, only logged."""

    delivered: bool
    attempts: int = 0
    status: int | None = None
    detail: str = ""


def payload_for(
    task_id: str,
    *,
    endpoint: str,
    state: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The notification body.

    What happened, not what was found. A result can be megabytes; the receiver
    almost always wants to know it can now collect it, and posting the whole
    payload to a third party is a data-flow decision nobody made by writing a
    URL into a query string. The error is included because it is small, and
    because "it failed" without a reason means another round trip.
    """
    body: dict[str, Any] = {
        "event": "task.completed" if not error else "task.failed",
        "task_id": task_id,
        "endpoint": endpoint,
        "state": state,
        "sent_at": datetime.now(UTC).isoformat(),
    }
    if error:
        body["error"] = {
            "code": str(error.get("code") or "INTERNAL"),
            "message": str(error.get("message") or "")[:500],
        }
    return body


def sign(body: bytes, secret: str) -> str:
    """``sha256=<hex>`` over the exact bytes sent.

    Over the bytes rather than over a re-serialization of the object, because
    a receiver can only verify what actually arrived - any difference in key
    order or separators would make an otherwise correct check fail.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _client() -> httpx.AsyncClient:
    """A client that verifies certificates and does not follow redirects.

    Both matter here. Redirects are off because a 302 is an instruction from
    the destination to visit somewhere else, and this request already goes to
    an address the caller chose - following one would hand that choice to the
    receiver as well.

    Certificate verification is the answer to DNS rebinding, and it is a better
    one than a dial-time address check would be. `recheck` resolves the name and
    refuses any private answer, but a name can resolve differently a moment
    later; what a rebinding attacker cannot do is present a valid certificate
    for the hostname they told us to visit. Callbacks are https-only for exactly
    this reason, and that is enforced at submission and again below.
    """
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, verify=True)


async def recheck(url: str) -> str | None:
    """Re-validate a stored callback URL. Returns a refusal, or ``None``.

    Checked again at delivery rather than trusted from submission: the setting
    can be turned off between the two, and a queued task can outlive the moment
    it was accepted.

    The resolution check narrows the rebinding window rather than closing it -
    the name is resolved again by the HTTP client a moment later. What closes
    it is that callbacks are https and certificates are verified: an address
    that answers on loopback cannot produce a valid certificate for the
    caller's hostname.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unparseable callback_url"
    if parts.scheme != "https":
        return "callback_url must be https"
    if not parts.hostname:
        return "callback_url has no host"
    if is_private_host(parts.hostname):
        return "callback_url points at a private or loopback address"
    try:
        # The loop's own resolver, not socket.getaddrinfo: that one blocks, and
        # a worker running four tasks concurrently would stall all of them on
        # one slow lookup for a webhook nobody is waiting on.
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, None)
    except OSError as exc:
        return f"callback_url does not resolve: {exc}"
    for info in infos:
        if is_private_host(str(info[4][0])):
            # Rebinding, or simply a name that points inside. Either way it is
            # not somewhere this service may be made to post.
            return "callback_url resolves to a private address"
    return None


async def deliver(
    url: str,
    body: dict[str, Any],
    *,
    secret: str = "",
    client: httpx.AsyncClient | None = None,
) -> Delivery:
    """POST one notification, retrying a few times. Never raises.

    Never raising is the contract, not an implementation detail: the caller
    already has its data, and a webhook endpoint that is down must not be able
    to turn a successful fetch into a failed task.
    """
    refusal = await recheck(url)
    if refusal:
        log.warning("webhook.refused", reason=refusal)
        return Delivery(delivered=False, detail=refusal)

    encoded = json.dumps(body, ensure_ascii=False).encode()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        EVENT_HEADER: str(body.get("event") or "task.completed"),
        "User-Agent": "dtk-webhook/1",
    }
    if secret:
        headers[SIGNATURE_HEADER] = sign(encoded, secret)

    owned = client is None
    http = client or _client()
    last = ""
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await http.post(url, content=encoded, headers=headers)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"[:200]
            else:
                if 200 <= response.status_code < 300:
                    log.info(
                        "webhook.delivered",
                        # The host, never the full URL: a callback URL commonly
                        # carries a token in its path or query.
                        host=urlsplit(url).hostname,
                        status=response.status_code,
                        attempts=attempt,
                    )
                    return Delivery(delivered=True, attempts=attempt, status=response.status_code)
                last = f"HTTP {response.status_code}"
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    # The receiver understood and refused. Retrying a 404 or a
                    # 401 just repeats the same rejection.
                    break
            if attempt <= len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt - 1])
    finally:
        if owned:
            await http.aclose()

    log.warning("webhook.undelivered", host=urlsplit(url).hostname, detail=last)
    return Delivery(delivered=False, attempts=MAX_ATTEMPTS, detail=last)


__all__ = [
    "EVENT_HEADER",
    "MAX_ATTEMPTS",
    "SIGNATURE_HEADER",
    "TIMEOUT_SECONDS",
    "Delivery",
    "deliver",
    "payload_for",
    "recheck",
    "sign",
]
