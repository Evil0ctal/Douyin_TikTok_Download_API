"""The Douyin platform adapter.

Binds the endpoint table to the parsers and exposes them as the single object
the rest of the system imports. Discovered by :mod:`dtk.platforms.registry`
through the module-level ``ADAPTER`` name, so no registry edit is needed when a
platform is added.
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
from dtk.platforms.douyin import endpoints, parser
from dtk.platforms.douyin import params as params_module


@dataclass(frozen=True, slots=True)
class DouyinAdapter:
    """Implements :class:`~dtk.platforms.base.PlatformAdapter` for Douyin."""

    platform: Platform = Platform.DOUYIN
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
        """Douyin does not serve a follow graph to a guest identity.

        Measured 2026-09-08 against ``/aweme/v1/web/user/follower/list/`` and
        ``/aweme/v1/web/user/following/list/`` with a healthy guest identity:
        both answer ``status_code 8`` with a status message meaning "not signed
        in". Passing
        the numeric ``user_id`` alongside ``sec_user_id`` does not change it.

        So no Douyin follower endpoint is registered, and this exists to say
        that in the place someone will look rather than to leave the platform
        silently missing a method the protocol requires. TikTok serves both
        sides of the graph to a guest and does register them.
        """
        raise UnsupportedContent(
            "Douyin serves follower and following lists only to a signed-in "
            "identity; import one from the console to read them",
            details={"platform": self.platform.value, "capability": "author_list"},
        )

    def parse_author_collections(self, payload: Mapping[str, Any]) -> Page[Collection]:
        return parser.parse_author_collections(payload)

    def parse_collection_detail(self, payload: Mapping[str, Any]) -> Collection:
        """Douyin has no folder-by-id metadata path.

        ``collects/list/`` describes the folders and ``collects/video/list/``
        returns their contents, but nothing answers "what is this folder"
        from an id alone the way TikTok's ``/api/collection/detail/`` does.
        Raising here rather than leaving the capability silently missing is
        the same choice ``parse_author_list`` makes.
        """
        raise UnsupportedContent(
            "Douyin has no collection-detail endpoint",
            details={"platform": self.platform.value, "capability": "collection_detail"},
        )

    def parse_collection_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime, cursor: str | None = None
    ) -> Page[Content]:
        return parser.parse_collection_posts(payload, fetched_at=fetched_at)

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
        """Douyin has no videos in parts, so no part list is registered."""
        raise UnsupportedContent(
            "Douyin videos are not uploaded in parts",
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
        """No site-curated list is registered for Douyin; Bilibili's are."""
        raise UnsupportedContent(
            "Douyin has no popular list here",
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
        """Not registered: Douyin answers a guest with an empty body here.

        Measured 2026-10-20 with a minted guest and again in a browser that
        was not signed in, whose own hashtag page got the same zero bytes. A
        signed-in answer has not been seen, and a parser written without one
        would be a guess at its shape - so the capability is missing rather
        than wrong.
        """
        raise UnsupportedContent(
            "Douyin lists the posts under a hashtag only to a signed-in session, "
            "and that answer has not been measured here yet",
            details={"platform": self.platform.value, "capability": "hashtag_posts"},
        )

    def parse_search_posts(
        self, payload: Mapping[str, Any], *, fetched_at: datetime
    ) -> Page[Content]:
        """Not registered: Douyin refuses search to a guest by name.

        Measured 2026-10-20 on the general and the video search: `status_code
        2483` and a message asking to sign in before searching, to a healthy
        guest. As with the posts under a hashtag, no signed-in answer has been
        seen to write a parser against.
        """
        raise UnsupportedContent(
            "Douyin answers search only to a signed-in session, and that answer "
            "has not been measured here yet",
            details={"platform": self.platform.value, "capability": "search_posts"},
        )

    def parse_search_authors(self, payload: Mapping[str, Any]) -> Page[Author]:
        raise UnsupportedContent(
            "Douyin answers search only to a signed-in session, and that answer "
            "has not been measured here yet",
            details={"platform": self.platform.value, "capability": "search_authors"},
        )

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
        return parser.parse_live_ranking(payload)

    def parse_trending(self, payload: Mapping[str, Any]) -> Page[TrendingTopic]:
        return parser.parse_trending(payload)


ADAPTER: PlatformAdapter = DouyinAdapter()

__all__ = ["ADAPTER", "DouyinAdapter"]
