"""Normalized cross-platform models.

This is the hard contract every parser, serializer, MCP tool and frontend view
depends on. See docs/design/11-data-contracts.md.

Three rules hold everywhere in this module:

1. Absent values are ``None``, never ``0`` or ``""``. A missing play count and a
   play count of zero are different facts; conflating them puts phantom cliffs
   into the snapshot curves.
2. Every model keeps the untouched platform payload in ``raw`` so new metrics can
   be back-computed later without re-fetching. It is excluded from API responses
   unless explicitly requested.
3. Identifiers are strings. ``aweme_id`` is a 19-digit number that exceeds the
   JavaScript safe integer range, so an int silently loses precision in the UI.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from dtk.core.types import ContentKind, Platform


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Image(_Base):
    url: str
    #: Alternate CDN mirrors. Kept in full so the browser can fail over when a
    #: signed direct link expires; downloads never proxy through the server.
    urls: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None


class VideoStream(_Base):
    url: str
    urls: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    bitrate: int | None = None
    format: str | None = None
    size_bytes: int | None = None
    watermark: bool = False


class Media(_Base):
    #: Usually more than one: an animated cover plus a static fallback.
    covers: list[Image] = Field(default_factory=list)
    #: Present when kind is VIDEO. The watermark-free stream.
    video: VideoStream | None = None
    #: Alternate bitrates for the same video.
    streams: list[VideoStream] = Field(default_factory=list)
    #: Present when kind is IMAGE_ALBUM.
    images: list[Image] = Field(default_factory=list)


class Music(_Base):
    music_id: str | None = None
    title: str | None = None
    author: str | None = None
    duration_ms: int | None = None
    play_url: str | None = None
    cover: Image | None = None


class ContentStats(_Base):
    """Platform-neutral metric names.

    Douyin says ``digg_count``, TikTok says ``diggCount``, the UI says 'likes';
    this contract says ``digg_count`` everywhere.
    """

    play_count: int | None = None
    digg_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    collect_count: int | None = None


class AuthorStats(_Base):
    follower_count: int | None = None
    following_count: int | None = None
    content_count: int | None = None
    total_digg: int | None = None


class Author(_Base):
    platform: Platform
    #: The platform's stable primary key: Douyin ``sec_user_id`` (not ``uid``,
    #: which rotates), TikTok's numeric id. Never ``unique_id`` - users edit it.
    uid: str
    unique_id: str | None = None
    nickname: str
    signature: str | None = None
    avatar: Image | None = None
    web_url: str | None = None
    verified: bool = False
    #: Usually absent in list endpoints, which return an abbreviated author.
    stats: AuthorStats | None = None
    raw: dict | None = None


class Content(_Base):
    platform: Platform
    content_id: str
    kind: ContentKind
    web_url: str
    #: First line of the platform's single description field. Never empty when a
    #: description exists.
    title: str
    description: str
    created_at: datetime | None = None
    duration_ms: int | None = None
    is_deleted: bool = False
    is_private: bool = False

    author: Author
    stats: ContentStats
    media: Media
    music: Music | None = None
    #: Hashtags without the leading '#'.
    tags: list[str] = Field(default_factory=list)
    location: str | None = None

    fetched_at: datetime
    raw: dict | None = None


class Comment(_Base):
    platform: Platform
    comment_id: str
    content_id: str
    #: Set on second-level replies, pointing at the top-level comment.
    parent_id: str | None = None
    text: str
    created_at: datetime | None = None
    digg_count: int | None = None
    reply_count: int | None = None
    author: Author
    is_author_liked: bool = False
    is_pinned: bool = False
    raw: dict | None = None


class Collection(_Base):
    """A named folder a user has organized posts into.

    TikTok calls this a collection, Douyin a collects folder; both are private
    bookmark groupings rather than the author's own uploaded playlist (that is
    ``mix_id``/``Content`` paged through ``mix_posts``).
    """

    platform: Platform
    collection_id: str
    name: str
    cover: Image | None = None
    #: Posts filed in the folder. None when the platform did not state a count.
    item_count: int | None = None
    raw: dict | None = None


class Page[T](BaseModel):
    """A page of results with an opaque cursor.

    Douyin pages by a ``max_cursor`` timestamp and TikTok by an offset; both are
    encoded into one string so callers never need to understand either.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    cursor: str | None = None
    has_more: bool = False


__all__ = [
    "Author",
    "AuthorStats",
    "Collection",
    "Comment",
    "Content",
    "ContentStats",
    "Image",
    "Media",
    "Music",
    "Page",
    "VideoStream",
]
