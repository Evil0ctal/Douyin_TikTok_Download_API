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
from crawlers.tiktok.app.endpoints import TikTokAPIEndpoints
from crawlers.utils.utils import model_to_query_string

# 重试机制
from tenacity import *

# TikTok接口数据请求模型
from crawlers.tiktok.app.models import (
    BaseRequestModel, FeedVideoDetail
)

# 标记已废弃的方法
from crawlers.utils.deprecated import deprecated

# 配置文件路径
path = os.path.abspath(os.path.dirname(__file__))

# 读取配置文件
with open(f"{path}/config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)


class TikTokAPPCrawler:

    # 从配置文件中获取TikTok的请求头
    async def get_tiktok_headers(self):
        tiktok_config = config["TokenManager"]["tiktok"]
        kwargs = {
            "headers": {
                "User-Agent": tiktok_config["headers"]["User-Agent"],
                "Referer": tiktok_config["headers"]["Referer"],
                "Cookie": tiktok_config["headers"]["Cookie"],
                "x-ladon": "Hello From Evil0ctal!",
            },
            "proxies": {"http://": tiktok_config["proxies"]["http"],
                        "https://": tiktok_config["proxies"]["https"]}
        }
        return kwargs

    """-------------------------------------------------------handler接口列表-------------------------------------------------------"""

    # 获取单个作品数据
    # @deprecated("TikTok APP fetch_one_video is deprecated and will be removed in a future release. Use Web API instead. | TikTok APP fetch_one_video 已弃用，将在将来的版本中删除。请改用Web API。")
    @retry(stop=stop_after_attempt(10), wait=wait_fixed(1))
    async def fetch_one_video(self, aweme_id: str):
        # 获取TikTok的实时Cookie
        kwargs = await self.get_tiktok_headers()
        params = FeedVideoDetail(aweme_id=aweme_id)
        param_str = model_to_query_string(params)
        url = f"{TikTokAPIEndpoints.HOME_FEED}?{param_str}"
        # 创建一个基础爬虫
        base_crawler = BaseCrawler(proxies=kwargs["proxies"], crawler_headers=kwargs["headers"])
        async with base_crawler as crawler:
            response = await crawler.fetch_get_json(url)
            response = response.get("aweme_list")[0]
            if response.get("aweme_id") != aweme_id:
                raise Exception("作品ID错误/Video ID error")
        return response

    """-------------------------------------------------------main------------------------------------------------------"""

    async def main(self):
        # 获取单个作品数据/Fetch single post data
        aweme_id = "7339393672959757570"
        response = await self.fetch_one_video(aweme_id)
        print(response)

        # 占位
        pass


if __name__ == "__main__":
    # 初始化
    TikTokAPPCrawler = TikTokAPPCrawler()

    # 开始时间
    start = time.time()

    asyncio.run(TikTokAPPCrawler.main())

    # 结束时间
    end = time.time()
    print(f"耗时：{end - start}")
