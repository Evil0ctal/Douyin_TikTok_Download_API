#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2021/11/06
# @Function:
# Get the TikTok shared text entered by the user
# And display it on the web page after being parsed in the background.

from pywebio.input import *
from pywebio.output import *
import requests
import re
import json


def find_url(string):
    # 解析抖音分享口令中的链接并返回列表
    url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
    return url


def valid_check(kou_ling):
    # 校验输入的内容
    if find_url(kou_ling):
        return None
    else:
        return '抖音分享口令有误!'


def get_video_info(url):
    # 利用官方接口解析链接信息
    headers = {'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'}
    try:
        r = requests.get(url=find_url(url)[0])
        key = re.findall('video/(\d+)?', str(r.url))[0]
        jx_url = f'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={key}'
        js = json.loads(requests.get(url=jx_url, headers=headers).text)
        # 去水印后视频链接
        video_url = str(js['item_list'][0]['video']['play_addr']['url_list'][0]).replace('playwm', 'play')
        # 视频标题
        video_title = str(js['item_list'][0]['desc'])
        # 视频作者昵称
        video_author = str(js['item_list'][0]['author']['nickname'])
        # 视频作者抖音号
        video_author_id = str(js['item_list'][0]['author']['unique_id'])
        # 返回包含数据的列表
        video_info = [video_url, video_title, video_author, video_author_id]
        return video_info
    except:
        print("无法解析输入内容，请检查输入内容及网络，如多次尝试仍失败，请移步GitHub提交issue。")
        print("https://github.com/Evil0ctal/")


def main():
    placeholder = "格式: 1.02 GIi:/电动车真环保吗？ https://v.douyin.com/RATN1fk/ 复制此链接，打开Dou音搜索，直接观看视频！"
    kou_ling = input('请将抖音分享的口令粘贴于此', type=TEXT, validate=valid_check, required=True, placeholder=placeholder)
    if kou_ling:
        try:
            video_info = get_video_info(kou_ling)
            print(video_info)
            put_table([
                ['类型', '内容'],
                ['无水印链接', put_link('点击打开视频', video_info[0])],
                ['视频标题', video_info[1]],
                ['作者昵称', video_info[2]],
                ['作者抖音ID', video_info[3]],
            ])
        except:
            put_text("无法解析输入内容，请检查输入内容及网络，如多次尝试仍失败，请移步GitHub提交issue。")
            put_link('Github: Evil0ctal', 'https://github.com/Evil0ctal/')


if __name__ == "__main__":
    # First Time to do :
    # pip install -r requirements.txt
    while True:
        main()
