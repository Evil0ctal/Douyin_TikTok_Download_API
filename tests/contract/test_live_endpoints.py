"""Live platform contract checks.

Marked `live` and skipped unless a deployment is configured, so an ordinary test
run and an ordinary CI job never touch the network.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.contract.subjects import SUBJECTS, by_kind

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

BASE_URL = os.environ.get("DTK_CONTRACT_BASE_URL", "")
API_KEY = os.environ.get("DTK_CONTRACT_API_KEY", "")

skip_unconfigured = pytest.mark.skipif(
    not BASE_URL or not API_KEY,
    reason="set DTK_CONTRACT_BASE_URL and DTK_CONTRACT_API_KEY to run contract tests",
)
skip_no_subjects = pytest.mark.skipif(
    not SUBJECTS, reason="no test subjects configured; see tests/contract/subjects.py"
)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=60.0,
    ) as c:
        yield c


def assert_envelope(payload: dict) -> dict:
    assert "success" in payload and "meta" in payload
    assert payload["meta"].get("request_id"), "no request_id to quote in a bug report"
    if not payload["success"]:
        code = payload["error"]["code"]
        pytest.fail(f"{code}: {payload['error'].get('message')}")
    return payload["data"]


@skip_unconfigured
@skip_no_subjects
@pytest.mark.parametrize("subject", by_kind("video"), ids=lambda s: s.note)
async def test_video_still_parses(client, subject):
    """Structural, strict: a change of shape must fail loudly."""
    response = await client.post("/api/v1/parse", json={"url": subject.url}, params={"wait": 30})
    assert response.status_code in (200, 202), response.text
    if response.status_code == 202:
        pytest.skip("the pool could not serve this in time; not a platform change")

    data = assert_envelope(response.json())
    for field in ("platform", "content_id", "kind", "web_url", "author", "stats", "media"):
        assert field in data, f"missing {field}; the platform response shape changed"
    assert isinstance(data["content_id"], str), "ids must stay strings"
    assert data["author"]["uid"], "author identity is missing"


@skip_unconfigured
@skip_no_subjects
@pytest.mark.parametrize("subject", by_kind("video"), ids=lambda s: s.note)
async def test_video_metrics_are_plausible(client, subject):
    """Numeric, loose: invariants only.

    Never exact values - a public video's counters change by the second, and
    pinning them would make this suite flap for reasons that are not failures.
    """
    response = await client.post("/api/v1/parse", json={"url": subject.url}, params={"wait": 30})
    if response.status_code == 202:
        pytest.skip("not served in time")
    stats = assert_envelope(response.json())["stats"]
    for name, value in stats.items():
        if value is not None:
            assert isinstance(value, int) and value >= 0, f"{name} is implausible: {value}"


@skip_unconfigured
async def test_pool_is_usable(client):
    """A red run has to be attributable. If the pool is empty the platform is
    not the thing that changed, and the report should say so."""
    response = await client.get("/api/v1/admin/endpoints/health")
    if response.status_code == 403:
        pytest.skip("this key lacks admin scope")
    assert response.status_code == 200, response.text
