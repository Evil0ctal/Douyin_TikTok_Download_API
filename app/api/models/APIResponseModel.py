from fastapi import Body, FastAPI, Query, Request, HTTPException
from pydantic import BaseModel
from typing import Any, Callable, Type, Optional, Dict
from functools import wraps
import datetime

app = FastAPI()


# 定义响应模型
class ResponseModel(BaseModel):
    code: int = 200
    router: str = "Endpoint path"
    data: Optional[Any] = {}


# 定义错误响应模型
class ErrorResponseModel(BaseModel):
    code: int = 400
    message: str = "An error occurred."
    support: str = "Please contact us on Github: https://github.com/Evil0ctal/Douyin_TikTok_Download_API"
    time: str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    router: str
    params: dict = {}


# 混合解析响应模型
class HybridResponseModel(BaseModel):
    code: int = 200
    router: str = "Hybrid parsing single video endpoint"
    data: Optional[Any] = {}


# iOS_Shortcut响应模型
class iOS_Shortcut(BaseModel):
    version: str
    update: str
    link: str
    link_en: str
    note: str
    note_en: str
