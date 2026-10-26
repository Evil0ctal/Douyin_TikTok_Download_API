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


class AlbumImage(Image):
    """One slide of an image album.

    A type of its own rather than a field on :class:`Image`, because an avatar
    or a cover never moves: putting ``video`` on every image would print
    ``"video": null`` beside every avatar in every response.
    """

    #: The clip a live photo plays when it is held: a few seconds of MP4 that
    #: the still was taken from. Douyin marks such a slide ``live_photo_type:
    #: 1`` and hangs the clip off the slide itself; an album may mix them with
    #: ordinary stills, which leave this ``None``. Before this field existed
    #: the clips were reachable only through ``include_raw``.
    video: VideoStream | None = None


class Media(_Base):
    #: Usually more than one: an animated cover plus a static fallback.
    covers: list[Image] = Field(default_factory=list)
    #: Present when kind is VIDEO. The watermark-free stream.
    video: VideoStream | None = None
    #: Alternate bitrates for the same video.
    streams: list[VideoStream] = Field(default_factory=list)
    #: Present when kind is IMAGE_ALBUM.
    images: list[AlbumImage] = Field(default_factory=list)


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
    #: The id this project's own author endpoints key on.
    #:
    #: On Douyin it is the same value as :attr:`uid` and the duplication is the
    #: point: a caller should not have to know which platform puts its usable id
    #: in which field. On TikTok they differ, and that difference is why this
    #: field exists - ``author_posts``, ``author_likes``, ``followers`` and
    #: ``following`` all key on ``secUid``, and ``uid`` carries the numeric
    #: ``id``, which none of them accept. Without this the API returned nothing
    #: that could be fed back into itself: parsing a TikTok video gave you a
    #: numeric id and an @handle, and every author endpoint refuses both.
    #:
    #: Optional because a parser may meet an author node that omits it; a
    #: response carrying `None` here means "ask the profile endpoint", not
    #: "this author has no id".
    sec_uid: str | None = None
    unique_id: str | None = None
    nickname: str
    signature: str | None = None
    avatar: Image | None = None
    web_url: str | None = None
    verified: bool = False
    #: Usually absent in list endpoints, which return an abbreviated author.
    stats: AuthorStats | None = None
    raw: dict | None = None


class ContentPart(_Base):
    """One part of a video uploaded in several, as Bilibili allows.

    A post is still one post: its id, title and statistics are the whole
    upload's. A part is what a player plays and a download stores, and it is
    addressed by its 1-based position, which is what the site's own ``?p=``
    links count. ``cid`` is Bilibili's id for it and is what ``playurl`` takes.
    """

    part: int
    cid: str
    title: str
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None


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
    #: The parts of a video uploaded in several, in order. Bilibili only, where
    #: a single-part video lists its one part; empty on the other platforms,
    #: which have no such thing.
    parts: list[ContentPart] = Field(default_factory=list)

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

    TikTok calls this a collection, Douyin a collects folder; either way it is
    a grouping of OTHER people's posts, which is what separates it from the
    author's own uploaded playlist (that is ``mix_id``/``Content`` paged
    through ``mix_posts``).

    Not necessarily private. TikTok lets each folder be public or private
    individually, and a public one is readable by anyone - see ``is_public``.
    Douyin's web client only ever shows you your own.
    """

    platform: Platform
    collection_id: str
    name: str
    cover: Image | None = None
    #: Posts filed in the folder. None when the platform did not state a count.
    item_count: int | None = None
    #: Whether anyone can open this folder, or None when the platform did not
    #: say. See the TikTok parser for what "did not say" covers.
    is_public: bool | None = None
    #: The account the folder belongs to. TikTok states both on every folder
    #: node; Douyin states neither, because you can only ever ask about your
    #: own.
    owner_id: str | None = None
    owner_name: str | None = None
    raw: dict | None = None


class Mix(_Base):
    """An author's own series: posts they put in order and published as one.

    Douyin calls it a mix, TikTok a playlist and Bilibili a season - the same
    Chinese word on both Chinese sites. Not a :class:`Collection`, which is a viewer filing
    away other people's posts; the difference is whose posts they are, and it
    decides which endpoint the id goes to - ``mix_id`` to ``/mix/posts``.
    """

    platform: Platform
    mix_id: str
    name: str
    description: str | None = None
    cover: Image | None = None
    #: Posts in the series, as the platform counts them.
    item_count: int | None = None
    #: Plays across the series, where the platform counts them.
    play_count: int | None = None
    web_url: str | None = None
    #: The author who published it, by the id the author endpoints take.
    owner_id: str | None = None
    owner_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict | None = None


class TrendingTopic(_Base):
    """One line of a trending board: a phrase people are searching for.

    Not a post. The boards list words, and the posts behind a word are a
    search away; ``web_url`` is the platform's own page for it.
    """

    platform: Platform
    word: str
    #: Position on the board, from 1. ``None`` for a line pinned above the
    #: ranking, which Douyin's board opens with.
    rank: int | None = None
    #: The heat the platform prints beside the word, in its own units.
    hot_value: int | None = None
    #: Posts the board files under the word, where it counts them.
    content_count: int | None = None
    cover: Image | None = None
    web_url: str | None = None
    raw: dict | None = None


class LiveStream(_Base):
    """One way to play a live room: a protocol at a quality.

    Live pull URLs are signed and expire within hours, and a player or a
    recorder is what reads them; nothing here proxies or records a stream.
    """

    url: str
    #: ``flv`` or ``hls``, as the platform delivers it.
    format: str
    #: The platform's own name for the quality - Douyin's ``FULL_HD1``,
    #: TikTok's ``origin``, Bilibili's ``qn`` number - since the ladders do not
    #: line up between them.
    quality: str | None = None


class LiveRoom(_Base):
    """A live room, broadcasting or not."""

    platform: Platform
    #: What ``/live`` takes to ask about this room again: Douyin's ``web_rid``
    #: (the number in a live.douyin.com link), the @handle on TikTok, and
    #: Bilibili's room number.
    room_id: str
    #: The platform's own id for the broadcast on air, while there is one:
    #: Douyin's and TikTok's ``room_id``, a new one each time the room goes
    #: live. It is what an app's share link carries, what ``/live?live_id=``
    #: and the other live reads take, and why it is not ``room_id`` above -
    #: that one outlives the broadcast. Bilibili numbers a room once, so has
    #: none.
    live_id: str | None = None
    is_live: bool
    title: str | None = None
    cover: Image | None = None
    web_url: str | None = None
    #: The count the platform prints beside the room. Douyin and TikTok count
    #: who is in the room now; Bilibili counts who has watched this broadcast.
    viewer_count: int | None = None
    like_count: int | None = None
    started_at: datetime | None = None
    author: Author | None = None
    #: Empty when the room is not live.
    streams: list[LiveStream] = Field(default_factory=list)
    raw: dict | None = None


class LiveStatus(_Base):
    """Whether one broadcast is still on air, and nothing else about it.

    The answer to asking about many broadcasts at once: a poller watching a
    list of rooms asks this, and asks ``/live`` for the one that just came on.
    """

    platform: Platform
    #: The broadcast asked about, as ``LiveRoom.live_id`` names it.
    live_id: str
    is_live: bool


class LiveViewer(_Base):
    """One place on a live room's audience ranking: the list the room's own
    page shows beside the stream, top first."""

    platform: Platform
    #: 1 for the top of the list.
    rank: int
    #: None for a viewer the platform does not name: one who chose to hide
    #: from the list, whom the answer still describes and this does not pass
    #: on, and one it gives a guest only as a masked nickname with no id.
    author: Author | None = None


class MusicDetail(Music):
    """A sound on its own page: what a post's ``music`` names, and more.

    A type of its own rather than more fields on :class:`Music`, for the
    reason :class:`AlbumImage` is one: every post carries a ``music`` block,
    and printing ``"content_count": null`` in each of them would describe a
    count nobody asked for. Only the sound's own endpoint knows how many posts
    use it and who published it.
    """

    platform: Platform
    #: Required here, unlike on a post's block: the detail is asked for by it.
    music_id: str
    web_url: str | None = None
    #: Posts that use the sound, as the platform counts them.
    content_count: int | None = None
    #: Whether this is the audio of the post it was first published with,
    #: rather than a track from the platform's library.
    is_original: bool | None = None
    #: The account that published it, when the platform names one. A library
    #: track usually has none.
    owner: Author | None = None
    raw: dict | None = None


class Hashtag(_Base):
    """A hashtag: the topic page a post's ``#tag`` opens.

    ``Content.tags`` carries only the names; this is the page behind one of
    them, with the id the posts under it are listed by.
    """

    platform: Platform
    hashtag_id: str
    #: Without the leading '#', as ``Content.tags`` spells it.
    name: str
    description: str | None = None
    cover: Image | None = None
    web_url: str | None = None
    #: Posts filed under it, as the platform counts them. Douyin calls the
    #: number ``user_count``; TikTok's is ``videoCount``.
    content_count: int | None = None
    view_count: int | None = None
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
    "AlbumImage",
    "Author",
    "AuthorStats",
    "Collection",
    "Comment",
    "Content",
    "ContentPart",
    "ContentStats",
    "Hashtag",
    "Image",
    "LiveRoom",
    "LiveStatus",
    "LiveStream",
    "LiveViewer",
    "Media",
    "Mix",
    "Music",
    "MusicDetail",
    "Page",
    "TrendingTopic",
    "VideoStream",
]
