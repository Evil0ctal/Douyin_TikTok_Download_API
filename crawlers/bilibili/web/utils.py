import time
from urllib.parse import urlencode
import random
from crawlers.bilibili.web import wrid
from crawlers.utils.logger import logger

# 装饰器 检查是否正确生成endpoint
def Check_gen(func):
    def checker(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            raise RuntimeError("生成w_rid失败:{0}, 函数地址:{1}".format(e, func.__name__))

    return checker

class EndpointModels:
    def __init__(self):
        # 实例化WridManager
        self.wridmanager = WridManager()
        # 当前时间戳
        self.wts = round(time.time())
        # 固定inter也能获得结果。如果失效见--WridManager().get_inter
        self.inter = '{"ds":[],"wh":[3557,5674,5],"of":[154,308,154]}'

    # 获取wrid示例 通过uid 生成包含w_rid和wts的字典
    @Check_gen
    async def get_wrid_wts_by_uid(self, uid: str) -> dict:
        params = {
            'dm_cover_img_str': 'QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ',
            'dm_img_inter': self.inter,
            'dm_img_list': [],
            'dm_img_str': 'V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ',
            'mid': uid,
            'platform': 'web',
            'token': '',
            'web_location': '1550101',
            'wts': f'{self.wts}ea1db124af3c7062474693fa704f4ff8'
        }
        # 获取w_rid参数
        w_rid = await self.wridmanager.get_wrid(params=params)
        reslut = {
            "w_rid": w_rid,
            "wts": self.wts
        }
        return reslut

    # 获取用户发布视频作品数据 生成enpoint
    @Check_gen
    async def user_post_videos_endpoint(self, uid: str, pn: int, ps: int = 30) -> str:
        # 编码inter
        new_inter = self.inter.replace(" ", "").replace('{', "%7B").replace("'", "%22").replace("}", "%7D")
        # 构建请求参数
        params = {
            "dm_cover_img_str": "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ",
            "dm_img_inter": self.inter,
            "dm_img_list": [],
            "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
            "keyword": "",
            "mid": uid,
            "order": "pubdate",
            "order_avoided": "true",
            "platform": "web",
            "pn": pn,
            "ps": ps,
            "tid": "0",
            "web_location": "1550101",
            "wts": f"{self.wts}ea1db124af3c7062474693fa704f4ff8",
        }
        # 获取wrid
        w_rid = await self.wridmanager.get_wrid(params=params)
        # 将上面结果拼接成最终结果并返回
        final_endpoint = f'https://api.bilibili.com/x/space/wbi/arc/search?mid={uid}&ps={ps}&tid=0&pn={pn}&keyword=&order=pubdate&platform=web&web_location=1550101&order_avoided=true&dm_img_list=[]&dm_img_str=V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ&dm_cover_img_str=QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ&dm_img_inter={new_inter}&w_rid={w_rid}&wts={self.wts}'
        return final_endpoint

    # 获取指定用户的信息 生成enpoint
    @Check_gen
    async def user_profile_endpoint(self, uid: str) -> str:
        # 编码inter
        new_inter = self.inter.replace(" ", "").replace('{', "%7B").replace("'", "%22").replace("}", "%7D")
        # 构建请求参数
        params = {
            'dm_cover_img_str': 'QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ',
            'dm_img_inter': self.inter,
            'dm_img_list': [],
            'dm_img_str': 'V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ',
            'mid': uid,
            'platform': 'web',
            'token': '',
            'web_location': '1550101',
            'wts': f'{self.wts}ea1db124af3c7062474693fa704f4ff8'
        }
        # 获取wrid
        w_rid = await self.wridmanager.get_wrid(params=params)
        # 将上面结果拼接成最终字符串并返回
        final_endpoint = f'https://api.bilibili.com/x/space/wbi/acc/info?mid={uid}&token=&platform=web&web_location=1550101&dm_img_list=[]&dm_img_str=V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ&dm_cover_img_str=QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ&dm_img_inter={new_inter}&w_rid={w_rid}&wts={self.wts}'
        return final_endpoint

    # 获取综合热门视频信息 生成enpoint
    @Check_gen
    async def com_popular_endpoint(self, pn: int) -> str:
        # 构建请求参数
        params = {
            "pn": pn,
            "ps": "20",
            "web_location": "333.934",
            "wts": f"{self.wts}ea1db124af3c7062474693fa704f4ff8",
        }
        # 获取wrid
        w_rid = await self.wridmanager.get_wrid(params=params)
        # 将上面结果拼接成最终结果并返回
        final_endpoint = f"https://api.bilibili.com/x/web-interface/popular?ps=20&pn={pn}&web_location=333.934&w_rid={w_rid}&wts={self.wts}"
        return final_endpoint

    # 获取指定用户动态
    @Check_gen
    async def user_dynamic_endpoint(self, uid: str, offset: str):
        # 编码inter
        new_inter = self.inter.replace(" ", "").replace('{', "%7B").replace("'", "%22").replace("}", "%7D")
        # 构建请求参数
        params = {
            "dm_cover_img_str": "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ",
            "dm_img_inter": self.inter,
            "dm_img_list": [],
            "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ&features=itemOpusStyle%2ClistOnlyfans%2CopusBigCover%2ConlyfansVote%2CdecorationCard%2CforwardListHidden%2CugcDelete",
            "host_mid": uid,
            "offset": offset,
            "platform": "web",
            "timezone_offset": "-480",
            "web_location": "333.999",
            "wts": self.wts,
            "x-bili-device-req-json": "%7B%22platform%22%3A%22web%22%2C%22device%22%3A%22pc%22%7D",
            "x-bili-web-req-json": "%7B%22spm_id%22%3A%22333.999%22%7Dea1db124af3c7062474693fa704f4ff8"
        }
        # 获取wrid
        w_rid = await self.wridmanager.get_wrid(params=params)
        # 将上面结果拼接成最终结果并返回
        final_endpoint = f'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?offset={offset}&host_mid={uid}&timezone_offset=-480&platform=web&features=itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,decorationCard,forwardListHidden,ugcDelete&web_location=333.999&dm_img_list=[]&dm_img_str=V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ&dm_cover_img_str=QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDMwNTAgTGFwdG9wIEdQVSAoMHgwMDAwMjVBMikgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ&dm_img_inter={new_inter}&x-bili-device-req-json=%7B%22platform%22:%22web%22,%22device%22:%22pc%22%7D&x-bili-web-req-json=%7B%22spm_id%22:%22333.999%22%7D&w_rid={w_rid}&wts={self.wts}'
        return final_endpoint


class WridManager:

    def s(self) -> list:
        x = random.randint(0, 113)
        return [2 * 1488 + 2 * 311 + 3 * x, 4 * 1488 - 311 + x, x]

    def d(self) -> list:
        x = random.randint(0, 513)
        return [x, 2 * x, x]

    def get_inter(self) -> dict:
        return {"ds": [], "wh": self.s(), "of": self.d()}

    async def get_encode_query(self, params: dict) -> str:
        params = dict(sorted(params.items()))  # 按照 key 重排参数
        # 过滤 value 中的 "!'()*" 字符
        params = {
            k: ''.join(filter(lambda chr: chr not in "!'()*", str(v)))
            for k, v
            in params.items()
        }
        query = urlencode(params)  # 序列化参数
        return query

    async def get_wrid(self, params: dict) -> str:
        encode_query = await self.get_encode_query(params)
        # 获取w_rid参数
        w_rid = wrid.get_wrid(e=encode_query)
        return w_rid

async def bv2av(bv_id:str) -> int:
    table = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
    s = [11, 10, 3, 8, 4, 6, 2, 9, 5, 7]
    xor = 177451812
    add_105 = 8728348608
    add_all = 8728348608 - (2 ** 31 - 1) - 1
    tr = [0] * 128
    for i in range(58):
        tr[ord(table[i])] = i
    r = 0
    for i in range(6):
        r += tr[ord(bv_id[s[i]])] * (58 ** i)
    add = add_105
    if r < add:
        add = add_all
    aid = (r - add) ^ xor
    return aid


# 响应分析
class ResponseAnalyzer:
    # 用户收藏夹信息
    @classmethod
    async def collect_folders_analyze(cls, response: dict) -> dict:
        if response['data']:
            return response
        else:
            logger.warning("该用户收藏夹为空/用户设置为不可见")
            return {"code": 1, "message": "该用户收藏夹为空/用户设置为不可见"}
