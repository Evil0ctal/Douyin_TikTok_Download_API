"""Notification triggers, deduplication and dispatch.

The deduplication tests drive a fake clock rather than sleeping: the windows in
doc 15 run from fifteen minutes to a day, so wall-clock testing is not an
option, and a window that silently stopped working would only be discovered by
a user whose phone stopped ringing. Per-channel payload shapes live in
``test_ops_channels.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from dtk.core.types import Language
from dtk.ops import channels, notify
from dtk.ops.channels import ChannelType
from dtk.ops.notify import NotifyEvent, Severity


class FakeRedis:
    """Only the three commands the deduplicator uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)


class Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RecordingChannel:
    def __init__(self, name: str, language: Language = Language.EN) -> None:
        self.name = name
        self.language = language
        self.type = ChannelType.WEBHOOK
        self.received: list[notify.Message] = []

    async def send(self, message: notify.Message, client: httpx.AsyncClient) -> None:
        del client
        self.received.append(message)


class FailingChannel(RecordingChannel):
    def __init__(self, name: str = "broken") -> None:
        super().__init__(name)
        self.attempts = 0

    async def send(self, message: notify.Message, client: httpx.AsyncClient) -> None:
        del message, client
        self.attempts += 1
        raise httpx.ConnectError("connection refused")


class StubConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str) -> Any:
        return self._values.get(key)


def make_notifier(targets: list[Any], clock: Clock) -> tuple[notify.Notifier, FakeRedis]:
    redis = FakeRedis()
    notifier = notify.Notifier(
        targets,
        redis=redis,  # type: ignore[arg-type]
        clock=clock,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
    )
    return notifier, redis


# --------------------------------------------------------------------------
# trigger table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,severity,window,scope",
    [
        (NotifyEvent.ENDPOINT_CIRCUIT_OPEN, Severity.ERROR, 30 * 60, ("endpoint",)),
        (NotifyEvent.POOL_EMPTY, Severity.ERROR, 15 * 60, ("platform",)),
        (NotifyEvent.POOL_BELOW_MIN, Severity.WARNING, 60 * 60, ("platform",)),
        (NotifyEvent.PROXY_UNHEALTHY, Severity.WARNING, 60 * 60, ("proxy",)),
        (NotifyEvent.SIGNATURE_STALE, Severity.ERROR, 24 * 3600, ("endpoint",)),
        (NotifyEvent.COOKIE_EXPIRING, Severity.WARNING, 24 * 3600, ("identity_id",)),
        (NotifyEvent.BACKUP_FAILED, Severity.ERROR, 24 * 3600, ()),
    ],
)
def test_trigger_table_matches_the_specification(
    event: NotifyEvent, severity: Severity, window: int, scope: tuple[str, ...]
) -> None:
    spec = notify.TRIGGERS[event]
    assert spec.severity is severity
    assert spec.dedup_seconds == window
    assert spec.scope_fields == scope


def test_every_event_has_a_trigger() -> None:
    assert set(notify.TRIGGERS) == set(NotifyEvent)


# --------------------------------------------------------------------------
# deduplication
# --------------------------------------------------------------------------


async def test_window_suppresses_repeats_until_it_expires() -> None:
    clock = Clock()
    dedup = notify.Deduplicator(redis=FakeRedis(), clock=clock)  # type: ignore[arg-type]
    key = notify.dedup_key(NotifyEvent.ENDPOINT_CIRCUIT_OPEN, {"endpoint": "/aweme/detail/"})
    window = notify.TRIGGERS[NotifyEvent.ENDPOINT_CIRCUIT_OPEN].dedup_seconds

    assert await dedup.allow(key, window) is True
    assert await dedup.allow(key, window) is False

    clock.advance(window - 1)
    assert await dedup.allow(key, window) is False

    clock.advance(2)
    assert await dedup.allow(key, window) is True
    assert await dedup.allow(key, window) is False


async def test_scopes_are_independent() -> None:
    clock = Clock()
    dedup = notify.Deduplicator(redis=FakeRedis(), clock=clock)  # type: ignore[arg-type]
    window = notify.TRIGGERS[NotifyEvent.ENDPOINT_CIRCUIT_OPEN].dedup_seconds

    first = notify.dedup_key(NotifyEvent.ENDPOINT_CIRCUIT_OPEN, {"endpoint": "/a/"})
    second = notify.dedup_key(NotifyEvent.ENDPOINT_CIRCUIT_OPEN, {"endpoint": "/b/"})

    assert first != second
    assert await dedup.allow(first, window) is True
    assert await dedup.allow(second, window) is True


def test_an_event_without_a_scope_is_global() -> None:
    key = notify.dedup_key(NotifyEvent.BACKUP_FAILED, {"reason": "disk full"})
    assert key.endswith(":global")
    assert key == notify.dedup_key(NotifyEvent.BACKUP_FAILED, {"reason": "other"})


async def test_a_tripped_endpoint_pages_once_not_once_per_request() -> None:
    clock = Clock()
    channel = RecordingChannel("ops")
    notifier, _redis = make_notifier([channel], clock)

    for _ in range(50):
        await notifier.notify(
            NotifyEvent.ENDPOINT_CIRCUIT_OPEN,
            platform="douyin",
            endpoint="/aweme/detail/",
            risk_rate=71,
            samples=20,
            retry_after=60,
        )

    assert len(channel.received) == 1
    await notifier.aclose()


async def test_suppressed_delivery_reports_itself() -> None:
    clock = Clock()
    notifier, _redis = make_notifier([RecordingChannel("ops")], clock)

    first = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")
    second = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")
    other = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="tiktok")

    assert first.suppressed is False and first.sent == ("ops",)
    assert second.suppressed is True and second.sent == ()
    assert other.suppressed is False
    await notifier.aclose()


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_rendering_uses_the_recipient_language() -> None:
    english = notify.render(NotifyEvent.POOL_EMPTY, Language.EN, platform="douyin")
    chinese = notify.render(NotifyEvent.POOL_EMPTY, Language.ZH, platform="douyin")

    assert english.title and chinese.title
    assert english.title != chinese.title
    assert english.body != chinese.body
    assert english.severity is Severity.ERROR
    assert "douyin" in english.body


async def test_each_channel_gets_its_own_language() -> None:
    clock = Clock()
    english = RecordingChannel("ops-en", Language.EN)
    chinese = RecordingChannel("ops-zh", Language.ZH)
    notifier, _redis = make_notifier([english, chinese], clock)

    await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")

    assert english.received[0].title != chinese.received[0].title
    await notifier.aclose()


def test_missing_template_arguments_do_not_leak_braces() -> None:
    message = notify.render(NotifyEvent.POOL_BELOW_MIN, Language.EN, platform="douyin")
    assert "{" not in message.body and "}" not in message.body


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


async def test_http_channel_posts_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200)

    channel = channels.WebhookChannel(name="hook", url="https://hooks.example.com/dtk")
    notifier = notify.Notifier(
        [channel],
        redis=FakeRedis(),  # type: ignore[arg-type]
        clock=Clock(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    delivery = await notifier.notify(NotifyEvent.BACKUP_FAILED, reason="disk full")

    assert delivery.sent == ("hook",)
    assert len(calls) == 2
    await notifier.aclose()


async def test_a_failing_channel_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify, "RETRY_BACKOFF_SECONDS", 0.0)
    broken = FailingChannel()
    working = RecordingChannel("ops")
    notifier, _redis = make_notifier([broken, working], Clock())

    delivery = await notifier.notify(NotifyEvent.BACKUP_FAILED, reason="disk full")

    assert delivery.sent == ("ops",)
    assert "broken" in delivery.failed
    assert broken.attempts == notify.MAX_ATTEMPTS
    await notifier.aclose()


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_notifier_from_config_inherits_the_default_language() -> None:
    notifier = notify.notifier_from_config(
        StubConfig(
            {
                "notify.enabled": True,
                "notify.language": "zh",
                "notify.channels": [
                    {"type": "webhook", "name": "hook", "url": "https://hooks.example.com/dtk"},
                    {
                        "type": "bark",
                        "name": "bark",
                        "url": "https://api.day.app/key",
                        "language": "en",
                    },
                ],
            }
        ),
        redis=FakeRedis(),  # type: ignore[arg-type]
    )

    by_name = {c.name: c for c in notifier.channels}
    assert by_name["hook"].language is Language.ZH
    assert by_name["bark"].language is Language.EN


async def test_a_disabled_notifier_sends_nothing() -> None:
    channel = RecordingChannel("ops")
    notifier = notify.Notifier(
        [channel],
        redis=FakeRedis(),  # type: ignore[arg-type]
        clock=Clock(),
        enabled=False,
    )

    delivery = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")

    assert delivery.suppressed is True
    assert channel.received == []


async def test_an_alert_that_reached_nobody_is_not_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window suppresses delivered alerts, not failed ones.

    Holding the mark after every channel refused would turn one bad minute of
    network into up to a day of silence about a pool that is still empty.
    """
    monkeypatch.setattr(notify, "RETRY_BACKOFF_SECONDS", 0.0)
    broken = FailingChannel()
    notifier, _redis = make_notifier([broken], Clock())

    first = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")
    second = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")

    assert first.delivered is False
    assert second.suppressed is False
    assert broken.attempts == 2 * notify.MAX_ATTEMPTS
    await notifier.aclose()


def test_a_failure_reason_never_repeats_the_channel_url() -> None:
    """A Telegram bot token lives in the URL, so exception text cannot carry it."""
    channel = channels.TelegramChannel(
        name="tg",
        url=channels.TELEGRAM_API.format(token="123:SUPERSECRETTOKENVALUE"),
        options={"chat_id": "-1"},
    )
    exc = httpx.ConnectError(f"failed to connect to {channel.url}")

    reason = notify.failure_reason(channel, exc)

    assert "SUPERSECRETTOKENVALUE" not in reason
    assert "telegram" in reason
    assert reason.startswith("ConnectError")


async def test_aclose_does_not_orphan_a_client_created_afterwards() -> None:
    """After aclose the notifier owns whatever client it creates next."""
    borrowed = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    notifier = notify.Notifier(
        [], redis=FakeRedis(), clock=Clock(), client=borrowed  # type: ignore[arg-type]
    )

    await notifier.aclose()
    assert borrowed.is_closed is False

    created = await notifier._http()
    await notifier.aclose()
    assert created.is_closed is True
    await borrowed.aclose()
