from typing import Any
from pydantic import BaseModel
from urllib.parse import quote, unquote

from crawlers.tiktok.web.utils import TokenManager
from crawlers.utils.utils import get_timestamp


# Model
class BaseRequestModel(BaseModel):
    WebIdLastTime: str = str(get_timestamp("sec"))
    aid: str = "1988"
    app_language: str = "en"
    app_name: str = "tiktok_web"
    browser_language: str = "en-US"
    browser_name: str = "Mozilla"
    browser_online: str = "true"
    browser_platform: str = "Win32"
    browser_version: str = quote(
        "5.0 (Windows)",
        safe="",
    )
    channel: str = "tiktok_web"
    cookie_enabled: str = "true"
    device_id: int = 7380187414842836523
    odinId: int = 7404669909585003563
    device_platform: str = "web_pc"
    focus_state: str = "true"
    from_page: str = "user"
    history_len: int = 4
    is_fullscreen: str = "false"
    is_page_visible: str = "true"
    language: str = "en"
    os: str = "windows"
    priority_region: str = "US"
    referer: str = ""
    region: str = "US"  # SG JP KR...
    root_referer: str = quote("https://www.tiktok.com/", safe="")
    screen_height: int = 1080
    screen_width: int = 1920
    webcast_language: str = "en"
    tz_name: str = quote("America/Tijuana", safe="")
    # verifyFp: str = VerifyFpManager.gen_verify_fp()
    msToken: str = TokenManager.gen_real_msToken()


# router model
class UserProfile(BaseRequestModel):
    secUid: str = ""
    uniqueId: str


class UserPost(BaseModel):
    WebIdLastTime: str = "1714385892"
    aid: str = "1988"
    app_language: str = "zh-Hans"
    app_name: str = "tiktok_web"
    browser_language: str = "zh-CN"
    browser_name: str = "Mozilla"
    browser_online: str = "true"
    browser_platform: str = "Win32"
    browser_version: str = "5.0%20%28Windows%29"
    channel: str = "tiktok_web"
    cookie_enabled: str = "true"
    count: int = 20
    coverFormat: int = 2
    cursor: int = 0
    data_collection_enabled: str = "true"
    device_id: str = "7380187414842836523"
    device_platform: str = "web_pc"
    focus_state: str = "true"
    from_page: str = "user"
    history_len: str = "3"
    is_fullscreen: str = "false"
    is_page_visible: str = "true"
    language: str = "zh-Hans"
    locate_item_id: str = ""
    needPinnedItemIds: str = "true"
    odinId: str = "7404669909585003563"
    os: str = "windows"
    # 0：默认排序，1：热门排序，2：最旧排序
    post_item_list_request_type: int = 0
    priority_region: str = "US"
    referer: str = ""
    region: str = "US"
    screen_height: str = "827"
    screen_width: str = "1323"
    secUid: str
    tz_name: str = "America%2FLos_Angeles"
    user_is_login: str = "true"
    webcast_language: str = "zh-Hans"
    msToken: str = "SXtP7K0MMFlQmzpuWfZoxAlAaKqt-2p8oAbOHFBw-k3TA2g4jE_FXrFKf3i38lR-xNh_bV1_qfTPRnj4PXbkBfrVD2iAazeUkASIASHT0pu-Bx2_POx7O3nBBHZe2SI7CPsanerdclxHht1hcoUTlg%3D%3D"
    _signature: str = "_02B4Z6wo000017oyWOQAAIDD9xNhTSnfaDu6MFxAAIlj23"


class UserLike(BaseRequestModel):
    coverFormat: int = 2
    count: int = 30
    cursor: int = 0
    secUid: str


class UserCollect(BaseRequestModel):
    cookie: str = ""
    coverFormat: int = 2
    count: int = 30
    cursor: int = 0
    secUid: str


class UserPlayList(BaseRequestModel):
    count: int = 30
    cursor: int = 0
    secUid: str


class UserMix(BaseRequestModel):
    count: int = 30
    cursor: int = 0
    mixId: str


class PostDetail(BaseRequestModel):
    itemId: str


class PostComment(BaseRequestModel):
    aweme_id: str
    count: int = 20
    cursor: int = 0
    current_region: str = "US"


# 作品评论回复 (Post Comment Reply)
class PostCommentReply(BaseRequestModel):
    item_id: str
    comment_id: str
    count: int = 20
    cursor: int = 0
    current_region: str = "US"


# 用户粉丝 (User Fans)
class UserFans(BaseRequestModel):
    secUid: str
    count: int = 30
    maxCursor: int = 0
    minCursor: int = 0
    scene: int = 67


# 用户关注 (User Follow)
class UserFollow(BaseRequestModel):
    secUid: str
    count: int = 30
    maxCursor: int = 0
    minCursor: int = 0
    scene: int = 21
