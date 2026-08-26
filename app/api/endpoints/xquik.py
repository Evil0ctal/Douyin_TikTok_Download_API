from fastapi import APIRouter, Header, HTTPException, Path, Request

from app.api.models.APIResponseModel import ResponseModel
from crawlers.xquik.xquik_crawler import XquikCrawler, XquikError

router = APIRouter()
xquik_crawler = XquikCrawler()


@router.get(
    "/tweets/{tweet_id}",
    response_model=ResponseModel,
    summary="获取 X 帖子/Get X post",
)
async def get_x_post(
    request: Request,
    tweet_id: str = Path(
        example="1893456789012345678",
        description="X 帖子数字 ID/Numeric X post ID",
    ),
    x_api_key: str = Header(
        alias="x-api-key",
        description="调用者自己的 Xquik API 密钥/Caller's Xquik API key",
    ),
):
    """Return X post text, author, metrics, and media through Xquik."""
    try:
        data = await xquik_crawler.fetch_tweet(tweet_id, api_key=x_api_key)
    except XquikError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.status_code,
                "router": request.url.path,
                "message": str(exc),
            },
        ) from exc

    return ResponseModel(code=200, router=request.url.path, data=data)
