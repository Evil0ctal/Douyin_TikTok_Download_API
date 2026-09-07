"""The console shell: how the SPA's HTML is served.

The console is a static bundle, but its one HTML document is not static: it
carries the language the server negotiated for that request, so the shell and
the API answer to the same Accept-Language header. Everything here is about
that seam, including the cache headers the negotiation forces, and the one
route the shell must decline: a path under an API prefix is answered as an API
error rather than as HTML.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.core.errors import ErrorCode
from dtk.core.types import Language
from dtk.i18n.messages import render
from tests.integration import test_api_support as support

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration


async def test_the_console_shell_is_served_in_the_negotiated_language(client: Any) -> None:
    """The shell and the API answer to the same header.

    Before this, index.html was a static file with lang="en" baked in, so a
    browser sending Accept-Language: zh got Chinese API errors inside an English
    shell until it reached navigator.language on the client. The two now agree
    from the first byte.
    """
    english = await client.get("/", headers={"Accept-Language": "en-GB,en;q=0.9"})
    assert english.status_code == 200
    assert 'lang="en"' in english.text
    assert 'data-language-source="negotiated"' in english.text

    chinese = await client.get("/", headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    assert chinese.status_code == 200
    assert 'lang="zh"' in chinese.text
    assert 'data-language-source="build"' not in chinese.text

    # An unsupported language is not half-served: it falls back rather than
    # leaving the marker claiming a negotiation that did not happen.
    french = await client.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"})
    assert 'lang="en"' in french.text
    assert 'data-language-source="negotiated"' in french.text


async def test_the_shell_says_it_varies_and_must_not_be_shared(client: Any) -> None:
    """Without these headers a proxy hands one visitor's language to the next."""
    response = await client.get("/", headers={"Accept-Language": "zh"})
    assert "accept-language" in response.headers.get("vary", "").lower()
    assert "no-store" in response.headers.get("cache-control", "").lower()


async def test_index_html_by_name_answers_the_same_as_the_root(client: Any) -> None:
    """Otherwise /index.html would be the one URL that ignores the header."""
    named = await client.get("/index.html", headers={"Accept-Language": "zh"})
    assert 'lang="zh"' in named.text
    assert "accept-language" in named.headers.get("vary", "").lower()


async def test_a_query_parameter_still_wins_over_the_header(client: Any) -> None:
    """?lang= outranks Accept-Language for the shell exactly as it does for the API."""
    response = await client.get("/?lang=zh", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert 'lang="zh"' in response.text


async def test_an_unknown_api_path_is_refused_in_the_negotiated_language(client: Any) -> None:
    """The catch-all declines API prefixes, and that refusal is an API answer.

    It used to hand back an envelope built by hand with an English sentence in
    it, which made a mistyped API path the one response whose message ignored
    the header the rest of the API obeys.
    """
    for language in (Language.EN, Language.ZH):
        response = await client.get(
            "/api/v1/no-such-endpoint", headers={"Accept-Language": language.value}
        )
        assert response.status_code == 404
        body = support.envelope(response)
        assert body["error"]["code"] == ErrorCode.NOT_FOUND.value
        assert body["error"]["message"] == render(ErrorCode.NOT_FOUND, language)
        # The correlation id is the whole point of the meta block; a hand-built
        # envelope is exactly where it goes missing.
        assert body["meta"]["request_id"] == response.headers["X-Request-ID"]

    # Both languages matching their own template is only meaningful while the
    # templates differ; equal ones would mean zh silently fell back to English.
    assert render(ErrorCode.NOT_FOUND, Language.EN) != render(ErrorCode.NOT_FOUND, Language.ZH)
