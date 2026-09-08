"""The downloader client.

Like the browser-rpc client, its central property is that being unavailable is
never fatal: media storage is an opt-in container, and an instance without it
still archives everything. So every failure path degrades into a message a
caller can act on rather than a 500.
"""

from __future__ import annotations

import httpx
import pytest

from dtk.media.client import DownloaderBusy, DownloaderClient, DownloaderUnavailable

BASE = "http://downloader:9100"


def client_with(handler) -> DownloaderClient:
    return DownloaderClient(BASE, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


class TestConfiguration:
    async def test_an_unconfigured_client_reports_itself_unavailable(self):
        client = DownloaderClient("")
        assert client.configured is False
        health = await client.health()
        assert health.available is False
        assert "not configured" in health.detail

    async def test_an_unconfigured_submit_raises_the_degradable_error(self):
        with pytest.raises(DownloaderUnavailable):
            await DownloaderClient("").submit({"id": "x"})


class TestHealth:
    async def test_reports_the_queue_and_the_volume(self):
        def handler(request):
            assert request.url.path == "/health"
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "version": "1.0",
                    "workers": 4,
                    "queued": 1,
                    "running": 2,
                    "total_bytes": 4096,
                },
            )

        health = await client_with(handler).health()
        assert health.available
        assert (health.queued, health.running, health.total_bytes) == (1, 2, 4096)

    async def test_a_dead_sidecar_is_a_report_not_an_exception(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        health = await client_with(handler).health()
        assert health.available is False
        assert health.detail


class TestSubmit:
    async def test_a_full_queue_is_its_own_error(self):
        # Distinct from unavailable on purpose: the caller should back off and
        # retry, not report the downloader as broken.
        def handler(request):
            return httpx.Response(429, json={"error": "the download queue is full"})

        with pytest.raises(DownloaderBusy):
            await client_with(handler).submit({"id": "x"})

    async def test_a_refusal_carries_the_sidecars_own_message(self):
        # "host p42.example.com is not an allowed media domain" is the single
        # most useful sentence this system can print when a download fails.
        def handler(request):
            return httpx.Response(
                400, json={"error": "host p42.example.com is not an allowed media domain"}
            )

        with pytest.raises(DownloaderUnavailable) as caught:
            await client_with(handler).submit({"id": "x"})
        assert "p42.example.com" in str(caught.value)

    async def test_the_token_is_sent_when_one_is_configured(self):
        seen: dict[str, str] = {}

        def handler(request):
            seen.update(request.headers)
            return httpx.Response(202, json={"id": "x", "state": "queued"})

        client = DownloaderClient(
            BASE,
            token="shared",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        await client.submit({"id": "x"})
        assert seen["x-downloader-token"] == "shared"


class TestJob:
    async def test_a_forgotten_job_reads_as_none_rather_than_an_error(self):
        # The sidecar keeps a bounded history and forgets everything on
        # restart; the durable record is in Postgres, so the caller should
        # trust its own row rather than retry forever.
        def handler(request):
            return httpx.Response(404, json={"error": "no such job"})

        assert await client_with(handler).job("abc") is None

    async def test_an_unreachable_sidecar_still_raises(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with pytest.raises(DownloaderUnavailable):
            await client_with(handler).job("abc")


class TestFiles:
    async def test_deleting_nothing_does_not_call_the_sidecar(self):
        def handler(request):  # pragma: no cover - must never run
            raise AssertionError("an empty delete should not reach the network")

        assert await client_with(handler).delete([]) == {"freed_bytes": 0, "removed": []}

    async def test_delete_reports_what_was_freed(self):
        def handler(request):
            assert request.url.path == "/files/delete"
            return httpx.Response(200, json={"freed_bytes": 100, "removed": ["a/b/c"]})

        answer = await client_with(handler).delete(["a/b/c"])
        assert answer["freed_bytes"] == 100
