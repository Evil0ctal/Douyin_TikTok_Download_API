"""Outbound alerting.

Console-only alerts are not alerts: nobody is looking at the console when the
pool empties at 03:00 (docs/design/15-operations.md). Six channels are
supported because the payload template is the only cost of each: a generic
webhook, Bark, a WeCom bot, a DingTalk bot, a Telegram bot and SMTP.

**Deduplication is the feature, not an optimization.** A tripped endpoint would
otherwise alert on every single request; ten minutes of that and the user turns
notifications off, which leaves them worse off than before. Every trigger
therefore has a window and a scope - per endpoint, per proxy, per identity, per
platform - and a second alert inside that window is suppressed. The windows are
the ones in doc 15 and are not tunable per install: they are part of what makes
the alerts trustworthy. The one message with no window is the test an operator
sends by hand, which repeats only as often as someone presses the button.

Notifications render in the language configured on the receiving channel, not
the system default (docs/design/14-i18n.md), and they share the ``notify.*``
catalogue keys with the console.

Outbound URLs are user-configured, so they are an SSRF surface like any other:
https only, no private address space, a hard timeout and a capped retry. The
risk grade is one below a caller-supplied ``callback_url`` because only an
administrator can set these, which is why alerting is on by default and task
callbacks are not.
"""

from __future__ import annotations

import asyncio
import smtplib
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

import httpx
from redis.asyncio import Redis

from dtk.core.errors import NotConfigured, NotFound
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n.catalog import t
from dtk.i18n.negotiate import coerce_language
from dtk.ops.channels import SEND_TIMEOUT_SECONDS, RetryableDelivery, build_channels
from dtk.ops.masking import scrub

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from dtk.ops.channels import Channel

log = get_logger(__name__)

#: Redis key prefix for the deduplication marks.
DEDUP_PREFIX: Final[str] = "notify:dedup"

#: Retry ceiling for one delivery. Alerting that retries hard is alerting that
#: becomes the outage it is reporting.
MAX_ATTEMPTS: Final[int] = 2
RETRY_BACKOFF_SECONDS: Final[float] = 1.0


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class NotifyEvent(StrEnum):
    """Alertable events, plus the one an operator raises by hand.

    The value is also the ``notify.<event>`` catalog key.
    """

    ENDPOINT_CIRCUIT_OPEN = "endpoint_circuit_open"
    POOL_EMPTY = "pool_empty"
    POOL_BELOW_MIN = "pool_below_min"
    PROXY_UNHEALTHY = "proxy_unhealthy"
    SIGNATURE_STALE = "signature_stale"
    COOKIE_EXPIRING = "cookie_expiring"
    BACKUP_FAILED = "backup_failed"
    #: Nothing detects this one: it is the console's Test button, pressed to
    #: prove a channel is reachable before an incident depends on it.
    TEST = "test"


MINUTE: Final[int] = 60
HOUR: Final[int] = 3600


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    """Severity, deduplication window and scope for one event.

    ``scope_fields`` names the arguments that make two alerts distinct. A
    circuit alert is per endpoint, so two endpoints tripping both get through;
    the same endpoint tripping a thousand times does not.
    """

    severity: Severity
    dedup_seconds: int
    scope_fields: tuple[str, ...] = ()


#: The trigger table from docs/design/15-operations.md, verbatim, with the
#: manual test appended.
TRIGGERS: Final[Mapping[NotifyEvent, TriggerSpec]] = {
    NotifyEvent.ENDPOINT_CIRCUIT_OPEN: TriggerSpec(Severity.ERROR, 30 * MINUTE, ("endpoint",)),
    NotifyEvent.POOL_EMPTY: TriggerSpec(Severity.ERROR, 15 * MINUTE, ("platform",)),
    NotifyEvent.POOL_BELOW_MIN: TriggerSpec(Severity.WARNING, 60 * MINUTE, ("platform",)),
    NotifyEvent.PROXY_UNHEALTHY: TriggerSpec(Severity.WARNING, 60 * MINUTE, ("proxy",)),
    NotifyEvent.SIGNATURE_STALE: TriggerSpec(Severity.ERROR, 24 * HOUR, ("endpoint",)),
    NotifyEvent.COOKIE_EXPIRING: TriggerSpec(Severity.WARNING, 24 * HOUR, ("identity_id",)),
    NotifyEvent.BACKUP_FAILED: TriggerSpec(Severity.ERROR, 24 * HOUR, ()),
    # A test arrives one deliberate press at a time, so there is no storm to
    # damp. :meth:`Notifier.send_test` does not consult the deduplicator at
    # all; the zero window is here so this table cannot be read as promising a
    # suppression that never happens.
    NotifyEvent.TEST: TriggerSpec(Severity.INFO, 0, ()),
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Message:
    """One rendered alert, in one language."""

    event: NotifyEvent
    severity: Severity
    title: str
    body: str
    footer: str
    severity_label: str
    language: Language
    sent_at: datetime
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def subject(self) -> str:
        return f"[{self.severity_label}] {self.title}"

    @property
    def plain_text(self) -> str:
        return f"{self.subject}\n\n{self.body}\n\n{self.footer}"

    @property
    def markdown(self) -> str:
        return f"### {self.subject}\n\n{self.body}\n\n> {self.footer}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "severity": self.severity.value,
            "title": self.title,
            "body": self.body,
            "language": self.language.value,
            "sent_at": self.sent_at.isoformat(),
            "details": dict(self.details),
        }


def render(
    event: NotifyEvent,
    language: Language | str = DEFAULT_LANGUAGE,
    *,
    sent_at: datetime | None = None,
    **args: Any,
) -> Message:
    """Render one event into the recipient's language."""
    lang = coerce_language(language) or DEFAULT_LANGUAGE
    spec = TRIGGERS[event]
    return Message(
        event=event,
        severity=spec.severity,
        title=t(f"notify.{event.value}.title", lang),
        body=t(f"notify.{event.value}.body", lang, **args),
        footer=t("notify.footer", lang),
        severity_label=t(f"notify.severity.{spec.severity.value}", lang),
        language=lang,
        sent_at=sent_at or datetime.now(UTC),
        details=dict(args),
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def dedup_key(event: NotifyEvent, args: Mapping[str, Any]) -> str:
    """Redis key identifying one alert stream.

    Scope values are stringified rather than hashed so an operator reading
    ``KEYS notify:dedup:*`` can see which endpoint is noisy.
    """
    spec = TRIGGERS[event]
    parts = [str(args.get(field_name, "any")) for field_name in spec.scope_fields]
    scope = ":".join(parts) if parts else "global"
    return f"{DEDUP_PREFIX}:{event.value}:{scope}"


class Deduplicator:
    """Per-event suppression backed by Redis, driven by an injectable clock.

    The stored value is the timestamp of the last delivery and the clock is
    authoritative; the Redis TTL only garbage-collects. Doing it the other way
    round - relying on the TTL alone - would tie the window to Redis wall time,
    which no test can advance and no operator can reason about after a restore.
    """

    __slots__ = ("_clock", "_redis")

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._clock = clock

    def _client(self) -> Redis:
        return self._redis if self._redis is not None else get_redis()

    async def allow(self, key: str, window_seconds: int) -> bool:
        """True when an alert may be sent, marking the window as used."""
        now = self._clock()
        client = self._client()
        ttl = max(1, int(window_seconds))
        # The common case - no alert in flight for this scope - is a single
        # atomic SET NX, so two workers reporting the same trip cannot both win.
        if await client.set(key, repr(now), nx=True, ex=ttl):
            return True

        stored = await client.get(key)
        last = _as_float(stored)
        if last is not None and 0.0 <= now - last < window_seconds:
            return False
        # Either the mark is unreadable or the clock says the window has passed
        # while the key outlived it. Both mean: send, and restart the window.
        await client.set(key, repr(now), ex=ttl)
        return True

    async def reset(self, key: str) -> None:
        await self._client().delete(key)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bytes | bytearray):
        value = bytes(value).decode("utf-8", "ignore")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Delivery:
    """Outcome of one ``notify()`` call."""

    event: NotifyEvent
    severity: Severity
    suppressed: bool
    #: The window this delivery claimed. Empty when none was claimed, which is
    #: how a test reports itself.
    dedup_key: str = ""
    sent: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def delivered(self) -> bool:
        return bool(self.sent)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "severity": self.severity.value,
            "suppressed": self.suppressed,
            "dedup_key": self.dedup_key,
            "sent": list(self.sent),
            "failed": dict(self.failed),
        }


#: Longest failure reason kept. An exception carrying a response body must not
#: be able to push a wall of text into the logs or an API response.
MAX_REASON_LENGTH: Final[int] = 300


def failure_reason(channel: Channel, exc: BaseException) -> str:
    """Describe a delivery failure without repeating the channel's credentials.

    A Telegram bot token lives in the path of the URL and a DingTalk access
    token in its query, so any exception text that quotes the request URL is a
    credential - in a log line, and now also in a stored task result that the
    console renders.

    Two passes, because neither alone is enough. Replacing the channel's own URL
    names the target the way an operator thinks of it, but it only matches what
    was stored: DingTalk signs at request time and appends
    ``&timestamp=...&sign=...``, so the literal swap took the access token and
    left the HMAC. And an SMTP channel has no URL at all, which made the whole
    step a no-op for it. So the pattern pass runs unconditionally afterwards.
    """
    reason = f"{type(exc).__name__}: {exc}"
    url = str(getattr(channel, "url", "") or "")
    if url:
        reason = reason.replace(url, f"<{channel.type.value} target>")
    return scrub(reason)[:MAX_REASON_LENGTH]


class Notifier:
    """Renders one event per channel language and delivers it once."""

    def __init__(
        self,
        channels: Sequence[Channel] = (),
        *,
        redis: Redis | None = None,
        clock: Callable[[], float] = time.time,
        client: httpx.AsyncClient | None = None,
        enabled: bool = True,
        deduplicator: Deduplicator | None = None,
    ) -> None:
        self._channels = tuple(channels)
        self._clock = clock
        self._client = client
        self._owns_client = client is None
        self._enabled = enabled
        self._dedup = deduplicator or Deduplicator(redis=redis, clock=clock)

    @property
    def channels(self) -> tuple[Channel, ...]:
        return self._channels

    async def aclose(self) -> None:
        client, owned = self._client, self._owns_client
        # Ownership resets with the reference: a client created lazily after
        # this point is ours, and would otherwise never be closed because the
        # flag still described the borrowed client that has been dropped.
        self._client = None
        self._owns_client = True
        if client is not None and owned:
            await client.aclose()

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS, follow_redirects=False)
        return self._client

    async def notify(self, event: NotifyEvent, /, **args: Any) -> Delivery:
        """Deliver one alert unless its window is still open."""
        spec = TRIGGERS[event]
        key = dedup_key(event, args)
        if not self._enabled or not self._channels:
            return Delivery(event, spec.severity, suppressed=True, dedup_key=key)

        if not await self._dedup.allow(key, spec.dedup_seconds):
            log.debug("ops.notify.suppressed", alert=event.value, dedup_key=key)
            return Delivery(event, spec.severity, suppressed=True, dedup_key=key)

        sent_at = datetime.fromtimestamp(self._clock(), UTC)
        rendered: dict[Language, Message] = {}
        prepared: list[tuple[Channel, Message]] = []
        for channel in self._channels:
            message = rendered.get(channel.language)
            if message is None:
                message = render(event, channel.language, sent_at=sent_at, **args)
                rendered[channel.language] = message
            prepared.append((channel, message))

        sent, failed = await self._deliver_all(prepared)
        if not sent and failed:
            # The window exists to stop a storm of *delivered* alerts. Holding
            # it after an alert reached nobody would turn one refused
            # connection into up to a day of silence, so the mark is released
            # and the next occurrence gets a fresh attempt.
            await self._dedup.reset(key)

        log.info(
            "ops.notify.dispatched",
            alert=event.value,
            severity=spec.severity.value,
            sent=len(sent),
            failed=len(failed),
        )
        return Delivery(
            event=event,
            severity=spec.severity,
            suppressed=False,
            dedup_key=key,
            sent=sent,
            failed=failed,
        )

    async def send_test(self, channel: str | None = None) -> Delivery:
        """Deliver a test alert now, to one channel or to every channel.

        Deliberately not routed through :meth:`notify`. The window there exists
        to stop a repeating condition from paging every minute, and it would
        answer the second press of the console's Test button with silence -
        reporting a channel that works as one that does not, which is the exact
        doubt the button exists to remove. A test claims no window and reads
        none, so a real alert's suppression is neither started nor consumed by
        proving the channel first.
        """
        targets = self._select(channel)
        if not targets:
            # Two different problems with two different next steps: a fresh
            # install has nothing to test and needs to add a channel, while a
            # named channel that is absent is a stale console tab or a typo.
            # One code for both sent every new operator looking for a channel
            # they had never created.
            if not self._channels:
                raise NotConfigured(
                    "no notification channel is configured",
                    details={"channel": channel},
                )
            raise NotFound(
                "no notification channel to test",
                details={"channel": channel, "known": [c.name for c in self._channels]},
            )
        if not self._enabled:
            # Alerting is switched off. Delivering anyway would prove a channel
            # that is not going to be used and leave the operator believing
            # alerts are on their way.
            return Delivery(NotifyEvent.TEST, Severity.INFO, suppressed=True)

        sent_at = datetime.fromtimestamp(self._clock(), UTC)
        # Rendered per channel rather than per language: the body names the
        # channel it was sent to, so an operator holding two phones knows which
        # button produced which push.
        prepared = [
            (
                target,
                render(NotifyEvent.TEST, target.language, sent_at=sent_at, channel=target.name),
            )
            for target in targets
        ]
        sent, failed = await self._deliver_all(prepared)
        log.info(
            "ops.notify.test_dispatched",
            channel=channel or "*",
            sent=len(sent),
            failed=len(failed),
        )
        return Delivery(
            event=NotifyEvent.TEST,
            severity=Severity.INFO,
            suppressed=False,
            sent=sent,
            failed=failed,
        )

    def _select(self, channel: str | None) -> tuple[Channel, ...]:
        """The channels a request names. ``None`` means every one of them."""
        if channel is None:
            return self._channels
        return tuple(target for target in self._channels if target.name == channel)

    async def _deliver_all(
        self, prepared: Sequence[tuple[Channel, Message]]
    ) -> tuple[tuple[str, ...], dict[str, str]]:
        """Send every prepared message, collecting names and failure reasons."""
        client = await self._http()
        sent: list[str] = []
        failed: dict[str, str] = {}
        for channel, message in prepared:
            error = await self._deliver(channel, message, client)
            if error is None:
                sent.append(channel.name)
            else:
                failed[channel.name] = error
        return tuple(sent), failed

    async def _deliver(
        self, channel: Channel, message: Message, client: httpx.AsyncClient
    ) -> str | None:
        """Send to one channel. Returns the failure reason, or None."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await channel.send(message, client)
            except (RetryableDelivery, httpx.HTTPError, OSError, smtplib.SMTPException) as exc:
                reason = failure_reason(channel, exc)
                if attempt >= MAX_ATTEMPTS:
                    log.error(
                        "ops.notify.delivery_failed",
                        channel=channel.name,
                        type=channel.type.value,
                        reason=reason,
                    )
                    return reason
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
            except Exception as exc:
                reason = failure_reason(channel, exc)
                log.error(
                    "ops.notify.delivery_failed",
                    channel=channel.name,
                    type=channel.type.value,
                    reason=reason,
                )
                return reason
            else:
                return None
        return "exhausted attempts"


def notifier_from_config(
    config: Any,
    *,
    redis: Redis | None = None,
    clock: Callable[[], float] = time.time,
    client: httpx.AsyncClient | None = None,
) -> Notifier:
    """Build a notifier from the runtime settings snapshot.

    Channels with no language of their own inherit ``notify.language`` so a
    Chinese operator's webhook receives Chinese alerts.
    """
    enabled = bool(config.get("notify.enabled"))
    default_language = config.get("notify.language") or DEFAULT_LANGUAGE
    descriptors: list[Mapping[str, Any]] = []
    for entry in config.get("notify.channels") or []:
        if isinstance(entry, Mapping):
            merged = dict(entry)
            merged.setdefault("language", default_language)
            descriptors.append(merged)
    return Notifier(
        build_channels(descriptors),
        redis=redis,
        clock=clock,
        client=client,
        enabled=enabled,
    )


__all__ = [
    "DEDUP_PREFIX",
    "MAX_ATTEMPTS",
    "MAX_REASON_LENGTH",
    "RETRY_BACKOFF_SECONDS",
    "TRIGGERS",
    "Deduplicator",
    "Delivery",
    "Message",
    "Notifier",
    "NotifyEvent",
    "Severity",
    "TriggerSpec",
    "dedup_key",
    "failure_reason",
    "notifier_from_config",
    "render",
]
