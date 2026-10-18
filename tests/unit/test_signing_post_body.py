"""A POST's body is part of what Douyin signs.

`a_bogus` hashes the request body beside the query. Checked against two
signatures Douyin's own page produced for ``listcollection`` on 2026-10-17:
the body digest each carries matches ``count=10&cursor=...`` exactly, and
matches neither an empty body nor an HTML-escaped spelling of it.

Douyin did not enforce the binding when it was measured the same day: signed
over an empty body, or over the first page's body, a request for page two
still came back with page two. What a signature binds is still what it should
describe, because the page's own does - and an unchecked binding is one the
platform can start checking.

So every path from the endpoint table to a signature has to carry the body,
and none did: the worker handed the signer a URL and parameters, browser-rpc
accepted a method and dropped it, and its page signed a body-less GET.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

from dtk.core.errors import SigningFailed
from dtk.core.types import Platform
from dtk.platforms.douyin import ADAPTER, endpoints
from dtk.signing.base import RequestSpec, SigningSession, StaticFingerprint
from dtk.signing.native import abogus, websign
from dtk.signing.native.signer import NativeSigner
from dtk.signing.rpc import RpcSigner

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:153.0) Gecko/20100101 Firefox/153.0"
FINGERPRINT = StaticFingerprint(
    user_agent=UA, browser_platform="MacIntel", screen_width=1496, screen_height=967
)
JAR = {
    "s_v_web_id": "verify_test_visitor",
    "UIFID": "aabbccddeeff00112233445566778899",
    "sessionid": "redacted",
}
BODY = b"count=10&cursor=1792302641724339"


def bookmarks() -> RequestSpec:
    return RequestSpec.of(
        ADAPTER.build_request(endpoints.AUTHOR_BOOKMARKS, count=10, cursor="1792302641724339")
    )


def body_digest_of(signature: str) -> tuple[int, ...]:
    """The three bytes an a_bogus carries to bind the body it was computed over."""
    fields = abogus.decode(signature)["fields"]
    return tuple(fields[name] for name in abogus.DIGEST_CHAINS["body"].slots)


def binding(body: str) -> tuple[int, ...]:
    chain = abogus.DIGEST_CHAINS["body"]
    return abogus.chain_bytes(chain, abogus.digest_of(body))


class TestTheSigningSpec:
    def test_it_carries_everything_the_platform_built(self) -> None:
        spec = bookmarks()

        assert spec.method == "POST"
        assert spec.body == BODY
        assert spec.headers is not None
        assert spec.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
        assert "cursor" not in (spec.params or {})

    def test_a_get_is_what_it_always_was(self) -> None:
        spec = RequestSpec.of(ADAPTER.build_request(endpoints.CONTENT_DETAIL, aweme_id="7"))

        assert spec.method == "GET"
        assert spec.body is None


class TestNativeSigner:
    async def test_the_signature_binds_the_body_that_is_sent(self) -> None:
        # Re-signed until readable: about 1 in 690 correct values cannot be
        # decoded, a tail-branch quirk of the format that depends on the clock
        # and is described in dtk.signing.registry. Taking the first one made
        # this test fail at about that rate. Two draws have never both missed.
        for _ in range(5):
            signed = await NativeSigner(Platform.DOUYIN).sign(
                bookmarks(), FINGERPRINT, SigningSession(cookies=JAR)
            )
            if abogus.structure_error(signed.params["a_bogus"]) is None:
                break

        carried = body_digest_of(signed.params["a_bogus"])
        assert carried == binding(BODY.decode())
        assert carried != binding("")

    async def test_the_web_signature_still_covers_the_query_alone(self) -> None:
        """The other half of the page's signature does not hash the body: both
        captures reproduce from their query string with no body involved, and
        so does this one, recomputed from the query alone."""
        signed = await NativeSigner(Platform.DOUYIN).sign(
            bookmarks(), FINGERPRINT, SigningSession(cookies=JAR)
        )

        covered, _, signature = signed.query.rpartition(f"&{websign.SIGNATURE_PARAM}=")
        pairs = dict(parse_qsl(covered))
        preimage = f"{pairs['uifid']}_{pairs['timestamp']}_{websign.SALT}_{covered}"
        assert signature == hashlib.md5(preimage.encode()).hexdigest()
        assert "cursor" not in pairs


def rpc(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestRpcSigner:
    async def test_the_body_and_its_type_go_to_the_page(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.read()))
            return httpx.Response(
                200, json={"params": {"a_bogus": "sig"}, "user_agent": UA, "body_signed": True}
            )

        async with rpc(handler) as client:
            await RpcSigner(client, "http://browser-rpc:8000").sign(bookmarks(), FINGERPRINT)

        assert seen[0]["method"] == "POST"
        assert seen[0]["body"] == BODY.decode()
        assert seen[0]["content_type"].startswith("application/x-www-form-urlencoded")

    async def test_a_service_that_does_not_confirm_the_body_is_refused(self) -> None:
        """An older browser-rpc drops the field and signs an empty body.

        Douyin did not check the binding when measured, so such a request would
        work - until the day it is checked, when it would fail as identities
        being refused rather than as the stale image it is. Refused here, the
        error names the image.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"params": {"a_bogus": "sig"}, "user_agent": UA})

        async with rpc(handler) as client:
            with pytest.raises(SigningFailed, match="browser-rpc image"):
                await RpcSigner(client, "http://browser-rpc:8000").sign(bookmarks(), FINGERPRINT)

    async def test_a_get_sends_no_body_and_needs_no_confirmation(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.read()))
            return httpx.Response(200, json={"params": {"a_bogus": "sig"}, "user_agent": UA})

        spec = RequestSpec.of(ADAPTER.build_request(endpoints.CONTENT_DETAIL, aweme_id="7"))
        async with rpc(handler) as client:
            signed = await RpcSigner(client, "http://browser-rpc:8000").sign(spec, FINGERPRINT)

        assert "body" not in seen[0]
        assert signed.params["a_bogus"] == "sig"


class TestTheOpsPath:
    """`dtk fetch`, the identity probes and smoke sign through `call_endpoint`.

    It built its own signing spec from the URL and parameters and sent no body,
    so a form endpoint would have gone out as a POST with its page and size in
    neither the query nor the body.
    """

    async def test_it_signs_and_sends_the_body(self) -> None:
        from dtk.identity.pool import LiveIdentity
        from dtk.ops import pipeline
        from dtk.transport.base import Fingerprint, RawResponse
        from dtk.worker.registry import ENDPOINTS, ResolvedCall

        signed_specs: list[RequestSpec] = []
        sent: list[Any] = []

        class Signers:
            async def sign(self, spec: RequestSpec, *args: Any, **kwargs: Any) -> Any:
                signed_specs.append(spec)
                return await NativeSigner(Platform.DOUYIN).sign(spec, FINGERPRINT)

        class Transport:
            async def request(self, identity: Any, spec: Any, timeout: Any = None) -> RawResponse:
                sent.append(spec)
                return RawResponse(status=200, headers={}, body=b"{}", final_url=spec.url)

        identity = LiveIdentity(
            id="6f1d7f4e-4c0a-4b3a-9f2e-1d0c8a5b7e31",
            platform=Platform.DOUYIN,
            cookies=JAR,
            fingerprint=Fingerprint(user_agent=UA, platform="MacIntel", screen="1496x967"),
            proxy_url=None,
            authenticated=True,
        )
        call = ResolvedCall(
            definition=ENDPOINTS["douyin.author_bookmarks"],
            params={"count": 10, "cursor": "1792302641724339"},
            cache_ttl=0,
            parse=lambda payload: payload,
        )

        await pipeline.call_endpoint(Transport(), Signers(), identity, call)  # type: ignore[arg-type]

        assert signed_specs[0].body == BODY
        assert sent[0].method == "POST"
        assert sent[0].body == BODY
