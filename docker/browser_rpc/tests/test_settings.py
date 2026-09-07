"""Configuration parsing.

Every value here arrives as a string from a compose file, so the parsing is the
whole surface: a typo has to stop the process rather than silently produce a
zero-second timeout.
"""

from __future__ import annotations

import pytest
from browser_rpc.errors import ConfigError
from browser_rpc.settings import DEFAULT_GEO_PROBE_URL, Settings


class TestFromEnv:
    def test_empty_environment_gives_the_defaults(self) -> None:
        settings = Settings.from_env({})
        assert settings == Settings()
        assert settings.backend == "cloak"
        assert settings.geo_probe_url == DEFAULT_GEO_PROBE_URL

    def test_reads_every_kind_of_value(self) -> None:
        settings = Settings.from_env(
            {
                "DTK_BROWSER_BACKEND": "FAKE",
                "DTK_BROWSER_BACKEND_PIN": "https://github.com/CloakHQ/cloakbrowser@abc1234",
                "DTK_BROWSER_BIND_HOST": "0.0.0.0",
                "DTK_BROWSER_BIND_PORT": "9100",
                "DTK_BROWSER_WARM_CONTEXTS": "2",
                "DTK_BROWSER_WARM_REFRESH_SECONDS": "900.5",
                "DTK_BROWSER_PREWARM": "off",
                "DTK_BROWSER_DEFAULT_COUNTRY": "de",
                "DTK_BROWSER_HEADLESS": "0",
                "DTK_BROWSER_LOG_LEVEL": "DEBUG",
                "DTK_BROWSER_SIGN_PROXY_URL": "http://gate.example:8080",
            }
        )
        assert settings.backend == "fake"
        assert settings.bind_port == 9100
        assert settings.warm_contexts == 2
        assert settings.warm_refresh_seconds == 900.5
        assert settings.prewarm is False
        assert settings.default_country == "DE"
        assert settings.headless is False
        assert settings.log_level == "debug"
        assert settings.backend_pin.endswith("@abc1234")
        assert settings.sign_proxy_url == "http://gate.example:8080"

    def test_empty_probe_url_disables_the_probe(self) -> None:
        # An offline deployment sets this empty; the geo hint is then the only
        # source of timezone and locale.
        assert Settings.from_env({"DTK_BROWSER_GEO_PROBE_URL": ""}).geo_probe_url is None

    @pytest.mark.parametrize(
        "env",
        [
            {"DTK_BROWSER_BIND_PORT": "nine thousand"},
            {"DTK_BROWSER_WARM_REFRESH_SECONDS": "half an hour"},
            {"DTK_BROWSER_PREWARM": "maybe"},
            {"DTK_BROWSER_BIND_PORT": "70000"},
            {"DTK_BROWSER_MAX_CONCURRENT_MINTS": "0"},
            {"DTK_BROWSER_SIGN_TIMEOUT_SECONDS": "0"},
            {"DTK_BROWSER_DEFAULT_COUNTRY": "Germany"},
        ],
    )
    def test_rejects_nonsense(self, env: dict[str, str]) -> None:
        with pytest.raises(ConfigError):
            Settings.from_env(env)

    def test_is_immutable(self) -> None:
        settings = Settings.from_env({})
        with pytest.raises(AttributeError):
            settings.backend = "fake"  # type: ignore[misc]
