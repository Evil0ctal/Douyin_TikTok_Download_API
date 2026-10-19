"""The downloader and transcriber rows on the System page and in the self check.

Both sidecars sit behind a compose profile, so the first thing asserted is the
distinction the browser-rpc row already makes: a profile nobody started is
``configured: false``, not a fault. The rest is the contract the console reads
(``configured`` / ``ok`` / ``latency_ms`` / ``detail_code``) and the one thing
that must never ride along with it - the sidecar's own words, which carry an
internal address or a volume path.

Real clients over a mock transport rather than fakes, so a change to the
header a client sends or the path it asks for shows up here.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from dtk.api.routes import system
from dtk.media.client import DownloaderClient
from dtk.ops import sidecars
from dtk.transcriber import TranscriberClient

DOWNLOADER_URL = "http://downloader:9100"
TRANSCRIBER_URL = "http://transcriber:9200"
#: SYNTHETIC. Only ever compared with itself.
TOKEN = "sidecar-token-for-tests-only"


def downloader(handler: Any, *, token: str = "") -> DownloaderClient:
    return DownloaderClient(
        DOWNLOADER_URL,
        token=token,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def transcriber(handler: Any, *, token: str = TOKEN) -> TranscriberClient:
    return TranscriberClient(
        TRANSCRIBER_URL,
        token=token,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def contract(status: dict[str, Any]) -> None:
    """The shape GET /system/status promises for every optional component."""
    assert isinstance(status["configured"], bool)
    assert status["ok"] is None or isinstance(status["ok"], bool)
    assert set(status) <= {"configured", "ok", "latency_ms", "detail_code"}


# --------------------------------------------------------------------------
# absent
# --------------------------------------------------------------------------


async def test_a_profile_that_is_not_running_is_absent_not_broken() -> None:
    assert await sidecars.downloader_status(None) == {"configured": False, "ok": None}
    assert await sidecars.transcriber_status(None) == {"configured": False, "ok": None}
    # A client built from an empty URL is the same state, reached another way.
    assert (await sidecars.downloader_status(DownloaderClient("")))["configured"] is False
    assert (await sidecars.transcriber_status(TranscriberClient("")))["configured"] is False


# --------------------------------------------------------------------------
# the downloader
# --------------------------------------------------------------------------


async def test_an_answering_downloader_is_ok_with_a_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "root": "/data/media", "workers": 4})

    status = await sidecars.downloader_status(downloader(handler))

    contract(status)
    assert status["configured"] is True
    assert status["ok"] is True
    assert isinstance(status["latency_ms"], int)
    assert status["detail_code"] is None
    # The volume path it reported is the sidecar's business, not the row's.
    assert "/data/media" not in json.dumps(status)


async def test_a_downloader_that_refuses_the_connection_is_unreachable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to downloader:9100")

    status = await sidecars.downloader_status(downloader(handler))

    contract(status)
    assert status == {"configured": True, "ok": False, "detail_code": "unreachable"}


async def test_a_downloader_refusing_our_token_cannot_take_jobs_either() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    status = await sidecars.downloader_status(downloader(handler, token=TOKEN))

    assert status["ok"] is False
    assert status["detail_code"] == "unreachable"


async def test_a_slow_downloader_is_reported_as_a_timeout_not_waited_on() -> None:
    class Hanging:
        configured = True

        async def health(self) -> Any:
            await asyncio.sleep(10)

    status = await sidecars.downloader_status(Hanging(), timeout=0.01)

    contract(status)
    assert status == {"configured": True, "ok": False, "detail_code": "timeout"}


# --------------------------------------------------------------------------
# the transcriber
# --------------------------------------------------------------------------


async def test_the_transcriber_is_asked_with_its_token() -> None:
    """Its /health is behind the token, unlike the downloader's."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Transcriber-Token"))
        return httpx.Response(200, json={"status": "ok"})

    status = await sidecars.transcriber_status(transcriber(handler))

    contract(status)
    assert status["ok"] is True
    assert seen == [TOKEN]


async def test_a_transcriber_without_its_model_answers_but_is_not_ok() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "degraded", "detail": "model missing at /models/asr"}
        )

    status = await sidecars.transcriber_status(transcriber(handler))

    contract(status)
    assert status["configured"] is True
    assert status["ok"] is False
    assert status["detail_code"] == "degraded"
    assert isinstance(status["latency_ms"], int)
    assert "/models" not in json.dumps(status)


async def test_a_transcriber_that_errors_is_unreachable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "model unavailable"})

    status = await sidecars.transcriber_status(transcriber(handler))

    assert status == {"configured": True, "ok": False, "detail_code": "unreachable"}


# --------------------------------------------------------------------------
# the status route's use of them
# --------------------------------------------------------------------------


def fake_request(**settings: str) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(**settings)))
    )


@pytest.fixture(autouse=True)
def empty_cache() -> Any:
    system._sidecar_cache.clear()
    yield
    system._sidecar_cache.clear()


async def test_the_status_route_reports_an_unset_url_as_absent() -> None:
    request = fake_request(downloader_url="", transcriber_url="")

    assert await system._downloader_status(request) == {"configured": False, "ok": None}
    assert await system._transcriber_status(request) == {"configured": False, "ok": None}


async def test_the_status_route_reuses_a_recent_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four pages poll this route; the downloader walks its volume to answer."""
    calls: list[str] = []

    async def probe(client: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(client._base)
        return {"configured": True, "ok": True, "latency_ms": 3, "detail_code": None}

    monkeypatch.setattr(sidecars, "downloader_status", probe)
    request = fake_request(downloader_url=DOWNLOADER_URL, downloader_token="")

    first = await system._downloader_status(request)
    first["ok"] = False  # a caller mutating its copy must not reach the cache
    second = await system._downloader_status(request)

    assert calls == [DOWNLOADER_URL]
    assert second["ok"] is True


async def test_a_cached_answer_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def probe(client: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(client._base)
        return {"configured": True, "ok": False, "detail_code": "unreachable"}

    monkeypatch.setattr(sidecars, "transcriber_status", probe)
    monkeypatch.setattr(system, "SIDECAR_CACHE_SECONDS", 0.0)
    request = fake_request(transcriber_url=TRANSCRIBER_URL, transcriber_token=TOKEN)

    await system._transcriber_status(request)
    await system._transcriber_status(request)

    assert calls == [TRANSCRIBER_URL, TRANSCRIBER_URL]
