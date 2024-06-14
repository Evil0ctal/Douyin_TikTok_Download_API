from typing import List

from fastapi import APIRouter, Body, Query, Request, HTTPException  # 导入FastAPI组件
from app.api.models.APIResponseModel import ResponseModel, ErrorResponseModel  # 导入响应模型

from crawlers.douyin.web.web_crawler import DouyinWebCrawler  # 导入抖音Web爬虫


router = APIRouter()
DouyinWebCrawler = DouyinWebCrawler()


# 获取单个作品数据
@router.get("/fetch_one_video", response_model=ResponseModel, summary="获取单个作品数据/Get single video data")
async def fetch_one_video(request: Request,
                          aweme_id: str = Query(example="7372484719365098803", description="作品id/Video id")):
    """
    # [中文]
    ### 用途:
    - 获取单个作品数据
    ### 参数:
    - aweme_id: 作品id
    ### 返回:
    - 作品数据

    # [English]
    ### Purpose:
    - Get single video data
    ### Parameters:
    - aweme_id: Video id
    ### Return:
    - Video data

    # [示例/Example]
    aweme_id = "7372484719365098803"
    """
    try:
        data = await DouyinWebCrawler.fetch_one_video(aweme_id)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取用户作品集合数据
@router.get("/fetch_user_post_videos", response_model=ResponseModel,
            summary="获取用户主页作品数据/Get user homepage video data")
async def fetch_user_post_videos(request: Request,
                                 sec_user_id: str = Query(
                                     example="MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE",
                                     description="用户sec_user_id/User sec_user_id"),
                                 max_cursor: int = Query(default=0, description="最大游标/Maximum cursor"),
                                 count: int = Query(default=20, description="每页数量/Number per page")):
    """
    # [中文]
    ### 用途:
    - 获取用户主页作品数据
    ### 参数:
    - sec_user_id: 用户sec_user_id
    - max_cursor: 最大游标
    - count: 最大数量
    ### 返回:
    - 用户作品数据

    # [English]
    ### Purpose:
    - Get user homepage video data
    ### Parameters:
    - sec_user_id: User sec_user_id
    - max_cursor: Maximum cursor
    - count: Maximum count number
    ### Return:
    - User video data

    # [示例/Example]
    sec_user_id = "MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE"
    max_cursor = 0
    counts = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_user_post_videos(sec_user_id, max_cursor, count)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取用户喜欢作品数据
@router.get("/fetch_user_like_videos", response_model=ResponseModel,
            summary="获取用户喜欢作品数据/Get user like video data")
async def fetch_user_like_videos(request: Request,
                                 sec_user_id: str = Query(
                                     example="MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y",
                                     description="用户sec_user_id/User sec_user_id"),
                                 max_cursor: int = Query(default=0, description="最大游标/Maximum cursor"),
                                 counts: int = Query(default=20, description="每页数量/Number per page")):
    """
    # [中文]
    ### 用途:
    - 获取用户喜欢作品数据
    ### 参数:
    - sec_user_id: 用户sec_user_id
    - max_cursor: 最大游标
    - count: 最大数量
    ### 返回:
    - 用户作品数据

    # [English]
    ### Purpose:
    - Get user like video data
    ### Parameters:
    - sec_user_id: User sec_user_id
    - max_cursor: Maximum cursor
    - count: Maximum count number
    ### Return:
    - User video data

    # [示例/Example]
    sec_user_id = "MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y"
    max_cursor = 0
    counts = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_user_like_videos(sec_user_id, max_cursor, counts)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取用户收藏作品数据（用户提供自己的Cookie）
@router.get("/fetch_user_collection_videos", response_model=ResponseModel,
            summary="获取用户收藏作品数据/Get user collection video data")
async def fetch_user_collection_videos(request: Request,
                                       cookie: str = Query(example="YOUR_COOKIE",
                                                           description="用户网页版抖音Cookie/Your web version of Douyin Cookie"),
                                       max_cursor: int = Query(default=0, description="最大游标/Maximum cursor"),
                                       counts: int = Query(default=20, description="每页数量/Number per page")):
    """
    # [中文]
    ### 用途:
    - 获取用户收藏作品数据
    ### 参数:
    - cookie: 用户网页版抖音Cookie(此接口需要用户提供自己的Cookie)
    - max_cursor: 最大游标
    - count: 最大数量
    ### 返回:
    - 用户作品数据

    # [English]
    ### Purpose:
    - Get user collection video data
    ### Parameters:
    - cookie: User's web version of Douyin Cookie (This interface requires users to provide their own Cookie)
    - max_cursor: Maximum cursor
    - count: Maximum number
    ### Return:
    - User video data

    # [示例/Example]
    cookie = "YOUR_COOKIE"
    max_cursor = 0
    counts = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_user_collection_videos(cookie, max_cursor, counts)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取用户合辑作品数据
@router.get("/fetch_user_mix_videos", response_model=ResponseModel,
            summary="获取用户合辑作品数据/Get user mix video data")
async def fetch_user_mix_videos(request: Request,
                                mix_id: str = Query(example="7348687990509553679", description="合辑id/Mix id"),
                                max_cursor: int = Query(default=0, description="最大游标/Maximum cursor"),
                                counts: int = Query(default=20, description="每页数量/Number per page")):
    """
    # [中文]
    ### 用途:
    - 获取用户合辑作品数据
    ### 参数:
    - mix_id: 合辑id
    - max_cursor: 最大游标
    - count: 最大数量
    ### 返回:
    - 用户作品数据

    # [English]
    ### Purpose:
    - Get user mix video data
    ### Parameters:
    - mix_id: Mix id
    - max_cursor: Maximum cursor
    - count: Maximum number
    ### Return:
    - User video data

    # [示例/Example]
    url = https://www.douyin.com/collection/7348687990509553679
    mix_id = "7348687990509553679"
    max_cursor = 0
    counts = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_user_mix_videos(mix_id, max_cursor, counts)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取用户直播流数据
@router.get("/fetch_user_live_videos", response_model=ResponseModel,
            summary="获取用户直播流数据/Get user live video data")
async def fetch_user_live_videos(request: Request,
                                 webcast_id: str = Query(example="285520721194",
                                                         description="直播间webcast_id/Room webcast_id")):
    """
    # [中文]
    ### 用途:
    - 获取用户直播流数据
    ### 参数:
    - webcast_id: 直播间webcast_id
    ### 返回:
    - 直播流数据

    # [English]
    ### Purpose:
    - Get user live video data
    ### Parameters:
    - webcast_id: Room webcast_id
    ### Return:
    - Live stream data

    # [示例/Example]
    webcast_id = "285520721194"
    """
    try:
        data = await DouyinWebCrawler.fetch_user_live_videos(webcast_id)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取指定用户的直播流数据
@router.get("/fetch_user_live_videos_by_room_id",
            response_model=ResponseModel,
            summary="获取指定用户的直播流数据/Get live video data of specified user")
async def fetch_user_live_videos_by_room_id(request: Request,
                                            room_id: str = Query(example="7318296342189919011",
                                                                 description="直播间room_id/Room room_id")):
    """
    # [中文]
    ### 用途:
    - 获取指定用户的直播流数据
    ### 参数:
    - room_id: 直播间room_id
    ### 返回:
    - 直播流数据

    # [English]
    ### Purpose:
    - Get live video data of specified user
    ### Parameters:
    - room_id: Room room_id
    ### Return:
    - Live stream data

    # [示例/Example]
    room_id = "7318296342189919011"
    """
    try:
        data = await DouyinWebCrawler.fetch_user_live_videos_by_room_id(room_id)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取直播间送礼用户排行榜
@router.get("/fetch_live_gift_ranking",
            response_model=ResponseModel,
            summary="获取直播间送礼用户排行榜/Get live room gift user ranking")
async def fetch_live_gift_ranking(request: Request,
                                  room_id: str = Query(example="7356585666190461731",
                                                       description="直播间room_id/Room room_id"),
                                  rank_type: int = Query(default=30, description="排行类型/Leaderboard type")):
    """
    # [中文]
    ### 用途:
    - 获取直播间送礼用户排行榜
    ### 参数:
    - room_id: 直播间room_id
    - rank_type: 排行类型，默认为30不用修改。
    ### 返回:
    - 排行榜数据

    # [English]
    ### Purpose:
    - Get live room gift user ranking
    ### Parameters:
    - room_id: Room room_id
    - rank_type: Leaderboard type, default is 30, no need to modify.
    ### Return:
    - Leaderboard data

    # [示例/Example]
    room_id = "7356585666190461731"
    rank_type = 30
    """
    try:
        data = await DouyinWebCrawler.fetch_live_gift_ranking(room_id, rank_type)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 抖音直播间商品信息
@router.get("/fetch_live_room_product_result",
            response_model=ResponseModel,
            summary="抖音直播间商品信息/Douyin live room product information")
async def fetch_live_room_product_result(request: Request,
                                         cookie: str = Query(example="YOUR_COOKIE",
                                                             description="用户网页版抖音Cookie/Your web version of Douyin Cookie"),
                                         room_id: str = Query(example="7356742011975715619",
                                                              description="直播间room_id/Room room_id"),
                                         author_id: str = Query(example="2207432981615527",
                                                                description="作者id/Author id"),
                                         limit: int = Query(default=20, description="数量/Number")):
    """
    # [中文]
    ### 用途:
    - 抖音直播间商品信息
    ### 参数:
    - cookie: 用户网页版抖音Cookie(此接口需要用户提供自己的Cookie，如获取失败请手动过一次验证码)
    - room_id: 直播间room_id
    - author_id: 作者id
    - limit: 数量
    ### 返回:
    - 商品信息

    # [English]
    ### Purpose:
    - Douyin live room product information
    ### Parameters:
    - cookie: User's web version of Douyin Cookie (This interface requires users to provide their own Cookie, if the acquisition fails, please manually pass the captcha code once)
    - room_id: Room room_id
    - author_id: Author id
    - limit: Number
    ### Return:
    - Product information

    # [示例/Example]
    cookie = "YOUR_COOKIE"
    room_id = "7356742011975715619"
    author_id = "2207432981615527"
    limit = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_live_room_product_result(cookie, room_id, author_id, limit)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取指定用户的信息
@router.get("/handler_user_profile",
            response_model=ResponseModel,
            summary="获取指定用户的信息/Get information of specified user")
async def handler_user_profile(request: Request,
                               sec_user_id: str = Query(
                                   example="MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y",
                                   description="用户sec_user_id/User sec_user_id")):
    """
    # [中文]
    ### 用途:
    - 获取指定用户的信息
    ### 参数:
    - sec_user_id: 用户sec_user_id
    ### 返回:
    - 用户信息

    # [English]
    ### Purpose:
    - Get information of specified user
    ### Parameters:
    - sec_user_id: User sec_user_id
    ### Return:
    - User information

    # [示例/Example]
    sec_user_id = "MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y"
    """
    try:
        data = await DouyinWebCrawler.handler_user_profile(sec_user_id)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取单个视频评论数据
@router.get("/fetch_video_comments",
            response_model=ResponseModel,
            summary="获取单个视频评论数据/Get single video comments data")
async def fetch_video_comments(request: Request,
                               aweme_id: str = Query(example="7372484719365098803", description="作品id/Video id"),
                               cursor: int = Query(default=0, description="游标/Cursor"),
                               count: int = Query(default=20, description="数量/Number")):
    """
    # [中文]
    ### 用途:
    - 获取单个视频评论数据
    ### 参数:
    - aweme_id: 作品id
    - cursor: 游标
    - count: 数量
    ### 返回:
    - 评论数据

    # [English]
    ### Purpose:
    - Get single video comments data
    ### Parameters:
    - aweme_id: Video id
    - cursor: Cursor
    - count: Number
    ### Return:
    - Comments data

    # [示例/Example]
    aweme_id = "7372484719365098803"
    cursor = 0
    count = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_video_comments(aweme_id, cursor, count)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取指定视频的评论回复数据
@router.get("/fetch_video_comment_replies",
            response_model=ResponseModel,
            summary="获取指定视频的评论回复数据/Get comment replies data of specified video")
async def fetch_video_comments_reply(request: Request,
                                     item_id: str = Query(example="7354666303006723354", description="作品id/Video id"),
                                     comment_id: str = Query(example="7354669356632638218",
                                                             description="评论id/Comment id"),
                                     cursor: int = Query(default=0, description="游标/Cursor"),
                                     count: int = Query(default=20, description="数量/Number")):
    """
    # [中文]
    ### 用途:
    - 获取指定视频的评论回复数据
    ### 参数:
    - item_id: 作品id
    - comment_id: 评论id
    - cursor: 游标
    - count: 数量
    ### 返回:
    - 评论回复数据

    # [English]
    ### Purpose:
    - Get comment replies data of specified video
    ### Parameters:
    - item_id: Video id
    - comment_id: Comment id
    - cursor: Cursor
    - count: Number
    ### Return:
    - Comment replies data

    # [示例/Example]
    aweme_id = "7354666303006723354"
    comment_id = "7354669356632638218"
    cursor = 0
    count = 20
    """
    try:
        data = await DouyinWebCrawler.fetch_video_comments_reply(item_id, comment_id, cursor, count)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 生成真实msToken
@router.get("/generate_real_msToken",
            response_model=ResponseModel,
            summary="生成真实msToken/Generate real msToken")
async def generate_real_msToken(request: Request):
    """
    # [中文]
    ### 用途:
    - 生成真实msToken
    ### 返回:
    - msToken

    # [English]
    ### Purpose:
    - Generate real msToken
    ### Return:
    - msToken
    """
    try:
        data = await DouyinWebCrawler.gen_real_msToken()
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 生成ttwid
@router.get("/generate_ttwid",
            response_model=ResponseModel,
            summary="生成ttwid/Generate ttwid")
async def generate_ttwid(request: Request):
    """
    # [中文]
    ### 用途:
    - 生成ttwid
    ### 返回:
    - ttwid

    # [English]
    ### Purpose:
    - Generate ttwid
    ### Return:
    - ttwid
    """
    try:
        data = await DouyinWebCrawler.gen_ttwid()
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 生成verify_fp
@router.get("/generate_verify_fp",
            response_model=ResponseModel,
            summary="生成verify_fp/Generate verify_fp")
async def generate_verify_fp(request: Request):
    """
    # [中文]
    ### 用途:
    - 生成verify_fp
    ### 返回:
    - verify_fp

    # [English]
    ### Purpose:
    - Generate verify_fp
    ### Return:
    - verify_fp
    """
    try:
        data = await DouyinWebCrawler.gen_verify_fp()
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 生成s_v_web_id
@router.get("/generate_s_v_web_id",
            response_model=ResponseModel,
            summary="生成s_v_web_id/Generate s_v_web_id")
async def generate_s_v_web_id(request: Request):
    """
    # [中文]
    ### 用途:
    - 生成s_v_web_id
    ### 返回:
    - s_v_web_id

    # [English]
    ### Purpose:
    - Generate s_v_web_id
    ### Return:
    - s_v_web_id
    """
    try:
        data = await DouyinWebCrawler.gen_s_v_web_id()
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 使用接口地址生成Xbogus参数
@router.get("/generate_x_bogus",
            response_model=ResponseModel,
            summary="使用接口网址生成X-Bogus参数/Generate X-Bogus parameter using API URL")
async def generate_x_bogus(request: Request,
                           url: str = Query(
                               example="https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=7148736076176215311&device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1&version_code=170400&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Edge&browser_version=117.0.2045.47&browser_online=true&engine_name=Blink&engine_version="),
                           user_agent: str = Query(
                               example="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")):
    """
    # [中文]
    ### 用途:
    - 使用接口网址生成X-Bogus参数
    ### 参数:
    - url: 接口网址

    # [English]
    ### Purpose:
    - Generate X-Bogus parameter using API URL
    ### Parameters:
    - url: API URL

    # [示例/Example]
    url = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=7148736076176215311&device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1&version_code=170400&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Edge&browser_version=117.0.2045.47&browser_online=true&engine_name=Blink&engine_version=117.0.0.0&os_name=Windows&os_version=10&cpu_core_num=128&device_memory=10240&platform=PC&downlink=10&effective_type=4g&round_trip_time=100"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
    """
    try:
        x_bogus = await DouyinWebCrawler.get_x_bogus(url, user_agent)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=x_bogus)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 使用接口地址生成Abogus参数
@router.get("/generate_a_bogus",
            response_model=ResponseModel,
            summary="使用接口网址生成A-Bogus参数/Generate A-Bogus parameter using API URL")
async def generate_a_bogus(request: Request,
                           url: str = Query(
                               example="https://www.douyin.com/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1&version_code=190500&version_name=19.5.0&cookie_enabled=true&browser_language=zh-CN&browser_platform=Win32&browser_name=Firefox&browser_online=true&engine_name=Gecko&os_name=Windows&os_version=10&platform=PC&screen_width=1920&screen_height=1080&browser_version=124.0&engine_version=122.0.0.0&cpu_core_num=12&device_memory=8&aweme_id=7372484719365098803"),
                           user_agent: str = Query(
                               example="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")):
    """
    # [中文]
    ### 用途:
    - 使用接口网址生成A-Bogus参数
    ### 参数:
    - url: 接口网址
    - user_agent: 用户代理，暂时不支持自定义，直接使用默认值即可。

    # [English]
    ### Purpose:
    - Generate A-Bogus parameter using API URL
    ### Parameters:
    - url: API URL
    - user_agent: User agent, temporarily does not support customization, just use the default value.

    # [示例/Example]
    url = "https://www.douyin.com/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1&version_code=190500&version_name=19.5.0&cookie_enabled=true&browser_language=zh-CN&browser_platform=Win32&browser_name=Firefox&browser_online=true&engine_name=Gecko&os_name=Windows&os_version=10&platform=PC&screen_width=1920&screen_height=1080&browser_version=124.0&engine_version=122.0.0.0&cpu_core_num=12&device_memory=8&aweme_id=7372484719365098803"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
    """
    try:
        a_bogus = await DouyinWebCrawler.get_a_bogus(url, user_agent)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=a_bogus)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 提取单个用户id
@router.get("/get_sec_user_id",
            response_model=ResponseModel,
            summary="提取单个用户id/Extract single user id")
async def get_sec_user_id(request: Request,
                          url: str = Query(
                              example="https://www.douyin.com/user/MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE")):
    """
    # [中文]
    ### 用途:
    - 提取单个用户id
    ### 参数:
    - url: 用户主页链接
    ### 返回:
    - 用户sec_user_id

    # [English]
    ### Purpose:
    - Extract single user id
    ### Parameters:
    - url: User homepage link
    ### Return:
    - User sec_user_id

    # [示例/Example]
    url = "https://www.douyin.com/user/MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE"
    """
    try:
        data = await DouyinWebCrawler.get_sec_user_id(url)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 提取列表用户id
@router.post("/get_all_sec_user_id",
             response_model=ResponseModel,
             summary="提取列表用户id/Extract list user id")
async def get_all_sec_user_id(request: Request,
                              url: List[str] = Body(
                                  example=[
                                      "https://www.douyin.com/user/MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE?vid=7285950278132616463",
                                      "https://www.douyin.com/user/MS4wLjABAAAAVsneOf144eGDFf8Xp9QNb1VW6ovXnNT5SqJBhJfe8KQBKWKDTWK5Hh-_i9mJzb8C",
                                      "长按复制此条消息，打开抖音搜索，查看TA的更多作品。 https://v.douyin.com/idFqvUms/",
                                      "https://v.douyin.com/idFqvUms/",
                                  ],
                                  description="用户主页链接列表/User homepage link list"
                              )):
    """
    # [中文]
    ### 用途:
    - 提取列表用户id
    ### 参数:
    - url: 用户主页链接列表
    ### 返回:
    - 用户sec_user_id列表

    # [English]
    ### Purpose:
    - Extract list user id
    ### Parameters:
    - url: User homepage link list
    ### Return:
    - User sec_user_id list

    # [示例/Example]
    ```json
    {
   "urls":[
      "https://www.douyin.com/user/MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE?vid=7285950278132616463",
      "https://www.douyin.com/user/MS4wLjABAAAAVsneOf144eGDFf8Xp9QNb1VW6ovXnNT5SqJBhJfe8KQBKWKDTWK5Hh-_i9mJzb8C",
      "长按复制此条消息，打开抖音搜索，查看TA的更多作品。 https://v.douyin.com/idFqvUms/",
      "https://v.douyin.com/idFqvUms/"
       ]
    }
    ```
    """
    try:
        data = await DouyinWebCrawler.get_all_sec_user_id(url)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 提取单个作品id
@router.get("/get_aweme_id",
            response_model=ResponseModel,
            summary="提取单个作品id/Extract single video id")
async def get_aweme_id(request: Request,
                       url: str = Query(example="https://www.douyin.com/video/7298145681699622182")):
    """
    # [中文]
    ### 用途:
    - 提取单个作品id
    ### 参数:
    - url: 作品链接
    ### 返回:
    - 作品id

    # [English]
    ### Purpose:
    - Extract single video id
    ### Parameters:
    - url: Video link
    ### Return:
    - Video id

    # [示例/Example]
    url = "https://www.douyin.com/video/7298145681699622182"
    """
    try:
        data = await DouyinWebCrawler.get_aweme_id(url)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 提取列表作品id
@router.post("/get_all_aweme_id",
             response_model=ResponseModel,
             summary="提取列表作品id/Extract list video id")
async def get_all_aweme_id(request: Request,
                           url: List[str] = Body(
                               example=[
                                   "0.53 02/26 I@v.sE Fus:/ 你别太帅了郑润泽# 现场版live # 音乐节 # 郑润泽  https://v.douyin.com/iRNBho6u/ 复制此链接，打开Dou音搜索，直接观看视频!",
                                   "https://v.douyin.com/iRNBho6u/",
                                   "https://www.iesdouyin.com/share/video/7298145681699622182/?region=CN&mid=7298145762238565171&u_code=l1j9bkbd&did=MS4wLjABAAAAtqpCx0hpOERbdSzQdjRZw-wFPxaqdbAzsKDmbJMUI3KWlMGQHC-n6dXAqa-dM2EP&iid=MS4wLjABAAAANwkJuWIRFOzg5uCpDRpMj4OX-QryoDgn-yYlXQnRwQQ&with_sec_did=1&titleType=title&share_sign=05kGlqGmR4_IwCX.ZGk6xuL0osNA..5ur7b0jbOx6cc-&share_version=170400&ts=1699262937&from_aid=6383&from_ssr=1&from=web_code_link",
                                   "https://www.douyin.com/video/7298145681699622182?previous_page=web_code_link",
                                   "https://www.douyin.com/video/7298145681699622182",
                               ],
                               description="作品链接列表/Video link list")):
    """
    # [中文]
    ### 用途:
    - 提取列表作品id
    ### 参数:
    - url: 作品链接列表
    ### 返回:
    - 作品id列表

    # [English]
    ### Purpose:
    - Extract list video id
    ### Parameters:
    - url: Video link list
    ### Return:
    - Video id list

    # [示例/Example]
    ```json
    {
   "urls":[
       "0.53 02/26 I@v.sE Fus:/ 你别太帅了郑润泽# 现场版live # 音乐节 # 郑润泽  https://v.douyin.com/iRNBho6u/ 复制此链接，打开Dou音搜索，直接观看视频!",
       "https://v.douyin.com/iRNBho6u/",
       "https://www.iesdouyin.com/share/video/7298145681699622182/?region=CN&mid=7298145762238565171&u_code=l1j9bkbd&did=MS4wLjABAAAAtqpCx0hpOERbdSzQdjRZw-wFPxaqdbAzsKDmbJMUI3KWlMGQHC-n6dXAqa-dM2EP&iid=MS4wLjABAAAANwkJuWIRFOzg5uCpDRpMj4OX-QryoDgn-yYlXQnRwQQ&with_sec_did=1&titleType=title&share_sign=05kGlqGmR4_IwCX.ZGk6xuL0osNA..5ur7b0jbOx6cc-&share_version=170400&ts=1699262937&from_aid=6383&from_ssr=1&from=web_code_link",
       "https://www.douyin.com/video/7298145681699622182?previous_page=web_code_link",
       "https://www.douyin.com/video/7298145681699622182",
    ]
    }
    ```
    """
    try:
        data = await DouyinWebCrawler.get_all_aweme_id(url)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 提取列表直播间号
@router.get("/get_webcast_id",
            response_model=ResponseModel,
            summary="提取列表直播间号/Extract list webcast id")
async def get_webcast_id(request: Request,
                         url: str = Query(example="https://live.douyin.com/775841227732")):
    """
    # [中文]
    ### 用途:
    - 提取列表直播间号
    ### 参数:
    - url: 直播间链接
    ### 返回:
    - 直播间号

    # [English]
    ### Purpose:
    - Extract list webcast id
    ### Parameters:
    - url: Room link
    ### Return:
    - Room id

    # [示例/Example]
    url = "https://live.douyin.com/775841227732"
    """
    try:
        data = await DouyinWebCrawler.get_webcast_id(url)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())


# 提取列表直播间号
@router.post("/get_all_webcast_id",
             response_model=ResponseModel,
             summary="提取列表直播间号/Extract list webcast id")
async def get_all_webcast_id(request: Request,
                             url: List[str] = Body(
                                 example=[
                                     "https://live.douyin.com/775841227732",
                                     "https://live.douyin.com/775841227732?room_id=7318296342189919011&enter_from_merge=web_share_link&enter_method=web_share_link&previous_page=app_code_link",
                                     'https://webcast.amemv.com/douyin/webcast/reflow/7318296342189919011?u_code=l1j9bkbd&did=MS4wLjABAAAAEs86TBQPNwAo-RGrcxWyCdwKhI66AK3Pqf3ieo6HaxI&iid=MS4wLjABAAAA0ptpM-zzoliLEeyvWOCUt-_dQza4uSjlIvbtIazXnCY&with_sec_did=1&use_link_command=1&ecom_share_track_params=&extra_params={"from_request_id":"20231230162057EC005772A8EAA0199906","im_channel_invite_id":"0"}&user_id=3644207898042206&liveId=7318296342189919011&from=share&style=share&enter_method=click_share&roomId=7318296342189919011&activity_info={}',
                                     "6i- Q@x.Sl 03/23 【醒子8ke的直播间】  点击打开👉https://v.douyin.com/i8tBR7hX/  或长按复制此条消息，打开抖音，看TA直播",
                                     "https://v.douyin.com/i8tBR7hX/",
                                 ],
                                 description="直播间链接列表/Room link list")):
    """
    # [中文]
    ### 用途:
    - 提取列表直播间号
    ### 参数:
    - url: 直播间链接列表
    ### 返回:
    - 直播间号列表

    # [English]
    ### Purpose:
    - Extract list webcast id
    ### Parameters:
    - url: Room link list
    ### Return:
    - Room id list

    # [示例/Example]
    ```json
    {
      "urls": [
            "https://live.douyin.com/775841227732",
            "https://live.douyin.com/775841227732?room_id=7318296342189919011&enter_from_merge=web_share_link&enter_method=web_share_link&previous_page=app_code_link",
            'https://webcast.amemv.com/douyin/webcast/reflow/7318296342189919011?u_code=l1j9bkbd&did=MS4wLjABAAAAEs86TBQPNwAo-RGrcxWyCdwKhI66AK3Pqf3ieo6HaxI&iid=MS4wLjABAAAA0ptpM-zzoliLEeyvWOCUt-_dQza4uSjlIvbtIazXnCY&with_sec_did=1&use_link_command=1&ecom_share_track_params=&extra_params={"from_request_id":"20231230162057EC005772A8EAA0199906","im_channel_invite_id":"0"}&user_id=3644207898042206&liveId=7318296342189919011&from=share&style=share&enter_method=click_share&roomId=7318296342189919011&activity_info={}',
            "6i- Q@x.Sl 03/23 【醒子8ke的直播间】  点击打开👉https://v.douyin.com/i8tBR7hX/  或长按复制此条消息，打开抖音，看TA直播",
            "https://v.douyin.com/i8tBR7hX/",
            ]
    }
    ```
    """
    try:
        data = await DouyinWebCrawler.get_all_webcast_id(url)
        return ResponseModel(code=200,
                             router=request.url.path,
                             data=data)
    except Exception as e:
        status_code = 400
        detail = ErrorResponseModel(code=status_code,
                                    router=request.url.path,
                                    params=dict(request.query_params),
                                    )
        raise HTTPException(status_code=status_code, detail=detail.dict())
