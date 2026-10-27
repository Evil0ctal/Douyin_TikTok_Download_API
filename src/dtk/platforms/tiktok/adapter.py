"""The TikTok platform adapter.

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
from dtk.platforms.tiktok import endpoints, parser
from dtk.platforms.tiktok import params as params_module


@dataclass(frozen=True, slots=True)
class TikTokAdapter:
    """Implements :class:`~dtk.platforms.base.PlatformAdapter` for TikTok."""

    platform: Platform = Platform.TIKTOK
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
        """Every answer here is complete as it comes."""
        return None

    def parse_content(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Content:
        return parser.parse_content(payload, fetched_at=fetched_at)

    def parse_author(self, payload: Mapping[str, Any]) -> Author:
        return parser.parse_author(payload)

    def parse_author_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_author_posts(payload, fetched_at=fetched_at)

    def parse_author_list(self, payload: Mapping[str, Any]) -> Page[Author]:
        return parser.parse_author_list(payload)

    def parse_author_collections(self, payload: Mapping[str, Any]) -> Page[Collection]:
        return parser.parse_author_collections(payload)

    def parse_collection_detail(self, payload: Mapping[str, Any]) -> Collection:
        return parser.parse_collection_detail(payload)

    def parse_collection_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime, cursor: str | None = None
    ) -> Page[Content]:
        """TikTok serves this in the same itemList envelope as every other list."""
        return parser.parse_author_posts(payload, fetched_at=fetched_at)

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

    def parse_reply_previews(
        self, payload: Mapping[str, Any], *, content_id: str | None = None
    ) -> list[Comment]:
        return parser.parse_reply_previews(payload, content_id=content_id)

    def parse_content_parts(self, payload: Mapping[str, Any]) -> Page[ContentPart]:
        """TikTok has no videos in parts, so no part list is registered."""
        raise UnsupportedContent(
            "TikTok videos are not uploaded in parts",
            details={"platform": self.platform.value, "capability": "content_parts"},
        )

    def parse_popular(
        self,
        payload: Mapping[str, Any],
        *,
        board: str,
        fetched_at: datetime,
        cursor: str | None = None,
    ) -> Page[Content]:
        """No site-curated list is registered for TikTok; Bilibili's are."""
        raise UnsupportedContent(
            "TikTok has no popular list here",
            details={"platform": self.platform.value, "capability": board},
        )

    def parse_related_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_related_posts(payload, fetched_at=fetched_at)

    def parse_music_detail(self, payload: Mapping[str, Any]) -> MusicDetail:
        return parser.parse_music_detail(payload)

    def parse_music_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_music_posts(payload, fetched_at=fetched_at)

    def parse_hashtag_detail(self, payload: Mapping[str, Any]) -> Hashtag:
        return parser.parse_hashtag_detail(payload)

    def parse_hashtag_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_hashtag_posts(payload, fetched_at=fetched_at)

    def parse_search_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        return parser.parse_search_posts(payload, fetched_at=fetched_at)

    def parse_search_authors(self, payload: Mapping[str, Any]) -> Page[Author]:
        return parser.parse_search_authors(payload)

    def parse_author_mixes(self, payload: Mapping[str, Any]) -> Page[Mix]:
        return parser.parse_author_mixes(payload)

    def parse_mix_posts(self, payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
        return parser.parse_author_posts(payload, fetched_at=fetched_at)

    def parse_live_room(
        self, payload: Mapping[str, Any], *, room_id: str | None = None
    ) -> LiveRoom:
        return parser.parse_live_room(payload, room_id=room_id)

    def parse_live_room_by_id(self, payload: Mapping[str, Any]) -> LiveRoom:
        return parser.parse_live_room_by_id(payload)

    def parse_live_status(
        self, payload: Mapping[str, Any], *, live_ids: Sequence[str] = ()
    ) -> Page[LiveStatus]:
        return parser.parse_live_status(payload, live_ids=live_ids)

    def parse_live_ranking(self, payload: Mapping[str, Any]) -> Page[LiveViewer]:
        """No call registered: no audience ranking measured here."""
        raise UnsupportedContent(
            "TikTok has no live room ranking here",
            details={"platform": self.platform.value, "capability": "live_ranking"},
        )

    def parse_trending(self, payload: Mapping[str, Any]) -> Page[TrendingTopic]:
        """No board registered: nothing measured answers a guest with one."""
        raise UnsupportedContent(
            "TikTok has no trending board here",
            details={"platform": self.platform.value, "capability": "trending"},
        )


ADAPTER: PlatformAdapter = TikTokAdapter()

__all__ = ["ADAPTER", "TikTokAdapter"]
