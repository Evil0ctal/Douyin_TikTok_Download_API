from fastapi import APIRouter, Body, Query, Request, HTTPException  # 导入FastAPI组件
from app.api.models.APIResponseModel import ResponseModel, ErrorResponseModel  # 导入响应模型

from crawlers.bilibili.web.web_crawler import BilibiliWebCrawler  # 导入哔哩哔哩web爬虫


router = APIRouter()
BilibiliWebCrawler = BilibiliWebCrawler()


# 获取单个视频详情信息
@router.get("/fetch_one_video", response_model=ResponseModel, summary="获取单个视频详情信息/Get single video data")
async def fetch_one_video(request: Request,
                          bv_id: str = Query(example="BV1M1421t7hT", description="作品id/Video id")):
    """
    # [中文]
    ### 用途:
    - 获取单个视频详情信息
    ### 参数:
    - bv_id: 作品id
    ### 返回:
    - 视频详情信息

    # [English]
    ### Purpose:
    - Get single video data
    ### Parameters:
    - bv_id: Video id
    ### Return:
    - Video data

    # [示例/Example]
    bv_id = "BV1M1421t7hT"
    """
    try:
        data = await BilibiliWebCrawler.fetch_one_video(bv_id)
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


# 获取用户发布视频作品数据
@router.get("/fetch_user_post_videos", response_model=ResponseModel,
            summary="获取用户主页作品数据/Get user homepage video data")
async def fetch_user_post_videos(request: Request,
                                 uid: str = Query(example="178360345", description="用户UID"),
                                 pn: int = Query(default=1, description="页码/Page number"),):
    """
    # [中文]
    ### 用途:
    - 获取用户发布的视频数据
    ### 参数:
    - uid: 用户UID
    - pn: 页码
    ### 返回:
    - 用户发布的视频数据

    # [English]
    ### Purpose:
    - Get user post video data
    ### Parameters:
    - uid: User UID
    - pn: Page number
    ### Return:
    - User posted video data

    # [示例/Example]
    uid = "178360345"
    pn = 1
    """
    try:
        data = await BilibiliWebCrawler.fetch_user_post_videos(uid, pn)
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


# 获取用户所有收藏夹信息
@router.get("/fetch_collect_folders", response_model=ResponseModel,
            summary="获取用户所有收藏夹信息/Get user collection folders")
async def fetch_collect_folders(request: Request,
                                uid: str = Query(example="178360345", description="用户UID")):
    """
    # [中文]
    ### 用途:
    - 获取用户收藏作品数据
    ### 参数:
    - uid: 用户UID
    ### 返回:
    - 用户收藏夹信息

    # [English]
    ### Purpose:
    - Get user collection folders
    ### Parameters:
    - uid: User UID
    ### Return:
    - user collection folders

    # [示例/Example]
    uid = "178360345"
    """
    try:
        data = await BilibiliWebCrawler.fetch_collect_folders(uid)
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


# 获取指定收藏夹内视频数据
@router.get("/fetch_user_collection_videos", response_model=ResponseModel,
            summary="获取指定收藏夹内视频数据/Gets video data from a collection folder")
async def fetch_user_collection_videos(request: Request,
                                       folder_id: str = Query(example="1756059545",
                                                              description="收藏夹id/collection folder id"),
                                       pn: int = Query(default=1, description="页码/Page number")
                                       ):
    """
    # [中文]
    ### 用途:
    - 获取指定收藏夹内视频数据
    ### 参数:
    - folder_id: 用户UID
    - pn: 页码
    ### 返回:
    - 指定收藏夹内视频数据

    # [English]
    ### Purpose:
    - Gets video data from a collection folder
    ### Parameters:
    - folder_id: collection folder id
    - pn: Page number
    ### Return:
    - video data from collection folder

    # [示例/Example]
    folder_id = "1756059545"
    pn = 1
    """
    try:
        data = await BilibiliWebCrawler.fetch_folder_videos(folder_id, pn)
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
@router.get("/fetch_user_profile", response_model=ResponseModel,
            summary="获取指定用户的信息/Get information of specified user")
async def fetch_collect_folders(request: Request,
                                uid: str = Query(example="178360345", description="用户UID")):
    """
    # [中文]
    ### 用途:
    - 获取指定用户的信息
    ### 参数:
    - uid: 用户UID
    ### 返回:
    - 指定用户的个人信息

    # [English]
    ### Purpose:
    - Get information of specified user
    ### Parameters:
    - uid: User UID
    ### Return:
    - information of specified user

    # [示例/Example]
    uid = "178360345"
    """
    try:
        data = await BilibiliWebCrawler.fetch_user_profile(uid)
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


# 获取综合热门视频信息
@router.get("/fetch_com_popular", response_model=ResponseModel,
            summary="获取综合热门视频信息/Get comprehensive popular video information")
async def fetch_collect_folders(request: Request,
                                pn: int = Query(default=1, description="页码/Page number")):
    """
    # [中文]
    ### 用途:
    - 获取综合热门视频信息
    ### 参数:
    - pn: 页码
    ### 返回:
    - 综合热门视频信息

    # [English]
    ### Purpose:
    - Get comprehensive popular video information
    ### Parameters:
    - pn: Page number
    ### Return:
    - comprehensive popular video information

    # [示例/Example]
    pn = 1
    """
    try:
        data = await BilibiliWebCrawler.fetch_com_popular(pn)
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


# 获取指定视频的评论
@router.get("/fetch_video_comments", response_model=ResponseModel,
            summary="获取指定视频的评论/Get comments on the specified video")
async def fetch_collect_folders(request: Request,
                                bv_id: str = Query(example="BV1M1421t7hT", description="作品id/Video id"),
                                pn: int = Query(default=1, description="页码/Page number")):
    """
    # [中文]
    ### 用途:
    - 获取指定视频的评论
    ### 参数:
    - bv_id: 作品id
    - pn: 页码
    ### 返回:
    - 指定视频的评论数据

    # [English]
    ### Purpose:
    - Get comments on the specified video
    ### Parameters:
    - bv_id: Video id
    - pn: Page number
    ### Return:
    - comments of the specified video

    # [示例/Example]
    bv_id = "BV1M1421t7hT"
    pn = 1
    """
    try:
        data = await BilibiliWebCrawler.fetch_video_comments(bv_id, pn)
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


# 获取视频下指定评论的回复
@router.get("/fetch_comment_reply", response_model=ResponseModel,
            summary="获取视频下指定评论的回复/Get reply to the specified comment")
async def fetch_collect_folders(request: Request,
                                bv_id: str = Query(example="BV1M1421t7hT", description="作品id/Video id"),
                                pn: int = Query(default=1, description="页码/Page number"),
                                rpid: str = Query(example="237109455120", description="回复id/Reply id")):
    """
    # [中文]
    ### 用途:
    - 获取视频下指定评论的回复
    ### 参数:
    - bv_id: 作品id
    - pn: 页码
    - rpid: 回复id
    ### 返回:
    - 指定评论的回复数据

    # [English]
    ### Purpose:
    - Get reply to the specified comment
    ### Parameters:
    - bv_id: Video id
    - pn: Page number
    - rpid: Reply id
    ### Return:
    - Reply of the specified comment

    # [示例/Example]
    bv_id = "BV1M1421t7hT"
    pn = 1
    rpid = "237109455120"
    """
    try:
        data = await BilibiliWebCrawler.fetch_comment_reply(bv_id, pn, rpid)
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


# 获取指定用户动态
@router.get("/fetch_user_dynamic", response_model=ResponseModel,
            summary="获取指定用户动态/Get dynamic information of specified user")
async def fetch_collect_folders(request: Request,
                                uid: str = Query(example="16015678", description="用户UID"),
                                offset: str = Query(default="", example="953154282154098691",
                                                    description="开始索引/offset")):
    """
    # [中文]
    ### 用途:
    - 获取指定用户动态
    ### 参数:
    - uid: 用户UID
    - offset: 开始索引
    ### 返回:
    - 指定用户动态数据

    # [English]
    ### Purpose:
    - Get dynamic information of specified user
    ### Parameters:
    - uid: User UID
    - offset: offset
    ### Return:
    - dynamic information of specified user

    # [示例/Example]
    uid = "178360345"
    offset = "953154282154098691"
    """
    try:
        data = await BilibiliWebCrawler.fetch_user_dynamic(uid, offset)
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


# 获取指定直播间信息
@router.get("/fetch_live_room_detail", response_model=ResponseModel,
            summary="获取指定直播间信息/Get information of specified live room")
async def fetch_collect_folders(request: Request,
                                room_id: str = Query(example="22816111", description="直播间ID/Live room ID")):
    """
    # [中文]
    ### 用途:
    - 获取指定直播间信息
    ### 参数:
    - room_id: 直播间ID
    ### 返回:
    - 指定直播间信息

    # [English]
    ### Purpose:
    - Get information of specified live room
    ### Parameters:
    - room_id: Live room ID
    ### Return:
    - information of specified live room

    # [示例/Example]
    room_id = "22816111"
    """
    try:
        data = await BilibiliWebCrawler.fetch_live_room_detail(room_id)
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


# # 获取指定直播间视频流
# @router.get("/fetch_live_videos", response_model=ResponseModel,
#             summary="获取直播间视频流/Get live video data of specified room")
# async def fetch_collect_folders(request: Request,
#                                 room_id: str = Query(example="22816111", description="直播间ID/Live room ID")):
#     """
#     # [中文]
#     ### 用途:
#     - 获取指定直播间视频流
#     ### 参数:
#     - room_id: 直播间ID
#     ### 返回:
#     - 指定直播间视频流
#
#     # [English]
#     ### Purpose:
#     - Get live video data of specified room
#     ### Parameters:
#     - room_id: Live room ID
#     ### Return:
#     - live video data of specified room
#
#     # [示例/Example]
#     room_id = "22816111"
#     """
#     try:
#         data = await BilibiliWebCrawler.fetch_live_videos(room_id)
#         return ResponseModel(code=200,
#                              router=request.url.path,
#                              data=data)
#     except Exception as e:
#         status_code = 400
#         detail = ErrorResponseModel(code=status_code,
#                                     router=request.url.path,
#                                     params=dict(request.query_params),
#                                     )
#         raise HTTPException(status_code=status_code, detail=detail.dict())


# 获取所有直播分区列表
@router.get("/fetch_all_live_areas", response_model=ResponseModel,
            summary="获取所有直播分区列表/Get a list of all live areas")
async def fetch_collect_folders(request: Request,):
    """
    # [中文]
    ### 用途:
    - 获取所有直播分区列表
    ### 参数:
    ### 返回:
    - 所有直播分区列表

    # [English]
    ### Purpose:
    - Get a list of all live areas
    ### Parameters:
    ### Return:
    - list of all live areas

    # [示例/Example]
    """
    try:
        data = await BilibiliWebCrawler.fetch_all_live_areas()
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
