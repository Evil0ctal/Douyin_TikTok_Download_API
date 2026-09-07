"""The Douyin platform adapter.

Binds the endpoint table to the parsers and exposes them as the single object
the rest of the system imports. Discovered by :mod:`dtk.platforms.registry`
through the module-level ``ADAPTER`` name, so no registry edit is needed when a
platform is added.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dtk.core.types import Platform
from dtk.models import Author, Comment, Content, Page
from dtk.platforms.base import EndpointTable, PlatformAdapter, RequestSpec
from dtk.platforms.douyin import endpoints, parser


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


ADAPTER: PlatformAdapter = DouyinAdapter()

__all__ = ["ADAPTER", "DouyinAdapter"]
