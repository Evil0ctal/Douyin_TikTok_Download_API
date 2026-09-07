"""Input validation: the allowlist and the proxy parser."""

from __future__ import annotations

import pytest
from browser_rpc.errors import InvalidRequest
from browser_rpc.validation import (
    Platform,
    parse_platform,
    registrable_domain,
    validate_proxy_url,
    validate_target_url,
)


class TestParsePlatform:
    def test_accepts_wire_values(self) -> None:
        assert parse_platform("douyin") is Platform.DOUYIN
        assert parse_platform("TikTok") is Platform.TIKTOK

    def test_rejects_unknown(self) -> None:
        with pytest.raises(InvalidRequest) as excinfo:
            parse_platform("bilibili")
        # The message lists what is known: the caller is another service, and a
        # bare "invalid" makes for a long afternoon.
        assert "douyin" in str(excinfo.value)

    @pytest.mark.parametrize("value", [None, "", "   ", 7])
    def test_rejects_empty(self, value: object) -> None:
        with pytest.raises(InvalidRequest):
            parse_platform(value)


class TestTargetUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.douyin.com/aweme/v1/web/aweme/detail/",
            "https://www.tiktok.com/api/item/detail/",
            "https://m.iesdouyin.com/share/video/123",
        ],
    )
    def test_allows_platform_hosts(self, url: str) -> None:
        assert validate_target_url(url).startswith("https://")

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/",
            "https://douyin.com.evil.test/",
            "https://127.0.0.1/",
            "https://[::1]/",
            "http://localhost:8000/",
            "file:///etc/passwd",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_everything_else(self, url: str) -> None:
        with pytest.raises(InvalidRequest):
            validate_target_url(url)

    def test_rejects_cross_platform_url(self) -> None:
        # Signing a TikTok URL on a Douyin page yields a signature that verifies
        # nowhere, and the failure would surface much later as a risk-control hit.
        with pytest.raises(InvalidRequest):
            validate_target_url("https://www.tiktok.com/api/item/", platform=Platform.DOUYIN)

    def test_normalizes_host_and_drops_fragment(self) -> None:
        result = validate_target_url("https://WWW.Douyin.com/video/7?a=1#frag")
        assert result == "https://www.douyin.com/video/7?a=1"

    def test_registrable_domain(self) -> None:
        assert registrable_domain("live.douyin.com") == "douyin.com"
        assert registrable_domain("evil-douyin.com") is None


class TestProxyUrl:
    def test_none_is_no_proxy(self) -> None:
        assert validate_proxy_url(None) is None
        assert validate_proxy_url("  ") is None

    def test_splits_credentials(self) -> None:
        proxy = validate_proxy_url("http://user:pass%20word@gate.example:8080")
        assert proxy is not None
        assert proxy.server == "http://gate.example:8080"
        assert proxy.username == "user"
        assert proxy.password == "pass word"
        # Credentials are as sensitive as the cookies; they never reach a log.
        assert "pass" not in proxy.masked()

    @pytest.mark.parametrize("url", ["socks5://10.0.0.5:1080", "socks5h://gate.example:1080"])
    def test_allows_socks(self, url: str) -> None:
        assert validate_proxy_url(url) is not None

    @pytest.mark.parametrize("url", ["ftp://gate.example", "gate.example:8080", "file:///x"])
    def test_rejects_other_schemes(self, url: str) -> None:
        with pytest.raises(InvalidRequest):
            validate_proxy_url(url)

    def test_allows_private_addresses(self) -> None:
        # A proxy container on the same host is a supported deployment; the SSRF
        # rule applies to the destination, not to the exit.
        assert validate_proxy_url("http://proxy:3128") is not None
