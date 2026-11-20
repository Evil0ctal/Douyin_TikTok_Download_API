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
from browser_rpc.backends.fake import FAKE_USER_AGENT
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
    from dtk.core import errors as dtk_errors
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
                "user_agent": FAKE_USER_AGENT,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["params"]["a_bogus"]
        assert body["params"]["ms_token"]
        assert body["user_agent"] == FAKE_USER_AGENT

    async def test_a_user_agent_the_page_does_not_run_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        """Issue #832: the page signs under its own UA whatever it is asked.

        The answer used to echo the request's own UA back as the one signed
        under, so the caller's check compared its UA with itself. A signature
        sealing another UA is refused by TikTok; the refusal comes here instead,
        as a conflict the caller can act on rather than a browser failure.
        """
        foreign = FAKE_USER_AGENT.replace("Chrome/149", "Chrome/148")
        response = await client.post(
            "/rpc/sign",
            json={
                "platform": "tiktok",
                "url": "https://www.tiktok.com/api/user/detail/",
                "query": "uniqueId=tiktok",
                "user_agent": foreign,
            },
        )
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "user_agent_mismatch"
        assert "as Chrome/149 on Windows" in error["message"]
        assert "is Chrome/148 on Windows" in error["message"]
        assert "dtk signs it natively where it may" in error["message"]

    async def test_douyin_is_refused_only_another_operating_system(
        self, client: httpx.AsyncClient
    ) -> None:
        """#832 review, measured 2026-11-20: Douyin accepted the page's
        signature for an identity on another Chrome major, and refused it for
        one on macOS. The answer names the page's own UA either way."""

        async def sign(agent: str) -> httpx.Response:
            return await client.post(
                "/rpc/sign",
                json={
                    "platform": "douyin",
                    "url": DOUYIN_URL,
                    "query": "aweme_id=7",
                    "user_agent": agent,
                },
            )

        older = await sign(FAKE_USER_AGENT.replace("Chrome/149", "Chrome/130"))
        assert older.status_code == 200
        assert older.json()["params"]["a_bogus"]
        assert older.json()["user_agent"] == FAKE_USER_AGENT

        mac = await sign(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        )
        assert mac.status_code == 409
        message = mac.json()["error"]["message"]
        assert "is Chrome/149 on macOS" in message
        assert "naming another operating system" in message

    async def test_a_body_is_signed_and_said_to_be(self, client: httpx.AsyncClient) -> None:
        """`body_signed` is how a caller tells this build from one before bodies.

        An older service ignores the field and signs an empty body, and its
        answer looks the same in every other respect - so the confirmation is
        the only thing the caller has to go on.
        """
        base = {
            "platform": "douyin",
            "url": DOUYIN_URL,
            "method": "POST",
            "query": "aid=6383",
            "params": {"aid": "6383"},
        }
        plain = (await client.post("/rpc/sign", json=base)).json()
        posted = (
            await client.post(
                "/rpc/sign",
                json={
                    **base,
                    "body": "count=10&cursor=0",
                    "content_type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
        ).json()

        assert plain["body_signed"] is False
        assert posted["body_signed"] is True
        # The fake hashes the body like the real signature does, so a body that
        # reached the page changes what comes back.
        assert posted["params"]["a_bogus"] != plain["params"]["a_bogus"]

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

    async def test_the_callers_cookies_reach_the_signing_page(
        self, client: httpx.AsyncClient
    ) -> None:
        """The route has to forward the jar, not just accept it.

        `verifyFp` is the `s_v_web_id` of whichever browser signed, so a route
        that parsed `cookies` and dropped it would leave the request quoting
        browser-rpc's own visitor while carrying the caller's cookies - the
        exact incoherence both platforms answer by withholding the payload.
        """
        response = await client.post(
            "/rpc/sign",
            json={
                "platform": "douyin",
                "url": DOUYIN_URL,
                "query": "aweme_id=7",
                "params": {"aweme_id": "7"},
                "cookies": {"s_v_web_id": "verify_caller", "ttwid": "1|abc"},
                "identity_id": "ident-a",
            },
        )
        assert response.status_code == 200
        assert response.json()["params"]["verifyFp"] == "verify_caller"

    async def test_a_caller_that_sends_no_cookies_still_gets_a_signature(
        self, client: httpx.AsyncClient
    ) -> None:
        """`cookies` is optional on the wire, so an older client keeps working."""
        response = await client.post(
            "/rpc/sign",
            json={"platform": "douyin", "url": DOUYIN_URL, "params": {"aweme_id": "7"}},
        )
        assert response.status_code == 200
        assert response.json()["params"]["a_bogus"]

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
        assert body["user_agent"] == FAKE_USER_AGENT
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
        # Not the page's own User-Agent, on purpose: Douyin does not bind it,
        # and neither side may refuse a Douyin identity for it (#832 review).
        fingerprint = dtk_signing_base.StaticFingerprint(user_agent="Mozilla/5.0")

        signed = await signer.sign(spec, fingerprint)

        assert signed.signer == dtk_signing_base.SIGNER_BROWSER
        assert signed.algorithm is dtk_signing_base.SignatureAlgorithm.A_BOGUS
        assert "aweme_id=7" in signed.query
        assert "a_bogus=" in signed.query
        assert "msToken=" in signed.query

    async def test_rpc_signer_reads_a_user_agent_refusal_as_one(
        self, client: httpx.AsyncClient
    ) -> None:
        """The registry signs natively on exactly this refusal (#832), so the
        code it keys on has to survive the wire in both directions."""
        signer = dtk_signing.RpcSigner(
            client, "http://browser-rpc", platform=dtk_types.Platform.TIKTOK
        )
        spec = dtk_signing_base.RequestSpec.get(
            "https://www.tiktok.com/api/user/detail/", params={"uniqueId": "tiktok"}
        )
        foreign = FAKE_USER_AGENT.replace("Chrome/149", "Chrome/145")
        fingerprint = dtk_signing_base.StaticFingerprint(user_agent=foreign)

        with pytest.raises(dtk_errors.SigningFailed) as excinfo:
            await signer.sign(spec, fingerprint)
        assert dtk_signing.is_user_agent_mismatch(excinfo.value)

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
        # Against the platforms the application routes here, not all of them:
        # Bilibili signs natively and must never reach browser-rpc, and
        # comparing with the whole enum failed from the day it was added.
        assert {p.value for p in Platform} == {p.value for p in dtk_signing_base.RPC_PLATFORMS}


#: User-Agents across every row of dtk's system table, and some it has no row for.
_SYSTEM_SAMPLES = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 Chrome/109.0.0.0",
    "Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 Chrome/49.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/145.0.0.0",
    "Mozilla/5.0 (Macintosh; PPC) Gecko/20100101 Firefox/3.6",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/145.0.0.0",
    "Mozilla/5.0 (X11; FreeBSD amd64) AppleWebKit/537.36 Chrome/145.0.0.0",
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 Chrome/145.0.0.0",
    "Mozilla/5.0 (X11; SunOS i86pc) Gecko/20100101 Firefox/91.0",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/145.0.0.0 Mobile",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0",
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0",
    "Mozilla/5.0",
    "",
)


@requires_dtk
@pytest.mark.parametrize("agent", _SYSTEM_SAMPLES)
def test_douyin_is_judged_on_the_system_dtk_writes_into_the_query(agent: str) -> None:
    """browser-rpc reads the system from the User-Agent; dtk writes one into the
    query from the same string; Douyin compares the query's with the page's. The
    two readings had drifted - X11, SunOS and a bare Macintosh read as no system
    here and as Linux, Sun OS and Mac OS there - and the page signed queries it
    should have refused (review of the #832 fix)."""
    from browser_rpc.service import _is_foreign, agent_system

    from dtk.platforms.base import operating_system

    names = {"macOS": "Mac OS X"}
    ours = agent_system(agent) or "Windows"
    dtk_reading = operating_system(agent)
    dtk_system = dtk_reading[0] if dtk_reading else "Windows"
    assert names.get(ours, ours) == dtk_system

    windows_page = _SYSTEM_SAMPLES[0]
    assert _is_foreign(Platform.DOUYIN, agent, windows_page) is (dtk_system != "Windows")
