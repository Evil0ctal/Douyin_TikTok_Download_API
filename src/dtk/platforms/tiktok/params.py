"""Query parameter construction for the TikTok web P0 endpoints.

Distilled from V4's ``crawlers/tiktok/web/models.py`` and
``app/api/endpoints/tiktok_web.py`` (``vendor_salvage/tiktok_web_models.py`` and
``vendor_salvage/REF_tiktok_web.py`` here) - the only record of which parameters
each endpoint actually needs.

Differences from V4, for the same reasons as the Douyin module:

* ``msToken`` and ``_signature`` are not built here. V4 pasted a captured
  ``msToken`` and ``_signature`` straight into the model defaults, which meant
  every deployment shipped one user's expired token. The signing layer owns
  them now.
* Fingerprint values arrive as a :class:`~dtk.platforms.base.ClientProfile`.
* Values are returned **un-encoded**. V4 stored ``browser_version`` as the
  already-quoted ``"5.0%20%28Windows%29"``, which double-encoded whenever the
  HTTP client also encoded; the transport encodes exactly once.
"""

from __future__ import annotations

import random
from typing import Final

from dtk.core.errors import InvalidParam
from dtk.platforms.base import ClientProfile

#: TikTok's web client presents as a US-English desktop browser. TikTok reports
#: ``browser_name``/``browser_version`` as the navigator product strings rather
#: than the marketing name, which is why they look nothing like Douyin's.
DEFAULT_PROFILE: Final = ClientProfile(
    browser_name="Mozilla",
    browser_version="5.0 (Windows)",
    browser_platform="Win32",
    browser_language="en-US",
    engine_name="Blink",
    engine_version="130.0.0.0",
    os_name="Windows",
    os_version="10",
    screen_width=1920,
    screen_height=1080,
    cpu_core_num=12,
    device_memory=8,
    language="en",
    timezone="America/Los_Angeles",
    region="US",
)

APP_ID: Final = "1988"
APP_NAME: Final = "tiktok_web"
CHANNEL: Final = "tiktok_web"
WEB_ORIGIN: Final = "https://www.tiktok.com/"

#: Default page size. TikTok caps ``count`` at 35 on the post list.
DEFAULT_PAGE_SIZE: Final = 30

#: TikTok's cover format selector: 2 asks for WebP covers.
COVER_FORMAT: Final = "2"

#: `device_id` is not optional, and its absence does not look like an error.
#:
#: Measured on 2026-09-08. Without it TikTok answers 200 with an EMPTY BODY and
#: the header `tt_orcas_res: 1` - no status code, no message, nothing that reads
#: as "you forgot a parameter". Every TikTok endpoint this project calls was
#: silently returning nothing for exactly this reason, and it survived a long
#: hunt through signatures, cookies, TLS profiles and identity pools because an
#: empty 200 looks like risk control.
#:
#: One parameter lifts it. Adding only `device_id` to an otherwise unchanged
#: request took /api/item/detail/ from 0 bytes to a full itemStruct, while
#: adding only `odinId` or only `WebIdLastTime` left it gated. The value does
#: not have to be a real one: a random 19-digit number works exactly as well as
#: the one the browser had been issued.
#:
#: It is generated per call rather than per identity, which is a compromise
#: worth naming: a real device id is stable for the life of a browser, so an
#: identity whose device changes every request is not perfectly plausible. The
#: alternative available at this layer - one value shared by every identity -
#: would be worse, because it would link them to each other. Making it properly
#: per-identity needs the identity to reach the parameter builder, which it does
#: not today.
DEVICE_ID_DIGITS: Final = 19


def _device_id(rng: random.Random | None = None) -> str:
    """A well-formed TikTok web device id. See DEVICE_ID_DIGITS for why it exists."""
    source = rng or random
    first = source.randint(1, 9)
    rest = "".join(str(source.randint(0, 9)) for _ in range(DEVICE_ID_DIGITS - 1))
    return f"{first}{rest}"


def base_params(profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    """The parameters every TikTok web API call carries."""
    return {
        "aid": APP_ID,
        "app_language": profile.language,
        "app_name": APP_NAME,
        "browser_language": profile.browser_language,
        "browser_name": profile.browser_name,
        "browser_online": "true",
        "browser_platform": profile.browser_platform,
        "browser_version": profile.browser_version,
        "channel": CHANNEL,
        "cookie_enabled": "true",
        "device_id": _device_id(),
        "device_platform": "web_pc",
        "focus_state": "true",
        "from_page": "user",
        "history_len": "4",
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "language": profile.language,
        "os": profile.os_name.lower(),
        "priority_region": profile.region,
        "referer": "",
        "region": profile.region,
        "root_referer": WEB_ORIGIN,
        "screen_height": str(profile.screen_height),
        "screen_width": str(profile.screen_width),
        "tz_name": profile.timezone,
        "webcast_language": profile.language,
    }


def content_detail_params(
    *, item_id: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """Parameters for ``/api/item/detail/``."""
    return {**base_params(profile), "itemId": str(item_id)}


def author_profile_params(
    *,
    sec_uid: str | None = None,
    unique_id: str | None = None,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/user/detail/``.

    TikTok accepts either identifier and needs both keys present even when one
    is blank, so 'at least one of' cannot be expressed as a required-parameter
    list and is checked here instead.
    """
    if not (sec_uid or "").strip() and not (unique_id or "").strip():
        raise InvalidParam(
            "author profile needs sec_uid or unique_id",
            details={"endpoint": "tiktok.author_profile", "missing": ["sec_uid", "unique_id"]},
        )
    return {
        **base_params(profile),
        "secUid": str(sec_uid or ""),
        "uniqueId": str(unique_id or ""),
    }


def author_posts_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/post/item_list/``.

    The opaque cursor decodes to TikTok's ``cursor``, a millisecond timestamp
    offset; ``"0"`` asks for the first page.
    """
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
        "needPinnedItemIds": "true",
        "locate_item_id": "",
        # 0 default order, 1 most popular, 2 oldest first.
        "post_item_list_request_type": "0",
    }


def author_likes_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/favorite/item_list/``.

    TikTok hides an account's likes by default, so an empty page is the normal
    answer unless the author has turned the list on.
    """
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
    }


def mix_posts_params(
    *,
    mix_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/mix/item_list/``.

    A mix is TikTok's playlist. ``mix_id`` comes from the ``playlistId`` a post
    carries when it belongs to one.
    """
    return {
        **base_params(profile),
        "mixId": str(mix_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
    }


#: ``scene`` selects which side of the follow graph ``/api/user/list/`` returns.
#: The endpoint path is identical for both, so this value is the whole request.
FOLLOWERS_SCENE: Final = "67"
FOLLOWING_SCENE: Final = "21"


def _user_list_params(
    *,
    sec_uid: str,
    scene: str,
    cursor: str | None,
    count: int,
    profile: ClientProfile,
) -> dict[str, str]:
    """Shared body of the two ``/api/user/list/`` calls.

    The cursor is TikTok's ``minCursor``, a second-resolution timestamp of the
    oldest entry on the page just returned. ``maxCursor`` stays 0: the pair
    bounds a window and only the lower bound moves while paging backwards.
    """
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "count": str(count),
        "minCursor": _cursor(cursor),
        "maxCursor": "0",
        "scene": scene,
    }


def author_followers_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/user/list/`` asking for an author's followers."""
    return _user_list_params(
        sec_uid=sec_uid, scene=FOLLOWERS_SCENE, cursor=cursor, count=count, profile=profile
    )


def author_following_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/user/list/`` asking who an author follows.

    Accounts hide this list far more often than they hide their followers, so
    an empty page is a common and correct answer.
    """
    return _user_list_params(
        sec_uid=sec_uid, scene=FOLLOWING_SCENE, cursor=cursor, count=count, profile=profile
    )


def comments_params(
    *,
    aweme_id: str,
    cursor: str | None = None,
    count: int = 20,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/comment/list/``.

    The comment endpoints are the one place TikTok's web API keeps the Douyin
    snake_case naming, including ``aweme_id`` for what everything else calls
    ``itemId``.
    """
    return {
        **base_params(profile),
        "aweme_id": str(aweme_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "current_region": profile.region,
    }


def comment_replies_params(
    *,
    item_id: str,
    comment_id: str,
    cursor: str | None = None,
    count: int = 20,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/comment/list/reply/``."""
    return {
        **base_params(profile),
        "item_id": str(item_id),
        "comment_id": str(comment_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "current_region": profile.region,
    }


def _cursor(cursor: str | None) -> str:
    """Decode the opaque page cursor into TikTok's numeric cursor."""
    if cursor is None:
        return "0"
    text = cursor.strip()
    return text or "0"


__all__ = [
    "APP_ID",
    "COVER_FORMAT",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_PROFILE",
    "author_posts_params",
    "author_profile_params",
    "base_params",
    "comment_replies_params",
    "comments_params",
    "content_detail_params",
]
