"""The HTTP surface, and the contract with the clients that call it.

The second half of this file is the important part: it drives the real
`dtk.identity.minting.client.BrowserRpcClient` and `dtk.signing.rpc.RpcSigner`
against this service over an in-process ASGI transport. Both sides of the wire
contract from docs/design/04-transport-signing.md are then checked by the same
test, which is the only way a field rename gets caught before deployment.
"""

from __future__ import annotations

import importlib.util

import httpx
import pytest
from browser_rpc.validation import Platform

DOUYIN_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

# browser-rpc ships as its own image and does not depend on `dtk`, so the
# contract half of this file is conditional. The skip is per class rather than
# `pytest.importorskip` at module scope: that skips the whole module, which
# would quietly take the RPC surface tests above with it in any environment
# where only the browser image is built.
_HAS_DTK = importlib.util.find_spec("dtk") is not None
requires_dtk = pytest.mark.skipif(not _HAS_DTK, reason="dtk is not installed")

if _HAS_DTK:
    from dtk.core import types as dtk_types
    from dtk.identity.minting import client as dtk_client
    from dtk.signing import base as dtk_signing_base
    from dtk.signing import rpc as dtk_signing


class TestMintEndpoint:
    async def test_returns_the_documented_shape(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/rpc/mint",
            json={"platform": "douyin", "proxy_url": None, "geo_hint": {"country": "DE"}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cookies"]
        assert body["browser_family"] == "chrome"
        assert body["browser_major"]
        assert body["timezone"] == "Europe/Berlin"
        # `platform_hint` is navigator.platform; `platform` is what was minted
        # for. The client reads the first and would silently store the second.
        assert body["platform"] == "douyin"
        assert body["platform_hint"] == "Win32"

    async def test_rejects_an_unknown_platform(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/rpc/mint", json={"platform": "bilibili"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"

    async def test_rejects_a_proxy_that_is_not_a_proxy(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/rpc/mint",
            json={"platform": "douyin", "proxy_url": "ftp://gate.example"},
        )
        assert response.status_code == 400


class TestSignEndpoint:
    async def test_signs_with_the_callers_query(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/rpc/sign",
            json={
                "platform": "douyin",
                "url": DOUYIN_URL,
                "query": "aweme_id=7&device_platform=webapp",
                "params": {"aweme_id": "7", "device_platform": "webapp"},
                "user_agent": "Mozilla/5.0",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["params"]["a_bogus"]
        assert body["params"]["ms_token"]

    async def test_builds_a_query_when_the_caller_sends_only_params(
        self, client: httpx.AsyncClient
    ) -> None:
        # The minting client sends no `query`; the signature still has to cover
        # the parameters it did send.
        response = await client.post(
            "/rpc/sign",
            json={
                "platform": "tiktok",
                "url": "https://www.tiktok.com/api/item/",
                "params": {"a": "1"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        # The response carries the parameters under "params" and, beside them,
        # the User-Agent they were signed under - TikTok validates the two byte
        # for byte, so a caller that cannot see the UA cannot honour it.
        assert body["params"], "no signature parameters returned"
        assert "user_agent" in body

    async def test_rejects_a_url_off_the_allowlist(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/rpc/sign",
            json={"platform": "douyin", "url": "https://example.com/", "params": {}},
        )
        assert response.status_code == 400

    async def test_rejects_a_url_from_the_other_platform(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/rpc/sign",
            json={"platform": "douyin", "url": "https://www.tiktok.com/api/item/", "params": {}},
        )
        assert response.status_code == 400


class TestHealthEndpoint:
    async def test_reports_backend_and_versions(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/rpc/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["backend"] == "fake"
        assert body["chromium_major"]
        assert "warm_contexts" in body
        assert body["uptime"] >= 0


@requires_dtk
class TestClientContract:
    """The application's own clients, driven against this service."""

    async def test_minting_client_builds_a_usable_identity(self, client: httpx.AsyncClient) -> None:
        rpc = dtk_client.BrowserRpcClient("http://browser-rpc", client=client)
        result = await rpc.mint(
            dtk_types.Platform.DOUYIN,
            proxy_url="http://gate.example:8080",
            geo_hint={"country": "JP"},
        )

        assert result.cookies["ttwid"]
        fingerprint = result.fingerprint
        # An identity whose emulation profile cannot be chosen is refused by the
        # pool, so these two fields decide whether the mint was worth anything.
        assert fingerprint.emulatable
        assert fingerprint.browser_family is dtk_types.BrowserFamily.CHROME
        assert fingerprint.timezone == "Asia/Tokyo"
        assert fingerprint.platform == "Win32"
        assert fingerprint.screen == "1920x1080"

    async def test_minting_client_reads_health(self, client: httpx.AsyncClient) -> None:
        rpc = dtk_client.BrowserRpcClient("http://browser-rpc", client=client)
        health = await rpc.health()
        assert health.available
        assert health.chromium_major
        assert health.backend_version

    async def test_rpc_signer_produces_a_query(self, client: httpx.AsyncClient) -> None:
        signer = dtk_signing.RpcSigner(
            client, "http://browser-rpc", platform=dtk_types.Platform.DOUYIN
        )
        spec = dtk_signing_base.RequestSpec.get(DOUYIN_URL, params={"aweme_id": "7"})
        fingerprint = dtk_signing_base.StaticFingerprint(user_agent="Mozilla/5.0")

        signed = await signer.sign(spec, fingerprint)

        assert signed.signer == dtk_signing_base.SIGNER_BROWSER
        assert signed.algorithm is dtk_signing_base.SignatureAlgorithm.A_BOGUS
        assert "aweme_id=7" in signed.query
        assert "a_bogus=" in signed.query
        assert "msToken=" in signed.query

    async def test_rpc_signer_health_matches_warm_contexts(self, client: httpx.AsyncClient) -> None:
        signer = dtk_signing.RpcSigner(client, "http://browser-rpc")
        # Nothing warmed yet: the signer must report itself unusable rather than
        # promise a fallback that would take seconds to answer.
        assert (await signer.health()).healthy is False

        await client.post(
            "/rpc/sign",
            json={"platform": "douyin", "url": DOUYIN_URL, "params": {"a": "1"}},
        )
        health = await signer.health()
        assert health.healthy is True
        assert health.warm_contexts == 1
        assert health.uptime_seconds is not None


@requires_dtk
class TestPlatformParity:
    def test_wire_values_match_the_application_enum(self) -> None:
        # Two enums, one wire format. They are separate because browser-rpc
        # ships as its own image, so nothing but a test keeps them aligned.
        assert {p.value for p in Platform} == {p.value for p in dtk_types.Platform}
