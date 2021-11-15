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
from pywebio.platform.flask import webio_view
from flask import Flask, request, jsonify
from retrying import retry
import time
import requests
import re
import json


app = Flask(__name__)
headers = {'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'}


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


def error_msg():
    put_text("无法解析输入内容，请检查输入内容及网络，如多次尝试仍失败，请移步GitHub提交issue。")
    put_link('Github: Evil0ctal', 'https://github.com/Evil0ctal/')


def error_log(e):
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('log.txt', 'a') as f:
        f.write(date + ": " + str(e) + '\n')


@retry(stop_max_attempt_number=3)
def get_video_info(url):
    # 利用官方接口解析链接信息
    try:
        r = requests.get(url=find_url(url)[0])
        key = re.findall('video/(\d+)?', str(r.url))[0]
        api_url = f'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={key}'
        js = json.loads(requests.get(url=api_url, headers=headers).text)
        # 判断是否为图集
        try:
            image_data = js['item_list'][0]['images']
            # 图集背景音频
            image_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
            # 图集标题
            image_title = str(js['item_list'][0]['desc'])
            # 图集作者昵称
            image_author = str(js['item_list'][0]['author']['nickname'])
            # 图集作者抖音号
            image_author_id = str(js['item_list'][0]['author']['short_id'])
            # 去水印图集链接
            images_url = []
            for data in image_data:
                images_url.append(data['url_list'][0])
            image_info = [images_url, image_music, image_title, image_author, image_author_id]
            return image_info, 'image'
        # 报错后判断为视频
        except:
            # 去水印后视频链接
            video_url = str(js['item_list'][0]['video']['play_addr']['url_list'][0]).replace('playwm', 'play')
            # 视频背景音频
            video_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
            # 视频标题
            video_title = str(js['item_list'][0]['desc'])
            # 视频作者昵称
            video_author = str(js['item_list'][0]['author']['nickname'])
            # 视频作者抖音号
            video_author_id = str(js['item_list'][0]['author']['unique_id'])
            # 返回包含数据的列表
            video_info = [video_url, video_music, video_title, video_author, video_author_id]
            return video_info, 'video'
    except Exception as e:
        # 异常捕获
        error_log(e)


@app.route("/api")
def webapi():
    try:
        post_content = request.args.get("url")
        if post_content:
            response_data, type = get_video_info(post_content)
            if type == 'image':
                # 返回图集信息json
                return jsonify(Type=type, image_url=response_data[0], image_music=response_data[1],
                               image_title=response_data[2], image_author=response_data[3],
                               image_author_id=response_data[4])
            else:
                # 返回视频信息json
                return jsonify(Type=type, video_url=response_data[0], video_music=response_data[1],
                               video_title=response_data[2], video_author=response_data[3],
                               video_author_id=response_data[4])
    except Exception as e:
        # 异常捕获
        return jsonify(Result=False, Message=str(e), Data=None)


def put_result(item):
    video_info, type = get_video_info(item)
    if type == 'video':
        put_table([
            ['类型', '内容'],
            ['格式:', type],
            ['视频直链: ', put_link('点击打开视频', video_info[0], new_window=True)],
            ['背景音乐直链: ', put_link('点击打开音频', video_info[1], new_window=True)],
            ['视频标题: ', video_info[2]],
            ['作者昵称: ', video_info[3]],
            ['作者抖音ID: ', video_info[4]]
        ])
    else:
        put_table([
            ['类型', '内容'],
            ['格式:', type],
        ])
        for i in video_info[0]:
            put_table([
                ['图片直链: ', put_link('点击打开图片', i, new_window=True)]
            ])
        put_table([
            ['背景音乐直链: ', put_link('点击打开音频', video_info[1], new_window=True)],
            ['视频标题: ', video_info[2]],
            ['作者昵称: ', video_info[3]],
            ['作者抖音ID: ', video_info[4]]
        ])


@app.route("/")
def main():
    placeholder = "如需批量解析请使用英文逗号进行分隔！ \n格式: 1.02 GIi:/电动车真环保吗？ https://v.douyin.com/RATN1fk/ 复制此链接，打开Dou音搜索，直接观看视频！"
    kou_ling = textarea('请将抖音分享的口令粘贴于此', type=TEXT, validate=valid_check, required=True, placeholder=placeholder)
    try:
        if ',' in kou_ling:
            kou_ling = kou_ling.split(',')
            for item in kou_ling:
                put_result(item)
        else:
            put_result(kou_ling)
    except Exception as e:
        # 异常捕获
        error_msg()
        error_log(e)


if __name__ == "__main__":
    # 访问URL  http://127.0.0.1:80/tiktok
    # 访问API http://127.0.0.1:80/api?url=[抖音分享口令]
    app.add_url_rule('/tiktok', 'webio_view', webio_view(main), methods=['GET', 'POST', 'OPTIONS'])
    app.run(host='0.0.0.0', port=80)
