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
        assert RUNTIME_SETTINGS["security.enable_download_proxy"].scope is Scope.SENSITIVE

    def test_dangerous_defaults_are_off(self):
        cfg = Config.defaults()
        assert cfg.get("security.enable_download_proxy") is False
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

            async def sign(self, spec, identity_fingerprint):  # pragma: no cover - unused
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

        assert await registry._select(key) is rpc, "rpc is the shipped default"

        # Exactly what settings_store does on a console write: build a new
        # snapshot and swap the reference. No registry is rebuilt.
        holder[0] = Config({**holder[0].as_dict(), "signing.mode": "native"}, version=1)
        assert await registry._select(key) is native

        holder[0] = Config({**holder[0].as_dict(), "signing.mode": "rpc"}, version=2)
        assert await registry._select(key) is rpc

    @pytest.mark.asyncio
    async def test_the_console_cannot_store_a_mode_the_registry_would_ignore(self):
        """The picker's options and the accepted values are one list, not two."""
        from dtk.signing.registry import SigningMode

        accepted = set(get_args(SigningMode))
        assert set(RUNTIME_SETTINGS["signing.mode"].choices or ()) == accepted

        with pytest.raises(ValueError, match="must be one of"):
            coerce("signing.mode", "browser")
