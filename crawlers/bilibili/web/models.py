import time
from pydantic import BaseModel


class BaseRequestsModel(BaseModel):
    wts: str = str(round(time.time()))


class UserPostVideos(BaseRequestsModel):
    dm_img_inter: str = '{"ds":[],"wh":[3557,5674,5],"of":[154,308,154]}'
    dm_img_list: list = []
    mid: str
    pn: int
    ps: str = "20"


class UserProfile(BaseRequestsModel):
    mid: str


class UserDynamic(BaseRequestsModel):
    host_mid: str
    offset: str
    wts: str = str(round(time.time()))


class ComPopular(BaseRequestsModel):
    pn: int
    ps: str = "20"
    web_location: str = "333.934"

    
class PlayUrl(BaseRequestsModel):
    qn: str
    fnval: str = '4048'
    bvid: str
    cid: str
    

