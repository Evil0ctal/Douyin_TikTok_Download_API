class DouyinAPIEndpoints:
    """
    API Endpoints for Douyin
    """

    # 抖音域名 (Douyin Domain)
    DOUYIN_DOMAIN = "https://www.douyin.com"

    # 抖音短域名 (Short Domain)
    IESDOUYIN_DOMAIN = "https://www.iesdouyin.com"

    # 直播域名 (Live Domain)
    LIVE_DOMAIN = "https://live.douyin.com"

    # 直播域名2 (Live Domain 2)
    LIVE_DOMAIN2 = "https://webcast.amemv.com"

    # SSO域名 (SSO Domain)
    SSO_DOMAIN = "https://sso.douyin.com"

    # WSS域名 (WSS Domain)
    WEBCAST_WSS_DOMAIN = "wss://webcast5-ws-web-lf.douyin.com"

    # 首页Feed (Home Feed)
    TAB_FEED = f"{DOUYIN_DOMAIN}/aweme/v1/web/tab/feed/"

    # 用户短信息 (User Short Info)
    USER_SHORT_INFO = f"{DOUYIN_DOMAIN}/aweme/v1/web/im/user/info/"

    # 用户详细信息 (User Detail Info)
    USER_DETAIL = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/profile/other/"

    # 作品基本 (Post Basic)
    BASE_AWEME = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/"

    # 用户作品 (User Post)
    USER_POST = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/post/"

    # 定位作品 (Post Local)
    LOCATE_POST = f"{DOUYIN_DOMAIN}/aweme/v1/web/locate/post/"

    # 综合搜索 (General Search)
    GENERAL_SEARCH = f"{DOUYIN_DOMAIN}/aweme/v1/web/general/search/single/"

    # 视频搜索 (Video Search)
    VIDEO_SEARCH = f"{DOUYIN_DOMAIN}/aweme/v1/web/search/item/"

    # 用户搜索 (User Search)
    USER_SEARCH = f"{DOUYIN_DOMAIN}/aweme/v1/web/discover/search/"

    # 直播间搜索 (Live Search)
    LIVE_SEARCH = f"{DOUYIN_DOMAIN}/aweme/v1/web/live/search/"

    # 作品信息 (Post Detail)
    POST_DETAIL = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/detail/"

    # 单个作品视频弹幕数据 (Post Danmaku)
    POST_DANMAKU = f"{DOUYIN_DOMAIN}/aweme/v1/web/danmaku/get_v2/"

    # 用户喜欢A (User Like A)
    USER_FAVORITE_A = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/favorite/"

    # 用户喜欢B (User Like B)
    USER_FAVORITE_B = f"{IESDOUYIN_DOMAIN}/web/api/v2/aweme/like/"

    # 关注用户(User Following)
    USER_FOLLOWING = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/following/list/"

    # 粉丝用户 (User Follower)
    USER_FOLLOWER = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/follower/list/"

    # 合集作品
    MIX_AWEME = f"{DOUYIN_DOMAIN}/aweme/v1/web/mix/aweme/"

    # 用户历史 (User History)
    USER_HISTORY = f"{DOUYIN_DOMAIN}/aweme/v1/web/history/read/"

    # 用户收藏 (User Collection)
    USER_COLLECTION = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/listcollection/"

    # 用户收藏夹 (User Collects)
    USER_COLLECTS = f"{DOUYIN_DOMAIN}/aweme/v1/web/collects/list/"

    # 用户收藏夹作品 (User Collects Posts)
    USER_COLLECTS_VIDEO = f"{DOUYIN_DOMAIN}/aweme/v1/web/collects/video/list/"

    # 用户音乐收藏 (User Music Collection)
    USER_MUSIC_COLLECTION = f"{DOUYIN_DOMAIN}/aweme/v1/web/music/listcollection/"

    # 首页朋友作品 (Friend Feed)
    FRIEND_FEED = f"{DOUYIN_DOMAIN}/aweme/v1/web/familiar/feed/"

    # 关注用户作品 (Follow Feed)
    FOLLOW_FEED = f"{DOUYIN_DOMAIN}/aweme/v1/web/follow/feed/"

    # 相关推荐 (Related Feed)
    POST_RELATED = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/related/"

    # 关注用户列表直播 (Follow User Live)
    FOLLOW_USER_LIVE = f"{DOUYIN_DOMAIN}/webcast/web/feed/follow/"

    # 直播信息接口 (Live Info)
    LIVE_INFO = f"{LIVE_DOMAIN}/webcast/room/web/enter/"

    # 直播信息接口2 (Live Info 2)
    LIVE_INFO_ROOM_ID = f"{LIVE_DOMAIN2}/webcast/room/reflow/info/"

    # 直播间送礼用户排行榜 (Live Gift Rank)
    LIVE_GIFT_RANK = f"{LIVE_DOMAIN}/webcast/ranklist/audience/"

    # 直播用户信息 (Live User Info)
    LIVE_USER_INFO = f"{LIVE_DOMAIN}/webcast/user/me/"

    # 推荐搜索词 (Suggest Words)
    SUGGEST_WORDS = f"{DOUYIN_DOMAIN}/aweme/v1/web/api/suggest_words/"

    # SSO登录 (SSO Login)
    SSO_LOGIN_GET_QR = f"{SSO_DOMAIN}/get_qrcode/"

    # 登录检查 (Login Check)
    SSO_LOGIN_CHECK_QR = f"{SSO_DOMAIN}/check_qrconnect/"

    # 登录确认 (Login Confirm)
    SSO_LOGIN_CHECK_LOGIN = f"{SSO_DOMAIN}/check_login/"

    # 登录重定向 (Login Redirect)
    SSO_LOGIN_REDIRECT = f"{DOUYIN_DOMAIN}/login/"

    # 登录回调 (Login Callback)
    SSO_LOGIN_CALLBACK = f"{DOUYIN_DOMAIN}/passport/sso/login/callback/"

    # 作品评论 (Post Comment)
    POST_COMMENT = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/list/"

    # 评论回复 (Comment Reply)
    POST_COMMENT_REPLY = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/list/reply/"

    # 回复评论 (Reply Comment)
    POST_COMMENT_PUBLISH = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/publish"

    # 删除评论 (Delete Comment)
    POST_COMMENT_DELETE = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/delete/"

    # 点赞评论 (Like Comment)
    POST_COMMENT_DIGG = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/digg"

    # 抖音热榜数据 (Douyin Hot Search)
    DOUYIN_HOT_SEARCH = f"{DOUYIN_DOMAIN}/aweme/v1/web/hot/search/list/"

    # 抖音视频频道 (Douyin Video Channel)
    DOUYIN_VIDEO_CHANNEL = f"{DOUYIN_DOMAIN}/aweme/v1/web/channel/feed/"

