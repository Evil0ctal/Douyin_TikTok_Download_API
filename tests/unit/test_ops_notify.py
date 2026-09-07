"""Notification triggers, deduplication, payload shapes and URL validation.

The deduplication tests drive a fake clock rather than sleeping: the windows in
doc 15 run from fifteen minutes to a day, so wall-clock testing is not an
option, and a window that silently stopped working would only be discovered by
a user whose phone stopped ringing.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from dtk.core.errors import InvalidParam
from dtk.core.types import Language
from dtk.ops import notify
from dtk.ops.notify import ChannelType, NotifyEvent, Severity


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


def make_notifier(channels: list[Any], clock: Clock) -> tuple[notify.Notifier, FakeRedis]:
    redis = FakeRedis()
    notifier = notify.Notifier(
        channels,
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
# per-channel payloads
# --------------------------------------------------------------------------


@pytest.fixture
def message() -> notify.Message:
    return notify.render(
        NotifyEvent.PROXY_UNHEALTHY, Language.EN, proxy="residential-1", failures=3
    )


def test_webhook_payload_is_a_documented_envelope(message: notify.Message) -> None:
    channel = notify.WebhookChannel(name="hook", url="https://hooks.example.com/dtk")
    prepared = channel.request(message)

    assert prepared.url == "https://hooks.example.com/dtk"
    assert prepared.headers["Content-Type"] == "application/json"
    assert prepared.payload["source"] == "dtk"
    assert prepared.payload["event"] == "proxy_unhealthy"
    assert prepared.payload["severity"] == "warning"
    assert prepared.payload["details"]["proxy"] == "residential-1"
    json.dumps(prepared.payload)


def test_bark_payload_maps_severity_to_a_push_level(message: notify.Message) -> None:
    channel = notify.BarkChannel(name="bark", url="https://api.day.app/devicekey")
    prepared = channel.request(message)

    assert prepared.payload["level"] == "active"
    assert prepared.payload["group"] == "dtk"
    assert prepared.payload["body"] == message.body

    error = notify.render(NotifyEvent.POOL_EMPTY, Language.EN, platform="douyin")
    assert channel.request(error).payload["level"] == "timeSensitive"


def test_wecom_payload_is_markdown(message: notify.Message) -> None:
    channel = notify.WeComChannel(name="wecom", url="https://qyapi.weixin.qq.com/cgi-bin/webhook")
    prepared = channel.request(message)

    assert prepared.payload["msgtype"] == "markdown"
    assert prepared.payload["markdown"]["content"].startswith("### ")


def test_dingtalk_payload_is_signed_when_a_secret_is_configured(
    message: notify.Message,
) -> None:
    plain = notify.DingTalkChannel(
        name="ding", url="https://oapi.dingtalk.com/robot/send?access_token=x"
    )
    signed = notify.DingTalkChannel(
        name="ding",
        url="https://oapi.dingtalk.com/robot/send?access_token=x",
        options={"secret": "SECxxxx"},
    )

    assert "sign=" not in plain.request(message).url
    signed_url = signed.request(message).url
    assert "timestamp=" in signed_url and "sign=" in signed_url
    assert signed.request(message).payload["msgtype"] == "markdown"


def test_telegram_payload_carries_the_chat_id(message: notify.Message) -> None:
    channel = notify.TelegramChannel(
        name="tg",
        url=notify.TELEGRAM_API.format(token="123:abc"),
        options={"chat_id": "-100200"},
    )
    prepared = channel.request(message)

    assert prepared.url.endswith("/sendMessage")
    assert prepared.payload["chat_id"] == "-100200"
    assert message.body in prepared.payload["text"]


def test_smtp_builds_a_plain_text_email(message: notify.Message) -> None:
    channel = notify.SmtpChannel(
        name="mail",
        url="",
        options={
            "host": "smtp.example.com",
            "sender": "dtk@example.com",
            "recipients": ["ops@example.com", "oncall@example.com"],
        },
    )
    mail = channel.build_email(message)

    assert mail["From"] == "dtk@example.com"
    assert mail["To"] == "ops@example.com, oncall@example.com"
    assert mail["Subject"] == message.subject
    assert message.body in mail.get_content()


async def test_http_channel_posts_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200)

    channel = notify.WebhookChannel(name="hook", url="https://hooks.example.com/dtk")
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
# outbound URL validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/dtk",
        "https://127.0.0.1/dtk",
        "https://localhost/dtk",
        "https://10.0.0.5/dtk",
        "https://192.168.1.10:8443/dtk",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/dtk",
        "https://user:pass@hooks.example.com/dtk",
        "https://intranet/dtk",
        "ftp://hooks.example.com/dtk",
        "",
        "not a url",
    ],
)
def test_unsafe_outbound_urls_are_refused(url: str) -> None:
    with pytest.raises(InvalidParam):
        notify.validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/dtk",
        "https://api.day.app/abcdef",
        "https://bark.example.com:8443/abcdef",
        "https://oapi.dingtalk.com/robot/send?access_token=x",
    ],
)
def test_public_https_targets_are_accepted(url: str) -> None:
    assert notify.validate_outbound_url(url) == url


def test_smtp_host_is_validated_too() -> None:
    with pytest.raises(InvalidParam):
        notify.validate_outbound_host("127.0.0.1")
    assert notify.validate_outbound_host("smtp.example.com") == "smtp.example.com"


# --------------------------------------------------------------------------
# building channels from settings
# --------------------------------------------------------------------------


def test_telegram_channel_builds_its_api_url() -> None:
    channel = notify.build_channel(
        {"type": "telegram", "token": "123:abc", "chat_id": "-100", "language": "zh"}
    )

    assert channel.type is ChannelType.TELEGRAM
    assert channel.language is Language.ZH
    assert channel.url == notify.TELEGRAM_API.format(token="123:abc")


@pytest.mark.parametrize(
    "descriptor",
    [
        {"type": "telegram", "chat_id": "-100"},
        {"type": "telegram", "token": "123:abc"},
        {"type": "smtp", "host": "smtp.example.com"},
        {"type": "smtp", "host": "smtp.example.com", "sender": "a@example.com"},
        {"type": "carrier-pigeon", "url": "https://example.com"},
        {"type": "webhook", "url": "http://example.com"},
    ],
)
def test_incomplete_channel_descriptors_are_refused(descriptor: dict[str, Any]) -> None:
    with pytest.raises(InvalidParam):
        notify.build_channel(descriptor)


def test_one_bad_channel_does_not_silence_the_others() -> None:
    channels = notify.build_channels(
        [
            {"type": "webhook", "url": "http://insecure.example.com"},
            {"type": "webhook", "name": "good", "url": "https://hooks.example.com/dtk"},
            {"type": "webhook", "name": "off", "url": "https://x.example.com", "enabled": False},
        ]
    )

    assert [c.name for c in channels] == ["good"]


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
