#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2022/04/15
# @Function:
# 创建一个接受提交参数的Flask应用程序。
# 将scraper.py返回的内容以JSON格式返回。

import os
import re
import time
import requests
import unicodedata
from scraper import Scraper
from werkzeug.urls import url_quote
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


def find_url(string):
    # 解析抖音分享口令中的链接并返回列表
    url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
    return url


def clean_filename(string, author_name):
    # 替换不能用于文件名的字符('/ \ : * ? " < > |')
    rstr = r"[\/\\\:\*\?\"\<\>\|]"
    # 将上述字符替换为下划线
    new_title = re.sub(rstr, "_", string)
    # 新文件名
    filename = ('douyin.wtf_' + new_title + '_' + author_name).replace('\n', '')
    return filename


@app.route("/", methods=["POST", "GET"])
def index():
    # 显示基础信息
    index_info = {'API status': 'Running',
                  'GitHub': 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API',
                  'Introduction': 'Free and open source Douyin/TikTok watermark-free video download tool, supports API calls.',
                  'Web interface': 'https://douyin.wtf/',
                  'iOS Shortcuts': 'https://api.douyin.wtf/ios',
                  'Parsing Douyin/TikTok videos': 'https://api.douyin.wtf/api?url=[Douyin/TikTok url]',
                  'Return Video MP4 File Download': 'https://api.douyin.wtf/video?url=[Douyin/TikTok url]',
                  'Return Video MP3 File Download': 'https://api.douyin.wtf/music?url=[Douyin/TikTok url]'}
    return jsonify(index_info)


@app.route("/api", methods=["POST", "GET"])
def webapi():
    # 创建一个Flask应用获取POST参数并返回结果
    api = Scraper()
    content = request.args.get("url")
    if content != '':
        post_content = find_url(content)[0]
        # 将API记录在API_logs.txt中
        date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open('API_logs.txt', 'a') as f:
            f.write(date + " : " + post_content + '\n')
            try:
                # 开始时间
                start = time.time()
                # 校验是否为TikTok链接
                if 'tiktok.com' in post_content:
                    result = api.tiktok(post_content)
                    # 以JSON格式返回TikTok信息
                    return jsonify(result)
                # 如果关键字不存在则判断为抖音链接
                elif 'douyin.com' in post_content:
                    result = api.douyin(post_content)
                    # 以JSON格式返回返回Douyin信息
                    return jsonify(result)
            except Exception as e:
                # 结束时间
                end = time.time()
                # 解析时间
                analyze_time = (format((end - start), '.4f') + 's')
                # 返回错误信息
                return jsonify(status='failed', reason=str(e), time=analyze_time, function='webapi()', value=content)
    else:
        # 返回错误信息
        return jsonify(status='failed', reason='url value cannot be empty', function='api()', value=content)


@app.route("/ios", methods=["POST", "GET"])
def ios_shortcut():
    # 用于检查快捷指令更新
    return jsonify(version='3.0',
                   update='2022/04/15',
                   link='https://www.icloud.com/shortcuts/126820d2783748d1bdec95a223a02639',
                   note='为快捷指令增加了检查更新的功能')


@app.route("/video", methods=["POST", "GET"])
def download_video():
    # 用于返回视频下载请求(返回MP4文件下载请求，面对大量请求时非常吃服务器内存，容易崩，慎用。)
    # 将api_switch的值设定为False可关闭该API
    api_switch = True
    if api_switch:
        api = Scraper()
        content = request.args.get("url")
        if content == '':
            return jsonify(status='failed', reason='url value cannot be empty', function='download_music()',
                           value=content)
        else:
            post_content = find_url(content)[0]
            try:
                if 'douyin.com' in post_content:
                    # 获取视频信息
                    result = api.douyin(post_content)
                    # 视频链接
                    video_url = result['nwm_video_url']
                    # 视频标题
                    video_title = result['video_title']
                    # 作者昵称
                    video_author = result['video_author']
                    # 清理文件名
                    file_name = clean_filename(video_title, video_author)
                elif 'tiktok.com' in post_content:
                    # 获取视频信息
                    result = api.tiktok(post_content)
                    # 无水印地址
                    video_url = result['nwm_video_url']
                    # 视频标题
                    video_title = result['video_title']
                    # 作者昵称
                    video_author = result['video_author']
                    # 清理文件名
                    file_name = clean_filename(video_title, video_author)
                else:
                    return jsonify(Status='Failed', Reason='Check submitted parameters!')
                # 获取视频文件字节流
                video_mp4 = requests.get(video_url, headers).content
                # 将字节流封装成返回对象
                response = make_response(video_mp4)
                # 添加响应头部信息
                response.headers['Content-Type'] = "video/mp4"
                # 他妈的,费了我老大劲才解决文件中文名的问题
                try:
                    filename = file_name.encode('latin-1')
                except UnicodeEncodeError:
                    filenames = {
                        'filename': unicodedata.normalize('NFKD', file_name).encode('latin-1', 'ignore'),
                        'filename*': "UTF-8''{}".format(url_quote(file_name) + '.mp4'),
                    }
                else:
                    filenames = {'filename': file_name + '.mp4'}
                # attachment表示以附件形式下载
                response.headers.set('Content-Disposition', 'attachment', **filenames)
                return response
            except Exception as e:
                return jsonify(status='failed', reason=str(e), function='download_video()', value=content)
    else:
        return jsonify(Status='Failed', Reason='This API is disabled. To enable it, set the value of "api_switch" to True.')


@app.route("/music", methods=["POST", "GET"])
def download_music():
    # 用于返回视频下载请求(返回MP3文件下载请求，面对大量请求时非常吃服务器内存，容易崩，慎用。)
    # 将api_switch的值设定为False可关闭该API
    api_switch = True
    if api_switch:
        api = Scraper()
        content = request.args.get("url")
        if content == '':
            return jsonify(status='failed', reason='url value cannot be empty', function='download_music()',
                           value=content)
        else:
            post_content = find_url(content)[0]
            try:
                if 'douyin.com' in post_content:
                    # 获取视频信息
                    result = api.douyin(post_content)
                    bgm_url = result['video_music']
                    if bgm_url == "None":
                        return jsonify(Status='Failed', Reason='This link has no music to get!')
                    else:
                        # 视频标题
                        bgm_title = result['video_music_title']
                        # 作者昵称
                        author_name = result['video_music_author']
                        # 清理文件名
                        file_name = clean_filename(bgm_title, author_name)
                elif 'tiktok.com' in post_content:
                    # 获取视频信息
                    result = api.douyin(post_content)
                    # BGM链接
                    bgm_url = result['video_music']
                    # 视频标题
                    bgm_title = result['video_music_title']
                    # 作者昵称
                    author_name = result['video_music_author']
                    # 清理文件名
                    file_name = clean_filename(bgm_title, author_name)
                else:
                    return jsonify(Status='Failed', Reason='This link has no music to get!')
                video_bgm = requests.get(bgm_url, headers).content
                # 将bgm字节流封装成response对象
                response = make_response(video_bgm)
                # 添加响应头部信息
                response.headers['Content-Type'] = "video/mp3"
                # 他妈的,费了我老大劲才解决文件中文名的问题
                try:
                    filename = file_name.encode('latin-1')
                except UnicodeEncodeError:
                    filenames = {
                        'filename': unicodedata.normalize('NFKD', file_name).encode('latin-1', 'ignore'),
                        'filename*': "UTF-8''{}".format(url_quote(file_name) + '.mp3'),
                    }
                else:
                    filenames = {'filename': file_name + '.mp3'}
                # attachment表示以附件形式下载
                response.headers.set('Content-Disposition', 'attachment', **filenames)
                return response
            except Exception as e:
                return jsonify(status='failed', reason=str(e), function='download_music()', value=content)
    else:
        return jsonify(Status='Failed', Reason='This API is disabled. To enable it, set the value of "api_switch" to True.')


if __name__ == '__main__':
    # 开启WebAPI
    if os.environ.get('PORT'):
        port = int(os.environ.get('PORT'))
    else:
        # 默认端口
        port = 2333
    app.run(host='0.0.0.0', port=port)
