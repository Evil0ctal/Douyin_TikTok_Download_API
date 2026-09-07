"""Runtime configuration: database authority, hot reload and validation.

The failure this guards against is subtle: once the database wins, a user who
edits .env and restarts sees nothing change. The tiering has to be deliberate
and observable, and an invalid stored value must degrade rather than crash.
"""

from __future__ import annotations

import pytest

from dtk.core.config import RUNTIME_SETTINGS, Config, Scope, coerce

# These exercise pure validation logic, so no event loop and no services.
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
