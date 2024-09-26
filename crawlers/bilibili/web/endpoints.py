class BilibiliAPIEndpoints:

    "-------------------------------------------------------域名-domain-------------------------------------------------------"
    # 哔哩哔哩接口域名
    BILIAPI_DOMAIN = "https://api.bilibili.com"

    # 哔哩哔哩直播域名
    LIVE_DOMAIN = "https://api.live.bilibili.com"

    "-------------------------------------------------------接口-api-------------------------------------------------------"
    # 作品信息 (Post Detail)
    POST_DETAIL = f"{BILIAPI_DOMAIN}/x/web-interface/view"

    # 作品视频流
    VIDEO_PLAYURL = f"{BILIAPI_DOMAIN}/x/player/wbi/playurl"

    # 用户发布视频作品数据
    USER_POST = f"{BILIAPI_DOMAIN}/x/space/wbi/arc/search"

    # 收藏夹列表
    COLLECT_FOLDERS = f"{BILIAPI_DOMAIN}/x/v3/fav/folder/created/list-all"

    # 收藏夹视频
    COLLECT_VIDEOS = f"{BILIAPI_DOMAIN}/x/v3/fav/resource/list"

    # 用户个人信息
    USER_DETAIL = f"{BILIAPI_DOMAIN}/x/space/wbi/acc/info"

    # 综合热门
    COM_POPULAR = f"{BILIAPI_DOMAIN}/x/web-interface/popular"

    # 每周必看
    WEEKLY_POPULAR = f"{BILIAPI_DOMAIN}/x/web-interface/popular/series/one"

    # 入站必刷
    PRECIOUS_POPULAR = f"{BILIAPI_DOMAIN}/x/web-interface/popular/precious"

    # 视频评论
    VIDEO_COMMENTS = f"{BILIAPI_DOMAIN}/x/v2/reply"

    # 用户动态
    USER_DYNAMIC = f"{BILIAPI_DOMAIN}/x/polymer/web-dynamic/v1/feed/space"

    # 评论的回复
    COMMENT_REPLY = f"{BILIAPI_DOMAIN}/x/v2/reply/reply"

    # 视频分p信息
    VIDEO_PARTS = f"{BILIAPI_DOMAIN}/x/player/pagelist"

    # 直播间信息
    LIVEROOM_DETAIL = f"{LIVE_DOMAIN}/room/v1/Room/get_info"

    # 直播分区列表
    LIVE_AREAS = f"{LIVE_DOMAIN}/room/v1/Area/getList"

    # 直播间视频流
    LIVE_VIDEOS = f"{LIVE_DOMAIN}/room/v1/Room/playUrl"

    # 正在直播的主播
    LIVE_STREAMER = f"{LIVE_DOMAIN}/xlive/web-interface/v1/second/getList"


