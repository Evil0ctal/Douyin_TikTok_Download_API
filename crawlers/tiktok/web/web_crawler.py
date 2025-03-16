# ==============================================================================
# Copyright (C) 2021 Evil0ctal
#
# This file is part of the Douyin_TikTok_Download_API project.
#
# This project is licensed under the Apache License 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================
#
# Contributor Link:
# - https://github.com/Evil0ctal
# - https://github.com/Johnserf-Seed
#
# ==============================================================================


import asyncio  # 异步I/O
import time  # 时间操作
import yaml  # 配置文件
import os  # 系统操作

# 基础爬虫客户端和TikTokAPI端点
from crawlers.base_crawler import BaseCrawler
from crawlers.tiktok.web.endpoints import TikTokAPIEndpoints
from crawlers.utils.utils import extract_valid_urls

# TikTok加密参数生成器
from crawlers.tiktok.web.utils import (
    AwemeIdFetcher,
    BogusManager,
    SecUserIdFetcher,
    TokenManager
)

# TikTok接口数据请求模型
from crawlers.tiktok.web.models import (
    UserProfile,
    UserPost,
    UserLike,
    UserMix,
    UserCollect,
    PostDetail,
    UserPlayList,
    PostComment,
    PostCommentReply,
    UserFans,
    UserFollow
)


# 配置文件路径
path = os.path.abspath(os.path.dirname(__file__))

# 读取配置文件
with open(f"{path}/config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)


class TikTokWebCrawler:

    def __init__(self):
        self.proxy_pool = None

    # 从配置文件中获取TikTok的请求头
    async def get_tiktok_headers(self):
        tiktok_config = config["TokenManager"]["tiktok"]
        kwargs = {
            "headers": {
                "User-Agent": tiktok_config["headers"]["User-Agent"],
                "Referer": tiktok_config["headers"]["Referer"],
                "Cookie": tiktok_config["headers"]["Cookie"],
            },
            "proxies": {"http://": tiktok_config["proxies"]["http"],
                        "https://": tiktok_config["proxies"]["https"]}
        }
        return kwargs

    """-------------------------------------------------------handler接口列表-------------------------------------------------------"""

    # 获取单个作品数据
    async def fetch_one_video(self, itemId: str):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个作品详情的BaseModel参数
            params = PostDetail(itemId=itemId)
            # 生成一个作品详情的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.POST_DETAIL, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的个人信息
    async def fetch_user_profile(self, secUid: str, uniqueId: str):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户详情的BaseModel参数
            params = UserProfile(secUid=secUid, uniqueId=uniqueId)
            # 生成一个用户详情的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_DETAIL, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的作品列表
    async def fetch_user_post(self, secUid: str, cursor: int = 0, count: int = 35, coverFormat: int = 2):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # proxies = {"http://": 'http://43.159.29.191:24144', "https://": 'http://43.159.29.191:24144'}
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户作品的BaseModel参数
            params = UserPost(secUid=secUid, cursor=cursor, count=count, coverFormat=coverFormat)
            # 生成一个用户作品的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_POST, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的点赞列表
    async def fetch_user_like(self, secUid: str, cursor: int = 0, count: int = 30, coverFormat: int = 2):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户点赞的BaseModel参数
            params = UserLike(secUid=secUid, cursor=cursor, count=count, coverFormat=coverFormat)
            # 生成一个用户点赞的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_LIKE, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的收藏列表
    async def fetch_user_collect(self, cookie: str, secUid: str, cursor: int = 0, count: int = 30,
                                 coverFormat: int = 2):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        kwargs["headers"]["Cookie"] = cookie
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户收藏的BaseModel参数
            params = UserCollect(cookie=cookie, secUid=secUid, cursor=cursor, count=count, coverFormat=coverFormat)
            # 生成一个用户收藏的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_COLLECT, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的播放列表
    async def fetch_user_play_list(self, secUid: str, cursor: int = 0, count: int = 30):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户播放列表的BaseModel参数
            params = UserPlayList(secUid=secUid, cursor=cursor, count=count)
            # 生成一个用户播放列表的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_PLAY_LIST, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的合辑列表
    async def fetch_user_mix(self, mixId: str, cursor: int = 0, count: int = 30):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户合辑的BaseModel参数
            params = UserMix(mixId=mixId, cursor=cursor, count=count)
            # 生成一个用户合辑的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_MIX, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取作品的评论列表
    async def fetch_post_comment(self, aweme_id: str, cursor: int = 0, count: int = 20, current_region: str = ""):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # proxies = {"http://": 'http://43.159.18.174:25263', "https://": 'http://43.159.18.174:25263'}
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个作品评论的BaseModel参数
            params = PostComment(aweme_id=aweme_id, cursor=cursor, count=count, current_region=current_region)
            # 生成一个作品评论的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.POST_COMMENT, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取作品的评论回复列表
    async def fetch_post_comment_reply(self, item_id: str, comment_id: str, cursor: int = 0, count: int = 20,
                                       current_region: str = ""):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个作品评论的BaseModel参数
            params = PostCommentReply(item_id=item_id, comment_id=comment_id, cursor=cursor, count=count,
                                      current_region=current_region)
            # 生成一个作品评论的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.POST_COMMENT_REPLY, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的粉丝列表
    async def fetch_user_fans(self, secUid: str, count: int = 30, maxCursor: int = 0, minCursor: int = 0):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户关注的BaseModel参数
            params = UserFans(secUid=secUid, count=count, maxCursor=maxCursor, minCursor=minCursor)
            # 生成一个用户关注的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_FANS, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户的关注列表
    async def fetch_user_follow(self, secUid: str, count: int = 30, maxCursor: int = 0, minCursor: int = 0):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建一个用户关注的BaseModel参数
            params = UserFollow(secUid=secUid, count=count, maxCursor=maxCursor, minCursor=minCursor)
            # 生成一个用户关注的带有加密参数的Endpoint
            endpoint = BogusManager.model_2_endpoint(
                TikTokAPIEndpoints.USER_FOLLOW, params.dict(), kwargs["headers"]["User-Agent"]
            )
            response = await crawler.fetch_get_json(endpoint)
        return response

    """-------------------------------------------------------utils接口列表-------------------------------------------------------"""

    # 生成真实msToken
    async def fetch_real_msToken(self):
        result = {
            "msToken": TokenManager().gen_real_msToken()
        }
        return result

    # 生成ttwid
    async def gen_ttwid(self, cookie: str):
        result = {
            "ttwid": TokenManager().gen_ttwid(cookie)
        }
        return result

    # 生成xbogus
    async def gen_xbogus(self, url: str, user_agent: str):
        url = BogusManager.xb_str_2_endpoint(user_agent, url)
        result = {
            "url": url,
            "x_bogus": url.split("&X-Bogus=")[1],
            "user_agent": user_agent
        }
        return result

    # 提取单个用户id
    async def get_sec_user_id(self, url: str):
        return await SecUserIdFetcher.get_secuid(url)

    # 提取列表用户id
    async def get_all_sec_user_id(self, urls: list):
        # 提取有效URL
        urls = extract_valid_urls(urls)

        # 对于URL列表
        return await SecUserIdFetcher.get_all_secuid(urls)

    # 提取单个作品id
    async def get_aweme_id(self, url: str):
        return await AwemeIdFetcher.get_aweme_id(url)

    # 提取列表作品id
    async def get_all_aweme_id(self, urls: list):
        # 提取有效URL
        urls = extract_valid_urls(urls)

        # 对于URL列表
        return await AwemeIdFetcher.get_all_aweme_id(urls)

    # 获取用户unique_id
    async def get_unique_id(self, url: str):
        return await SecUserIdFetcher.get_uniqueid(url)

    # 获取列表unique_id列表
    async def get_all_unique_id(self, urls: list):
        # 提取有效URL
        urls = extract_valid_urls(urls)

        # 对于URL列表
        return await SecUserIdFetcher.get_all_uniqueid(urls)

    """-------------------------------------------------------main接口列表-------------------------------------------------------"""

    async def main(self):
        # 获取单个作品数据
        # item_id = "7369296852669205791"
        # response = await self.fetch_one_video(item_id)
        # print(response)

        # 获取用户的个人信息
        # secUid = "MS4wLjABAAAAfDPs6wbpBcMMb85xkvDGdyyyVAUS2YoVCT9P6WQ1bpuwEuPhL9eFtTmGvxw1lT2C"
        # uniqueId = "c4shjaz"
        # response = await self.fetch_user_profile(secUid, uniqueId)
        # print(response)

        # 获取用户的作品列表
        # secUid = "MS4wLjABAAAAfDPs6wbpBcMMb85xkvDGdyyyVAUS2YoVCT9P6WQ1bpuwEuPhL9eFtTmGvxw1lT2C"
        # cursor = 0
        # count = 35
        # coverFormat = 2
        # response = await self.fetch_user_post(secUid, cursor, count, coverFormat)
        # print(response)

        # 获取用户的点赞列表
        # secUid = "MS4wLjABAAAAq1iRXNduFZpY301UkVpJ1eQT60_NiWS9QQSeNqmNQEDJp0pOF8cpleNEdiJx5_IU"
        # cursor = 0
        # count = 30
        # coverFormat = 2
        # response = await self.fetch_user_like(secUid, cursor, count, coverFormat)
        # print(response)

        # 获取用户的收藏列表
        # cookie = "put your cookie here"
        # secUid = "MS4wLjABAAAAq1iRXNduFZpY301UkVpJ1eQT60_NiWS9QQSeNqmNQEDJp0pOF8cpleNEdiJx5_IU"
        # cursor = 0
        # count = 30
        # coverFormat = 2
        # response = await self.fetch_user_collect(cookie, secUid, cursor, count, coverFormat)
        # print(response)

        # 获取用户的播放列表
        # secUid = "MS4wLjABAAAAtGboV-mJHSIQqh-SsG30QKweGhSqkr4xJLq1qqgAWDzu3vDO5LUhUcCP4UEY5LwC"
        # cursor = 0
        # count = 30
        # response = await self.fetch_user_play_list(secUid, cursor, count)
        # print(response)

        # 获取用户的合辑列表
        # mixId = "7101538765474106158"
        # cursor = 0
        # count = 30
        # response = await self.fetch_user_mix(mixId, cursor, count)
        # print(response)

        # 获取作品的评论列表
        # aweme_id = "7304809083817774382"
        # cursor = 0
        # count = 20
        # current_region = ""
        # response = await self.fetch_post_comment(aweme_id, cursor, count, current_region)
        # print(response)

        # 获取作品的评论回复列表
        # item_id = "7304809083817774382"
        # comment_id = "7304877760886588191"
        # cursor = 0
        # count = 20
        # current_region = ""
        # response = await self.fetch_post_comment_reply(item_id, comment_id, cursor, count, current_region)
        # print(response)

        # 获取用户的关注列表
        # secUid = "MS4wLjABAAAAtGboV-mJHSIQqh-SsG30QKweGhSqkr4xJLq1qqgAWDzu3vDO5LUhUcCP4UEY5LwC"
        # count = 30
        # maxCursor = 0
        # minCursor = 0
        # response = await self.fetch_user_follow(secUid, count, maxCursor, minCursor)
        # print(response)

        # 获取用户的粉丝列表
        # secUid = "MS4wLjABAAAAtGboV-mJHSIQqh-SsG30QKweGhSqkr4xJLq1qqgAWDzu3vDO5LUhUcCP4UEY5LwC"
        # count = 30
        # maxCursor = 0
        # minCursor = 0
        # response = await self.fetch_user_fans(secUid, count, maxCursor, minCursor)
        # print(response)

        """-------------------------------------------------------utils接口列表-------------------------------------------------------"""

        # # 生成真实msToken
        # response = await self.fetch_real_msToken()
        # print(response)

        # 生成ttwid
        # cookie = "put your cookie here"
        # response = await self.gen_ttwid(cookie)
        # print(response)

        # 生成xbogus
        # url = "https://www.tiktok.com/api/item/detail/?WebIdLastTime=1712665533&aid=1988&app_language=en&app_name=tiktok_web&browser_language=en-US&browser_name=Mozilla&browser_online=true&browser_platform=Win32&browser_version=5.0%20%28Windows%29&channel=tiktok_web&cookie_enabled=true&device_id=7349090360347690538&device_platform=web_pc&focus_state=true&from_page=user&history_len=4&is_fullscreen=false&is_page_visible=true&language=en&os=windows&priority_region=US&referer=&region=US&root_referer=https%3A%2F%2Fwww.tiktok.com%2F&screen_height=1080&screen_width=1920&webcast_language=en&tz_name=America%2FTijuana&msToken=AYFCEapCLbMrS8uTLBoYdUMeeVLbCdFQ_QF_-OcjzJw1CPr4JQhWUtagy0k4a9IITAqi5Qxr2Vdh9mgCbyGxTnvWLa4ZVY6IiSf6lcST-tr0IXfl-r_ZTpzvWDoQfqOVsWCTlSNkhAwB-tap5g==&itemId=7339393672959757570"
        # user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        # response = await self.gen_xbogus(url, user_agent)
        # print(response)

        # 提取单个用户secUid
        # url = "https://www.tiktok.com/@tiktok"
        # response = await self.get_sec_user_id(url)
        # print(response)

        # 提取多个用户secUid
        # urls = ["https://www.tiktok.com/@tiktok", "https://www.tiktok.com/@taylorswift"]
        # response = await self.get_all_sec_user_id(urls)
        # print(response)

        # 提取单个作品id
        # url = "https://www.tiktok.com/@taylorswift/video/7162153915952352558"
        # response = await self.get_aweme_id(url)
        # print(response)

        # 提取多个作品id
        # urls = ["https://www.tiktok.com/@taylorswift/video/7162153915952352558", "https://www.tiktok.com/@taylorswift/video/7137077445680745771"]
        # response = await self.get_all_aweme_id(urls)
        # print(response)

        # 获取用户unique_id
        # url = "https://www.tiktok.com/@tiktok"
        # response = await self.get_unique_id(url)
        # print(response)

        # 获取多个用户unique_id
        # urls = ["https://www.tiktok.com/@tiktok", "https://www.tiktok.com/@taylorswift"]
        # response = await self.get_all_unique_id(urls)
        # print(response)

        # 占位
        pass


if __name__ == "__main__":
    # 初始化
    TikTokWebCrawler = TikTokWebCrawler()

    # 开始时间
    start = time.time()

    asyncio.run(TikTokWebCrawler.main())

    # 结束时间
    end = time.time()
    print(f"耗时：{end - start}")
