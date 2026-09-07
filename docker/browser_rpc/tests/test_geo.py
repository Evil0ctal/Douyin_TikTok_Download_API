"""Geo alignment: the browser's clock and locale must match the exit."""

from __future__ import annotations

from typing import Any

import httpx
from browser_rpc.geo import FALLBACK_PROFILE, probe_exit, profile_for_country, resolve


class StubClient:
    """Stands in for httpx.AsyncClient, recording how it was constructed."""

    def __init__(self, payload: Any = None, error: Exception | None = None, **kwargs: Any) -> None:
        self.payload = payload
        self.error = error
        self.kwargs = kwargs
        StubClient.last = self

    last: StubClient | None = None

    async def __aenter__(self) -> StubClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        if self.error is not None:
            raise self.error
        return httpx.Response(200, json=self.payload, request=httpx.Request("GET", url))


def factory(payload: Any = None, error: Exception | None = None) -> Any:
    def build(**kwargs: Any) -> StubClient:
        return StubClient(payload=payload, error=error, **kwargs)

    return build


class TestProfiles:
    def test_known_country(self) -> None:
        profile = profile_for_country("de")
        assert profile is not None
        assert profile.timezone == "Europe/Berlin"
        assert profile.locale == "de-DE"
        assert profile.country == "DE"

    def test_unknown_country(self) -> None:
        assert profile_for_country("ZZ") is None
        assert profile_for_country(None) is None


class TestResolve:
    def test_hint_wins_over_measured_exit(self) -> None:
        profile = resolve({"country": "DE"}, exit_country="CN")
        assert profile.timezone == "Europe/Berlin"

    def test_measured_exit_used_without_hint(self) -> None:
        profile = resolve(None, exit_country="JP")
        assert profile.timezone == "Asia/Tokyo"
        assert profile.locale == "ja-JP"

    def test_individual_fields_override(self) -> None:
        profile = resolve({"country": "DE", "timezone": "Europe/Zurich"})
        assert profile.timezone == "Europe/Zurich"
        # Only the timezone was overridden; the locale still matches the country.
        assert profile.locale == "de-DE"

    def test_falls_back_to_default_country(self) -> None:
        profile = resolve(None, exit_country=None, default_country="SG")
        assert profile.timezone == "Asia/Singapore"

    def test_unknown_everything_is_still_usable(self) -> None:
        profile = resolve({"country": "ZZ"}, exit_country="ZZ", default_country="ZZ")
        assert profile.timezone == FALLBACK_PROFILE.timezone
        assert profile.locale == FALLBACK_PROFILE.locale


class TestProbeExit:
    async def test_reads_ip_and_country(self) -> None:
        info = await probe_exit(
            "http://gate.example:8080",
            "https://ipinfo.io/json",
            2.0,
            factory({"ip": "203.0.113.9", "country": "de"}),
        )
        assert info.ip == "203.0.113.9"
        assert info.country == "DE"

    async def test_goes_through_the_proxy(self) -> None:
        # Probing without the proxy would measure the container's own address
        # and align the browser to the wrong country - worse than not probing.
        await probe_exit("http://gate.example:8080", "https://echo.test/", 2.0, factory({}))
        assert StubClient.last is not None
        assert StubClient.last.kwargs["proxy"] == "http://gate.example:8080"

    async def test_no_proxy_means_no_proxy_argument(self) -> None:
        await probe_exit(None, "https://echo.test/", 2.0, factory({}))
        assert StubClient.last is not None
        assert "proxy" not in StubClient.last.kwargs

    async def test_disabled_probe_is_silent(self) -> None:
        info = await probe_exit("http://gate.example:8080", None, 2.0, factory({"ip": "1.2.3.4"}))
        assert info.ip is None

    async def test_failure_degrades(self) -> None:
        info = await probe_exit(
            None,
            "https://echo.test/",
            2.0,
            factory(error=httpx.ConnectError("no route")),
        )
        assert info.ip is None and info.country is None

    async def test_alternate_field_names(self) -> None:
        info = await probe_exit(
            None,
            "https://echo.test/",
            2.0,
            factory({"query": "198.51.100.7", "countryCode": "SG"}),
        )
        assert info.ip == "198.51.100.7"
        assert info.country == "SG"
