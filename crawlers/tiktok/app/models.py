import time
from typing import List

from pydantic import BaseModel


# API基础请求模型/Base Request Model
class BaseRequestModel(BaseModel):
    """
    Base Request Model for TikTok API
    """
    iid: int = 7318518857994389254
    device_id: int = 7318517321748022790
    channel: str = "googleplay"
    app_name: str = "musical_ly"
    version_code: str = "300904"
    device_platform: str = "android"
    device_type: str = "SM-ASUS_Z01QD"
    os_version: str = "9"


# Feed视频详情请求模型/Feed Video Detail Request Model
class FeedVideoDetail(BaseRequestModel):
    """
    Feed Video Detail Request Model
    """
    aweme_id: str
