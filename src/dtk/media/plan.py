"""Turning one archived post into a list of files to fetch.

This module is where the main service keeps every policy decision the
downloader is not allowed to make: which files a post is worth storing, what
they are called, how big each one may be, and which content types are
acceptable for it. The Go service receives the answers and executes them.

That split is deliberate. A downloader that decided its own filenames would be
a downloader deciding where bytes land, and a downloader that decided its own
ceiling would be one whose limits could not be changed from the console.

Naming is ours, always. ``ParseTool.tsx`` already demonstrates why the CDN's
own filename is not usable: the path component of a signed URL is an opaque
object key, not a title, and half of them have no extension at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

from dtk.core.types import Platform
from dtk.media.domains import allowed_mirrors

#: Covers and album images never approach the video ceiling, and a 500 MB
#: "image" is a sign something is wrong rather than a large photograph.
IMAGE_MAX_BYTES: Final = 32 * 1024 * 1024

#: How many files one post may produce. A Douyin image album tops out around
#: 35 slides; the cap is a guard against a malformed payload turning into a
#: thousand-item job, not a limit anyone should meet.
MAX_ITEMS: Final = 64

#: Content types each kind will accept.
#:
#: Video allows ``application/octet-stream`` because that is how several edges
#: answer a download of an mp4; it is a "we did not say" type, not a wrong one.
#: Images do not, because every image host measured returns a real image type,
#: and the whole value of the check is refusing the HTML interstitial and the
#: JSON error page that also arrive with a 200.
VIDEO_ACCEPT: Final[tuple[str, ...]] = ("video/", "application/octet-stream")
IMAGE_ACCEPT: Final[tuple[str, ...]] = ("image/",)

#: Extensions we are willing to take from a URL path. Anything else falls back
#: to the kind's default, so a query string ending in ``.php`` cannot name a
#: file on the operator's disk.
VIDEO_EXTENSIONS: Final[frozenset[str]] = frozenset({"mp4", "mov", "webm", "m4v"})
IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({"jpg", "jpeg", "png", "webp", "heic", "gif"})

#: The origin each platform's CDNs expect to see on a media request.
REFERER: Final[dict[Platform, str]] = {
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.TIKTOK: "https://www.tiktok.com/",
}

#: A desktop Chrome string. The CDNs are not signing on it, but an obviously
#: automated agent is answered differently by some edges, and this costs
#: nothing (see dtk.worker.parsing for the same reasoning on the expansion hop).
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class PlannedItem:
    """One file the downloader will be asked to fetch."""

    name: str
    kind: str
    mirrors: tuple[str, ...]
    accept: tuple[str, ...]
    max_bytes: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "mirrors": list(self.mirrors),
            "accept": list(self.accept),
            "max_bytes": self.max_bytes,
        }


@dataclass(frozen=True, slots=True)
class Plan:
    """What a job will fetch, and what it decided not to."""

    items: tuple[PlannedItem, ...] = ()
    #: Why a mirror or a whole item was left out, in the operator's words.
    skipped: tuple[str, ...] = field(default=())

    @property
    def empty(self) -> bool:
        return not self.items

    @property
    def total_ceiling(self) -> int:
        return sum(item.max_bytes for item in self.items)


def _extension(url: str, allowed: frozenset[str], fallback: str) -> str:
    """The file extension, taken from the URL only when it is one we know."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return fallback
    _, _, suffix = path.rpartition(".")
    suffix = suffix.lower()
    return suffix if suffix in allowed else fallback


def _mirrors(source: dict[str, Any], platform: Platform) -> tuple[list[str], list[str]]:
    """Every URL for one media object, best first, allowlist applied."""
    candidates: list[str] = []
    primary = source.get("url")
    if isinstance(primary, str) and primary:
        candidates.append(primary)
    alternates = source.get("urls")
    if isinstance(alternates, list):
        candidates.extend(url for url in alternates if isinstance(url, str) and url)
    return allowed_mirrors(candidates, platform)


def _video_streams(media: dict[str, Any]) -> list[dict[str, Any]]:
    """The video and its alternate bitrates, as one ordered list.

    The parser's own order is kept. It puts the watermark-free stream first,
    and reordering by bitrate here would quietly prefer a larger file with a
    watermark burned into it.
    """
    streams: list[dict[str, Any]] = []
    video = media.get("video")
    if isinstance(video, dict):
        streams.append(video)
    alternates = media.get("streams")
    if isinstance(alternates, list):
        streams.extend(entry for entry in alternates if isinstance(entry, dict))
    return streams


def build(
    media: dict[str, Any] | None,
    platform: Platform,
    *,
    max_file_bytes: int,
    include_cover: bool = True,
) -> Plan:
    """Plan the files to fetch for one post.

    ``media`` is the parsed manifest as the archive stores it. Absent or
    unusable, the result is an empty plan with a reason rather than an
    exception: "this post has nothing downloadable" is an answer the console
    should be able to show.
    """
    if not isinstance(media, dict) or not media:
        return Plan(skipped=("this post has no media manifest; parse it again",))

    items: list[PlannedItem] = []
    skipped: list[str] = []

    def note(reasons: list[str]) -> None:
        for reason in reasons:
            if reason not in skipped:
                skipped.append(reason)

    # --- the video ------------------------------------------------------
    # All bitrates of one video are mirrors of each other for this purpose:
    # any of them is the post, and the first that lands wins. Storing several
    # copies of the same clip is not a feature, it is a full disk.
    streams = _video_streams(media)
    if streams:
        mirrors: list[str] = []
        for stream in streams:
            allowed, refused = _mirrors(stream, platform)
            mirrors.extend(url for url in allowed if url not in mirrors)
            note(refused)
        if mirrors:
            items.append(
                PlannedItem(
                    name=f"video.{_extension(mirrors[0], VIDEO_EXTENSIONS, 'mp4')}",
                    kind="video",
                    mirrors=tuple(mirrors),
                    accept=VIDEO_ACCEPT,
                    max_bytes=max_file_bytes,
                )
            )
        else:
            skipped.append("no video mirror survived the media allowlist")

    # --- album images ---------------------------------------------------
    images = media.get("images")
    if isinstance(images, list):
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict) or len(items) >= MAX_ITEMS:
                continue
            mirrors, refused = _mirrors(image, platform)
            note(refused)
            if not mirrors:
                continue
            extension = _extension(mirrors[0], IMAGE_EXTENSIONS, "jpg")
            items.append(
                PlannedItem(
                    name=f"image-{index:02d}.{extension}",
                    kind="image",
                    mirrors=tuple(mirrors),
                    accept=IMAGE_ACCEPT,
                    max_bytes=min(max_file_bytes, IMAGE_MAX_BYTES),
                )
            )

    # --- the cover ------------------------------------------------------
    # Last, and only one of them. The covers list holds an animated version and
    # a static fallback of the same frame; both is a duplicate, and the cover
    # is the least important thing here - it must never be what pushes a post
    # past the item cap and drops an album slide.
    covers = media.get("covers")
    if include_cover and isinstance(covers, list) and len(items) < MAX_ITEMS:
        for cover in covers:
            if not isinstance(cover, dict):
                continue
            mirrors, refused = _mirrors(cover, platform)
            note(refused)
            if not mirrors:
                continue
            items.append(
                PlannedItem(
                    name=f"cover.{_extension(mirrors[0], IMAGE_EXTENSIONS, 'jpg')}",
                    kind="cover",
                    mirrors=tuple(mirrors),
                    accept=IMAGE_ACCEPT,
                    max_bytes=min(max_file_bytes, IMAGE_MAX_BYTES),
                )
            )
            break

    if not items and not skipped:
        skipped.append("this post has no downloadable media")
    return Plan(items=tuple(items), skipped=tuple(skipped))


def sidecar(row: Any, plan: Plan) -> dict[str, Any]:
    """The ``meta.json`` written beside the files.

    A video file on a disk is anonymous. This is what makes a folder found two
    years later still say whose post it was, when it was published and what it
    said - which is most of the reason for keeping the files at all.

    It carries no signed URL. Those expire within hours and would be the one
    part of the sidecar that is misleading rather than merely stale;
    ``web_url`` is the link that still resolves.
    """
    return {
        "platform": row.platform,
        "content_id": row.content_id,
        "kind": row.kind,
        "web_url": row.web_url,
        "title": row.title,
        "description": row.description,
        "created_at": row.platform_created_at.isoformat() if row.platform_created_at else None,
        "duration_ms": row.duration_ms,
        "author": {"uid": row.author_uid, "nickname": row.author_nickname},
        "music": {"music_id": row.music_id, "title": row.music_title},
        "tags": list(row.tags or []),
        "location": row.location,
        "files": [{"name": item.name, "kind": item.kind} for item in plan.items],
        "archived_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "written_by": "dtk",
    }


__all__ = [
    "IMAGE_ACCEPT",
    "IMAGE_MAX_BYTES",
    "MAX_ITEMS",
    "REFERER",
    "USER_AGENT",
    "VIDEO_ACCEPT",
    "Plan",
    "PlannedItem",
    "build",
    "sidecar",
]
