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

from dtk.core.errors import UnsupportedContent
from dtk.core.types import Platform
from dtk.models import Author, Collection, Comment, Content, Page
from dtk.platforms.base import (
    ClientProfile,
    EndpointTable,
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
        """Douyin's bookmark-folder equivalent (``collects/list/``) is not wired.

        The URL constant exists in ``endpoints.py`` - rediscovering it later
        would be expensive - but no ``EndpointSpec`` registers it yet, so no
        Douyin ``author_collections`` endpoint is offered. Exists here for the
        same reason ``parse_author_list`` does: to fail loudly for a capability
        the protocol declares rather than leave it silently missing.
        """
        raise UnsupportedContent(
            "Douyin bookmark folders are not wired into this build yet",
            details={"platform": self.platform.value, "capability": "author_collections"},
        )

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
