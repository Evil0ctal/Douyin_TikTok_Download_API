"""The probe client has to reach every scheme a proxy row may hold.

`socks5://` and `socks5h://` were accepted by the console and the API, dialled
fine by wreq for real traffic, and could never be probed: httpx needs the
`socks` extra for them and the project did not install it. The probe raised
ImportError, the sweep logged it and wrote nothing, and the console's Test
button answered INTERNAL - so a working SOCKS proxy sat with no health verdict
at all (#781). Each scheme here only has to come back with a verdict, not a
crash; a closed local port makes that verdict a fast, offline "no".
"""

from __future__ import annotations

import socket

import pytest

from dtk.api.routes.admin.proxy_urls import ALLOWED_SCHEMES
from dtk.worker.proxy_prober import HttpxProbeClient


def _closed_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.mark.parametrize("scheme", sorted(ALLOWED_SCHEMES))
async def test_every_accepted_scheme_gets_a_verdict(scheme: str) -> None:
    client = HttpxProbeClient("http://127.0.0.1:9/", timeout=2.0)

    result = await client.probe(f"{scheme}://127.0.0.1:{_closed_port()}")

    assert result.ok is False
    assert result.detail
