"""The TikTok platform adapter.

Binds the endpoint table to the parsers. Discovered by
:mod:`dtk.platforms.registry` through the module-level ``ADAPTER`` name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dtk.core.types import Platform
from dtk.models import Author, Collection, Comment, Content, Page
from dtk.platforms.base import (
    ClientProfile,
    EndpointTable,
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


ADAPTER: PlatformAdapter = TikTokAdapter()

__all__ = ["ADAPTER", "TikTokAdapter"]
