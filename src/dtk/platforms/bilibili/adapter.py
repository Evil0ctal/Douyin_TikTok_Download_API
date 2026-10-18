"""The Bilibili platform adapter.

Binds the endpoint table to the parsers. Discovered by
:mod:`dtk.platforms.registry` through the module-level ``ADAPTER`` name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dtk.core.errors import UnsupportedContent
from dtk.core.types import Platform
from dtk.models import Author, Collection, Comment, Content, Page
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

    def follow_up(self, endpoint: str, payload: Mapping[str, Any]) -> FollowUp | None:
        """A video's detail is followed by its ``playurl``, for the file itself.

        One more signed request per detail, made by the fetch layer under the
        same scheduler. Without it a Bilibili post would archive with no stream,
        and a download of it would have nothing to fetch.
        """
        if endpoint != endpoints.CONTENT_DETAIL:
            return None
        data = payload.get("data")
        bvid = data.get("bvid") if isinstance(data, Mapping) else None
        cid = parser.first_part_cid(payload)
        if not isinstance(bvid, str) or cid is None:
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


ADAPTER: PlatformAdapter = BilibiliAdapter()

__all__ = ["ADAPTER", "BilibiliAdapter"]
