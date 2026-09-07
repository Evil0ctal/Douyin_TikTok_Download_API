"""How the worker's background jobs reach the operator.

The jobs in this package are the only part of the system that watches the pool
and the egresses continuously, so they are where doc 15's alert triggers fire
from. Delivery, channel rendering and the deduplication windows all belong to
:mod:`dtk.ops.notify`; this module is only the seam that lets a job raise an
alert without knowing whether notifications are configured at all.

Alerts are best effort by construction. A webhook that times out must never
fail the maintenance pass that raised it.
"""

from __future__ import annotations

from typing import Any, Protocol

from dtk.core.logging import get_logger
from dtk.ops.notify import NotifyEvent

log = get_logger(__name__)


class Alerter(Protocol):
    """The slice of :class:`dtk.ops.notify.Notifier` the worker uses."""

    async def notify(self, event: NotifyEvent, /, **args: Any) -> Any: ...


async def raise_alert(alerter: Alerter | None, event: NotifyEvent, /, **args: Any) -> bool:
    """Send one alert. Returns whether it was handed to the notifier.

    ``None`` means notifications are not wired up in this process, which is the
    normal state for a deployment that has configured no channels.
    """
    if alerter is None:
        return False
    try:
        await alerter.notify(event, **args)
    except Exception as exc:
        log.warning(
            "worker.alert_failed",
            event=event.value,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return False
    return True


__all__ = ["Alerter", "NotifyEvent", "raise_alert"]
