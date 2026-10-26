"""Query parameter construction for the TikTok web P0 endpoints.

Distilled from V4's ``crawlers/tiktok/web/models.py`` and
``app/api/endpoints/tiktok_web.py`` on branch ``main`` (commit ``8c98fb7``) -
the only record of which parameters
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
import re
from dataclasses import replace
from typing import Any, Final

from dtk.core.errors import InvalidParam
from dtk.platforms.base import ClientProfile, ProfileSource, base_language

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

#: What TikTok calls each operating system in its ``os`` parameter, keyed by the
#: name :func:`dtk.platforms.base.operating_system` reports. TikTok's vocabulary
#: is its own and shorter than Douyin's: ``os=windows`` is corroborated by a
#: capture, the rest follow the same one-word pattern and are not.
_OS_NAMES: Final[dict[str, str]] = {
    "Windows": "Windows",
    "Mac OS X": "Mac",
    "Mac OS": "Mac",
    "Linux": "Linux",
    "Android": "Android",
    "iOS": "iOS",
    "Sun OS": "Linux",
}

#: ``navigator.appVersion``, as TikTok truncates it: the version, then the first
#: word of the platform token. ``Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...``
#: becomes ``5.0 (Windows)``, which is exactly what a live capture sends. Only
#: the Windows form is corroborated; the others follow the same rule.
_APP_VERSION = re.compile(r"Mozilla/([\d.]+) \(([^;)]+)")


def profile_for(source: ProfileSource) -> ClientProfile:
    """The query values that agree with one identity's User-Agent.

    Same purpose as Douyin's, different vocabulary, and the difference is the
    reason each platform owns this rather than sharing one function: TikTok
    reports the navigator product strings - ``browser_name`` is always the
    constant ``Mozilla`` and ``browser_version`` is a truncated
    ``navigator.appVersion`` - where Douyin reports the marketing name and the
    real version number.

    Anything the User-Agent does not state keeps :data:`DEFAULT_PROFILE`'s
    value, because a query parameter nobody can vouch for is worth no more than
    one that is simply wrong.
    """
    profile = DEFAULT_PROFILE.with_fingerprint(source)
    profile = replace(
        profile,
        # `browser_language` carries the full tag and `language` the bare
        # subtag; TikTok sends both, and sends the bare one three times.
        language=base_language(profile.browser_language) or profile.language,
        os_name=_OS_NAMES.get(profile.os_name, profile.os_name),
    )
    match = _APP_VERSION.match(source.user_agent or "")
    if match is None:
        return profile
    return replace(profile, browser_version=f"{match.group(1)} ({match.group(2).split()[0]})")


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


def session_check_params(**_: Any) -> dict[str, str]:
    """Parameters for ``/passport/token/beat/web/``.

    Not the shared base set. This is the passport service rather than the web
    app's own API, and a capture of the call the page makes carries three
    parameters - the rest of the base set is not sent and is not expected.
    """
    return {"device_platform": "web", "aid": "1459", "scene": "active"}


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


def author_bookmarks_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/user/collect/item_list/``.

    The posts an account saved, all of them, regardless of which folder they
    were filed in - the Saved tab's other half. ``author_collections`` lists the
    folders; this lists the contents of the tab.

    Every parameter here is from a capture of the page making this call while
    signed in, rather than copied from a sibling: ``needPinnedItemIds`` and
    ``post_item_list_request_type`` are sent for this path and are not part of
    the shared base set.

    Unlike ``author_collections``, this one really is owner-only: the request
    names an account rather than a folder, and there is no per-folder
    visibility to fall back on.

    Measured 2026-09-13 with a minted guest identity, against an account known
    to have saved posts: HTTP 200 with a 104-byte status envelope - no
    ``itemList`` at all - which classifies as BUSINESS_ERROR and surfaces as
    NOT_FOUND. Worth writing down because it is the good case: the refusal is
    explicit, so a caller is not handed an empty page it would have to read as
    "this account has saved nothing". Reading this needs an imported identity
    for the account itself; for someone else's public folder, use
    ``collection_posts``.
    """
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
        "needPinnedItemIds": "true",
        # 0 default order, as the page sends it. Shared with author_posts, which
        # documents the other values.
        "post_item_list_request_type": "0",
    }


def author_reposts_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/repost/item_list/``.

    The posts an author reposted onto their own profile - other people's
    videos, which is what makes this different from ``author_posts``. TikTok
    surfaces it as its own tab.

    Request shape is ``author_posts``' exactly, down to ``needPinnedItemIds``
    and ``post_item_list_request_type``, taken from a capture of the page.

    Unlike ``author_bookmarks`` this is not owner-only: the capture was taken
    from a signed-in browser looking at somebody ELSE's profile, and measured
    2026-09-13 a minted guest identity with no session cookie read the same
    tab. A profile tab other people can open is a public one.

    The cursor is NOT what ``author_posts`` uses. There it is a millisecond
    timestamp; here it is a plain item offset - a first page of 10 came back
    with ``cursor: "10"``, and asking for that returned the next 6 with no
    overlap and ``hasMore`` false. Both are opaque to callers and both pass
    through ``_cursor`` unchanged, so nothing downstream cares, but anyone
    reading these two builders side by side would otherwise assume they mean
    the same thing.
    """
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
        "needPinnedItemIds": "true",
        # 0 default order. Shared with author_posts, which documents the rest.
        "post_item_list_request_type": "0",
    }


def author_collections_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/user/collection_list/``.

    ``secUid``/``cursor``/``count``/``coverFormat`` match every other paged
    list here. ``needPinnedItemIds`` and ``publicOnly`` are specific to this
    endpoint; both come from this project's own V4 crawler for the same path
    (``fetch_user_collection_list``, added 2026-08-27).

    ``publicOnly=false`` asks for the private folders as well, and a guest is
    simply not given them. It is not the parameter that decides: visibility is
    a property of each folder. Measured 2026-09-13 against an account with one
    public folder and one private one - a minted guest identity, no session
    cookie, got the public folder complete with cover and item count, and no
    trace of the private one.
    """
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
        "needPinnedItemIds": "true",
        "publicOnly": "false",
    }


#: ``sourceType`` tells ``/api/collection/item_list/`` which kind of list the
#: ``collectionId`` names. 113 is a user's saved-posts folder, the only kind
#: this project asks for.
#:
#: The value comes from @BennoCrafter's V4 PR #738, not from a capture taken
#: here - so what is corroborated is that 113 works, measured 2026-09-13
#: against a public folder, and not what any other value would mean.
COLLECTION_SOURCE_TYPE: Final = "113"


def collection_posts_params(
    *,
    collection_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/collection/item_list/``.

    The posts inside ONE saved folder, where ``author_bookmarks`` returns every
    saved post regardless of folder and ``author_collections`` returns the
    folders themselves. ``collection_id`` is the ``collection_id`` that listing
    hands back.

    Keyed by the folder, not by the author: there is no ``secUid`` here, and
    that is the whole reason this is a separate endpoint rather than a
    parameter on one of the other two. It is also why a public folder is
    readable by a guest - the request names a thing, not a person.

    Measured 2026-09-13 against a folder set to public, with a minted guest
    identity and no session cookie of any kind.
    """
    return {
        **base_params(profile),
        "collectionId": str(collection_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "sourceType": COLLECTION_SOURCE_TYPE,
    }


#: ``scene`` for ``/api/collection/detail/``. Only one value has ever been
#: observed, so what this constant records is "the page sends 116", not what
#: any other value would select.
COLLECTION_DETAIL_SCENE: Final = "116"


def collection_detail_params(
    *,
    collection_id: str,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/collection/detail/``.

    One saved folder's own metadata - the thing ``author_collections`` returns
    a list of, asked for by id instead of by owner. That difference is the
    point: ``author_collections`` needs the owner's ``secUid``, so given only a
    collection id, which is all a shared link carries, there was previously no
    way to find out what it is.

    No ``secUid`` here, the same as ``collection_posts``, and not paged.
    """
    return {
        **base_params(profile),
        "collectionId": str(collection_id),
        "scene": COLLECTION_DETAIL_SCENE,
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


def related_posts_params(
    *,
    item_id: str,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/related/item_list/``.

    One batch, not a page: the answer's ``cursor`` is ``-1`` and there is no
    ``hasMore`` (measured 2026-10-20 with a guest, where ``count=30`` returned
    27 posts).
    """
    return {
        **base_params(profile),
        "itemID": str(item_id),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
    }


def music_detail_params(
    *, music_id: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """Parameters for ``/api/music/detail/``. ``musicId``, lower-case d."""
    return {**base_params(profile), "musicId": str(music_id)}


def music_posts_params(
    *,
    music_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/music/item_list/``.

    ``musicID`` here, upper-case D, where the detail says ``musicId``: the
    two paths spell the same id differently, and each answers only its own
    spelling. The cursor is an item offset.
    """
    return {
        **base_params(profile),
        "musicID": str(music_id),
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
    }


def hashtag_detail_params(
    *,
    challenge_id: str | None = None,
    challenge_name: str | None = None,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/challenge/detail/``, by id or by name.

    Both answer (measured 2026-10-20). Which one a value is never gets
    guessed from its digits: ``#2024`` is a name, and sending it as
    ``challengeId=2024`` asks for a different hashtag. A tag link names its
    hashtag, and :data:`dtk.worker.parsing.NAMED_BY_LINK` hands it over as one.
    """
    ident = (challenge_id or "").strip()
    name = (challenge_name or "").strip().lstrip("#")
    if ident and name:
        raise InvalidParam(
            "a hashtag is looked up by its id or by its name, not both",
            details={"endpoint": "tiktok.hashtag_detail", "fields": ["hashtag_id", "name"]},
        )
    if ident:
        if not ident.isdigit():
            raise InvalidParam(
                "hashtag_id is the number TikTok issues; pass a name as name",
                details={"endpoint": "tiktok.hashtag_detail", "field": "hashtag_id"},
            )
        return {**base_params(profile), "challengeId": ident}
    if not name:
        raise InvalidParam(
            "a hashtag needs its id or its name",
            details={"endpoint": "tiktok.hashtag_detail", "missing": ["hashtag_id", "name"]},
        )
    return {**base_params(profile), "challengeName": name}


def hashtag_posts_params(
    *,
    challenge_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/challenge/item_list/``, which takes the id only.

    A name is refused here rather than sent: ``challengeID=<name>`` is an
    HTTP 400 and ``illegal ID given``, the same answer as an id nobody has,
    which would tell the caller the hashtag does not exist.
    """
    ident = str(challenge_id).strip()
    if not ident.isdigit():
        raise InvalidParam(
            "the posts under a TikTok hashtag are listed by its numeric id; "
            "look the name up with /hashtag first and pass the hashtag_id it returns",
            details={"endpoint": "tiktok.hashtag_posts", "field": "hashtag_id"},
        )
    return {
        **base_params(profile),
        "challengeID": ident,
        "cursor": _cursor(cursor),
        "count": str(count),
        "coverFormat": COVER_FORMAT,
    }


#: What the search page says a query came from when it is typed into the box.
SEARCH_SOURCE: Final = "normal_search"


def _keyword(keyword: str) -> str:
    text = str(keyword).strip()
    if not text:
        raise InvalidParam("a search needs a keyword", details={"field": "keyword"})
    return text


def search_posts_params(
    *,
    keyword: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/search/item/full/``. The page pages it by ``offset``."""
    return {
        **base_params(profile),
        "keyword": _keyword(keyword),
        "offset": _cursor(cursor),
        "count": str(count),
        "search_source": SEARCH_SOURCE,
    }


def search_authors_params(
    *,
    keyword: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/search/user/full/``, which pages by ``cursor``.

    Not ``offset`` as the post search does: measured 2026-10-20, ``offset=10``
    answered the first page again, where ``cursor=10`` moved on.
    """
    return {
        **base_params(profile),
        "keyword": _keyword(keyword),
        "cursor": _cursor(cursor),
        "count": str(count),
        "search_source": SEARCH_SOURCE,
    }


def author_mixes_params(
    *,
    sec_uid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Parameters for ``/api/user/playlist/``: the playlists an author made."""
    return {
        **base_params(profile),
        "secUid": str(sec_uid),
        "cursor": _cursor(cursor),
        "count": str(count),
    }


#: What the live page says it is when it asks about a room by its owner.
LIVE_SOURCE_TYPE: Final = "54"


def live_room_params(*, unique_id: str, profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    """Parameters for ``/api-live/user/room/``: a live room, by its owner's
    @handle - the only thing a ``/@handle/live`` link carries."""
    handle = str(unique_id).strip().lstrip("@")
    if not handle:
        raise InvalidParam(
            "a TikTok live room is named by its @handle", details={"field": "room_id"}
        )
    return {**base_params(profile), "uniqueId": handle, "sourceType": LIVE_SOURCE_TYPE}


def live_room_by_id_params(
    *, room_id: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """Parameters for ``webcast.tiktok.com/webcast/room/info/``: a live room by
    the broadcast's own ``room_id``, the ``live_id`` a room answer carries."""
    broadcast = str(room_id).strip()
    if not broadcast.isdigit():
        raise InvalidParam("a TikTok broadcast id is a number", details={"field": "live_id"})
    return {**base_params(profile), "room_id": broadcast}


#: How many broadcasts one ``check_alive`` call asks about. The platform took
#: 200 in one call on 2026-10-26 and answered each; the cap is ours, half
#: that, to keep a query an ordinary URL length.
MAX_LIVE_IDS: Final = 100


def live_ids(value: str) -> tuple[str, ...]:
    """A comma-separated list of broadcast ids, checked and with repeats gone.

    Here rather than in the route alone so that every caller - REST, MCP, a
    task replayed from the queue - is held to the same list.
    """
    seen: dict[str, None] = {}
    for part in str(value).split(","):
        text = part.strip()
        if not text:
            continue
        if not text.isdigit():
            raise InvalidParam(
                "a TikTok broadcast id is a number",
                details={"field": "live_ids", "value": text[:32]},
            )
        seen[text] = None
    if not seen:
        raise InvalidParam("name at least one broadcast", details={"field": "live_ids"})
    if len(seen) > MAX_LIVE_IDS:
        raise InvalidParam(
            f"at most {MAX_LIVE_IDS} broadcasts at a time",
            details={"field": "live_ids", "count": len(seen), "maximum": MAX_LIVE_IDS},
        )
    return tuple(seen)


def live_status_params(
    *, room_ids: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """Parameters for ``webcast.tiktok.com/webcast/room/check_alive/``."""
    return {**base_params(profile), "room_ids": ",".join(live_ids(room_ids))}


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
    "COLLECTION_DETAIL_SCENE",
    "COLLECTION_SOURCE_TYPE",
    "COVER_FORMAT",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_PROFILE",
    "author_bookmarks_params",
    "author_collections_params",
    "author_posts_params",
    "author_profile_params",
    "author_reposts_params",
    "base_params",
    "collection_detail_params",
    "collection_posts_params",
    "comment_replies_params",
    "comments_params",
    "content_detail_params",
    "profile_for",
]
