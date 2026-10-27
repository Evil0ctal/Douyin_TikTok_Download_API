"""The Bilibili platform adapter.

Binds the endpoint table to the parsers. Discovered by
:mod:`dtk.platforms.registry` through the module-level ``ADAPTER`` name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dtk.core.errors import UnsupportedContent
from dtk.core.types import Platform
from dtk.models import (
    Author,
    Collection,
    Comment,
    Content,
    ContentPart,
    Hashtag,
    LiveRoom,
    LiveStatus,
    LiveViewer,
    Mix,
    MusicDetail,
    Page,
    TrendingTopic,
)
from dtk.platforms.base import (
    ClientProfile,
    EndpointTable,
    FollowUp,
    PlatformAdapter,
    ProfileSource,
    RequestSpec,
)
from dtk.platforms.bilibili import endpoints, parser
from dtk.platforms.bilibili import params as params_module


@dataclass(frozen=True, slots=True)
class BilibiliAdapter:
    """Implements :class:`~dtk.platforms.base.PlatformAdapter` for Bilibili."""

    platform: Platform = Platform.BILIBILI
    endpoints: EndpointTable = endpoints.ENDPOINTS
    default_headers: Mapping[str, str] = field(
        default_factory=lambda: dict(endpoints.DEFAULT_HEADERS)
    )

    def build_request(self, endpoint: str, /, **params: Any) -> RequestSpec:
        return self.endpoints[endpoint].to_request(default_headers=self.default_headers, **params)

    def profile_for(self, source: ProfileSource) -> ClientProfile:
        return params_module.profile_for(source)

    def detect_risk_control(self, payload: Mapping[str, Any]) -> str | None:
        return parser.detect_risk_control(payload)

    def follow_up(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> FollowUp | None:
        """The second request two answers here need.

        A video's detail is followed by its ``playurl``, for the file itself -
        the first part's, or the part ``params`` names. One more signed request
        per detail, made by the fetch layer under the same scheduler. Without
        it a Bilibili post would archive with no stream, and a download of it
        would have nothing to fetch.

        The weekly selection's index is followed by the issue: the one the
        cursor names, or the newest. The index alone lists no videos, so that
        follow-up is required.

        A live room that is broadcasting is followed by ``getRoomPlayInfo``,
        for the pull URLs.
        """
        asked = params or {}
        if endpoint == endpoints.LIVE_ROOM:
            # A room that is live is followed by its pull URLs. Not required:
            # the room, its title and its broadcaster are real without them.
            if not parser.live_room_is_on(payload):
                return None
            return FollowUp(
                endpoint=endpoints.LIVE_STREAMS,
                params={"room_id": str(asked.get("room_id") or "")},
                merge=parser.merge_live_play,
            )
        if endpoint == endpoints.POPULAR_WEEKLY:
            cursor = str(asked.get("cursor") or "").strip()
            number = int(cursor) if cursor.isdigit() and int(cursor) > 0 else None
            number = number or parser.latest_issue(payload)
            if number is None:
                return None
            return FollowUp(
                endpoint=endpoints.POPULAR_WEEKLY_ISSUE,
                params={"number": str(number)},
                merge=parser.merge_issue,
                required=True,
            )
        if endpoint != endpoints.CONTENT_DETAIL:
            return None
        data = payload.get("data")
        bvid = data.get("bvid") if isinstance(data, Mapping) else None
        if not isinstance(bvid, str):
            return None
        part = asked.get("part")
        if part is not None and params_module.part_number(part) > 1:
            number = params_module.part_number(part)
            return FollowUp(
                endpoint=endpoints.CONTENT_STREAMS,
                params={"bvid": bvid, "cid": parser.part_cid(payload, number)},
                merge=parser.merge_part_streams(number),
                # The detail without this part's file would be parsed as the
                # post with no stream, and a download of the part would fail
                # with "no downloadable media" instead of saying why.
                required=True,
            )
        cid = parser.first_part_cid(payload)
        if cid is None:
            return None
        return FollowUp(
            endpoint=endpoints.CONTENT_STREAMS,
            params={"bvid": bvid, "cid": cid},
            merge=parser.merge_streams,
        )

    def parse_content(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Content:
        return parser.parse_content(payload, fetched_at=fetched_at)

    def parse_author(self, payload: Mapping[str, Any]) -> Author:
        return parser.parse_author(payload)

    def parse_author_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_author_posts(payload, fetched_at=fetched_at)

    def parse_author_list(self, payload: Mapping[str, Any]) -> Page[Author]:
        raise UnsupportedContent(
            "Bilibili's follower and following lists are not wired in this build",
            details={"platform": self.platform.value, "capability": "author_list"},
        )

    def parse_author_collections(self, payload: Mapping[str, Any]) -> Page[Collection]:
        return parser.parse_author_collections(payload)

    def parse_collection_detail(self, payload: Mapping[str, Any]) -> Collection:
        raise UnsupportedContent(
            "Bilibili's folder list already carries what a folder detail would",
            details={"platform": self.platform.value, "capability": "collection_detail"},
        )

    def parse_collection_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime, cursor: str | None = None
    ) -> Page[Content]:
        return parser.parse_collection_posts(payload, fetched_at=fetched_at, cursor=cursor)

    def parse_comments(
        self, payload: Mapping[str, Any], *, content_id: str | None = None
    ) -> Page[Comment]:
        return parser.parse_comments(payload, content_id=content_id)

    def parse_comment_replies(
        self,
        payload: Mapping[str, Any],
        *,
        content_id: str | None = None,
        parent_id: str | None = None,
    ) -> Page[Comment]:
        return parser.parse_comment_replies(payload, content_id=content_id, parent_id=parent_id)

    def parse_content_parts(self, payload: Mapping[str, Any]) -> Page[ContentPart]:
        return parser.parse_content_parts(payload)

    def parse_popular(
        self,
        payload: Mapping[str, Any],
        *,
        board: str,
        fetched_at: datetime,
        cursor: str | None = None,
    ) -> Page[Content]:
        return parser.parse_popular(payload, board=board, fetched_at=fetched_at, cursor=cursor)

    def parse_related_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_related_posts(payload, fetched_at=fetched_at)

    def parse_music_detail(self, payload: Mapping[str, Any]) -> MusicDetail:
        """Bilibili has no sounds a post is made with; no music endpoint is
        registered."""
        raise UnsupportedContent(
            "Bilibili videos do not carry a reusable sound",
            details={"platform": self.platform.value, "capability": "music_detail"},
        )

    def parse_music_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        raise UnsupportedContent(
            "Bilibili videos do not carry a reusable sound",
            details={"platform": self.platform.value, "capability": "music_posts"},
        )

    def parse_hashtag_detail(self, payload: Mapping[str, Any]) -> Hashtag:
        """Bilibili's tags are a video's labels, not pages with posts filed
        under them the way a hashtag is; nothing is registered."""
        raise UnsupportedContent(
            "Bilibili has no hashtag pages",
            details={"platform": self.platform.value, "capability": "hashtag_detail"},
        )

    def parse_hashtag_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        raise UnsupportedContent(
            "Bilibili has no hashtag pages",
            details={"platform": self.platform.value, "capability": "hashtag_posts"},
        )

    def parse_search_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_search_posts(payload, fetched_at=fetched_at)

    def parse_search_authors(self, payload: Mapping[str, Any]) -> Page[Author]:
        return parser.parse_search_authors(payload)

    def parse_author_mixes(self, payload: Mapping[str, Any]) -> Page[Mix]:
        return parser.parse_author_mixes(payload)

    def parse_mix_posts(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
        return parser.parse_mix_posts(payload, fetched_at=fetched_at)

    def parse_live_room(
        self, payload: Mapping[str, Any], *, room_id: str | None = None
    ) -> LiveRoom:
        return parser.parse_live_room(payload, room_id=room_id)

    def parse_live_room_by_id(self, payload: Mapping[str, Any]) -> LiveRoom:
        """A Bilibili room has one number, and ``parse_live_room`` reads it."""
        raise UnsupportedContent(
            "a Bilibili live room has no id apart from its room number",
            details={"platform": self.platform.value, "capability": "live_room_by_id"},
        )

    def parse_live_status(
        self, payload: Mapping[str, Any], *, live_ids: Sequence[str] = ()
    ) -> Page[LiveStatus]:
        """No call registered: nothing measured asks about many rooms at once."""
        raise UnsupportedContent(
            "Bilibili has no way here to ask about many live rooms at once",
            details={"platform": self.platform.value, "capability": "live_status"},
        )

    def parse_live_ranking(self, payload: Mapping[str, Any]) -> Page[LiveViewer]:
        """No call registered: no audience ranking measured here."""
        raise UnsupportedContent(
            "Bilibili has no live room ranking here",
            details={"platform": self.platform.value, "capability": "live_ranking"},
        )

    def parse_trending(self, payload: Mapping[str, Any]) -> Page[TrendingTopic]:
        return parser.parse_trending(payload)


ADAPTER: PlatformAdapter = BilibiliAdapter()

__all__ = ["ADAPTER", "BilibiliAdapter"]
