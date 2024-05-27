from typing import List

from fastapi import APIRouter, Query, Body, Request, HTTPException  # 导入FastAPI组件

from app.api.models.APIResponseModel import ResponseModel, ErrorResponseModel  # 导入响应模型

from crawlers.tiktok.web.web_crawler import TikTokWebCrawler  # 导入TikTokWebCrawler类

router = APIRouter()
TikTokWebCrawler = TikTokWebCrawler()


# 获取单个作品数据
@router.get("/fetch_one_video",
            response_model=ResponseModel,
            summary="获取单个作品数据/Get single video data")
async def fetch_one_video(request: Request,
                            itemId: str = Query(example="7339393672959757570", description="作品id/Video id")):
    """
    # [中文]
    ### 用途:
    - 获取单个作品数据
    ### 参数:
    - itemId: 作品id
    ### 返回:
    - 作品数据

    # [English]
    ### Purpose:
    - Get single video data
    ### Parameters:
    - itemId: Video id
    ### Return:
    - Video data

    # [示例/Example]
    itemId = "7339393672959757570"
    """
    try:
        data = await TikTokWebCrawler.fetch_one_video(itemId)
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


# 获取用户的个人信息
@router.get("/fetch_user_profile",
            response_model=ResponseModel,
            summary="获取用户的个人信息/Get user profile")
async def fetch_user_profile(request: Request,
                             uniqueId: str = Query(default="tiktok", description="用户uniqueId/User uniqueId"),
                             secUid: str = Query(default="", description="用户secUid/User secUid"),):
    """
    # [中文]
    ### 用途:
    - 获取用户的个人信息
    ### 参数:
    - secUid: 用户secUid
    - uniqueId: 用户uniqueId
    - secUid和uniqueId至少提供一个, 优先使用uniqueId, 也就是用户主页的链接中的用户名。
    ### 返回:
    - 用户的个人信息

    # [English]
    ### Purpose:
    - Get user profile
    ### Parameters:
    - secUid: User secUid
    - uniqueId: User uniqueId
    - At least one of secUid and uniqueId is provided, and uniqueId is preferred, that is, the username in the user's homepage link.
    ### Return:
    - User profile

    # [示例/Example]
    secUid = "MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM"
    uniqueId = "tiktok"
    """
    try:
        data = await TikTokWebCrawler.fetch_user_profile(secUid, uniqueId)
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


# 获取用户的作品列表
@router.get("/fetch_user_post",
            response_model=ResponseModel,
            summary="获取用户的作品列表/Get user posts")
async def fetch_user_post(request: Request,
                          secUid: str = Query(example="MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM",
                                              description="用户secUid/User secUid"),
                          cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                          count: int = Query(default=35, description="每页数量/Number per page"),
                          coverFormat: int = Query(default=2, description="封面格式/Cover format")):
    """
    # [中文]
    ### 用途:
    - 获取用户的作品列表
    ### 参数:
    - secUid: 用户secUid
    - cursor: 翻页游标
    - count: 每页数量
    - coverFormat: 封面格式
    ### 返回:
    - 用户的作品列表

    # [English]
    ### Purpose:
    - Get user posts
    ### Parameters:
    - secUid: User secUid
    - cursor: Page cursor
    - count: Number per page
    - coverFormat: Cover format
    ### Return:
    - User posts

    # [示例/Example]
    secUid = "MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM"
    cursor = 0
    count = 35
    coverFormat = 2
    """
    try:
        data = await TikTokWebCrawler.fetch_user_post(secUid, cursor, count, coverFormat)
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


# 获取用户的点赞列表
@router.get("/fetch_user_like",
            response_model=ResponseModel,
            summary="获取用户的点赞列表/Get user likes")
async def fetch_user_like(request: Request,
                          secUid: str = Query(
                              example="MS4wLjABAAAAq1iRXNduFZpY301UkVpJ1eQT60_NiWS9QQSeNqmNQEDJp0pOF8cpleNEdiJx5_IU",
                              description="用户secUid/User secUid"),
                          cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                          count: int = Query(default=35, description="每页数量/Number per page"),
                          coverFormat: int = Query(default=2, description="封面格式/Cover format")):
    """
    # [中文]
    ### 用途:
    - 获取用户的点赞列表
    - 注意: 该接口需要用户点赞列表为公开状态
    ### 参数:
    - secUid: 用户secUid
    - cursor: 翻页游标
    - count: 每页数量
    - coverFormat: 封面格式
    ### 返回:
    - 用户的点赞列表

    # [English]
    ### Purpose:
    - Get user likes
    - Note: This interface requires that the user's like list be public
    ### Parameters:
    - secUid: User secUid
    - cursor: Page cursor
    - count: Number per page
    - coverFormat: Cover format
    ### Return:
    - User likes

    # [示例/Example]
    secUid = "MS4wLjABAAAAq1iRXNduFZpY301UkVpJ1eQT60_NiWS9QQSeNqmNQEDJp0pOF8cpleNEdiJx5_IU"
    cursor = 0
    count = 35
    coverFormat = 2
    """
    try:
        data = await TikTokWebCrawler.fetch_user_like(secUid, cursor, count, coverFormat)
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


# 获取用户的收藏列表
@router.get("/fetch_user_collect",
            response_model=ResponseModel,
            summary="获取用户的收藏列表/Get user favorites")
async def fetch_user_collect(request: Request,
                             cookie: str = Query(example="Your_Cookie", description="用户cookie/User cookie"),
                             secUid: str = Query(example="Your_SecUid", description="用户secUid/User secUid"),
                             cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                             count: int = Query(default=30, description="每页数量/Number per page"),
                             coverFormat: int = Query(default=2, description="封面格式/Cover format")):
    """
    # [中文]
    ### 用途:
    - 获取用户的收藏列表
    - 注意: 该接口目前只能获取自己的收藏列表，需要提供自己账号的cookie。
    ### 参数:
    - cookie: 用户cookie
    - secUid: 用户secUid
    - cursor: 翻页游标
    - count: 每页数量
    - coverFormat: 封面格式
    ### 返回:
    - 用户的收藏列表

    # [English]
    ### Purpose:
    - Get user favorites
    - Note: This interface can currently only get your own favorites list, you need to provide your account cookie.
    ### Parameters:
    - cookie: User cookie
    - secUid: User secUid
    - cursor: Page cursor
    - count: Number per page
    - coverFormat: Cover format
    ### Return:
    - User favorites

    # [示例/Example]
    cookie = "Your_Cookie"
    secUid = "Your_SecUid"
    cursor = 0
    count = 30
    coverFormat = 2
    """
    try:
        data = await TikTokWebCrawler.fetch_user_collect(cookie, secUid, cursor, count, coverFormat)
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


# 获取用户的播放列表
@router.get("/fetch_user_play_list",
            response_model=ResponseModel,
            summary="获取用户的播放列表/Get user play list")
async def fetch_user_play_list(request: Request,
                               secUid: str = Query(example="MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM",
                                                   description="用户secUid/User secUid"),
                               cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                               count: int = Query(default=30, description="每页数量/Number per page")):
    """
    # [中文]
    ### 用途:
    - 获取用户的播放列表
    ### 参数:
    - secUid: 用户secUid
    - cursor: 翻页游标
    - count: 每页数量
    ### 返回:
    - 用户的播放列表

    # [English]
    ### Purpose:
    - Get user play list
    ### Parameters:
    - secUid: User secUid
    - cursor: Page cursor
    - count: Number per page
    ### Return:
    - User play list

    # [示例/Eample]
    secUid = "MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM"
    cursor = 0
    count = 30
    """
    try:
        data = await TikTokWebCrawler.fetch_user_play_list(secUid, cursor, count)
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


# 获取用户的合辑列表
@router.get("/fetch_user_mix",
            response_model=ResponseModel,
            summary="获取用户的合辑列表/Get user mix list")
async def fetch_user_mix(request: Request,
                         mixId: str = Query(example="7101538765474106158",
                                            description="合辑id/Mix id"),
                         cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                         count: int = Query(default=30, description="每页数量/Number per page")):
    """
    # [中文]
    ### 用途:
    - 获取用户的合辑列表
    ### 参数:
    - mixId: 合辑id
    - cursor: 翻页游标
    - count: 每页数量
    ### 返回:
    - 用户的合辑列表

    # [English]
    ### Purpose:
    - Get user mix list
    ### Parameters:
    - mixId: Mix id
    - cursor: Page cursor
    - count: Number per page
    ### Return:
    - User mix list

    # [示例/Eample]
    mixId = "7101538765474106158"
    cursor = 0
    count = 30
    """
    try:
        data = await TikTokWebCrawler.fetch_user_mix(mixId, cursor, count)
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


# 获取作品的评论列表
@router.get("/fetch_post_comment",
            response_model=ResponseModel,
            summary="获取作品的评论列表/Get video comments")
async def fetch_post_comment(request: Request,
                             aweme_id: str = Query(example="7304809083817774382", description="作品id/Video id"),
                             cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                             count: int = Query(default=20, description="每页数量/Number per page"),
                             current_region: str = Query(default="", description="当前地区/Current region")):
    """
    # [中文]
    ### 用途:
    - 获取作品的评论列表
    ### 参数:
    - aweme_id: 作品id
    - cursor: 翻页游标
    - count: 每页数量
    - current_region: 当前地区，默认为空。
    ### 返回:
    - 作品的评论列表

    # [English]
    ### Purpose:
    - Get video comments
    ### Parameters:
    - aweme_id: Video id
    - cursor: Page cursor
    - count: Number per page
    - current_region: Current region, default is empty.
    ### Return:
    - Video comments

    # [示例/Eample]
    aweme_id = "7304809083817774382"
    cursor = 0
    count = 20
    current_region = ""
    """
    try:
        data = await TikTokWebCrawler.fetch_post_comment(aweme_id, cursor, count, current_region)
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


# 获取作品的评论回复列表
@router.get("/fetch_post_comment_reply",
            response_model=ResponseModel,
            summary="获取作品的评论回复列表/Get video comment replies")
async def fetch_post_comment_reply(request: Request,
                                   item_id: str = Query(example="7304809083817774382", description="作品id/Video id"),
                                   comment_id: str = Query(example="7304877760886588191",
                                                           description="评论id/Comment id"),
                                   cursor: int = Query(default=0, description="翻页游标/Page cursor"),
                                   count: int = Query(default=20, description="每页数量/Number per page"),
                                   current_region: str = Query(default="", description="当前地区/Current region")):
    """
    # [中文]
    ### 用途:
    - 获取作品的评论回复列表
    ### 参数:
    - item_id: 作品id
    - comment_id: 评论id
    - cursor: 翻页游标
    - count: 每页数量
    - current_region: 当前地区，默认为空。
    ### 返回:
    - 作品的评论回复列表

    # [English]
    ### Purpose:
    - Get video comment replies
    ### Parameters:
    - item_id: Video id
    - comment_id: Comment id
    - cursor: Page cursor
    - count: Number per page
    - current_region: Current region, default is empty.
    ### Return:
    - Video comment replies

    # [示例/Eample]
    item_id = "7304809083817774382"
    comment_id = "7304877760886588191"
    cursor = 0
    count = 20
    current_region = ""
    """
    try:
        data = await TikTokWebCrawler.fetch_post_comment_reply(item_id, comment_id, cursor, count, current_region)
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


# 获取用户的粉丝列表
@router.get("/fetch_user_fans",
            response_model=ResponseModel,
            summary="获取用户的粉丝列表/Get user followers")
async def fetch_user_fans(request: Request,
                          secUid: str = Query(example="MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM",
                                              description="用户secUid/User secUid"),
                          count: int = Query(default=30, description="每页数量/Number per page"),
                          maxCursor: int = Query(default=0, description="最大游标/Max cursor"),
                          minCursor: int = Query(default=0, description="最小游标/Min cursor")):
    """
    # [中文]
    ### 用途:
    - 获取用户的粉丝列表
    ### 参数:
    - secUid: 用户secUid
    - count: 每页数量
    - maxCursor: 最大游标
    - minCursor: 最小游标
    ### 返回:
    - 用户的粉丝列表

    # [English]
    ### Purpose:
    - Get user followers
    ### Parameters:
    - secUid: User secUid
    - count: Number per page
    - maxCursor: Max cursor
    - minCursor: Min cursor
    ### Return:
    - User followers

    # [示例/Example]
    secUid = "MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM"
    count = 30
    maxCursor = 0
    minCursor = 0
    """
    try:
        data = await TikTokWebCrawler.fetch_user_fans(secUid, count, maxCursor, minCursor)
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


# 获取用户的关注列表
@router.get("/fetch_user_follow",
            response_model=ResponseModel,
            summary="获取用户的关注列表/Get user followings")
async def fetch_user_follow(request: Request,
                            secUid: str = Query(example="MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM",
                                                description="用户secUid/User secUid"),
                            count: int = Query(default=30, description="每页数量/Number per page"),
                            maxCursor: int = Query(default=0, description="最大游标/Max cursor"),
                            minCursor: int = Query(default=0, description="最小游标/Min cursor")):
    """
    # [中文]
    ### 用途:
    - 获取用户的关注列表
    ### 参数:
    - secUid: 用户secUid
    - count: 每页数量
    - maxCursor: 最大游标
    - minCursor: 最小游标
    ### 返回:
    - 用户的关注列表

    # [English]
    ### Purpose:
    - Get user followings
    ### Parameters:
    - secUid: User secUid
    - count: Number per page
    - maxCursor: Max cursor
    - minCursor: Min cursor
    ### Return:
    - User followings

    # [示例/Example]
    secUid = "MS4wLjABAAAAv7iSuuXDJGDvJkmH_vz1qkDZYo1apxgzaxdBSeIuPiM"
    count = 30
    maxCursor = 0
    minCursor = 0
    """
    try:
        data = await TikTokWebCrawler.fetch_user_follow(secUid, count, maxCursor, minCursor)
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


"""-------------------------------------------------------utils接口列表-------------------------------------------------------"""


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
    - 真实msToken

    # [English]
    ### Purpose:
    - Generate real msToken
    ### Return:
    - Real msToken
    """
    try:
        data = await TikTokWebCrawler.fetch_real_msToken()
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
async def generate_ttwid(request: Request,
                         cookie: str = Query(example="Your_Cookie", description="用户cookie/User cookie")):
    """
    # [中文]
    ### 用途:
    - 生成ttwid
    ### 参数:
    - cookie: 用户cookie
    ### 返回:
    - ttwid

    # [English]
    ### Purpose:
    - Generate ttwid
    ### Parameters:
    - cookie: User cookie
    ### Return:
    - ttwid

    # [示例/Example]
    cookie = "Your_Cookie"
    """
    try:
        data = await TikTokWebCrawler.fetch_ttwid(cookie)
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


# 生成xbogus
@router.get("/generate_xbogus",
            response_model=ResponseModel,
            summary="生成xbogus/Generate xbogus")
async def generate_xbogus(request: Request,
                          url: str = Query(
                              example="https://www.tiktok.com/api/item/detail/?WebIdLastTime=1712665533&aid=1988&app_language=en&app_name=tiktok_web&browser_language=en-US&browser_name=Mozilla&browser_online=true&browser_platform=Win32&browser_version=5.0%20%28Windows%29&channel=tiktok_web&cookie_enabled=true&device_id=7349090360347690538&device_platform=web_pc&focus_state=true&from_page=user&history_len=4&is_fullscreen=false&is_page_visible=true&language=en&os=windows&priority_region=US&referer=&region=US&root_referer=https%3A%2F%2Fwww.tiktok.com%2F&screen_height=1080&screen_width=1920&webcast_language=en&tz_name=America%2FTijuana&msToken=AYFCEapCLbMrS8uTLBoYdUMeeVLbCdFQ_QF_-OcjzJw1CPr4JQhWUtagy0k4a9IITAqi5Qxr2Vdh9mgCbyGxTnvWLa4ZVY6IiSf6lcST-tr0IXfl-r_ZTpzvWDoQfqOVsWCTlSNkhAwB-tap5g==&itemId=7339393672959757570",
                              description="未签名的API URL/Unsigned API URL"),
                          user_agent: str = Query(
                              example="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
                              description="用户浏览器User-Agent/User browser User-Agent")):
    """
    # [中文]
    ### 用途:
    - 生成xbogus
    ### 参数:
    - url: 未签名的API URL
    - user_agent: 用户浏览器User-Agent
    ### 返回:
    - xbogus

    # [English]
    ### Purpose:
    - Generate xbogus
    ### Parameters:
    - url: Unsigned API URL
    - user_agent: User browser User-Agent
    ### Return:
    - xbogus

    # [示例/Example]
    url = "https://www.tiktok.com/api/item/detail/?WebIdLastTime=1712665533&aid=1988&app_language=en&app_name=tiktok_web&browser_language=en-US&browser_name=Mozilla&browser_online=true&browser_platform=Win32&browser_version=5.0%20%28Windows%29&channel=tiktok_web&cookie_enabled=true&device_id=7349090360347690538&device_platform=web_pc&focus_state=true&from_page=user&history_len=4&is_fullscreen=false&is_page_visible=true&language=en&os=windows&priority_region=US&referer=&region=US&root_referer=https%3A%2F%2Fwww.tiktok.com%2F&screen_height=1080&screen_width=1920&webcast_language=en&tz_name=America%2FTijuana&msToken=AYFCEapCLbMrS8uTLBoYdUMeeVLbCdFQ_QF_-OcjzJw1CPr4JQhWUtagy0k4a9IITAqi5Qxr2Vdh9mgCbyGxTnvWLa4ZVY6IiSf6lcST-tr0IXfl-r_ZTpzvWDoQfqOVsWCTlSNkhAwB-tap5g==&itemId=7339393672959757570"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    """
    try:
        data = await TikTokWebCrawler.gen_xbogus(url, user_agent)
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
@router.get("/get_sec_user_id",
            response_model=ResponseModel,
            summary="提取列表用户id/Extract list user id")
async def get_sec_user_id(request: Request,
                          url: str = Query(
                              example="https://www.tiktok.com/@tiktok",
                              description="用户主页链接/User homepage link")):
    """
    # [中文]
    ### 用途:
    - 提取列表用户id
    ### 参数:
    - url: 用户主页链接
    ### 返回:
    - 用户id

    # [English]
    ### Purpose:
    - Extract list user id
    ### Parameters:
    - url: User homepage link
    ### Return:
    - User id

    # [示例/Example]
    url = "https://www.tiktok.com/@tiktok"
    """
    try:
        data = await TikTokWebCrawler.get_sec_user_id(url)
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
                                  example=["https://www.tiktok.com/@tiktok"],
                                  description="用户主页链接/User homepage link")):
    """
    # [中文]
    ### 用途:
    - 提取列表用户id
    ### 参数:
    - url: 用户主页链接
    ### 返回:
    - 用户id

    # [English]
    ### Purpose:
    - Extract list user id
    ### Parameters:
    - url: User homepage link
    ### Return:
    - User id

    # [示例/Example]
    url = ["https://www.tiktok.com/@tiktok"]
    """
    try:
        data = await TikTokWebCrawler.get_all_sec_user_id(url)
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
                       url: str = Query(
                           example="https://www.tiktok.com/@owlcitymusic/video/7218694761253735723",
                           description="作品链接/Video link")):
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
    url = "https://www.tiktok.com/@owlcitymusic/video/7218694761253735723"
    """
    try:
        data = await TikTokWebCrawler.get_aweme_id(url)
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
                               example=["https://www.tiktok.com/@owlcitymusic/video/7218694761253735723"],
                               description="作品链接/Video link")):
    """
    # [中文]
    ### 用途:
    - 提取列表作品id
    ### 参数:
    - url: 作品链接
    ### 返回:
    - 作品id

    # [English]
    ### Purpose:
    - Extract list video id
    ### Parameters:
    - url: Video link
    ### Return:
    - Video id

    # [示例/Example]
    url = ["https://www.tiktok.com/@owlcitymusic/video/7218694761253735723"]
    """
    try:
        data = await TikTokWebCrawler.get_all_aweme_id(url)
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


# 获取用户unique_id
@router.get("/get_unique_id",
            response_model=ResponseModel,
            summary="获取用户unique_id/Get user unique_id")
async def get_unique_id(request: Request,
                        url: str = Query(
                            example="https://www.tiktok.com/@tiktok",
                            description="用户主页链接/User homepage link")):
    """
    # [中文]
    ### 用途:
    - 获取用户unique_id
    ### 参数:
    - url: 用户主页链接
    ### 返回:
    - unique_id

    # [English]
    ### Purpose:
    - Get user unique_id
    ### Parameters:
    - url: User homepage link
    ### Return:
    - unique_id

    # [示例/Example]
    url = "https://www.tiktok.com/@tiktok"
    """
    try:
        data = await TikTokWebCrawler.get_unique_id(url)
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


# 获取列表unique_id列表
@router.post("/get_all_unique_id",
             response_model=ResponseModel,
             summary="获取列表unique_id/Get list unique_id")
async def get_all_unique_id(request: Request,
                            url: List[str] = Body(
                                example=["https://www.tiktok.com/@tiktok"],
                                description="用户主页链接/User homepage link")):
    """
    # [中文]
    ### 用途:
    - 获取列表unique_id
    ### 参数:
    - url: 用户主页链接
    ### 返回:
    - unique_id

    # [English]
    ### Purpose:
    - Get list unique_id
    ### Parameters:
    - url: User homepage link
    ### Return:
    - unique_id

    # [示例/Example]
    url = ["https://www.tiktok.com/@tiktok"]
    """
    try:
        data = await TikTokWebCrawler.get_all_unique_id(url)
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
