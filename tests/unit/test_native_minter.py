"""Minting a Bilibili guest over plain HTTP.

The answer shape is the one ``/x/frontend/finger/spi`` gave a fresh client on
2026-10-18; the values are synthetic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dtk.core.types import Platform
from dtk.identity.minting import NativeMinter, NativeMintFailed
from dtk.identity.minting.native import FINGER_SPI_URL, chrome_fingerprint
from dtk.transport.base import RawResponse, TransportFailure
from dtk.transport.emulation import profile_for

SPI = {"code": 0, "message": "ok", "data": {"b_3": "SYNTHETIC-b3infoc", "b_4": "SYNTHETIC-b4=="}}


class FakeTransport:
    def __init__(self, answer: RawResponse | BaseException) -> None:
        self.answer = answer
        self.senders: list[Any] = []
        self.specs: list[Any] = []
        self.evicted: list[str] = []

    async def request(self, identity: Any, spec: Any, timeout: float | None = None) -> RawResponse:
        self.senders.append(identity)
        self.specs.append(spec)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    async def evict(self, identity_id: str) -> None:
        self.evicted.append(identity_id)


def answer(payload: Any, *, status: int = 200) -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


async def test_a_guest_is_the_visitor_ids_spi_hands_out() -> None:
    transport = FakeTransport(answer(SPI))
    minted = await NativeMinter(transport, clock=lambda: 1792300000).mint(Platform.BILIBILI)  # type: ignore[arg-type]

    assert minted.cookies == {
        "buvid3": "SYNTHETIC-b3infoc",
        "buvid4": "SYNTHETIC-b4==",
        "b_nut": "1792300000",
    }
    assert transport.specs[0].url == FINGER_SPI_URL


async def test_it_is_minted_through_the_identity_s_own_exit_and_client() -> None:
    """The session comes from the exit and the TLS profile that will spend it."""
    transport = FakeTransport(answer(SPI))
    minted = await NativeMinter(transport).mint(  # type: ignore[arg-type]
        Platform.BILIBILI, proxy_url="http://exit.example:8080", geo_hint={"timezone": "Asia/Tokyo"}
    )

    sender = transport.senders[0]
    assert sender.proxy_url == "http://exit.example:8080"
    assert sender.fingerprint == minted.fingerprint
    assert minted.fingerprint.timezone == "Asia/Tokyo"
    # The throwaway client is not left in the transport's cache.
    assert transport.evicted == [sender.id]


def test_its_fingerprint_is_one_the_transport_emulates_exactly() -> None:
    fingerprint = chrome_fingerprint()
    match = profile_for(fingerprint)
    assert match.exact
    assert f"Chrome/{fingerprint.browser_major}." in (fingerprint.user_agent or "")


@pytest.mark.parametrize(
    "outcome",
    [
        answer({"code": 0, "data": {}}),
        answer({"code": -412, "message": "intercepted", "data": None}),
        answer({}, status=412),
        RawResponse(status=200, headers={}, body=b"<html>"),
        TransportFailure("connection reset", identity_id="mint", url=FINGER_SPI_URL, elapsed_ms=5),
    ],
)
async def test_anything_but_a_visitor_id_is_a_refusal_to_back_off_from(outcome: Any) -> None:
    transport = FakeTransport(outcome)
    with pytest.raises(NativeMintFailed):
        await NativeMinter(transport).mint(Platform.BILIBILI)  # type: ignore[arg-type]
    assert len(transport.evicted) == 1


async def test_a_platform_it_does_not_mint_is_refused() -> None:
    minter = NativeMinter(FakeTransport(answer(SPI)))  # type: ignore[arg-type]
    assert minter.supports(Platform.BILIBILI)
    assert not minter.supports(Platform.DOUYIN)
    with pytest.raises(NativeMintFailed):
        await minter.mint(Platform.DOUYIN)
