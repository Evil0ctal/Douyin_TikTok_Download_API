"""Bilibili's WBI signature, its keys, and the one place it must not be sent.

The arithmetic is checked against the worked example bilibili-API-collect
publishes for the algorithm - fixed keys, parameters and timestamp - so a
regression in the mixing table or the encoding fails here rather than as a
412 page from the platform.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from dtk.core.errors import SigningFailed
from dtk.core.types import Platform
from dtk.signing.base import (
    SIGNER_BROWSER,
    SIGNER_NATIVE,
    RequestSpec,
    SignatureAlgorithm,
    StaticFingerprint,
)
from dtk.signing.native import wbi
from dtk.signing.native.signer import NativeSigner
from dtk.signing.native.wbi_keys import FRESH_SECONDS, STALE_SECONDS, NavKeySource
from dtk.signing.protection import requires_browser_signature
from dtk.signing.registry import RegistryPolicy, SignerRegistry
from tests.unit.test_signing import FakeClock, FakeSigner

#: The published example: bilibili-API-collect, docs/misc/sign/wbi.md.
KEYS = wbi.WbiKeys("7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45")
FINGERPRINT = StaticFingerprint(user_agent="Mozilla/5.0 Chrome/146.0.0.0 Safari/537.36")
VIEW = RequestSpec.get(
    "https://api.bilibili.com/x/web-interface/wbi/view", params={"bvid": "BV1xx411c7mD"}
)


class TestTheAlgorithm:
    def test_the_published_example(self) -> None:
        assert KEYS.mixin == "ea1db124af3c7062474693fa704f4ff8"
        query, w_rid = wbi.sign({"foo": "114", "bar": "514", "zab": 1919810}, KEYS, wts=1702204169)
        assert w_rid == "8f6f2b5b3d485fe1886cec6a0be8c5d4"
        assert query == "bar=514&foo=114&wts=1702204169&zab=1919810&w_rid=" + w_rid

    def test_values_are_encoded_as_the_page_encodes_them(self) -> None:
        """encodeURIComponent, not urlencode: a space is %20 and '/' is escaped.
        The query is sent exactly as hashed, so either difference would sign
        one string and send the platform another."""
        assert wbi.encode({"q": "a b/c"}) == "q=a%20b%2Fc"

    def test_the_page_s_deleted_characters_are_deleted(self) -> None:
        assert wbi.encode({"q": "it's (fine)!*"}) == "q=its%20fine"

    def test_pagination_json_survives_the_trip(self) -> None:
        assert wbi.encode({"pagination_str": '{"offset":""}'}) == (
            "pagination_str=%7B%22offset%22%3A%22%22%7D"
        )

    def test_keys_are_read_from_the_image_names_nav_publishes(self) -> None:
        nav = {
            "code": -101,
            "data": {
                "wbi_img": {
                    "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                    "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                }
            },
        }
        assert wbi.keys_from_nav(nav) == KEYS

    @pytest.mark.parametrize(
        "nav",
        [{}, {"data": {}}, {"data": {"wbi_img": {"img_url": "x.png", "sub_url": "y.png"}}}],
    )
    def test_a_nav_without_usable_keys_is_refused(self, nav: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            wbi.keys_from_nav(nav)


def nav_transport(calls: list[int], *, fail_after: int | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if fail_after is not None and len(calls) > fail_after:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "code": -101,
                "data": {
                    "wbi_img": {
                        "img_url": f"https://i0.hdslb.com/bfs/wbi/{KEYS.img_key}.png",
                        "sub_url": f"https://i0.hdslb.com/bfs/wbi/{KEYS.sub_key}.png",
                    }
                },
            },
        )

    return httpx.MockTransport(handler)


class TestTheKeySource:
    async def test_keys_are_fetched_once_and_held(self) -> None:
        calls: list[int] = []
        clock = FakeClock()
        async with httpx.AsyncClient(transport=nav_transport(calls)) as client:
            source = NavKeySource(client=client, clock=clock)
            assert await source.keys() == KEYS
            clock.advance(FRESH_SECONDS - 1)
            assert await source.keys() == KEYS
        assert len(calls) == 1

    async def test_a_failed_refresh_keeps_signing_with_the_keys_it_has(self) -> None:
        """Refusing every Bilibili request because nav timed out once would turn
        a blip into an outage; yesterday's keys are almost certainly today's."""
        calls: list[int] = []
        clock = FakeClock()
        async with httpx.AsyncClient(transport=nav_transport(calls, fail_after=1)) as client:
            source = NavKeySource(client=client, clock=clock)
            await source.keys()
            clock.advance(FRESH_SECONDS + 1)
            assert await source.keys() == KEYS
        assert len(calls) == 2

    async def test_past_a_day_a_failed_refresh_fails_the_signature(self) -> None:
        calls: list[int] = []
        clock = FakeClock()
        async with httpx.AsyncClient(transport=nav_transport(calls, fail_after=1)) as client:
            source = NavKeySource(client=client, clock=clock)
            await source.keys()
            clock.advance(STALE_SECONDS + 1)
            with pytest.raises(SigningFailed, match="WBI keys"):
                await source.keys()

    async def test_no_keys_at_all_is_a_signing_failure_not_a_crash(self) -> None:
        async with httpx.AsyncClient(transport=nav_transport([], fail_after=0)) as client:
            with pytest.raises(SigningFailed):
                await NavKeySource(client=client).keys()


class FixedKeys:
    async def keys(self) -> wbi.WbiKeys:
        return KEYS


class TestTheSigner:
    async def test_a_bilibili_request_is_signed_with_wbi(self) -> None:
        signer = NativeSigner(
            Platform.BILIBILI, wbi_keys=FixedKeys(), wall_clock=lambda: 1702204169
        )
        signed = await signer.sign(VIEW, FINGERPRINT)

        assert signed.algorithm is SignatureAlgorithm.WBI
        assert signed.query.startswith("bvid=BV1xx411c7mD&wts=1702204169&w_rid=")
        _, expected = wbi.sign({"bvid": "BV1xx411c7mD"}, KEYS, wts=1702204169)
        assert signed.params == {"wts": "1702204169", "w_rid": expected}

    async def test_no_ms_token_is_invented_for_it(self) -> None:
        """Bilibili has none, and a stray parameter is still signed over."""
        signer = NativeSigner(Platform.BILIBILI, wbi_keys=FixedKeys(), wall_clock=lambda: 1)
        signed = await signer.sign(VIEW, FINGERPRINT)
        assert "msToken" not in signed.query


class TestNoBrowserForBilibili:
    def test_it_is_never_said_to_need_one(self) -> None:
        assert not requires_browser_signature(Platform.BILIBILI, VIEW.url, {})

    @pytest.mark.parametrize("mode", ["rpc", "auto"])
    async def test_whatever_the_mode_it_is_signed_natively(self, mode: str) -> None:
        """browser-rpc's page holds Douyin's and TikTok's SDKs and nobody else's.
        `signing.mode=rpc` must not hand it a Bilibili request to sign."""
        native = FakeSigner(SIGNER_NATIVE)
        rpc = FakeSigner(SIGNER_BROWSER)
        registry = SignerRegistry(
            {Platform.BILIBILI: native},
            rpc,
            policy=replace(RegistryPolicy(), mode=mode),  # type: ignore[arg-type]
        )
        await registry.sign(VIEW, FINGERPRINT, platform=Platform.BILIBILI)
        assert (native.calls, rpc.calls) == (1, 0)

    async def test_a_native_failure_does_not_fall_back_to_the_browser(self) -> None:
        native = FakeSigner(SIGNER_NATIVE, fail=SigningFailed("no keys"))
        rpc = FakeSigner(SIGNER_BROWSER)
        registry = SignerRegistry({Platform.BILIBILI: native}, rpc)
        with pytest.raises(SigningFailed):
            await registry.sign(VIEW, FINGERPRINT, platform=Platform.BILIBILI)
        assert rpc.calls == 0

    def test_it_is_never_shadow_sampled(self) -> None:
        registry = SignerRegistry(
            {Platform.BILIBILI: FakeSigner(SIGNER_NATIVE)}, FakeSigner(SIGNER_BROWSER)
        )
        assert not registry.should_shadow(VIEW, platform=Platform.BILIBILI)
