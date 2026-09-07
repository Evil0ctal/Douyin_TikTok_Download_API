"""Notification channels: the six payload templates and their targets.

One trigger produces one :class:`~dtk.ops.notify.Message`; this module is only
about how that message reaches a receiver. Each channel exposes a pure
``request()`` that builds the exact JSON body the third-party service expects,
so the shape can be asserted in a test without a network, and a ``send()`` that
posts it.

Outbound URLs are configured by an administrator and are still an SSRF surface:
https only, no private address space, one timeout and a capped retry
(docs/design/08-security.md, docs/design/15-operations.md). Validation happens
when the channel is built rather than when an alert fires, so a bad target is
rejected while the person who typed it is still looking at the settings page.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Protocol
from urllib.parse import quote, urlsplit

import httpx

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language
from dtk.i18n.negotiate import coerce_language
from dtk.urls import is_private_host

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    # Type-only, so this module never imports the dispatcher at runtime and the
    # dependency between the two stays one-directional.
    from dtk.ops.notify import Message

log = get_logger(__name__)

#: Per-delivery timeout. Alerting must never become the slow path that holds up
#: the thing it is reporting on.
SEND_TIMEOUT_SECONDS: Final[float] = 8.0

#: Statuses worth a second attempt. Anything else is the receiver saying no.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

TELEGRAM_API: Final[str] = "https://api.telegram.org/bot{token}/sendMessage"


class RetryableDelivery(RuntimeError):
    """A delivery failure worth exactly one more attempt."""


class ChannelType(StrEnum):
    WEBHOOK = "webhook"
    BARK = "bark"
    WECOM = "wecom"
    DINGTALK = "dingtalk"
    TELEGRAM = "telegram"
    SMTP = "smtp"


# ---------------------------------------------------------------------------
# Outbound URL validation
# ---------------------------------------------------------------------------


def validate_outbound_url(url: str) -> str:
    """Return ``url`` if this service may POST to it, else raise.

    https only, no credentials in the authority, and never a loopback, private,
    link-local or otherwise non-routable host. A port is allowed: a self-hosted
    Bark or webhook receiver on 8443 is perfectly normal, and the port is not
    what makes an address internal.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise InvalidParam("notification URL is empty")
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise InvalidParam(f"notification URL is malformed: {exc}") from exc

    if parts.scheme.lower() != "https":
        raise InvalidParam(
            "notification URLs must use https; plain http would put the alert "
            "payload and any token in the URL on the wire in clear text",
            details={"scheme": parts.scheme},
        )
    if "@" in parts.netloc:
        raise InvalidParam("notification URLs must not carry credentials in the authority")
    try:
        host = parts.hostname
    except ValueError as exc:
        raise InvalidParam(f"notification URL has an invalid host: {exc}") from exc
    if not host:
        raise InvalidParam("notification URL has no host")
    validate_outbound_host(host)
    return candidate


def validate_outbound_host(host: str) -> str:
    """Reject a host that names this machine or a non-routable network."""
    name = host.strip().lower()
    if not name or not name.isascii():
        raise InvalidParam("notification host is empty or not an ASCII hostname")
    if is_private_host(name):
        raise InvalidParam(
            "notification targets must be publicly routable; private, loopback "
            "and link-local addresses are refused",
            details={"host": name},
        )
    return name


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """What an HTTP channel would send. Pure, so payload shapes are testable."""

    url: str
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class Channel(Protocol):
    """A destination an alert can be delivered to."""

    @property
    def type(self) -> ChannelType: ...

    @property
    def name(self) -> str: ...

    @property
    def language(self) -> Language: ...

    async def send(self, message: Message, client: httpx.AsyncClient) -> None: ...


@dataclass(frozen=True, slots=True)
class _Base:
    name: str
    url: str
    language: Language = DEFAULT_LANGUAGE
    options: dict[str, Any] = field(default_factory=dict)


class HttpChannel(_Base):
    """Common POST-with-JSON behaviour; subclasses only build the payload."""

    type: ChannelType = ChannelType.WEBHOOK

    def request(self, message: Message) -> HttpRequest:  # pragma: no cover - abstract
        raise NotImplementedError

    async def send(self, message: Message, client: httpx.AsyncClient) -> None:
        prepared = self.request(message)
        response = await client.post(
            prepared.url,
            json=prepared.payload,
            headers=prepared.headers or None,
            timeout=SEND_TIMEOUT_SECONDS,
        )
        if response.status_code in RETRYABLE_STATUS:
            raise RetryableDelivery(f"{self.type.value} returned {response.status_code}")
        if response.status_code >= 400:
            raise RuntimeError(f"{self.type.value} returned {response.status_code}")


class WebhookChannel(HttpChannel):
    """Generic JSON POST. The shape is ours, so it is documented and stable."""

    type = ChannelType.WEBHOOK

    def request(self, message: Message) -> HttpRequest:
        return HttpRequest(
            url=self.url,
            payload={"source": "dtk", **message.as_dict()},
            headers={"Content-Type": "application/json"},
        )


class BarkChannel(HttpChannel):
    """Bark push. ``level`` decides whether the phone rings through a focus."""

    type = ChannelType.BARK

    #: Keyed by the severity's string value rather than by the enum, so this
    #: module needs no runtime import from the dispatcher.
    LEVELS: Mapping[str, str] = MappingProxyType(
        {"error": "timeSensitive", "warning": "active", "info": "passive"}
    )

    def request(self, message: Message) -> HttpRequest:
        return HttpRequest(
            url=self.url,
            payload={
                "title": message.subject,
                "body": message.body,
                "level": self.LEVELS[str(message.severity)],
                "group": str(self.options.get("group", "dtk")),
            },
        )


class WeComChannel(HttpChannel):
    """WeCom group bot. Markdown so the severity line renders as a heading."""

    type = ChannelType.WECOM

    def request(self, message: Message) -> HttpRequest:
        return HttpRequest(
            url=self.url,
            payload={"msgtype": "markdown", "markdown": {"content": message.markdown}},
        )


class DingTalkChannel(HttpChannel):
    """DingTalk group bot, with optional HMAC signing.

    A bot secured by keyword instead of a secret needs that keyword to appear
    in the text; the title carries it, so nothing extra is required here.
    """

    type = ChannelType.DINGTALK

    def request(self, message: Message) -> HttpRequest:
        secret = self.options.get("secret")
        url = self.url
        if secret:
            url = sign_dingtalk_url(url, str(secret), message.sent_at.timestamp())
        return HttpRequest(
            url=url,
            payload={
                "msgtype": "markdown",
                "markdown": {"title": message.subject, "text": message.markdown},
            },
        )


def sign_dingtalk_url(url: str, secret: str, now: float) -> str:
    """Append DingTalk's ``timestamp`` and ``sign`` query parameters."""
    timestamp = str(int(now * 1000))
    digest = hmac.new(
        secret.encode("utf-8"), f"{timestamp}\n{secret}".encode(), hashlib.sha256
    ).digest()
    signature = quote(base64.b64encode(digest).decode("ascii"), safe="")
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}timestamp={timestamp}&sign={signature}"


class TelegramChannel(HttpChannel):
    """Telegram bot. The token lives in the URL, so it is never logged."""

    type = ChannelType.TELEGRAM

    def request(self, message: Message) -> HttpRequest:
        return HttpRequest(
            url=self.url,
            payload={
                "chat_id": str(self.options.get("chat_id", "")),
                "text": message.plain_text,
                "disable_web_page_preview": True,
            },
        )


class SmtpChannel(_Base):
    """Email. ``smtplib`` is blocking, so a send runs on a worker thread."""

    type = ChannelType.SMTP

    def build_email(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = str(self.options["sender"])
        mail["To"] = ", ".join(self.recipients)
        mail.set_content(message.plain_text)
        return mail

    @property
    def recipients(self) -> tuple[str, ...]:
        raw = self.options.get("recipients") or []
        if isinstance(raw, str):
            return tuple(part.strip() for part in raw.split(",") if part.strip())
        return tuple(str(part).strip() for part in raw if str(part).strip())

    async def send(self, message: Message, client: httpx.AsyncClient) -> None:
        del client  # SMTP does not speak HTTP; the shared client is unused.
        await asyncio.to_thread(self._send_blocking, self.build_email(message))

    def _send_blocking(self, mail: EmailMessage) -> None:
        host = str(self.options["host"])
        port = int(self.options.get("port", 587))
        username = self.options.get("username")
        password = self.options.get("password")
        use_ssl = bool(self.options.get("ssl", False))
        use_starttls = bool(self.options.get("starttls", not use_ssl))

        server: smtplib.SMTP | smtplib.SMTP_SSL
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=SEND_TIMEOUT_SECONDS)
        else:
            server = smtplib.SMTP(host, port, timeout=SEND_TIMEOUT_SECONDS)
        with server:
            if use_starttls and not use_ssl:
                server.starttls()
            if username and password:
                server.login(str(username), str(password))
            server.send_message(mail)


_HTTP_CHANNELS: Final[Mapping[ChannelType, type[HttpChannel]]] = {
    ChannelType.WEBHOOK: WebhookChannel,
    ChannelType.BARK: BarkChannel,
    ChannelType.WECOM: WeComChannel,
    ChannelType.DINGTALK: DingTalkChannel,
    ChannelType.TELEGRAM: TelegramChannel,
}


def build_channel(descriptor: Mapping[str, Any]) -> Channel:
    """Turn one ``notify.channels`` entry into a channel.

    Every URL is validated here rather than at send time, so a bad target is
    rejected while the administrator is still looking at the settings page.
    """
    raw_type = str(descriptor.get("type", "")).strip().lower()
    try:
        channel_type = ChannelType(raw_type)
    except ValueError as exc:
        raise InvalidParam(
            f"unknown notification channel type: {raw_type or '(empty)'}",
            details={"supported": [c.value for c in ChannelType]},
        ) from exc

    language = coerce_language(descriptor.get("language")) or DEFAULT_LANGUAGE
    name = str(descriptor.get("name") or channel_type.value)
    options = {
        key: value
        for key, value in descriptor.items()
        if key not in {"type", "name", "url", "language", "enabled"}
    }

    if channel_type is ChannelType.SMTP:
        host = validate_outbound_host(str(options.get("host", "")))
        if not options.get("sender"):
            raise InvalidParam("smtp channel requires 'sender'")
        channel = SmtpChannel(name=name, url="", language=language, options=options)
        if not channel.recipients:
            raise InvalidParam("smtp channel requires at least one recipient")
        log.debug("ops.notify.channel_built", channel=name, type=channel_type.value, host=host)
        return channel

    if channel_type is ChannelType.TELEGRAM:
        token = str(options.get("token", "")).strip()
        if not token:
            raise InvalidParam("telegram channel requires 'token'")
        if not str(options.get("chat_id", "")).strip():
            raise InvalidParam("telegram channel requires 'chat_id'")
        url = validate_outbound_url(TELEGRAM_API.format(token=token))
    else:
        url = validate_outbound_url(str(descriptor.get("url", "")))

    return _HTTP_CHANNELS[channel_type](name=name, url=url, language=language, options=options)


def build_channels(descriptors: Sequence[Mapping[str, Any]]) -> tuple[Channel, ...]:
    """Build every enabled channel, skipping the ones that do not validate.

    One malformed entry must not silence the other channels: alerting that
    fails closed is how an instance goes quiet without anyone noticing.
    """
    built: list[Channel] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            log.warning("ops.notify.channel_invalid", reason="not an object")
            continue
        if not descriptor.get("enabled", True):
            continue
        try:
            built.append(build_channel(descriptor))
        except InvalidParam as exc:
            log.error(
                "ops.notify.channel_rejected",
                channel=str(descriptor.get("name") or descriptor.get("type") or "?"),
                reason=str(exc),
            )
    return tuple(built)


__all__ = [
    "RETRYABLE_STATUS",
    "SEND_TIMEOUT_SECONDS",
    "TELEGRAM_API",
    "BarkChannel",
    "Channel",
    "ChannelType",
    "DingTalkChannel",
    "HttpChannel",
    "HttpRequest",
    "RetryableDelivery",
    "SmtpChannel",
    "TelegramChannel",
    "WeComChannel",
    "WebhookChannel",
    "build_channel",
    "build_channels",
    "sign_dingtalk_url",
    "validate_outbound_host",
    "validate_outbound_url",
]
