"""TikTok requests carry the device id the platform issued the identity's session.

TikTok binds a video link to the device id the API was asked with and the
session's tt_chain_token together. dtk used to send a random device id on every
call, and every video link it handed out answered 403 to every cookie there is
(#831, measured 2026-11-20 against the live CDN). The id is TikTok's own
``wid``, read once per identity off one of its pages and kept in the
fingerprint; these tests hold each step of that.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

from dtk.core.errors import DtkError
from dtk.core.types import BrowserFamily, Platform
from dtk.identity.pool import LiveIdentity, _fingerprint_from_json, _fingerprint_to_json
from dtk.identity.rotation import rotate
from dtk.platforms.base import RequestSpec as PlatformRequestSpec
from dtk.platforms.douyin import params as douyin_params
from dtk.platforms.tiktok import parser as tiktok_parser
from dtk.platforms.tiktok.params import base_params, profile_for
from dtk.services.fetch import FetchService
from dtk.signing.base import SIGNER_BROWSER, SignedParams
from dtk.transport.base import Fingerprint, RawResponse, TransportFailure, TransportIdentity
from tests.replay.support import load
from tests.unit.test_fetch_classification import FakeScheduler, FakeSession, FakeTransport

WID = "7311111111111111111"
IDENTITY_ID = str(uuid.UUID("0b8f6c1e-5d2a-4c3b-9e71-2f4a6d8c0e15"))
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36"


def page(wid: object = WID) -> str:
    state = {"__DEFAULT_SCOPE__": {"webapp.app-context": {"wid": wid, "language": "en"}}}
    return (
        "<html><head></head><body>"
        f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        f"{json.dumps(state)}</script></body></html>"
    )


class TestReadingTheId:
    def test_the_web_app_state_names_it(self) -> None:
        assert tiktok_parser.read_device_id(page()) == WID

    def test_a_numeric_wid_is_read_as_text(self) -> None:
        assert tiktok_parser.read_device_id(page(int(WID))) == WID

    @pytest.mark.parametrize(
        "html",
        [
            "<html>a login wall, no state</html>",
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{not json</script>',
            page(""),
            page("not-a-number"),
            page(None),
        ],
    )
    def test_anything_else_is_no_id_rather_than_a_guess(self, html: str) -> None:
        assert tiktok_parser.read_device_id(html) is None


class TestTheQuery:
    def test_a_known_id_is_sent(self) -> None:
        profile = profile_for(Fingerprint(user_agent=CHROME_UA, device_id=WID))
        assert base_params(profile)["device_id"] == WID

    def test_an_unknown_one_is_still_made_up_rather_than_left_out(self) -> None:
        """Without any device_id TikTok answers an empty 200 (2026-09-08)."""
        made_up = base_params(profile_for(Fingerprint(user_agent=CHROME_UA)))["device_id"]
        assert made_up.isdigit() and len(made_up) == 19

    def test_douyin_s_query_is_untouched(self) -> None:
        profile = douyin_params.profile_for(Fingerprint(user_agent=CHROME_UA, device_id=WID))
        assert WID not in json.dumps(douyin_params.base_params(profile))

    def test_the_id_survives_the_identity_record(self) -> None:
        stored = _fingerprint_to_json(Fingerprint(user_agent=CHROME_UA, device_id=WID))
        assert _fingerprint_from_json(stored).device_id == WID
        assert _fingerprint_from_json({"user_agent": CHROME_UA}).device_id is None


# --------------------------------------------------------------------------
# The fetch service reads it once per identity
# --------------------------------------------------------------------------


class Pool:
    def __init__(self, fingerprint: Fingerprint) -> None:
        self.identity = LiveIdentity(
            id=IDENTITY_ID,
            platform=Platform.TIKTOK,
            cookies={"tt_chain_token": "chain", "ttwid": "x"},
            fingerprint=fingerprint,
            proxy_url=None,
            authenticated=False,
        )
        self.remembered: list[tuple[str, str]] = []
        self.absorbed: list[tuple[str, tuple[str, ...]]] = []

    async def load(self, _session: Any, _identity_id: str, **_: Any) -> LiveIdentity:
        return self.identity

    async def remember_device_id(self, _session: Any, identity_id: str, device_id: str) -> None:
        self.remembered.append((identity_id, device_id))
        from dataclasses import replace

        self.identity = replace(
            self.identity, fingerprint=replace(self.identity.fingerprint, device_id=device_id)
        )

    async def absorb_cookies(
        self, _session: Any, identity_id: str, offered: Mapping[str, str]
    ) -> tuple[str, ...]:
        from dataclasses import replace

        rotation = rotate(Platform.TIKTOK, self.identity.cookies, offered)
        self.absorbed.append((identity_id, rotation.changed))
        self.identity = replace(self.identity, cookies=rotation.jar)
        return rotation.changed

    async def record_outcome(self, *_: Any, **__: Any) -> None: ...


class Transport(FakeTransport):
    """The home page answers with ``home``; every API call with a detail body."""

    def __init__(self, home: RawResponse | BaseException) -> None:
        super().__init__(_json(200, load("tiktok", "video_normal")))
        self.home = home

    async def request(self, identity: Any, spec: Any, timeout: float | None = None) -> RawResponse:
        if spec.url == tiktok_parser.DEVICE_ID_PAGE:
            self.senders.append(identity)
            self.specs.append(spec)
            if isinstance(self.home, BaseException):
                raise self.home
            return self.home
        return await super().request(identity, spec, timeout)

    def pages(self) -> int:
        return sum(1 for spec in self.specs if spec.url == tiktok_parser.DEVICE_ID_PAGE)

    def sent_device_ids(self) -> list[str]:
        found = []
        for spec in self.specs:
            if spec.url == tiktok_parser.DEVICE_ID_PAGE:
                continue
            query = dict(parse_qsl(urlsplit(spec.url).query))
            query.update(spec.params or {})
            found.append(query.get("device_id", ""))
        return found


def _json(status: int, payload: Any) -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
        final_url="https://www.tiktok.com/api/item/detail/",
        elapsed_ms=5,
    )


def _html(status: int, body: str, cookies: Mapping[str, str] | None = None) -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "text/html"},
        body=body.encode(),
        final_url=tiktok_parser.DEVICE_ID_PAGE,
        elapsed_ms=5,
        set_cookies=dict(cookies or {}),
    )


async def echo_signer(platform: Platform, spec: PlatformRequestSpec, sender: TransportIdentity):
    """Signs nothing and sends the query exactly as built, device_id included."""
    params = {key: str(value) for key, value in (spec["params"] or {}).items()}
    return SignedParams(query=urlencode(params), params=params, signer=SIGNER_BROWSER)


def tiktok_service(pool: Pool, transport: Transport) -> FetchService:
    return FetchService(
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        sign=echo_signer,
    )


async def fetch_detail(svc: FetchService) -> None:
    await svc.fetch(
        FakeSession(),  # type: ignore[arg-type]
        Platform.TIKTOK,
        "tiktok.content_detail",
        {"item_id": "7218694761253735723"},
        parse=lambda payload: SimpleNamespace(**payload),
    )


def fingerprint(device_id: str | None = None) -> Fingerprint:
    return Fingerprint(
        browser_family=BrowserFamily.CHROME,
        browser_major=146,
        user_agent=CHROME_UA,
        device_id=device_id,
    )


class TestTheFetchService:
    async def test_the_first_call_reads_the_id_and_sends_it(self) -> None:
        pool = Pool(fingerprint())
        transport = Transport(_html(200, page()))
        svc = tiktok_service(pool, transport)

        await fetch_detail(svc)

        assert transport.pages() == 1
        assert pool.remembered == [(IDENTITY_ID, WID)]
        assert transport.sent_device_ids() == [WID]
        # Read as the identity: its own jar, not an anonymous visit, because
        # the id belongs to the session the jar is.
        assert transport.senders[0].cookies["tt_chain_token"] == "chain"

    async def test_a_known_id_costs_no_page(self) -> None:
        pool = Pool(fingerprint(WID))
        transport = Transport(_html(200, page("7399999999999999999")))
        await fetch_detail(tiktok_service(pool, transport))

        assert transport.pages() == 0
        assert pool.remembered == []
        assert transport.sent_device_ids() == [WID]

    @pytest.mark.parametrize(
        "home",
        [
            _html(403, "blocked"),
            _html(200, "<html>no state</html>"),
            TransportFailure("refused", identity_id=IDENTITY_ID, url="x", elapsed_ms=1),
        ],
    )
    async def test_a_page_that_says_nothing_leaves_the_call_as_it_was(self, home: Any) -> None:
        pool = Pool(fingerprint())
        transport = Transport(home)
        svc = tiktok_service(pool, transport)

        await fetch_detail(svc)
        await fetch_detail(svc)

        assert pool.remembered == []
        # Asked once, not on every call: a page the platform will not serve is
        # not worth a request in front of each real one.
        assert transport.pages() == 1
        sent = transport.sent_device_ids()
        assert len(sent) == 2 and all(value.isdigit() and len(value) == 19 for value in sent)

    async def test_a_platform_with_no_such_page_is_never_asked(self) -> None:
        from tests.unit.test_fetch_classification import FakePool, identity, run_fetch

        transport = FakeTransport(_json(200, load("douyin", "video_normal")))
        svc = FetchService(
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            pool=FakePool(identity()),  # type: ignore[arg-type]
            transport=transport,  # type: ignore[arg-type]
            sign=echo_signer,
        )
        await run_fetch(svc, FakeSession())
        assert len(transport.specs) == 1


class TestThePagesCookies:
    """The page is read as the identity, and it sets cookies like any page."""

    USABLE = "u" * 152

    def pool(self) -> Pool:
        pool = Pool(fingerprint())
        pool.identity = replace(
            pool.identity, cookies={**pool.identity.cookies, "msToken": self.USABLE}
        )
        return pool

    async def test_they_are_kept_and_sent_with_the_call_that_follows(self) -> None:
        pool = self.pool()
        transport = Transport(_html(200, page(), {"ttwid": "rotated"}))
        await fetch_detail(tiktok_service(pool, transport))

        assert pool.absorbed == [(IDENTITY_ID, ("ttwid",))]
        api_call = transport.senders[-1]
        assert api_call.cookies["ttwid"] == "rotated"

    async def test_its_bootstrap_token_never_replaces_the_usable_one(self) -> None:
        """The HTML document sets a 128-character msToken before the SDK swaps
        in the real one; keeping it would break the identity on its first use."""
        pool = self.pool()
        transport = Transport(_html(200, page(), {"msToken": "b" * 128}))
        await fetch_detail(tiktok_service(pool, transport))

        assert pool.identity.cookies["msToken"] == self.USABLE
        assert transport.senders[-1].cookies["msToken"] == self.USABLE

    async def test_a_page_that_stated_no_id_still_sets_its_cookies(self) -> None:
        """Kept the way any answer's are; the id is asked for again later."""
        pool = self.pool()
        transport = Transport(_html(200, "<html>no state</html>", {"ttwid": "rotated"}))
        await fetch_detail(tiktok_service(pool, transport))

        assert pool.remembered == []
        assert pool.absorbed == [(IDENTITY_ID, ("ttwid",))]
        assert transport.senders[-1].cookies["ttwid"] == "rotated"

    async def test_they_are_written_with_the_calls_answer_not_before_it(self) -> None:
        """Issue #832: writing them takes the identity's row lock until commit.

        Written straight off the page, the lock was held across signing and
        the upstream round trip, and every other request on the identity
        waited for both - 1.9-2.0s of a 2.0s call, measured on Postgres. The
        call still carries them; they are written once, with its answer.
        """
        pool = self.pool()
        transport = Transport(_html(200, page(), {"ttwid": "rotated"}))
        order: list[str] = []
        request, absorb = transport.request, pool.absorb_cookies

        async def watched_request(identity: Any, spec: Any, timeout: float | None = None):
            response = await request(identity, spec, timeout)
            order.append("page" if spec.url == tiktok_parser.DEVICE_ID_PAGE else "call")
            return response

        async def watched_absorb(session: Any, identity_id: str, offered: Mapping[str, str]):
            order.append("write")
            return await absorb(session, identity_id, offered)

        transport.request = watched_request  # type: ignore[method-assign]
        pool.absorb_cookies = watched_absorb  # type: ignore[method-assign]
        await fetch_detail(tiktok_service(pool, transport))

        assert order == ["page", "call", "write"]
        assert pool.absorbed == [(IDENTITY_ID, ("ttwid",))]
        assert transport.senders[-1].cookies["ttwid"] == "rotated"

    async def test_they_are_kept_when_the_call_itself_keeps_nothing(self) -> None:
        """The page answered even when the call did not: what it set is kept."""
        pool = self.pool()
        transport = Transport(_html(200, page(), {"ttwid": "rotated"}))
        request = transport.request

        async def failing_call(identity: Any, spec: Any, timeout: float | None = None):
            if spec.url == tiktok_parser.DEVICE_ID_PAGE:
                return await request(identity, spec, timeout)
            raise TransportFailure("refused", identity_id=IDENTITY_ID, url="x", elapsed_ms=1)

        transport.request = failing_call  # type: ignore[method-assign]
        with pytest.raises(DtkError):
            await fetch_detail(tiktok_service(pool, transport))

        # Under the lease's id, which this fake scheduler does not share with
        # the fake pool's identity; in the service the two are one.
        assert pool.absorbed[0][1] == ("ttwid",)

    async def test_a_page_that_never_answered_sets_nothing(self) -> None:
        pool = self.pool()
        transport = Transport(_html(502, "bad gateway", {"ttwid": "gateway"}))
        await fetch_detail(tiktok_service(pool, transport))

        assert pool.absorbed == []
        assert transport.senders[-1].cookies["ttwid"] == "x"
