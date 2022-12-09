#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/13
# @Description: Douyin/TikTok async data scraper demo.
# PyPi: https://pypi.org/project/douyin-tiktok-scraper/


import asyncio
# pip install douyin-tiktok-scraper
from douyin_tiktok_scraper.scraper import Scraper

api = Scraper()


async def async_test(url: str):
    if 'douyin' in url:  # 抖音数据爬取
        douyin_url = await api.convert_share_urls(url)
        douyin_id = await api.get_douyin_video_id(douyin_url)
        douyin_data = await api.get_douyin_video_data(douyin_id)
        print(f"视频URL：{douyin_url}\n视频ID：{douyin_id}\n视频数据：{douyin_data}\n")
    elif 'tiktok' in url:   # TikTok data scraper
        tiktok_url = await api.convert_share_urls(url)
        tiktok_id = await api.get_tiktok_video_id(tiktok_url)
        tiktok_data = await api.get_tiktok_video_data(tiktok_id)
        print(f"Video URL：{tiktok_url}\nVideo ID：{tiktok_id}\nVideo Data：{tiktok_data}\n")
    # Hybrid parsing(Any platform URL)
    hybrid_data = await api.hybrid_parsing(url)


asyncio.run(async_test(url=input("Paste Douyin/TikTok share URL here: ")))
