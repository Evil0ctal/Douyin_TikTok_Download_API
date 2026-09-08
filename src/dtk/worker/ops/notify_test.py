"""Send one test alert, for the console's Test button.

Submitted by ``POST /admin/notifications/test`` with ``{"channel": <name>}``,
where the name is the label the operator gave a channel and ``null`` means
every configured channel. ``web/src/pages/Notifications.tsx`` awaits the task
and reads no field of it, so the payload is written for the record the task
leaves behind: ``channel`` is what was asked for, ``status`` is one of
``sent`` / ``partial`` / ``failed`` / ``disabled``, and ``delivered`` and
``failed`` name the channels on each side of that verdict.

The delivery itself is :meth:`dtk.ops.notify.Notifier.send_test`, which renders
the alert through the catalogue in each channel's own language and claims no
deduplication window. Both matter here: a test that was suppressed would report
a healthy channel as a silent one, and a test that took a window would buy its
own reassurance with a real alert's silence.

**The channels come from the live config, not from the worker's own notifier.**
:func:`dtk.worker.runtime.build_runtime` builds one notifier when the process
starts and freezes today's channel list into it - its own docstring says as
much. The Test button is pressed hardest on a channel that was saved a minute
ago, and answering "no such channel" about a row the operator is looking at is
precisely the doubt this button exists to remove, so the channels are rebuilt
from the config for this one delivery. ``deps.notifier`` still answers the
question it is there for: whether this deployment alerts at all. A worker
started without one cannot be tested into having one.

Building from the config also decides what "unknown channel" means: a channel
the operator switched off, or one whose URL does not validate, is never built,
so testing it answers ``NOT_FOUND`` like a name that was never there. The
worker log separates the two - ``ops.notify.channel_rejected`` names the
channel and the reason it was refused.

**No target reaches the result.** A channel descriptor holds a URL, and for
Telegram, DingTalk and Bark that URL *is* the credential. This result is stored
in ``tasks.result`` and rendered in a browser, so channels appear here by name
only, and the reasons under ``failed`` are the ones
:func:`dtk.ops.notify.failure_reason` has already stripped of the URL.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from dtk.core.errors import Internal, InvalidParam
from dtk.core.logging import get_logger
from dtk.ops.notify import notifier_from_config

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.ops.notify import Delivery
    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)

#: Verdicts, in the vocabulary of the stored result. Codes, never translated:
#: an operator reading a task months later needs "nothing was sent because
#: alerting is off" to be a different word from "it was sent and refused".
STATUS_SENT: Final[str] = "sent"
STATUS_PARTIAL: Final[str] = "partial"
STATUS_FAILED: Final[str] = "failed"
STATUS_DISABLED: Final[str] = "disabled"


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Send the test alert and report which channels took it.

    ``session`` is unused: proving a channel works writes no row.
    """
    del session
    if deps.notifier is None:
        raise Internal("this worker was started without a notifier")

    channel = _channel(params)
    started = time.monotonic()
    # No Redis is passed: nothing about a test is deduplicated, so the
    # deduplicator this builds is never asked for a client.
    notifier = notifier_from_config(deps.config())
    try:
        delivery = await notifier.send_test(channel)
    finally:
        # Both the notifier and the HTTP client it opened belong to this task.
        await notifier.aclose()

    status = _status(delivery)
    log.info("worker.ops.notify_test", channel=channel or "*", status=status)
    return {
        "data": {
            "channel": channel,
            "status": status,
            "sent": delivery.delivered,
            "delivered": list(delivery.sent),
            "failed": dict(delivery.failed),
        },
        "meta": {
            "endpoint": "notify.test",
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    }


def _status(delivery: Delivery) -> str:
    """Reduce a delivery to the one word the stored result is read for."""
    if delivery.suppressed:
        return STATUS_DISABLED
    if not delivery.sent:
        return STATUS_FAILED
    return STATUS_PARTIAL if delivery.failed else STATUS_SENT


def _channel(params: Mapping[str, Any]) -> str | None:
    """Read the one parameter this job takes.

    The route sends ``null`` for "every channel", which is what the button
    above the table asks for; a blank string is the same request typed badly.
    """
    raw = params.get("channel")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidParam("notify.test takes a channel name", details={"param": "channel"})
    return raw.strip() or None


__all__ = ["run"]
