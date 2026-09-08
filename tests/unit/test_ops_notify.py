"""Notification triggers, deduplication and dispatch.

The deduplication tests drive a fake clock rather than sleeping: the windows in
doc 15 run from fifteen minutes to a day, so wall-clock testing is not an
option, and a window that silently stopped working would only be discovered by
a user whose phone stopped ringing. Per-channel payload shapes live in
``test_ops_channels.py``.

The last section reaches out of this module into the three places that raise an
alert but had no test holding them to it. A declared trigger nobody emits is
indistinguishable, from the console, from one that has simply not fired yet, so
the trigger table alone proves nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from dtk.core.errors import Internal
from dtk.core.types import Language, Outcome, Platform
from dtk.ops import channels, notify
from dtk.ops.channels import ChannelType
from dtk.ops.notify import NotifyEvent, Severity
from dtk.scheduler import circuit
from dtk.scheduler import scheduler as scheduler_module
from dtk.scheduler.leases import Lease
from dtk.scheduler.scheduler import Scheduler, SchedulerConfig
from dtk.signing.base import (
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    SignerHealth,
    SigningFingerprint,
    StaticFingerprint,
)
from dtk.signing.registry import RegistryPolicy, SignerRegistry
from dtk.worker.ops import backup as backup_op


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
    def __init__(
        self,
        name: str,
        language: Language = Language.EN,
        events: list[str] | None = None,
    ) -> None:
        self.name = name
        self.language = language
        self.type = ChannelType.WEBHOOK
        # Where a real channel keeps its subscription, so a test can narrow one.
        self.options: dict[str, object] = {"events": events} if events is not None else {}
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
        [],
        redis=FakeRedis(),
        clock=Clock(),
        client=borrowed,  # type: ignore[arg-type]
    )

    await notifier.aclose()
    assert borrowed.is_closed is False

    created = await notifier._http()
    await notifier.aclose()
    assert created.is_closed is True
    await borrowed.aclose()


async def test_notify_delivers_only_to_the_channels_that_subscribed() -> None:
    """Asserted through notify(), not through the helper it calls.

    The console writes the subscription into the descriptor and nothing read it,
    so a channel narrowed to one event kept receiving all of them - configured
    in the UI, unconfigured in fact. An earlier version of this test called the
    selector directly and still passed with the filter unwired, which proves
    nothing about delivery.
    """
    pool_only = RecordingChannel("pool", events=["pool_empty"])
    everything = RecordingChannel("everything")
    notifier, _redis = make_notifier([pool_only, everything], Clock())

    await notifier.notify(NotifyEvent.PROXY_UNHEALTHY, proxy="p1", failures=3)

    assert pool_only.received == [], "a narrowed channel was paged for an event it declined"
    assert len(everything.received) == 1

    await notifier.notify(NotifyEvent.POOL_EMPTY, platform="douyin")
    assert len(pool_only.received) == 1
    assert len(everything.received) == 2


async def test_an_empty_subscription_list_still_means_every_event() -> None:
    """How the console encodes "all": it omits the field, or leaves it empty."""
    listed = RecordingChannel("all", events=[])
    absent = RecordingChannel("also-all")
    notifier, _redis = make_notifier([listed, absent], Clock())

    for event in (NotifyEvent.POOL_EMPTY, NotifyEvent.PROXY_UNHEALTHY):
        assert [c.name for c in notifier._subscribers(event)] == ["all", "also-all"]


async def test_a_channel_added_after_startup_is_used_without_a_restart() -> None:
    """The worker holds one notifier for the life of the process.

    Built from a snapshot, it kept the channels it saw at boot, so an operator
    who added one in the console and waited for the next alert waited forever -
    and waiting for an alert is exactly how someone confirms a channel works.
    """
    from dtk.core.config import Config

    snapshot = {"notify.enabled": True, "notify.language": "en", "notify.channels": []}
    live = Config({**Config.defaults().as_dict(), **snapshot}, version=1)
    holder = [live]

    notifier = notify.notifier_from_config(lambda: holder[0])
    assert notifier.channels == ()

    holder[0] = Config(
        {
            **live.as_dict(),
            "notify.channels": [
                {"type": "webhook", "name": "ops", "url": "https://hooks.test/added"}
            ],
        },
        version=2,
    )
    assert [c.name for c in notifier.channels] == ["ops"]


async def test_turning_alerts_off_takes_effect_on_the_next_alert() -> None:
    """The same for the master switch, which is the more urgent direction."""
    from dtk.core.config import Config

    base = Config.defaults().as_dict()
    on = Config({**base, "notify.enabled": True, "notify.channels": []}, version=1)
    holder = [on]
    notifier = notify.notifier_from_config(lambda: holder[0])
    assert notifier._enabled is True

    holder[0] = Config({**on.as_dict(), "notify.enabled": False}, version=2)
    assert notifier._enabled is False


# --------------------------------------------------------------------------
# the emitters
#
# Three triggers were declared here and raised nowhere: an operator ticking
# "endpoint circuit opened", "signing algorithm may be stale" or "backup
# failed" in the console was arming an alert that could not fire. Each test
# below fails if its emitter goes away again.
# --------------------------------------------------------------------------


async def drain_alerts() -> None:
    """Let the fire-and-forget deliveries finish before asserting on them.

    The scheduler and the signer registry schedule their alerts rather than
    awaiting them - the property pinned by
    ``test_the_request_does_not_wait_for_the_alert`` - so asserting straight
    after the call would be asserting on a task that has not run yet.
    """
    for _ in range(5):
        pending = notify.pending_alerts()
        if not pending:
            return
        await asyncio.gather(*pending)


class NoCandidates:
    """The scheduler's source, unused here: releasing a lease asks for none."""

    async def candidates(self, platform: Platform, state: Any) -> tuple[()]:
        del platform, state
        return ()


#: What the breaker stores when it trips: the code plus the numbers behind it.
TRIPPED = circuit.TripReason(
    circuit.RISK_ACROSS_IDENTITIES,
    {"risk_rate": 0.71, "samples": 20, "identities": 4},
).encode()

LEASE = Lease(
    identity_id="11111111-1111-1111-1111-111111111111",
    endpoint="douyin.content_detail",
    lease_id="lease-1",
    tokens_left=1.0,
)


@pytest.fixture
def tripping_circuit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Take Redis out of ``Scheduler.release`` and force the trip decision.

    What Redis does under these four calls is an integration concern and is
    covered there. What matters here is the step after them, which existed in
    the trigger table and nowhere else.
    """
    tripped: list[str] = []

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def should_trip(endpoint: str, cfg: Any, *, now: float) -> tuple[bool, str]:
        del endpoint, cfg, now
        return True, TRIPPED

    async def trip(endpoint: str, cfg: Any, reason: str, *, now: float) -> None:
        del cfg, reason, now
        tripped.append(endpoint)

    monkeypatch.setattr(scheduler_module, "release", noop)
    monkeypatch.setattr(circuit, "record", noop)
    monkeypatch.setattr(circuit, "should_trip", should_trip)
    monkeypatch.setattr(circuit, "trip", trip)
    return tripped


async def test_a_tripped_endpoint_pages_the_operator(tripping_circuit: list[str]) -> None:
    """The alert an operator most expects, from the only place that knows."""
    channel = RecordingChannel("ops")
    notifier, _redis = make_notifier([channel], Clock())
    scheduler = Scheduler(NoCandidates(), SchedulerConfig(), alerter=notifier)

    await scheduler.release(LEASE, Outcome.RISK_CONTROL)
    await drain_alerts()

    assert tripping_circuit == ["douyin.content_detail"]
    assert [message.event for message in channel.received] == [NotifyEvent.ENDPOINT_CIRCUIT_OPEN]
    body = channel.received[0].body
    # The numbers the console shows for this trip, rendered the same way.
    assert "douyin.content_detail" in body
    assert "71%" in body
    assert "20" in body
    assert str(SchedulerConfig().circuit.open_seconds) in body
    await notifier.aclose()


async def test_a_tripped_endpoint_pages_once_however_many_requests_trip_it(
    tripping_circuit: list[str],
) -> None:
    """The window is what makes this alert survivable; it has to be claimed."""
    channel = RecordingChannel("ops")
    notifier, _redis = make_notifier([channel], Clock())
    scheduler = Scheduler(NoCandidates(), SchedulerConfig(), alerter=notifier)

    for _ in range(20):
        await scheduler.release(LEASE, Outcome.RISK_CONTROL)
    await drain_alerts()

    assert len(tripping_circuit) == 20
    assert len(channel.received) == 1, "the 30-minute window was not consulted"
    await notifier.aclose()


async def test_the_request_does_not_wait_for_the_alert(tripping_circuit: list[str]) -> None:
    """A webhook that hangs may not become the latency of the request.

    ``release`` runs inside the fetch path, and a channel taking its full
    timeout twice would add those seconds to whichever request happened to trip
    the circuit - a slow alert turning into a slow platform.
    """
    delivering = asyncio.Event()
    finish = asyncio.Event()

    class SlowAlerter:
        async def notify(self, event: NotifyEvent, /, **args: Any) -> None:
            del event, args
            delivering.set()
            await finish.wait()

    scheduler = Scheduler(NoCandidates(), SchedulerConfig(), alerter=SlowAlerter())
    # Bounded rather than a plain await: an emitter that went back to awaiting
    # its delivery would hang here forever, and a hanging suite says less than
    # a failing test.
    await asyncio.wait_for(scheduler.release(LEASE, Outcome.RISK_CONTROL), timeout=1)

    await asyncio.sleep(0)
    assert delivering.is_set(), "the alert was never scheduled"
    assert not finish.is_set()
    finish.set()
    await drain_alerts()


async def test_an_alert_that_fails_does_not_fail_the_request(tripping_circuit: list[str]) -> None:
    """Nor may a broken channel turn a tripped endpoint into a broken release."""

    class Exploding:
        async def notify(self, event: NotifyEvent, /, **args: Any) -> None:
            del event, args
            raise RuntimeError("the webhook host is down")

    scheduler = Scheduler(NoCandidates(), SchedulerConfig(), alerter=Exploding())

    await scheduler.release(LEASE, Outcome.RISK_CONTROL)
    await drain_alerts()


class OneParamSigner:
    """A signer that always contributes the same value for one parameter."""

    def __init__(self, name: str, value: str, param: str = "X-Bogus") -> None:
        self.name = name
        self._value = value
        self._param = param

    async def sign(
        self, spec: RequestSpec, identity_fingerprint: SigningFingerprint
    ) -> SignedParams:
        del spec, identity_fingerprint
        return SignedParams(
            query=f"{self._param}={self._value}",
            params={self._param: self._value},
            signer=self.name,
            algorithm=SignatureAlgorithm.X_BOGUS,
        )

    async def health(self) -> SignerHealth:
        return SignerHealth(signer=self.name, healthy=True)


SHADOW_SPEC = RequestSpec.get(
    "https://www.douyin.com/aweme/v1/web/aweme/detail/",
    params={"aweme_id": "7345492945006595379"},
)
SHADOW_FINGERPRINT = StaticFingerprint(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")


async def test_a_stale_signature_pages_the_operator() -> None:
    """The registry's hook had no callback, so the mismatch went nowhere."""
    channel = RecordingChannel("ops")
    notifier, _redis = make_notifier([channel], Clock())
    registry = SignerRegistry(
        {Platform.DOUYIN: OneParamSigner("native", "native-sig")},
        OneParamSigner("browser", "browser-sig"),
        policy=RegistryPolicy(mode="auto"),
        on_alert=notify.signing_alert_hook(notifier),
    )

    assert await registry.compare_shadow(SHADOW_SPEC, SHADOW_FINGERPRINT) is False
    await drain_alerts()

    assert [message.event for message in channel.received] == [NotifyEvent.SIGNATURE_STALE]
    # Asserted on the arguments rather than on the sentence: the wording is a
    # locale string, and it is the parameter *names* that must travel - a
    # signature value in an alert body would be the leak this trigger exists to
    # report.
    assert channel.received[0].details == {
        "platform": "douyin",
        "endpoint": "/aweme/v1/web/aweme/detail/",
        "parameters": "X-Bogus",
    }
    assert "native-sig" not in channel.received[0].plain_text

    assert await registry.compare_shadow(SHADOW_SPEC, SHADOW_FINGERPRINT) is False
    await drain_alerts()
    assert len(channel.received) == 1, "the 24-hour window was not consulted"
    await notifier.aclose()


def failing_backup_deps(notifier: notify.Notifier) -> Any:
    """Only the two members the job reads. Passing nothing else is the point."""
    return cast(Any, SimpleNamespace(secret_key="b" * 48, notifier=notifier))


async def test_a_failed_backup_pages_the_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failure nobody watches: a backup that stopped running months ago."""
    monkeypatch.setenv("DTK_SECRET_KEY", "b" * 48)
    monkeypatch.setenv("DTK_BACKUP_DIR", str(tmp_path / "backups"))
    channel = RecordingChannel("ops")
    notifier, _redis = make_notifier([channel], Clock())

    async def full_disk(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device", str(tmp_path / "backups" / "dtk.tar.gz"))

    monkeypatch.setattr(backup_op, "create_backup", full_disk)

    with pytest.raises(Internal):
        await backup_op.run(failing_backup_deps(notifier), cast(Any, None), {})

    assert [message.event for message in channel.received] == [NotifyEvent.BACKUP_FAILED]
    body = channel.received[0].body
    assert "No space left on device" in body
    # The archive's path describes the host to whoever receives the webhook.
    assert str(tmp_path) not in channel.received[0].plain_text
    await notifier.aclose()


async def test_a_backup_that_fails_for_any_other_reason_pages_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A database that goes away mid-export leaves the same hole in the history.

    Its message is not quoted either: a driver puts its DSN in the exception
    text, and this one leaves the host.
    """
    monkeypatch.setenv("DTK_SECRET_KEY", "b" * 48)
    monkeypatch.setenv("DTK_BACKUP_DIR", str(tmp_path / "backups"))
    channel = RecordingChannel("ops")
    notifier, _redis = make_notifier([channel], Clock())

    async def database_gone(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            'connection to server at "db", port 5432 failed: password authentication'
        )

    monkeypatch.setattr(backup_op, "create_backup", database_gone)

    with pytest.raises(RuntimeError):
        await backup_op.run(failing_backup_deps(notifier), cast(Any, None), {})

    assert [message.event for message in channel.received] == [NotifyEvent.BACKUP_FAILED]
    body = channel.received[0].body
    assert "RuntimeError" in body
    assert "5432" not in body
    await notifier.aclose()
