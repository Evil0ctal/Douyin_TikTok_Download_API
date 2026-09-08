"""Runtime configuration: database authority, hot reload and validation.

The failure this guards against is subtle: once the database wins, a user who
edits .env and restarts sees nothing change. The tiering has to be deliberate
and observable, and an invalid stored value must degrade rather than crash.
"""

from __future__ import annotations

from typing import get_args

import pytest

from dtk.core.config import RUNTIME_SETTINGS, Config, Scope, coerce
from dtk.core.types import Platform

# Mostly pure validation logic, so no services; the signing-mode class below
# marks its own coroutines rather than putting the whole module on a loop.
pytestmark = [pytest.mark.integration]


class TestCoercion:
    def test_unknown_key_is_rejected_before_any_write(self):
        """Typos must not silently become settings."""
        with pytest.raises(KeyError):
            coerce("sched.does_not_exist", 1)

    @pytest.mark.parametrize(
        "raw,expected",
        [("yes", True), ("1", True), ("on", True), ("false", False), ("no", False), ("", False)],
    )
    def test_bool_from_string(self, raw, expected):
        assert coerce("notify.enabled", raw) is expected

    def test_list_from_comma_string(self):
        assert coerce("security.cors_allow_origins", "https://a.com, https://b.com") == [
            "https://a.com",
            "https://b.com",
        ]

    def test_list_rejects_a_scalar(self):
        with pytest.raises(ValueError):
            coerce("security.cors_allow_origins", 5)

    def test_numeric_coercion(self):
        assert coerce("sched.max_wait_seconds", "15") == 15
        assert coerce("sched.circuit_risk_threshold", "0.75") == 0.75


class TestRegistry:
    def test_every_setting_declares_a_scope_and_type(self):
        for key, spec in RUNTIME_SETTINGS.items():
            assert spec.key == key
            assert isinstance(spec.scope, Scope)
            assert spec.type_ in (int, float, bool, str, list)

    def test_defaults_round_trip_through_coercion(self):
        """A default that its own validator rejects would break every fresh install."""
        for key, spec in RUNTIME_SETTINGS.items():
            assert coerce(key, spec.default) == spec.default

    def test_ssrf_and_cors_settings_are_sensitive(self):
        """Widening either enlarges the attack surface, so they need confirmation."""
        assert RUNTIME_SETTINGS["security.url_allowlist"].scope is Scope.SENSITIVE
        assert RUNTIME_SETTINGS["security.cors_allow_origins"].scope is Scope.SENSITIVE
        assert RUNTIME_SETTINGS["security.enable_task_webhook"].scope is Scope.SENSITIVE

    def test_dangerous_defaults_are_off(self):
        cfg = Config.defaults()
        assert cfg.get("security.url_allowlist") == []
        assert cfg.get("security.enable_task_webhook") is False
        assert cfg.get("security.cors_allow_origins") == []
        assert cfg.get("system.check_updates") is False


class TestSnapshot:
    def test_snapshot_is_replaced_not_mutated(self):
        """An in-flight request must keep the values it started with."""
        first = Config.defaults()
        second = Config({**first.as_dict(), "sched.max_wait_seconds": 99}, version=1)
        assert first.get("sched.max_wait_seconds") != 99
        assert second.get("sched.max_wait_seconds") == 99

    def test_unknown_key_raises_rather_than_returning_none(self):
        with pytest.raises(KeyError):
            Config.defaults().get("nope")


class TestSigningModeReachesTheSigner:
    """The console's signing page is only true if a change reaches the worker.

    The worker holds one long-lived registry, so a mode captured at construction
    would leave the page reporting a value no request uses until the process is
    restarted - and a restart is the last thing anyone wants at the moment a
    signer has gone stale. These assert the wiring end to end, from the settings
    snapshot to the signer the registry actually hands back.
    """

    @staticmethod
    def _registry(config_holder):
        from dtk.signing.registry import SignerRegistry
        from dtk.worker.runtime import _signing_policy

        class Stub:
            def __init__(self, name: str) -> None:
                self.name = name

            async def sign(
                self, spec, identity_fingerprint, session=None
            ):  # pragma: no cover - unused
                raise AssertionError("selection is what is under test, not signing")

            async def health(self):
                from dtk.signing.base import SignerHealth

                return SignerHealth(signer=self.name, healthy=True)

        native, rpc = Stub("native"), Stub("browser")
        registry = SignerRegistry(
            {Platform.DOUYIN: native},
            rpc,
            policy=_signing_policy(lambda: config_holder[0]),
        )
        return registry, native, rpc

    @pytest.mark.asyncio
    async def test_changing_the_mode_changes_the_signer_without_rebuilding(self):
        holder = [Config.defaults()]
        registry, native, rpc = self._registry(holder)
        key = (Platform.DOUYIN, "/aweme/v1/web/aweme/detail/")

        assert await registry._select(key) is native, "native is the shipped default"

        # Exactly what settings_store does on a console write: build a new
        # snapshot and swap the reference. No registry is rebuilt.
        holder[0] = Config({**holder[0].as_dict(), "signing.mode": "rpc"}, version=1)
        assert await registry._select(key) is rpc

        holder[0] = Config({**holder[0].as_dict(), "signing.mode": "native"}, version=2)
        assert await registry._select(key) is native

    @pytest.mark.asyncio
    async def test_the_console_cannot_store_a_mode_the_registry_would_ignore(self):
        """The picker's options and the accepted values are one list, not two."""
        from dtk.signing.registry import SigningMode

        accepted = set(get_args(SigningMode))
        assert set(RUNTIME_SETTINGS["signing.mode"].choices or ()) == accepted

        with pytest.raises(ValueError, match="must be one of"):
            coerce("signing.mode", "browser")


class TestUrlAllowlistIsPinnedDownOnWrite:
    """The one setting whose entries decide what the service may fetch.

    A rejected value naming the offender is worth far more here than a stored
    string every reader has to re-interpret, so the checking happens once, on
    the way in, and readers are handed hostnames they can trust.
    """

    def test_entries_are_canonicalized(self):
        assert coerce("security.url_allowlist", "  CDN.Example.COM. , a.example.com ") == [
            "a.example.com",
            "cdn.example.com",
        ]

    @pytest.mark.parametrize(
        "value",
        ["localhost", "127.0.0.1", "169.254.169.254", "db.internal", "*.example.com", "printer"],
    )
    def test_an_entry_that_would_widen_the_ssrf_surface_is_refused(self, value):
        with pytest.raises(ValueError):
            coerce("security.url_allowlist", value)

    def test_a_stored_list_reaches_the_host_check(self):
        """The console edit, the snapshot and the allowlist are one path."""
        from dtk.core.config import extra_url_hosts
        from dtk.urls import is_allowed_host

        before = Config.defaults()
        assert extra_url_hosts(before) == frozenset()
        assert not is_allowed_host("https://cdn.example.com/r/1")

        after = Config(
            {
                **before.as_dict(),
                "security.url_allowlist": coerce("security.url_allowlist", "cdn.example.com"),
            },
            version=1,
        )
        hosts = extra_url_hosts(after)
        assert hosts == frozenset({"cdn.example.com"})
        assert is_allowed_host("https://cdn.example.com/r/1", extra_hosts=hosts)


class TestCircuitThresholdsReachTheBreaker:
    """Three settings the console offers as the breaker's tuning knobs.

    They were declared and never read: CircuitConfig's own defaults won every
    time, so an operator who widened the threshold after a bad afternoon saw an
    audit row and no change in behaviour.
    """

    def test_the_settings_and_the_dataclass_agree_on_the_defaults(self):
        """A fresh install must not behave differently from what the page shows."""
        from dtk.scheduler.circuit import CircuitConfig

        shipped = CircuitConfig()
        cfg = Config.defaults()
        assert cfg.get("sched.circuit_risk_threshold") == shipped.risk_threshold
        assert cfg.get("sched.circuit_min_samples") == shipped.min_samples
        assert cfg.get("sched.circuit_min_identities") == shipped.min_identities

    def test_a_changed_setting_changes_the_thresholds(self):
        from dtk.scheduler.circuit import circuit_config

        tuned = Config(
            {
                **Config.defaults().as_dict(),
                "sched.circuit_risk_threshold": 0.9,
                "sched.circuit_min_samples": 100,
                "sched.circuit_min_identities": 5,
            },
            version=1,
        )
        built = circuit_config(tuned)
        assert (built.risk_threshold, built.min_samples, built.min_identities) == (0.9, 100, 5)
        # Not exposed, so they keep the values the breaker ships with.
        assert built.open_seconds == 300

        # Assembled the way the worker assembles it: the scheduler reads its
        # thresholds off this object on every release.
        from dtk.scheduler.scheduler import SchedulerConfig

        assert SchedulerConfig(circuit=built).circuit.min_samples == 100

    def test_the_worker_builds_the_breaker_from_the_settings(self):
        """The knobs are only real if the process that trips circuits is given them."""
        import inspect

        from dtk.worker.runtime import build_runtime

        assert "circuit=circuit_config(" in inspect.getsource(build_runtime), (
            "worker.runtime.build_runtime must pass circuit=circuit_config(current) to "
            "SchedulerConfig, otherwise CircuitConfig's hardcoded defaults win and the "
            "three sched.circuit_* settings are inert."
        )


class TestTheAllowlistReachesTheExpander:
    """The setting is only true if the process that follows redirects has it."""

    @pytest.mark.asyncio
    async def test_the_worker_expands_through_an_allowlisted_hop(self, monkeypatch):
        import uuid

        from dtk.worker import parsing
        from dtk.worker.main import TaskRun, TaskWorker

        aweme_id = "7345492945006595379"
        hops = {
            "https://v.douyin.com/abc123": "https://cdn.example.com/r/abc123",
            "https://cdn.example.com/r/abc123": f"https://www.douyin.com/video/{aweme_id}",
        }
        seen: dict[str, frozenset[str]] = {}

        def fake_egress_fetcher(egress, *, timeout=0.0, extra_hosts=frozenset()):
            seen["fetcher"] = extra_hosts

            async def fetch(url: str) -> str | None:
                return hops.get(url)

            return fetch

        monkeypatch.setattr(parsing, "egress_fetcher", fake_egress_fetcher)

        config = Config(
            {
                **Config.defaults().as_dict(),
                "security.url_allowlist": coerce("security.url_allowlist", "cdn.example.com"),
            },
            version=1,
        )
        worker = TaskWorker(
            fetch=None,  # type: ignore[arg-type]
            store=None,  # type: ignore[arg-type]
            config=config,
        )
        run = TaskRun(
            id=uuid.uuid4(),
            endpoint="parse",
            params={"url": "https://v.douyin.com/abc123"},
        )

        endpoint, params = await worker._resolve_endpoint(run)
        assert (endpoint, params) == ("douyin.content_detail", {"content_id": aweme_id})
        # Both halves of the hop check get the same list, or the fetcher refuses
        # the hop the expander just allowed.
        assert seen["fetcher"] == frozenset({"cdn.example.com"})
