from typing import Any, List
from pydantic import BaseModel, Field

from crawlers.douyin.web.utils import TokenManager, VerifyFpManager


# Base Model
class BaseRequestModel(BaseModel):
    device_platform: str = "webapp"
    aid: str = "6383"
    channel: str = "channel_pc_web"
    pc_client_type: int = 1
    version_code: str = "290100"
    version_name: str = "29.1.0"
    cookie_enabled: str = "true"
    screen_width: int = 1920
    screen_height: int = 1080
    browser_language: str = "zh-CN"
    browser_platform: str = "Win32"
    browser_name: str = "Chrome"
    browser_version: str = "130.0.0.0"
    browser_online: str = "true"
    engine_name: str = "Blink"
    engine_version: str = "130.0.0.0"
    os_name: str = "Windows"
    os_version: str = "10"
    cpu_core_num: int = 12
    device_memory: int = 8
    platform: str = "PC"
    downlink: str = "10"
    effective_type: str = "4g"
    from_user_page: str = "1"
    locate_query: str = "false"
    need_time_list: str = "1"
    pc_libra_divert: str = "Windows"
    publish_video_strategy_type: str = "2"
    round_trip_time: str = "0"
    show_live_replay_strategy: str = "1"
    time_list_query: str = "0"
    whale_cut_token: str = ""
    update_version_code: str = "170400"
    msToken: str = TokenManager.gen_real_msToken()


class BaseLiveModel(BaseModel):
    aid: str = "6383"
    app_name: str = "douyin_web"
    live_id: int = 1
    device_platform: str = "web"
    language: str = "zh-CN"
    cookie_enabled: str = "true"
    screen_width: int = 1920
    screen_height: int = 1080
    browser_language: str = "zh-CN"
    browser_platform: str = "Win32"
    browser_name: str = "Edge"
    browser_version: str = "119.0.0.0"
    enter_source: Any = ""
    is_need_double_stream: str = "false"
    # msToken: str = TokenManager.gen_real_msToken()
    # _signature: str = ''


class BaseLiveModel2(BaseModel):
    verifyFp: str = VerifyFpManager.gen_verify_fp()
    type_id: str = "0"
    live_id: str = "1"
    sec_user_id: str = ""
    version_code: str = "99.99.99"
    app_id: str = "1128"
    msToken: str = TokenManager.gen_real_msToken()


class BaseLoginModel(BaseModel):
    service: str = "https://www.douyin.com"
    need_logo: str = "false"
    need_short_url: str = "true"
    device_platform: str = "web_app"
    aid: str = "6383"
    account_sdk_source: str = "sso"
    sdk_version: str = "2.2.7-beta.6"
    language: str = "zh"


# Model
class UserProfile(BaseRequestModel):
    sec_user_id: str


class UserPost(BaseRequestModel):
    max_cursor: int
    count: int
    sec_user_id: str


# 获取单个作品视频弹幕数据
class PostDanmaku(BaseRequestModel):
    item_id: str
    duration: int
    end_time: int
    start_time: int = 0


class UserLike(BaseRequestModel):
    max_cursor: int
    count: int
    sec_user_id: str


class UserCollection(BaseRequestModel):
    # POST
    cursor: int
    count: int


class UserCollects(BaseRequestModel):
    # GET
    cursor: int
    count: int


class UserCollectsVideo(BaseRequestModel):
    # GET
    cursor: int
    count: int
    collects_id: str


class UserMusicCollection(BaseRequestModel):
    # GET
    cursor: int
    count: int


class UserMix(BaseRequestModel):
    cursor: int
    count: int
    mix_id: str


class FriendFeed(BaseRequestModel):
    cursor: int = 0
    level: int = 1
    aweme_ids: str = ""
    room_ids: str = ""
    pull_type: int = 0
    address_book_access: int = 2
    gps_access: int = 2
    recent_gids: str = ""


class PostFeed(BaseRequestModel):
    count: int = 10
    tag_id: str = ""
    share_aweme_id: str = ""
    live_insert_type: str = ""
    refresh_index: int = 1
    video_type_select: int = 1
    aweme_pc_rec_raw_data: dict = {}  # {"is_client":false}
    globalwid: str = ""
    pull_type: str = ""
    min_window: str = ""
    free_right: str = ""
    ug_source: str = ""
    creative_id: str = ""


class FollowFeed(BaseRequestModel):
    cursor: int = 0
    level: int = 1
    count: int = 20
    pull_type: str = ""


class PostRelated(BaseRequestModel):
    aweme_id: str
    count: int = 20
    filterGids: str  # id,id,id
    awemePcRecRawData: dict = {}  # {"is_client":false}
    sub_channel_id: int = 3
    # Seo-Flag: int = 0


class PostDetail(BaseRequestModel):
    aweme_id: str


class PostComments(BaseRequestModel):
    aweme_id: str
    cursor: int = 0
    count: int = 20
    item_type: int = 0
    insert_ids: str = ""
    whale_cut_token: str = ""
    cut_version: int = 1
    rcFT: str = ""


class PostCommentsReply(BaseRequestModel):
    item_id: str
    comment_id: str
    cursor: int = 0
    count: int = 20
    item_type: int = 0


class PostLocate(BaseRequestModel):
    sec_user_id: str
    max_cursor: str  # last max_cursor
    locate_item_id: str = ""  # aweme_id
    locate_item_cursor: str
    locate_query: str = "true"
    count: int = 10
    publish_video_strategy_type: int = 2


class UserLive(BaseLiveModel):
    web_rid: str
    room_id_str: str


# 直播间送礼用户排行榜
class LiveRoomRanking(BaseRequestModel):
    webcast_sdk_version: int = 2450
    room_id: int
    # anchor_id: int
    # sec_anchor_id: str
    rank_type: int = 30


class UserLive2(BaseLiveModel2):
    room_id: str


class FollowUserLive(BaseRequestModel):
    scene: str = "aweme_pc_follow_top"


class SuggestWord(BaseRequestModel):
    query: str = ""
    count: int = 8
    business_id: str
    from_group_id: str
    rsp_source: str = ""
    penetrate_params: dict = {}


class LoginGetQr(BaseLoginModel):
    verifyFp: str = ""
    fp: str = ""
    # msToken: str = TokenManager.gen_real_msToken()


class LoginCheckQr(BaseLoginModel):
    token: str = ""
    verifyFp: str = ""
    fp: str = ""
    # msToken: str = TokenManager.gen_real_msToken()


class UserFollowing(BaseRequestModel):
    user_id: str = ""
    sec_user_id: str = ""
    offset: int = 0  # 相当于cursor
    min_time: int = 0
    max_time: int = 0
    count: int = 20
    # source_type = 1: 最近关注 需要指定max_time(s) 3: 最早关注 需要指定min_time(s) 4: 综合排序
    source_type: int = 4
    gps_access: int = 0
    address_book_access: int = 0
    is_top: int = 1


class UserFollower(BaseRequestModel):
    user_id: str
    sec_user_id: str
    offset: int = 0  # 相当于cursor 但只对source_type: = 2 有效，其他情况为 0 即可
    min_time: int = 0
    max_time: int = 0
    count: int = 20
    # source_type = 1: 最近关注 需要指定max_time(s) 2: 综合关注(意义不明)
    source_type: int = 1
    gps_access: int = 0
    address_book_access: int = 0
    is_top: int = 1


# 列表作品
class URL_List(BaseModel):
    urls: List[str] = [
        "https://test.example.com/xxxxx/",
        "https://test.example.com/yyyyy/",
        "https://test.example.com/zzzzz/"
    ]

