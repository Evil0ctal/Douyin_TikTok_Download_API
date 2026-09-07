"""The browser-rpc client.

Its central property is that being unavailable is never fatal. With no minting
the pool falls back to manually imported cookies, and with no RPC signer the
native one still runs, so every failure path here has to degrade rather than
raise something the caller cannot handle.
"""

from __future__ import annotations

import httpx
import pytest

from dtk.core.errors import SigningFailed
from dtk.core.types import BrowserFamily, Platform
from dtk.identity.minting.client import BrowserRpcClient, BrowserRpcUnavailable

BASE = "http://browser-rpc:9000"


def client_with(handler) -> BrowserRpcClient:
    transport = httpx.MockTransport(handler)
    return BrowserRpcClient(BASE, client=httpx.AsyncClient(transport=transport))


class TestConfiguration:
    async def test_unconfigured_client_reports_itself_unavailable(self):
        rpc = BrowserRpcClient("")
        assert rpc.configured is False
        health = await rpc.health()
        assert health.available is False
        assert "not configured" in health.detail

    async def test_unconfigured_mint_raises_the_degradable_error(self):
        """The pool catches this and carries on with imported cookies."""
        with pytest.raises(BrowserRpcUnavailable):
            await BrowserRpcClient("").mint(Platform.DOUYIN, proxy_url=None)


class TestHealth:
    async def test_reports_warm_contexts_and_versions(self):
        def handler(request):
            assert request.url.path == "/rpc/health"
            return httpx.Response(
                200,
                json={"warm_contexts": 2, "chromium_major": 149, "backend_version": "1.2.3"},
            )

        health = await client_with(handler).health()
        assert health.available
        assert health.warm_contexts == 2
        assert health.chromium_major == 149

    async def test_a_transport_error_is_reported_not_raised(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        health = await client_with(handler).health()
        assert health.available is False
        assert health.detail

    async def test_malformed_body_is_reported_not_raised(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        assert (await client_with(handler).health()).available is False


class TestMint:
    def _ok(self, **overrides):
        body = {
            "cookies": {"ttwid": "abc", "odin_tt": "def"},
            "browser_family": "chrome",
            "browser_major": 149,
            "user_agent": "Mozilla/5.0 ... Chrome/149.0.0.0 Safari/537.36",
            "platform_hint": "MacIntel",
            "screen": "1920x1080",
            "language": "de-DE",
            "timezone": "Europe/Berlin",
            "exit_ip": "203.0.113.7",
        }
        body.update(overrides)
        return body

    async def test_returns_cookies_and_a_usable_fingerprint(self):
        def handler(request):
            assert request.url.path == "/rpc/mint"
            return httpx.Response(200, json=self._ok())

        result = await client_with(handler).mint(
            Platform.DOUYIN, proxy_url="http://user:pass@proxy:8080"
        )
        assert result.cookies == {"ttwid": "abc", "odin_tt": "def"}
        assert result.fingerprint.browser_family is BrowserFamily.CHROME
        assert result.fingerprint.browser_major == 149
        assert result.fingerprint.emulatable
        assert result.exit_ip == "203.0.113.7"

    async def test_timezone_travels_with_the_fingerprint(self):
        """A German exit reporting Asia/Shanghai is a tell given away for free,
        so the browser's aligned zone has to survive into the identity."""

        def handler(request):
            return httpx.Response(200, json=self._ok())

        result = await client_with(handler).mint(Platform.DOUYIN, proxy_url=None)
        assert result.fingerprint.timezone == "Europe/Berlin"
        assert result.fingerprint.language == "de-DE"

    async def test_empty_cookie_set_is_a_failed_mint(self):
        def handler(request):
            return httpx.Response(200, json={"cookies": {}})

        with pytest.raises(BrowserRpcUnavailable):
            await client_with(handler).mint(Platform.DOUYIN, proxy_url=None)

    async def test_unknown_browser_family_falls_back_to_chrome(self):
        """CloakBrowser is Chromium, so an unrecognised label is a reporting
        quirk rather than a genuinely different engine."""

        def handler(request):
            return httpx.Response(200, json=self._ok(browser_family="cloak-chromium"))

        result = await client_with(handler).mint(Platform.DOUYIN, proxy_url=None)
        assert result.fingerprint.browser_family is BrowserFamily.CHROME

    async def test_non_numeric_major_becomes_none_not_zero(self):
        def handler(request):
            return httpx.Response(200, json=self._ok(browser_major="unknown"))

        result = await client_with(handler).mint(Platform.DOUYIN, proxy_url=None)
        assert result.fingerprint.browser_major is None
        assert result.fingerprint.emulatable is False

    async def test_http_error_degrades(self):
        def handler(request):
            return httpx.Response(503, json={"detail": "no contexts"})

        with pytest.raises(BrowserRpcUnavailable):
            await client_with(handler).mint(Platform.DOUYIN, proxy_url=None)

    async def test_proxy_is_passed_through(self):
        seen = {}

        def handler(request):
            import json as _json

            seen.update(_json.loads(request.content))
            return httpx.Response(200, json=self._ok())

        await client_with(handler).mint(
            Platform.TIKTOK, proxy_url="socks5://p:1080", geo_hint={"country": "DE"}
        )
        assert seen["proxy_url"] == "socks5://p:1080"
        assert seen["platform"] == "tiktok"
        assert seen["geo_hint"] == {"country": "DE"}


class TestSign:
    async def test_returns_signature_parameters(self):
        def handler(request):
            assert request.url.path == "/rpc/sign"
            return httpx.Response(200, json={"a_bogus": "AAA", "msToken": "BBB", "x": None})

        signed = await client_with(handler).sign(
            Platform.DOUYIN, url="https://www.douyin.com/x", params={"a": "1"}, user_agent="UA"
        )
        assert signed == {"a_bogus": "AAA", "msToken": "BBB"}

    async def test_failure_surfaces_as_signing_failed(self):
        """The caller distinguishes this from rate limiting, so it must not
        arrive as a generic transport error."""

        def handler(request):
            raise httpx.ConnectError("down")

        with pytest.raises(SigningFailed):
            await client_with(handler).sign(
                Platform.DOUYIN, url="https://x", params={}, user_agent=None
            )
