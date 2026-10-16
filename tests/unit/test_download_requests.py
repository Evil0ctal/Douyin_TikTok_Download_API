"""Input resolution shared by REST and MCP media downloads."""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dtk.core.config import Config
from dtk.core.errors import InvalidParam, NotConfigured, QueueFull
from dtk.core.types import Platform
from dtk.ops.capacity import CapacityReport, CapacityState
from dtk.services import download_requests
from dtk.services.download_requests import resolve_target


def test_a_full_post_url_resolves_to_its_key() -> None:
    assert resolve_target(
        url="share https://www.douyin.com/video/7100000000000000000 end",
        platform=None,
        content_id=None,
    ) == (Platform.DOUYIN, "7100000000000000000")


def test_platform_and_content_id_are_accepted() -> None:
    assert resolve_target(
        url=None,
        platform=Platform.TIKTOK,
        content_id="7100000000000000000",
    ) == (Platform.TIKTOK, "7100000000000000000")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"url": "https://v.douyin.com/example/", "platform": None, "content_id": None},
        {
            "url": "https://www.douyin.com/video/7100000000000000000",
            "platform": Platform.TIKTOK,
            "content_id": None,
        },
        {
            "url": "https://www.douyin.com/video/7100000000000000000",
            "platform": None,
            "content_id": "7200000000000000000",
        },
        {"url": "https://example.com/video/7100", "platform": None, "content_id": None},
        {"url": None, "platform": None, "content_id": None},
    ],
)
def test_invalid_or_ambiguous_targets_are_refused(kwargs) -> None:
    with pytest.raises(InvalidParam):
        resolve_target(**kwargs)


async def test_an_unconfigured_downloader_is_refused_before_writing() -> None:
    with pytest.raises(NotConfigured):
        await download_requests.start(
            cast(AsyncSession, object()),
            config=Config.defaults(),
            downloader_configured=False,
            url=None,
            platform=Platform.DOUYIN,
            content_id="7100000000000000000",
        )


async def test_disabled_media_is_refused_before_writing() -> None:
    with pytest.raises(NotConfigured):
        await download_requests.start(
            cast(AsyncSession, object()),
            config=Config({"media.enabled": False}),
            downloader_configured=True,
            url=None,
            platform=Platform.DOUYIN,
            content_id="7100000000000000000",
        )


async def test_capacity_hard_stop_is_refused_before_writing(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        download_requests.capacity,
        "evaluate",
        lambda **_kwargs: CapacityReport(CapacityState.FULL, (), detail="media is 99% full"),
    )
    with pytest.raises(QueueFull) as caught:
        await download_requests.start(
            cast(AsyncSession, object()),
            config=Config.defaults(),
            downloader_configured=True,
            url=None,
            platform=Platform.DOUYIN,
            content_id="7100000000000000000",
        )
    assert caught.value.retry_after == download_requests.CAPACITY_RETRY_AFTER
