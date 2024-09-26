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
#
# - https://github.com/Koyomi781
#
# ==============================================================================

import asyncio  # 异步I/O
import os  # 系统操作
import time  # 时间操作
import yaml  # 配置文件

# 基础爬虫客户端和哔哩哔哩API端点
from crawlers.base_crawler import BaseCrawler
from crawlers.bilibili.web.endpoints import BilibiliAPIEndpoints
# 哔哩哔哩工具类
from crawlers.bilibili.web.utils import EndpointGenerator, bv2av, ResponseAnalyzer
# 数据请求模型
from crawlers.bilibili.web.models import UserPostVideos, UserProfile, ComPopular, UserDynamic, PlayUrl

# 配置文件路径
path = os.path.abspath(os.path.dirname(__file__))

# 读取配置文件
with open(f"{path}/config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)


class BilibiliWebCrawler:

    # 从配置文件读取哔哩哔哩请求头
    async def get_bilibili_headers(self):
        bili_config = config['TokenManager']['bilibili']
        kwargs = {
            "headers": {
                "accept-language": bili_config["headers"]["accept-language"],
                "origin": bili_config["headers"]["origin"],
                "referer": bili_config["headers"]["referer"],
                "user-agent": bili_config["headers"]["user-agent"],
                "cookie": bili_config["headers"]["cookie"],
            },
            "proxies": {"http://": bili_config["proxies"]["http"], "https://": bili_config["proxies"]["https"]},
        }
        return kwargs

    "-------------------------------------------------------handler接口列表-------------------------------------------------------"

    # 获取单个视频详情信息
    async def fetch_one_video(self, bv_id: str) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.POST_DETAIL}?bvid={bv_id}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取视频流地址
    async def fetch_video_playurl(self, bv_id: str, cid: str, qn: str = "64") -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 通过模型生成基本请求参数
            params = PlayUrl(bvid=bv_id, cid=cid, qn=qn)
            # 创建请求endpoint
            generator = EndpointGenerator(params.dict())
            endpoint = await generator.video_playurl_endpoint()
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户发布视频作品数据
    async def fetch_user_post_videos(self, uid: str, pn: int) -> dict:
        """
        :param uid: 用户uid
        :param pn: 页码
        :return:
        """
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 通过模型生成基本请求参数
            params = UserPostVideos(mid=uid, pn=pn)
            # 创建请求endpoint
            generator = EndpointGenerator(params.dict())
            endpoint = await generator.user_post_videos_endpoint()
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取用户所有收藏夹信息
    async def fetch_collect_folders(self, uid: str) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.COLLECT_FOLDERS}?up_mid={uid}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        # 分析响应结果
        result_dict = await ResponseAnalyzer.collect_folders_analyze(response=response)
        return result_dict

    # 获取指定收藏夹内视频数据
    async def fetch_folder_videos(self, folder_id: str, pn: int) -> dict:
        """
        :param folder_id: 收藏夹id-- 可从<获取用户所有收藏夹信息>获得
        :param pn: 页码
        :return:
        """
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        # 发送请求，获取请求响应结果
        async with base_crawler as crawler:
            endpoint = f"{BilibiliAPIEndpoints.COLLECT_VIDEOS}?media_id={folder_id}&pn={pn}&ps=20&keyword=&order=mtime&type=0&tid=0&platform=web"
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取指定用户的信息
    async def fetch_user_profile(self, uid: str) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 通过模型生成基本请求参数
            params = UserProfile(mid=uid)
            # 创建请求endpoint
            generator = EndpointGenerator(params.dict())
            endpoint = await generator.user_profile_endpoint()
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取综合热门视频信息
    async def fetch_com_popular(self, pn: int) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 通过模型生成基本请求参数
            params = ComPopular(pn=pn)
            # 创建请求endpoint
            generator = EndpointGenerator(params.dict())
            endpoint = await generator.com_popular_endpoint()
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取指定视频的评论
    async def fetch_video_comments(self, bv_id: str, pn: int) -> dict:
        # 评论排序 -- 1:按点赞数排序. 0:按时间顺序排序
        sort = 1
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.VIDEO_COMMENTS}?type=1&oid={bv_id}&sort={sort}&nohot=0&ps=20&pn={pn}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取视频下指定评论的回复
    async def fetch_comment_reply(self, bv_id: str, pn: int, rpid: str) -> dict:
        """
        :param bv_id: 目标视频bv号
        :param pn: 页码
        :param rpid: 目标评论id，可通过fetch_video_comments获得
        :return:
        """
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.COMMENT_REPLY}?type=1&oid={bv_id}&root={rpid}&&ps=20&pn={pn}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
            return response

    # 获取指定用户动态
    async def fetch_user_dynamic(self, uid: str, offset: str) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 通过模型生成基本请求参数
            params = UserDynamic(host_mid=uid, offset=offset)
            # 创建请求endpoint
            generator = EndpointGenerator(params.dict())
            endpoint = await generator.user_dynamic_endpoint()
            print(endpoint)
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取视频实时弹幕
    async def fetch_video_danmaku(self, cid: str):
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"https://comment.bilibili.com/{cid}.xml"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_response(endpoint)
        return response.text

    # 获取指定直播间信息
    async def fetch_live_room_detail(self, room_id: str) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.LIVEROOM_DETAIL}?room_id={room_id}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取指定直播间视频流
    async def fetch_live_videos(self, room_id: str) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.LIVE_VIDEOS}?cid={room_id}&quality=4"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取指定分区正在直播的主播
    async def fetch_live_streamers(self, area_id: str, pn: int):
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.LIVE_STREAMER}?platform=web&parent_area_id={area_id}&page={pn}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    "-------------------------------------------------------utils接口列表-------------------------------------------------------"

    # 通过bv号获得视频aid号
    async def bv_to_aid(self, bv_id: str) -> int:
        aid = await bv2av(bv_id=bv_id)
        return aid

    # 通过bv号获得视频分p信息
    async def fetch_video_parts(self, bv_id: str) -> str:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = f"{BilibiliAPIEndpoints.VIDEO_PARTS}?bvid={bv_id}"
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    # 获取所有直播分区列表
    async def fetch_all_live_areas(self) -> dict:
        # 获取请求头信息
        kwargs = await self.get_bilibili_headers()
        # 创建基础爬虫对象
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            # 创建请求endpoint
            endpoint = BilibiliAPIEndpoints.LIVE_AREAS
            # 发送请求，获取请求响应结果
            response = await crawler.fetch_get_json(endpoint)
        return response

    "-------------------------------------------------------main-------------------------------------------------------"

    async def main(self):
        """-------------------------------------------------------handler接口列表-------------------------------------------------------"""

        # 获取单个作品数据
        # bv_id = 'BV1M1421t7hT'
        # result = await self.fetch_one_video(bv_id=bv_id)
        # print(result)

        # 获取视频流地址
        # bv_id = 'BV1y7411Q7Eq'
        # cid = '171776208'
        # result = await self.fetch_video_playurl(bv_id=bv_id, cid=cid)
        # print(result)

        # 获取用户发布作品数据
        # uid = '94510621'
        # pn = 1
        # result = await self.fetch_user_post_videos(uid=uid, pn=pn)
        # print(result)

        # 获取用户所有收藏夹信息
        # uid = '178360345'
        # reslut = await self.fetch_collect_folders(uid=uid)
        # print(reslut)

        # 获取用户指定收藏夹内视频数据
        # folder_id = '1756059545'  # 收藏夹id，可从<获取用户所有收藏夹信息>获得
        # pn = 1
        # result = await self.fetch_folder_videos(folder_id=folder_id, pn=pn)
        # print(result)

        # 获取指定用户的信息
        # uid = '178360345'
        # result = await self.fetch_user_profile(uid=uid)
        # print(result)

        # 获取综合热门信息
        # pn = 1  # 页码
        # result = await self.fetch_com_popular(pn=pn)
        # print(result)

        # 获取指定视频的评论(不登录只能获取一页的评论)
        # bv_id = "BV1M1421t7hT"
        # pn = 1
        # result = await self.fetch_video_comments(bv_id=bv_id, pn=pn)
        # print(result)

        # 获取视频下指定评论的回复(不登录只能获取一页的评论)
        # bv_id = "BV1M1421t7hT"
        # rpid = "237109455120"
        # pn = 1
        # result = await self.fetch_comment_reply(bv_id=bv_id, pn=pn, rpid=rpid)
        # print(result)

        # 获取指定用户动态
        # uid = "16015678"
        # offset = ""     # 翻页索引，为空即从最新动态开始
        # result = await self.fetch_user_dynamic(uid=uid, offset=offset)
        # print(result)

        # 获取视频实时弹幕
        # cid = "1639235405"
        # result = await self.fetch_video_danmaku(cid=cid)
        # print(result)

        # 获取指定直播间信息
        # room_id = "1815229528"
        # result = await self.fetch_live_room_detail(room_id=room_id)
        # print(result)

        # 获取直播间视频流
        # room_id = "1815229528"
        # result = await self.fetch_live_videos(room_id=room_id)
        # print(result)

        # 获取指定分区正在直播的主播
        pn = 1
        area_id = '9'
        result = await self.fetch_live_streamers(area_id=area_id, pn=pn)
        print(result)

        "-------------------------------------------------------utils接口列表-------------------------------------------------------"
        # 通过bv号获得视频aid号
        # bv_id = 'BV1M1421t7hT'
        # aid = await self.get_aid(bv_id=bv_id)
        # print(aid)

        # 通过bv号获得视频分p信息
        # bv_id = "BV1vf421i7hV"
        # result = await self.fetch_video_parts(bv_id=bv_id)
        # print(result)

        # 获取所有直播分区列表
        # result = await self.fetch_all_live_areas()
        # print(result)


if __name__ == '__main__':
    # 初始化
    BilibiliWebCrawler = BilibiliWebCrawler()

    # 开始时间
    start = time.time()

    asyncio.run(BilibiliWebCrawler.main())

    # 结束时间
    end = time.time()
    print(f"耗时：{end - start}")
