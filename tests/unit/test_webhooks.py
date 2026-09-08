"""Task callbacks: the half of `callback_url` that never existed.

The parameter was declared, validated and stored from the first release, and
no line in the repository ever made a request to it. So the tests here are
mostly about the two properties that make delivery safe to add at all: it
cannot hurt the task, and it cannot be pointed inside.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from dtk.ops import webhooks


def client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestPayload:
    def test_carries_what_happened_not_what_was_found(self):
        """A result can be megabytes, and posting it to a third party is a
        data-flow decision nobody made by writing a URL in a query string."""
        body = webhooks.payload_for("t1", endpoint="douyin.content_detail", state="done")
        assert body["event"] == "task.completed"
        assert body["task_id"] == "t1"
        assert "result" not in body
        assert "data" not in body

    def test_a_failure_carries_its_reason(self):
        body = webhooks.payload_for(
            "t1",
            endpoint="parse",
            state="failed",
            error={"code": "NOT_FOUND", "message": "gone"},
        )
        assert body["event"] == "task.failed"
        assert body["error"]["code"] == "NOT_FOUND"

    def test_a_long_error_message_is_bounded(self):
        body = webhooks.payload_for(
            "t1", endpoint="parse", state="failed", error={"code": "X", "message": "y" * 5000}
        )
        assert len(body["error"]["message"]) == 500


class TestSignature:
    def test_signs_the_exact_bytes_sent(self):
        """Over the bytes rather than a re-serialization: a receiver can only
        verify what actually arrived, and key order would break the check."""
        body = json.dumps({"a": 1}).encode()
        expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert webhooks.sign(body, "secret") == f"sha256={expected}"


class TestRecheck:
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/hook",
            "https://127.0.0.1/hook",
            "https://localhost/hook",
            "https://192.168.1.1/hook",
            "ftp://example.com/hook",
            "not a url at all",
        ],
    )
    async def test_refuses_what_should_never_be_dialled(self, url: str):
        assert await webhooks.recheck(url) is not None

    async def test_re_validates_rather_than_trusting_submission(self):
        # A queued task can outlive the moment it was accepted, and the setting
        # can be turned off in between.
        assert await webhooks.recheck("https://10.0.0.1/hook") is not None

    async def test_an_unresolvable_host_is_refused_rather_than_dialled(self):
        assert await webhooks.recheck("https://nx.invalid/hook") is not None


class TestDelivery:
    async def test_a_2xx_is_delivered_once(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200)

        result = await webhooks.deliver(
            "https://example.com/hook",
            {"event": "task.completed"},
            secret="s",
            client=client_with(handler),
        )
        assert result.delivered is True
        assert result.attempts == 1
        assert seen[webhooks.SIGNATURE_HEADER.lower()].startswith("sha256=")
        assert seen[webhooks.EVENT_HEADER.lower()] == "task.completed"

    async def test_no_secret_means_no_signature_header(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(204)

        await webhooks.deliver(
            "https://example.com/hook", {"event": "task.completed"}, client=client_with(handler)
        )
        assert webhooks.SIGNATURE_HEADER.lower() not in seen

    async def test_a_4xx_is_not_retried(self):
        """The receiver understood and refused; repeating a 404 just repeats
        the same rejection."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        result = await webhooks.deliver(
            "https://example.com/hook", {"event": "x"}, client=client_with(handler)
        )
        assert result.delivered is False
        assert calls["n"] == 1

    async def test_a_dead_endpoint_never_raises(self, monkeypatch):
        """The contract, not an implementation detail: the caller already has
        its data, and a webhook that is down must not fail a good task."""
        monkeypatch.setattr(webhooks, "RETRY_DELAYS", (0.0, 0.0))

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        result = await webhooks.deliver(
            "https://example.com/hook", {"event": "x"}, client=client_with(handler)
        )
        assert result.delivered is False
        assert result.attempts == webhooks.MAX_ATTEMPTS

    async def test_a_private_target_is_refused_without_a_request(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("a private target must never be dialled")

        result = await webhooks.deliver(
            "https://127.0.0.1/hook", {"event": "x"}, client=client_with(handler)
        )
        assert result.delivered is False
        assert "private" in result.detail or "loopback" in result.detail

    async def test_the_url_is_never_logged_in_full(self, caplog):
        """A callback URL commonly carries a token in its path or query.

        This first failed on httpx's own request logger rather than on anything
        in this module - it prints the full URL at INFO - which is why
        `dtk.core.logging.configure` now pins that logger to WARNING. The
        assertion lives here because this is the caller that makes it matter.
        """
        from dtk.core.logging import configure

        configure(level="info", json_output=False)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        # at_level() on the root logger only. The point is precisely that the
        # httpx logger is pinned below it, so forcing httpx's own level here
        # would test the opposite of what this asserts.
        with caplog.at_level("INFO", logger=""):
            await webhooks.deliver(
                "https://example.com/hook?token=supersecret",
                {"event": "x"},
                client=client_with(handler),
            )
        assert "supersecret" not in caplog.text
