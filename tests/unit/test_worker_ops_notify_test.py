"""The console's notification Test button, as the worker runs it.

Three things carry the weight here. A channel descriptor holds a URL that is
the credential for most providers, while this result is stored in
``tasks.result`` and rendered in a browser - the last test in the file drives a
real notifier whose channels quote their own URL back in the error the way
``httpx`` does, and looks for the secrets in the answer. A test alert must not
be deduplicated, because the second press of a button reporting silence is how
a working channel gets replaced. And "nothing was sent because alerting is off"
has to read differently from "it was sent and refused", or the operator debugs
the wrong end.

The notifier is real throughout; only its HTTP transport is a mock, so the
channels, the language inheritance and the redaction under test are the ones
that ship.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from dtk.api.routes.operations import unwrap
from dtk.core.errors import DtkError, ErrorCode
from dtk.core.types import Language
from dtk.ops import notify
from dtk.ops.channels import build_channels
from dtk.ops.notify import NotifyEvent, Severity
from dtk.worker.ops import notify_test

#: Channel targets. Everything after the host is a bearer credential, and the
#: leak test hunts for exactly these strings.
WEBHOOK_URL = "https://hooks.example.com/services/T00/B00/whsecret"
TELEGRAM_TOKEN = "77:AAtelegramsecret"
DINGTALK_URL = "https://oapi.dingtalk.com/robot/send?access_token=dingsecret"
DINGTALK_SIGNING_SECRET = "SECdingsign"

WEBHOOK = {"type": "webhook", "name": "ops", "url": WEBHOOK_URL}
TELEGRAM = {
    "type": "telegram",
    "name": "phone",
    "token": TELEGRAM_TOKEN,
    "chat_id": "4242",
    "language": "zh",
}
DINGTALK = {
    "type": "dingtalk",
    "name": "group",
    "url": DINGTALK_URL,
    "secret": DINGTALK_SIGNING_SECRET,
}


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class StubConfig:
    """The two settings :func:`notifier_from_config` reads, plus the channels."""

    def __init__(self, channels: list[Mapping[str, Any]], *, enabled: bool = True) -> None:
        self._values: dict[str, Any] = {
            "notify.enabled": enabled,
            "notify.language": "en",
            "notify.channels": channels,
        }

    def get(self, key: str) -> Any:
        return self._values.get(key)


class FakeRedis:
    """Only the commands the deduplicator uses, so a dedup can be caught."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)


class Recorder:
    """A mock transport that keeps every request and answers as told."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda _request: httpx.Response(200))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def a_deps(config: StubConfig, *, notifier: Any = ...) -> Any:
    """An ``OperationDeps`` with only the members this job reads populated."""
    return SimpleNamespace(
        config=lambda: config,
        cipher=object(),
        pool=object(),
        transport=object(),
        signers=object(),
        filler=None,
        prober=None,
        notifier=object() if notifier is ... else notifier,
        rpc=None,
    )


def install(
    monkeypatch: pytest.MonkeyPatch,
    recorder: Recorder,
    redis: FakeRedis | None = None,
) -> httpx.AsyncClient:
    """Build the job's notifier for real, over a mock transport.

    Only the HTTP client is substituted: the channels come from the config the
    job passes in, which is the half of this job's behaviour worth testing.
    """
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))

    def factory(config: Any, **_kwargs: Any) -> notify.Notifier:
        return notify.notifier_from_config(
            config,
            client=client,
            redis=redis,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(notify_test, "notifier_from_config", factory)
    monkeypatch.setattr(notify, "RETRY_BACKOFF_SECONDS", 0.0)
    return client


async def run_job(deps: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    return await notify_test.run(deps, None, params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# the delivery
# --------------------------------------------------------------------------


async def test_the_named_channel_is_the_only_one_that_receives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK, DINGTALK]))

    result = await run_job(deps, {"channel": "ops"})

    data, meta = unwrap(result)
    assert data["channel"] == "ops"
    assert data["status"] == "sent"
    assert data["sent"] is True
    assert data["delivered"] == ["ops"]
    assert data["failed"] == {}
    assert meta["endpoint"] == "notify.test"
    assert [str(request.url) for request in recorder.requests] == [WEBHOOK_URL]
    await client.aclose()


async def test_no_channel_means_every_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK, TELEGRAM]))

    result = await run_job(deps, {"channel": None})

    data, _meta = unwrap(result)
    assert data["channel"] is None
    assert data["delivered"] == ["ops", "phone"]
    assert len(recorder.requests) == 2
    await client.aclose()


async def test_the_body_is_rendered_in_each_channels_own_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalogue is asked, per channel, for the test keys - never a literal."""
    asked: list[tuple[str, Any, dict[str, Any]]] = []

    def fake_t(key: str, language: Any = Language.EN, /, **args: Any) -> str:
        asked.append((key, language, args))
        return f"{key}:{getattr(language, 'value', language)}"

    monkeypatch.setattr(notify, "t", fake_t)
    recorder = Recorder()
    client = install(monkeypatch, recorder)
    # The webhook inherits notify.language (en); the telegram bot asked for zh.
    deps = a_deps(StubConfig([WEBHOOK, TELEGRAM]))

    await run_job(deps, {"channel": None})

    bodies = {(key, language) for key, language, _args in asked}
    assert ("notify.test.title", Language.EN) in bodies
    assert ("notify.test.body", Language.ZH) in bodies
    assert all(
        args == {"channel": "phone"}
        for key, language, args in asked
        if key == "notify.test.body" and language is Language.ZH
    )
    assert recorder.payloads[0]["language"] == "en"
    await client.aclose()


# --------------------------------------------------------------------------
# deduplication: a test must never be suppressed
# --------------------------------------------------------------------------


def test_the_trigger_table_exempts_the_test_from_suppression() -> None:
    spec = notify.TRIGGERS[NotifyEvent.TEST]
    assert spec.severity is Severity.INFO
    assert spec.dedup_seconds == 0


async def test_pressing_the_button_twice_sends_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window here would report a working channel as a silent one."""
    recorder = Recorder()
    redis = FakeRedis()
    client = install(monkeypatch, recorder, redis)
    deps = a_deps(StubConfig([WEBHOOK]))

    first = await run_job(deps, {"channel": "ops"})
    second = await run_job(deps, {"channel": "ops"})

    assert unwrap(first)[0]["status"] == "sent"
    assert unwrap(second)[0]["status"] == "sent"
    assert len(recorder.requests) == 2
    # Not merely un-suppressed: no mark was written, so nothing to expire.
    assert redis.store == {}
    await client.aclose()


async def test_a_test_does_not_consume_the_window_of_a_real_alert() -> None:
    """Proving the channel first must not buy silence for the pool alarm."""
    redis = FakeRedis()
    notifier = notify.Notifier(
        build_channels([WEBHOOK]),
        redis=redis,  # type: ignore[arg-type]
        client=httpx.AsyncClient(transport=httpx.MockTransport(Recorder())),
    )

    await notifier.send_test("ops")
    delivery = await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")

    assert delivery.suppressed is False
    assert delivery.sent == ("ops",)
    await notifier.aclose()


# --------------------------------------------------------------------------
# the four outcomes
# --------------------------------------------------------------------------


async def test_alerting_switched_off_is_not_a_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK], enabled=False))

    data, _meta = unwrap(await run_job(deps, {"channel": "ops"}))

    assert data["status"] == "disabled"
    assert data["sent"] is False
    assert data["failed"] == {}
    assert recorder.requests == []
    await client.aclose()


async def test_a_channel_that_refuses_is_a_finished_task_not_a_failed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(lambda _request: httpx.Response(403))
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK]))

    data, _meta = unwrap(await run_job(deps, {"channel": "ops"}))

    assert data["status"] == "failed"
    assert data["sent"] is False
    assert data["delivered"] == []
    assert "403" in data["failed"]["ops"]
    await client.aclose()


async def test_one_dead_channel_among_several_is_reported_as_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if "telegram" in str(request.url) else 200)

    recorder = Recorder(respond)
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK, TELEGRAM]))

    data, _meta = unwrap(await run_job(deps, {"channel": None}))

    assert data["status"] == "partial"
    assert data["sent"] is True
    assert data["delivered"] == ["ops"]
    assert list(data["failed"]) == ["phone"]
    await client.aclose()


async def test_an_unknown_channel_name_is_a_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK]))

    with pytest.raises(DtkError) as caught:
        await run_job(deps, {"channel": "typo"})

    assert caught.value.code is ErrorCode.NOT_FOUND
    assert caught.value.details == {"channel": "typo", "known": ["ops"]}
    assert recorder.requests == []
    await client.aclose()


async def test_an_instance_with_no_channels_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOT_CONFIGURED, because there is nothing to find rather than a miss."""
    client = install(monkeypatch, Recorder())
    deps = a_deps(StubConfig([]))

    with pytest.raises(DtkError) as caught:
        await run_job(deps, {"channel": None})

    assert caught.value.code is ErrorCode.NOT_CONFIGURED
    assert caught.value.retryable is False
    await client.aclose()


async def test_a_worker_without_a_notifier_says_so() -> None:
    deps = a_deps(StubConfig([WEBHOOK]), notifier=None)

    with pytest.raises(DtkError) as caught:
        await run_job(deps, {"channel": "ops"})

    assert caught.value.code is ErrorCode.INTERNAL


async def test_a_channel_that_is_not_a_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, Recorder())
    deps = a_deps(StubConfig([WEBHOOK]))

    with pytest.raises(DtkError) as caught:
        await run_job(deps, {"channel": ["ops"]})

    assert caught.value.code is ErrorCode.INVALID_PARAM
    await client.aclose()


# --------------------------------------------------------------------------
# the channel list is the live one
# --------------------------------------------------------------------------


async def test_a_channel_saved_after_the_worker_started_is_testable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker's own notifier froze its channels at boot; this job must not.

    The Test button is pressed hardest on a row that was saved a minute ago,
    and answering "no such channel" about it would be the very doubt the button
    exists to remove.
    """
    recorder = Recorder()
    client = install(monkeypatch, recorder)
    stale = notify.Notifier(build_channels([DINGTALK]))
    deps = a_deps(StubConfig([WEBHOOK]), notifier=stale)

    data, _meta = unwrap(await run_job(deps, {"channel": "ops"}))

    assert data["status"] == "sent"
    assert [channel.name for channel in stale.channels] == ["group"]
    await client.aclose()


# --------------------------------------------------------------------------
# the credential must not reach the result
# --------------------------------------------------------------------------


async def test_no_target_reaches_the_stored_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure reason is the one field that could quote a webhook URL.

    ``httpx`` puts the request URL in its connection errors, and for Telegram
    and DingTalk that URL carries the bot token. This result is stored in
    ``tasks.result`` and rendered in a browser, so it may carry none of it.
    """

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to connect to {request.url}")

    recorder = Recorder(explode)
    client = install(monkeypatch, recorder)
    deps = a_deps(StubConfig([WEBHOOK, TELEGRAM, DINGTALK]))

    result = await run_job(deps, {"channel": None})

    data, _meta = unwrap(result)
    assert data["status"] == "failed"
    assert sorted(data["failed"]) == ["group", "ops", "phone"]
    rendered = json.dumps(result)
    for secret in (
        WEBHOOK_URL,
        TELEGRAM_TOKEN,
        DINGTALK_URL,
        DINGTALK_SIGNING_SECRET,
        "whsecret",
        "dingsecret",
        "hooks.example.com",
        "api.telegram.org",
        "oapi.dingtalk.com",
    ):
        assert secret not in rendered, f"{secret} leaked into the task result"
    await client.aclose()


async def test_a_dingtalk_signature_does_not_ride_out_in_the_failure_reason() -> None:
    """The case the leak test looked like it covered and did not.

    DingTalk signs at request time: the URL that reaches httpx is the stored one
    plus ``&timestamp=<ms>&sign=<base64 HMAC>``. failure_reason used to scrub by
    literal substring of the STORED url, so the access token went and the
    signature stayed. Asserting only that the raw secret is absent passes by
    construction for any HMAC, which is why this asserts on the signature.
    """
    from dtk.ops.channels import ChannelType
    from dtk.ops.notify import failure_reason

    channel = SimpleNamespace(
        type=ChannelType.DINGTALK,
        url="https://oapi.dingtalk.com/robot/send?access_token=abc123def456",
    )
    signed = f"{channel.url}&timestamp=1788827382300&sign=yDg3CsSGvOltC8N6uDmUSM6YeGizMFHboAV%2B85i659s%3D"
    reason = failure_reason(cast(Any, channel), ConnectionError(f"cannot reach {signed}"))

    assert "sign=" not in reason or "sign=***" in reason
    assert "yDg3CsSG" not in reason
    assert "abc123def456" not in reason


async def test_an_smtp_channel_is_scrubbed_even_though_it_has_no_url() -> None:
    """The guard that skipped the scrub entirely for one channel type.

    SmtpChannel is built with url="", so the `if url:` branch never ran and
    whatever the exception said passed through verbatim.
    """
    from dtk.ops.channels import ChannelType
    from dtk.ops.notify import failure_reason

    channel = SimpleNamespace(type=ChannelType.SMTP, url="")
    reason = failure_reason(
        cast(Any, channel),
        ConnectionError("smtp://alerts:hunter2@mail.example.com:587 refused"),  # SYNTHETIC
    )

    assert "hunter2" not in reason


async def test_no_channels_at_all_is_not_the_same_as_an_unknown_channel() -> None:
    """Two problems with two different next steps.

    A fresh install has nothing to test and needs to add a channel; a named
    channel that is absent is a stale tab or a typo. Answering both with
    NOT_FOUND sent every new operator looking for a channel they had never
    created.
    """
    from dtk.core.errors import ErrorCode
    from dtk.ops.notify import Notifier

    with pytest.raises(DtkError) as empty:
        await Notifier([]).send_test(None)
    assert empty.value.code is ErrorCode.NOT_CONFIGURED

    configured = build_channels([{"type": "webhook", "name": "ops", "url": "https://x.test/h"}])
    with pytest.raises(DtkError) as unknown:
        await Notifier(configured).send_test("nope")
    assert unknown.value.code is ErrorCode.NOT_FOUND
    assert unknown.value.details["known"] == ["ops"]
