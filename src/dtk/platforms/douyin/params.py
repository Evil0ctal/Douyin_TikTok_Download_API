"""Query parameter construction for the Douyin web P0 endpoints.

The parameter names and default values are the distilled form of V4's
``crawlers/douyin/web/models.py`` and ``app/api/endpoints/douyin_web.py``
on branch ``main`` (commit ``8c98fb7``). That knowledge is not documented anywhere by the platform, which is why
the salvage doc lists those two files as read-only references worth mining.

Two deliberate differences from V4:

* **Nothing is read from a config file.** V4's ``BaseRequestModel`` called
  ``TokenManager.gen_real_msToken()`` at class-definition time, which made
  importing the model perform a network request against a cookie taken from
  ``config.yaml``. Here ``msToken``, ``a_bogus`` and ``X-Bogus`` are appended by
  the signing layer, which owns the identity they belong to.
* **Fingerprint values are injected.** ``screen_width``, ``browser_version`` and
  friends are echoed back to the platform and must agree with the TLS emulation
  and User-Agent of the identity making the call, so they arrive as a
  :class:`~dtk.platforms.base.ClientProfile`.

Values are returned un-encoded. The transport layer percent-encodes exactly
once, because the signature is computed over the encoded query string.
"""

from __future__ import annotations

from typing import Final

from dtk.platforms.base import ClientProfile

#: Douyin's web client identifies itself as a Chinese-locale desktop browser.
#: A profile whose language does not match the rest of the fingerprint is itself
#: a signal, so this default is a coherent whole rather than a set of knobs.
DEFAULT_PROFILE: Final = ClientProfile(
    browser_name="Chrome",
    browser_version="130.0.0.0",
    browser_platform="Win32",
    browser_language="zh-CN",
    engine_name="Blink",
    engine_version="130.0.0.0",
    os_name="Windows",
    os_version="10",
    screen_width=1920,
    screen_height=1080,
    cpu_core_num=12,
    device_memory=8,
    language="zh-CN",
    timezone="Asia/Shanghai",
    region="CN",
)

#: Web client build identifiers. Douyin rejects requests whose ``version_code``
#: is far behind the live web build, so these need refreshing when the platform
#: ships a major web release; that is a browser task, not a code change.
AID: Final = "6383"
CHANNEL: Final = "channel_pc_web"
VERSION_CODE: Final = "290100"
VERSION_NAME: Final = "29.1.0"
UPDATE_VERSION_CODE: Final = "170400"

#: Default page size. Douyin silently caps larger values.
DEFAULT_PAGE_SIZE: Final = 20


def base_params(profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    """The parameters every Douyin web API call carries.

    Ported from V4's ``BaseRequestModel``, minus ``msToken`` which the signing
    layer supplies.
    """
    return {
        "device_platform": "webapp",
        "aid": AID,
        "channel": CHANNEL,
        "pc_client_type": "1",
        "version_code": VERSION_CODE,
        "version_name": VERSION_NAME,
        "cookie_enabled": "true",
        "screen_width": str(profile.screen_width),
        "screen_height": str(profile.screen_height),
        "browser_language": profile.browser_language,
        "browser_platform": profile.browser_platform,
        "browser_name": profile.browser_name,
        "browser_version": profile.browser_version,
        "browser_online": "true",
        "engine_name": profile.engine_name,
        "engine_version": profile.engine_version,
        "os_name": profile.os_name,
        "os_version": profile.os_version,
        "cpu_core_num": str(profile.cpu_core_num),
        "device_memory": str(profile.device_memory),
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "0",
        "update_version_code": UPDATE_VERSION_CODE,
    }


def session_check_params(*, profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/query/user/``.

    The base set and nothing else. The question this endpoint answers - whose
    session is this - is asked entirely by the cookies and the signature, so
    there is nothing to name in the query string.
    """
    return base_params(profile)


def content_detail_params(
    *, aweme_id: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/aweme/detail/``."""
    return {**base_params(profile), "aweme_id": str(aweme_id)}


def author_profile_params(
    *, sec_user_id: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/user/profile/other/``.

    ``sec_user_id`` and not ``uid``: the numeric uid rotates, the sec id does
    not. See the Author contract in ``docs/design/11-data-contracts.md``.
    """
    return {
        **base_params(profile),
        "sec_user_id": str(sec_user_id),
        "publish_video_strategy_type": "2",
        "personal_center_strategy": "1",
    }


def author_posts_params(
    *,
    sec_user_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/aweme/post/``.

    ``cursor`` is the opaque cursor handed back by the previous page. For Douyin
    it decodes to ``max_cursor``, a millisecond publish timestamp; ``"0"`` asks
    for the first page. Callers never need to know that.
    """
    return {
        **base_params(profile),
        "sec_user_id": str(sec_user_id),
        "max_cursor": _cursor(cursor),
        "count": str(count),
        "publish_video_strategy_type": "2",
        "from_user_page": "1",
        "locate_query": "false",
        "need_time_list": "1",
        "show_live_replay_strategy": "1",
        "time_list_query": "0",
        "pc_libra_divert": profile.os_name,
        "whale_cut_token": "",
    }


def author_likes_params(
    *,
    sec_user_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/aweme/favorite/``.

    Only returns anything when the author has chosen to make their likes
    public, which most accounts have not. An empty page is the normal answer
    for a private list, not an error.
    """
    return {
        **base_params(profile),
        "sec_user_id": str(sec_user_id),
        "max_cursor": _cursor(cursor),
        "count": str(count),
        "publish_video_strategy_type": "2",
    }


def mix_posts_params(
    *,
    mix_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/mix/aweme/``.

    A mix is Douyin's series or playlist. ``mix_id`` comes from the ``mix_info``
    block of any post that belongs to one.
    """
    return {
        **base_params(profile),
        "mix_id": str(mix_id),
        "cursor": _cursor(cursor),
        "count": str(count),
    }


def comments_params(
    *,
    aweme_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/comment/list/``.

    Here the opaque cursor decodes to a plain offset, unlike the timestamp used
    by the post list - which is exactly why the contract makes callers treat it
    as opaque.
    """
    return {
        **base_params(profile),
        "aweme_id": str(aweme_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "item_type": "0",
        "insert_ids": "",
        "whale_cut_token": "",
        "cut_version": "1",
        "rcFT": "",
    }


def comment_replies_params(
    *,
    item_id: str,
    comment_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/aweme/v1/web/comment/list/reply/``.

    Note the name change: this endpoint calls the post ``item_id`` while the
    comment list calls it ``aweme_id``. Same value, different spelling.
    """
    return {
        **base_params(profile),
        "item_id": str(item_id),
        "comment_id": str(comment_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "item_type": "0",
    }


def _cursor(cursor: str | None) -> str:
    """Decode the opaque page cursor into Douyin's numeric cursor."""
    if cursor is None:
        return "0"
    text = cursor.strip()
    return text or "0"


__all__ = [
    "AID",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_PROFILE",
    "author_posts_params",
    "author_profile_params",
    "base_params",
    "comment_replies_params",
    "comments_params",
    "content_detail_params",
]
