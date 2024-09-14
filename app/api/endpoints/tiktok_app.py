from fastapi import APIRouter, Query, Request, HTTPException  # 导入FastAPI组件
from app.api.models.APIResponseModel import ResponseModel, ErrorResponseModel  # 导入响应模型

from crawlers.tiktok.app.app_crawler import TikTokAPPCrawler  # 导入APP爬虫

router = APIRouter()
TikTokAPPCrawler = TikTokAPPCrawler()


# 获取单个作品数据
@router.get("/fetch_one_video",
            response_model=ResponseModel,
            summary="获取单个作品数据/Get single video data"
            )
async def fetch_one_video(request: Request,
                          aweme_id: str = Query(example="7350810998023949599", description="作品id/Video id")):
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
    aweme_id = "7350810998023949599"
    """
    try:
        data = await TikTokAPPCrawler.fetch_one_video(aweme_id)
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
    