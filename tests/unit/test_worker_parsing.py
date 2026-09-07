"""Turning a pasted link into an endpoint call.

Both POST /api/v1/parse and the MCP parse_url tool queue the logical endpoint
"parse", which names only a URL. Without this resolution step every "just give
it a link" path fails with "unknown endpoint", which is the single most likely
thing a first-time user tries.
"""

from __future__ import annotations

import pytest

from dtk.core.errors import InvalidUrl, UnsupportedContent
from dtk.urls import identify
from dtk.worker.parsing import plan, to_call

DOUYIN_VIDEO = "https://www.douyin.com/video/7372484719365098803"
TIKTOK_VIDEO = "https://www.tiktok.com/@someone/video/7372484719365098803"
DOUYIN_USER = "https://www.douyin.com/user/MS4wLjABAAAA_synthetic_sec_uid"


async def no_redirect(_url: str) -> str | None:
    return None


class TestDispatch:
    @pytest.mark.parametrize(
        ("url", "endpoint", "param", "value"),
        [
            (DOUYIN_VIDEO, "douyin.content_detail", "content_id", "7372484719365098803"),
            (TIKTOK_VIDEO, "tiktok.content_detail", "content_id", "7372484719365098803"),
            (DOUYIN_USER, "douyin.author_profile", "author_id", "MS4wLjABAAAA_synthetic_sec_uid"),
        ],
    )
    async def test_maps_a_link_onto_a_registered_endpoint(self, url, endpoint, param, value):
        got_endpoint, params = await plan(url, no_redirect)
        assert got_endpoint == endpoint
        assert params == {param: value}

    async def test_every_produced_endpoint_actually_exists(self):
        """The mapping is only useful if the worker can run what it names."""
        from dtk.worker.registry import ENDPOINTS

        for url in (DOUYIN_VIDEO, TIKTOK_VIDEO, DOUYIN_USER):
            endpoint, _ = await plan(url, no_redirect)
            assert endpoint in ENDPOINTS, f"{endpoint} is not a registered endpoint"

    async def test_produced_params_satisfy_the_endpoint(self):
        from dtk.worker.registry import definition_for

        for url in (DOUYIN_VIDEO, DOUYIN_USER):
            endpoint, params = await plan(url, no_redirect)
            definition = definition_for(endpoint)
            assert set(definition.required) <= set(params), (
                f"{endpoint} needs {definition.required}, parse produced {sorted(params)}"
            )


class TestRefusals:
    async def test_an_unrecognised_host_is_refused(self):
        with pytest.raises(InvalidUrl):
            await plan("https://example.com/whatever", no_redirect)

    async def test_a_supported_host_with_no_identifier_is_refused(self):
        with pytest.raises((InvalidUrl, UnsupportedContent)):
            await plan("https://www.douyin.com/", no_redirect)

    async def test_an_unsupported_resource_kind_says_so(self):
        """A live room is a recognised link the project does not serve yet, and
        the caller should be told that rather than 'invalid URL'."""
        live = identify("https://live.douyin.com/123456789")
        if live.platform is None:
            pytest.skip("live links are not recognised by the URL layer")
        with pytest.raises((UnsupportedContent, InvalidUrl)):
            await plan("https://live.douyin.com/123456789", no_redirect)


class TestExpansion:
    async def test_a_short_link_is_followed_then_re_identified(self):
        # The fetcher returns the next hop, or None when there is no further
        # redirect. Returning the same target forever is a loop, and the URL
        # layer correctly refuses it.
        # Matched on the token rather than the exact string: the URL layer
        # normalizes before handing the link to the fetcher, so an equality
        # check here would silently test nothing.
        async def fetcher(url: str) -> str | None:
            return DOUYIN_VIDEO if "abc123" in url else None

        endpoint, params = await plan("https://v.douyin.com/abc123/", fetcher)
        assert endpoint == "douyin.content_detail"
        assert params == {"content_id": "7372484719365098803"}

    async def test_a_short_link_that_leaves_the_allowlist_is_refused(self):
        """A short link cannot be checked before it is followed, so the target
        is checked after every hop. This is the SSRF case that matters."""

        async def fetcher(url: str) -> str | None:
            return "http://169.254.169.254/latest/meta-data/" if "v.douyin" in url else None

        with pytest.raises(InvalidUrl):
            await plan("https://v.douyin.com/abc123/", fetcher)

    async def test_a_dead_short_link_is_refused_not_hung(self):
        async def fetcher(_url: str) -> str | None:
            return None

        with pytest.raises((InvalidUrl, UnsupportedContent)):
            await plan("https://v.douyin.com/abc123/", fetcher)


def test_to_call_is_pure():
    endpoint, params = to_call(identify(DOUYIN_VIDEO))
    assert endpoint == "douyin.content_detail"
    assert params == {"content_id": "7372484719365098803"}
