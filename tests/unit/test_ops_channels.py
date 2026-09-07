"""Per-channel payload shapes and outbound URL validation.

Every ``request()`` is pure, so the body each third-party service receives is
asserted here without a network. The URL tests are the SSRF boundary: an
administrator typing an internal address into the settings page must be
refused, not turned into a request from inside the network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dtk.core.errors import InvalidParam
from dtk.core.types import Language
from dtk.ops import channels, notify
from dtk.ops.channels import ChannelType
from dtk.ops.notify import NotifyEvent


@pytest.fixture
def message() -> notify.Message:
    return notify.render(
        NotifyEvent.PROXY_UNHEALTHY, Language.EN, proxy="residential-1", failures=3
    )


# --------------------------------------------------------------------------
# payload shapes
# --------------------------------------------------------------------------


def test_webhook_payload_is_a_documented_envelope(message: notify.Message) -> None:
    channel = channels.WebhookChannel(name="hook", url="https://hooks.example.com/dtk")
    prepared = channel.request(message)

    assert prepared.url == "https://hooks.example.com/dtk"
    assert prepared.headers["Content-Type"] == "application/json"
    assert prepared.payload["source"] == "dtk"
    assert prepared.payload["event"] == "proxy_unhealthy"
    assert prepared.payload["severity"] == "warning"
    assert prepared.payload["details"]["proxy"] == "residential-1"
    json.dumps(prepared.payload)


def test_bark_payload_maps_severity_to_a_push_level(message: notify.Message) -> None:
    channel = channels.BarkChannel(name="bark", url="https://api.day.app/devicekey")
    prepared = channel.request(message)

    assert prepared.payload["level"] == "active"
    assert prepared.payload["group"] == "dtk"
    assert prepared.payload["body"] == message.body

    error = notify.render(NotifyEvent.POOL_EMPTY, Language.EN, platform="douyin")
    assert channel.request(error).payload["level"] == "timeSensitive"


def test_wecom_payload_is_markdown(message: notify.Message) -> None:
    channel = channels.WeComChannel(name="wecom", url="https://qyapi.weixin.qq.com/cgi-bin/webhook")
    prepared = channel.request(message)

    assert prepared.payload["msgtype"] == "markdown"
    assert prepared.payload["markdown"]["content"].startswith("### ")


def test_dingtalk_payload_is_signed_when_a_secret_is_configured(
    message: notify.Message,
) -> None:
    plain = channels.DingTalkChannel(
        name="ding", url="https://oapi.dingtalk.com/robot/send?access_token=x"
    )
    signed = channels.DingTalkChannel(
        name="ding",
        url="https://oapi.dingtalk.com/robot/send?access_token=x",
        options={"secret": "SECxxxx"},
    )

    assert "sign=" not in plain.request(message).url
    signed_url = signed.request(message).url
    assert "timestamp=" in signed_url and "sign=" in signed_url
    assert signed.request(message).payload["msgtype"] == "markdown"


def test_dingtalk_signature_is_deterministic_for_one_timestamp() -> None:
    first = channels.sign_dingtalk_url("https://oapi.dingtalk.com/robot/send", "s", 1.0)
    second = channels.sign_dingtalk_url("https://oapi.dingtalk.com/robot/send", "s", 1.0)
    later = channels.sign_dingtalk_url("https://oapi.dingtalk.com/robot/send", "s", 2.0)

    assert first == second
    assert first != later
    assert first.startswith("https://oapi.dingtalk.com/robot/send?timestamp=1000&sign=")


def test_telegram_payload_carries_the_chat_id(message: notify.Message) -> None:
    channel = channels.TelegramChannel(
        name="tg",
        url=channels.TELEGRAM_API.format(token="123:abc"),
        options={"chat_id": "-100200"},
    )
    prepared = channel.request(message)

    assert prepared.url.endswith("/sendMessage")
    assert prepared.payload["chat_id"] == "-100200"
    assert message.body in prepared.payload["text"]


def test_smtp_builds_a_plain_text_email(message: notify.Message) -> None:
    channel = channels.SmtpChannel(
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


def test_smtp_accepts_a_comma_separated_recipient_list() -> None:
    channel = channels.SmtpChannel(
        name="mail",
        url="",
        options={"host": "smtp.example.com", "sender": "a@b.com", "recipients": "x@b.com, y@b.com"},
    )
    assert channel.recipients == ("x@b.com", "y@b.com")


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
        channels.validate_outbound_url(url)


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
    assert channels.validate_outbound_url(url) == url


def test_smtp_host_is_validated_too() -> None:
    with pytest.raises(InvalidParam):
        channels.validate_outbound_host("127.0.0.1")
    assert channels.validate_outbound_host("smtp.example.com") == "smtp.example.com"


# --------------------------------------------------------------------------
# building channels from settings
# --------------------------------------------------------------------------


def test_telegram_channel_builds_its_api_url() -> None:
    channel = channels.build_channel(
        {"type": "telegram", "token": "123:abc", "chat_id": "-100", "language": "zh"}
    )

    assert channel.type is ChannelType.TELEGRAM
    assert channel.language is Language.ZH
    assert channel.url == channels.TELEGRAM_API.format(token="123:abc")


def test_every_channel_type_can_be_built() -> None:
    descriptors = [
        {"type": "webhook", "url": "https://hooks.example.com/dtk"},
        {"type": "bark", "url": "https://api.day.app/key"},
        {"type": "wecom", "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook"},
        {"type": "dingtalk", "url": "https://oapi.dingtalk.com/robot/send?access_token=x"},
        {"type": "telegram", "token": "1:a", "chat_id": "-1"},
        {
            "type": "smtp",
            "host": "smtp.example.com",
            "sender": "dtk@example.com",
            "recipients": ["ops@example.com"],
        },
    ]

    built = channels.build_channels(descriptors)

    assert {c.type for c in built} == set(ChannelType)


@pytest.mark.parametrize(
    "descriptor",
    [
        {"type": "telegram", "chat_id": "-100"},
        {"type": "telegram", "token": "123:abc"},
        {"type": "smtp", "host": "smtp.example.com"},
        {"type": "smtp", "host": "smtp.example.com", "sender": "a@example.com"},
        {"type": "carrier-pigeon", "url": "https://example.com"},
        {"type": "webhook", "url": "http://example.com"},
        {"type": "", "url": "https://example.com"},
    ],
)
def test_incomplete_channel_descriptors_are_refused(descriptor: dict[str, Any]) -> None:
    with pytest.raises(InvalidParam):
        channels.build_channel(descriptor)


def test_one_bad_channel_does_not_silence_the_others() -> None:
    built = channels.build_channels(
        [
            {"type": "webhook", "url": "http://insecure.example.com"},
            {"type": "webhook", "name": "good", "url": "https://hooks.example.com/dtk"},
            {"type": "webhook", "name": "off", "url": "https://x.example.com", "enabled": False},
        ]
    )

    assert [c.name for c in built] == ["good"]
