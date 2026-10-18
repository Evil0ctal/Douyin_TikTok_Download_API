"""How an in-flight download is reported to the listing.

The numbers come from the downloader sidecar while a job runs. The case that
prompted all of this: a 101 MB Bilibili video took a minute and a half, and the
page showed the cover's 366 KB the whole time, because the bytes were read from
a total the sidecar writes only when the job settles.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.api.routes.downloads import progress_of

COVER = 374_573
VIDEO = 101_385_944


def item(name: str, state: str, written: int = 0, **extra: Any) -> dict[str, Any]:
    kind = "video" if name.startswith("video") else "cover" if name == "cover.jpg" else "image"
    return {"name": name, "kind": kind, "state": state, "bytes": written, **extra}


def test_a_video_halfway_through_reports_its_bytes_and_its_size() -> None:
    job = {
        "state": "running",
        "bytes_total": 0,
        "items": [
            item("cover.jpg", "done", COVER, bytes_expected=COVER),
            item(
                "video.mp4",
                "running",
                40_000_000,
                bytes_expected=VIDEO,
                bytes_per_second=1_200_000,
            ),
        ],
    }

    progress = progress_of(job)

    assert progress["bytes"] == COVER + 40_000_000
    assert progress["bytes_expected"] == COVER + VIDEO
    assert progress["bytes_per_second"] == 1_200_000
    assert (progress["files_done"], progress["files_total"]) == (1, 2)
    video = progress["items"][1]
    assert video == {
        "name": "video.mp4",
        "kind": "video",
        "state": "running",
        "bytes": 40_000_000,
        "bytes_expected": VIDEO,
        "bytes_per_second": 1_200_000,
    }


@pytest.mark.parametrize(
    "waiting",
    [
        pytest.param(item("image-03.jpg", "queued"), id="a file not started yet"),
        pytest.param(item("video.mp4", "running", 500), id="a server that declared no size"),
    ],
)
def test_the_total_is_unknown_while_any_part_of_it_is(waiting: dict[str, Any]) -> None:
    job = {"state": "running", "items": [item("cover.jpg", "done", COVER), waiting]}

    progress = progress_of(job)

    # The page then counts files rather than drawing a bar against a guess.
    assert progress["bytes_expected"] is None
    assert progress["bytes"] == COVER + waiting["bytes"]


def test_a_failed_file_weighs_nothing() -> None:
    job = {
        "state": "running",
        "items": [
            item("image-01.jpg", "failed", 0, bytes_expected=9_000),
            item("video.mp4", "running", 100, bytes_expected=1_000),
        ],
    }

    assert progress_of(job)["bytes_expected"] == 1_000


def test_a_server_that_sends_more_than_it_declared_does_not_run_past_the_end() -> None:
    job = {"state": "running", "items": [item("video.mp4", "running", 1_500, bytes_expected=1_000)]}

    progress = progress_of(job)

    assert progress["bytes_expected"] == 1_500
    assert progress["bytes"] <= progress["bytes_expected"]


def test_an_older_sidecar_still_draws_by_files() -> None:
    """A sidecar from before 5.1.6 sends neither a size nor a rate."""
    job = {
        "state": "running",
        "bytes_total": 0,
        "items": [item("cover.jpg", "done", COVER), item("video.mp4", "queued"), "junk"],
    }

    progress = progress_of(job)

    assert progress["bytes"] == COVER
    assert progress["bytes_expected"] is None
    assert progress["bytes_per_second"] == 0
    assert (progress["files_done"], progress["files_total"]) == (1, 2)
