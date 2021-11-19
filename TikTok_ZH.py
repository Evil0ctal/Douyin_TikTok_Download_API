#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2021/11/19
# @Function:
# 基于 PyWebIO、Requests、Flask，可实现在线批量解析抖音的无水印视频/图集。
# 可用于下载作者禁止下载的视频，同时可搭配iOS的快捷指令APP配合本项目API实现应用内下载。


from TikTok_EN import *
from pywebio import config, session
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
title = "抖音在线解析"
description = "在线批量解析抖音的无水印视频/图集。"
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


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
    # 输出一个毫无用处的信息
    put_html("<hr>")
    put_text("无法解析输入内容，请检查输入内容或稍后再试。如多次尝试仍失败，请点击反馈。")
    put_html("<hr>")


def error_log(e):
    # 将错误记录在logs.txt中
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('logs.txt', 'a') as f:
        f.write(date + ": " + str(e) + '\n')


def loading():
    # 写一个进度条装装样子吧 :)
    set_scope('bar', position=3)
    with use_scope('bar'):
        put_processbar('bar')
        for i in range(1, 4):
            set_processbar('bar', i / 3)
            time.sleep(0.1)


@retry(stop_max_attempt_number=3)
def get_video_info(original_url):
    # 利用官方接口解析链接信息
    try:
        # 原视频链接
        # original_url = find_url(url)[0]
        r = requests.get(url=original_url)
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
            image_author_id = str(js['item_list'][0]['author']['unique_id'])
            if image_author_id == "":
                # 如果作者未修改过抖音号，应使用此值以避免无法获取其抖音ID
                image_author_id = str(js['item_list'][0]['author']['short_id'])
            # 去水印图集链接
            images_url = []
            for data in image_data:
                images_url.append(data['url_list'][0])
            image_info = [images_url, image_music, image_title, image_author, image_author_id, original_url]
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
            if video_author_id == "":
                # 如果作者未修改过抖音号，应使用此值以避免无法获取其抖音ID
                video_author_id = str(js['item_list'][0]['author']['short_id'])
            # 返回包含数据的列表
            video_info = [video_url, video_music, video_title, video_author, video_author_id, original_url]
            return video_info, 'video'
    except Exception as e:
        # 异常捕获
        error_log(e)


@app.route("/api")
def webapi():
    # 创建一个Flask应用获取POST参数并返回结果
    try:
        post_content = request.args.get("url")
        if post_content:
            response_data, result_type = get_video_info(post_content)
            if result_type == 'image':
                # 返回图集信息json
                return jsonify(Type=result_type, image_url=response_data[0], image_music=response_data[1],
                               image_title=response_data[2], image_author=response_data[3],
                               image_author_id=response_data[4], original_url=response_data[5])
            else:
                # 返回视频信息json
                return jsonify(Type=result_type, video_url=response_data[0], video_music=response_data[1],
                               video_title=response_data[2], video_author=response_data[3],
                               video_author_id=response_data[4], original_url=response_data[5])
    except Exception as e:
        # 异常捕获
        error_log(e)
        return jsonify(Message="解析失败", Reason=str(e), Result=False)


def put_result(item):
    # 根据解析格式向前端输出表格
    video_info, result_type = get_video_info(item)
    if result_type == 'video':
        put_table([
            ['类型', '内容'],
            ['格式:', result_type],
            ['视频直链: ', put_link('点击打开视频', video_info[0], new_window=True)],
            ['背景音乐直链: ', put_link('点击打开音频', video_info[1], new_window=True)],
            ['视频标题: ', video_info[2]],
            ['作者昵称: ', video_info[3]],
            ['作者抖音ID: ', video_info[4]],
            ['原视频链接: ', put_link('点击打开原视频', video_info[5], new_window=True)]
        ])
    else:
        put_table([
            ['类型', '内容'],
            ['格式:', result_type],
        ])
        for i in video_info[0]:
            put_table([
                ['图片直链: ', put_link('点击打开图片', i, new_window=True)]
            ])
        put_table([
            ['背景音乐直链: ', put_link('点击打开音频', video_info[1], new_window=True)],
            ['视频标题: ', video_info[2]],
            ['作者昵称: ', video_info[3]],
            ['作者抖音ID: ', video_info[4]],
            ['原视频链接: ', put_link('点击打开原视频', video_info[5], new_window=True)]
        ])


def github_pop_window():
    with popup("Github"):
        put_html('<h3>⭐欢迎Star</h3>')
        put_markdown('[TikTokDownloader_PyWebIO](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO)')


def feedback_pop_window():
    with popup("可以通过以下方式进行反馈"):
        put_html('<h3>🎯Github</h3>')
        put_markdown('提交：[issues](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)')
        put_html('<hr>')
        put_html('<h3>🤖WeChat</h3>')
        put_markdown('微信：[Evil0ctal](https://mycyberpunk.com/)')
        put_html('<hr>')


def api_document_pop_window():
    with popup("API文档"):
        put_markdown("🛰️API使用")
        put_markdown("API可将请求参数转换为需要提取的无水印视频/图片直链，配合IOS捷径可实现应用内下载。")
        put_link('[中文文档]', 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8', new_window=True)
        put_html('<br>')
        put_link('[英文文档]', 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README-EN.md#%EF%B8%8Fapi-usage', new_window=True)


def about_popup_window():
    with popup('更多信息'):
        put_html('<h3>⚠️关于解析失败</h3>')
        put_text('目前已知短时间大量访问抖音API可能触发其验证码。')
        put_text('若多次解析失败后，请等待一段时间再尝试。')
        put_link("下载本站错误日志", 'http://52.53.215.89:8888/down/oTi8UDPltRYn', new_window=True)
        put_html('<hr>')
        put_html('<h3>🌐视频/图集批量下载</h3>')
        put_markdown('可以使用[IDM](https://www.zhihu.com/topic/19746283/hot)之类的工具对结果页面的链接进行嗅探。')
        put_html('<hr>')
        put_html('<h3>📣关于本项目</h3>')
        put_markdown('本人技术有限，欢迎在[GitHub](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/pulls)提交pull请求。')
        put_html('<hr>')
        put_html('<h3>💖交个朋友</h3>')
        put_markdown('微信：[Evil0ctal](https://mycyberpunk.com/)')


def language_pop_window():
    with popup('Select Language'):
        put_link('[Chinese Language]', 'https://douyin.wtf')
        put_html('<br>')
        put_link('[English Language]', 'https://en.douyin.wtf')


@config(title=title, description=description)
def main():
    # 设置favicon
    favicon_url = "https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/favicon/android-chrome-512x512.png"
    session.run_js("""
    $('#favicon32,#favicon16').remove(); 
    $('head').append('<link rel="icon" type="image/png" href="%s">')
    """ % favicon_url)
    # 修改footer
    session.run_js("""$('footer').remove()""")
    put_markdown("""<div align='center' ><font size='20'>😼欢迎使用抖音在线解析</font></div>""")
    put_html('<hr>')
    put_row([put_button("Github", onclick=lambda: github_pop_window(), link_style=True, small=True),
             put_button("反馈", onclick=lambda: feedback_pop_window(), link_style=True, small=True),
             put_button("API文档", onclick=lambda: api_document_pop_window(), link_style=True, small=True),
             put_button("关于", onclick=lambda: about_popup_window(), link_style=True, small=True),
             put_button("Languages", onclick=lambda: language_pop_window(), link_style=True, small=True),
             put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg', title='访问记录')
             ])
    placeholder = "如需批量解析请直接粘贴多个口令或链接无需使用符号分开。"
    kou_ling = textarea('请将抖音的分享口令或网址粘贴于此', type=TEXT, validate=valid_check, required=True, placeholder=placeholder, position=0)
    if kou_ling:
        url_lists = find_url(kou_ling)
        # 解析开始时间
        start = time.time()
        try:
            loading()
            for url in url_lists:
                put_result(url)
            clear('bar')
            # 解析结束时间
            end = time.time()
            put_html("<br><hr>")
            put_text('解析完成: 耗时: %.4f秒' % (end - start))
        except Exception as e:
            # 异常捕获
            clear('bar')
            error_msg()
            end = time.time()
            put_text('解析完成: 耗时: %.4f秒' % (end - start))
            error_log(e)


if __name__ == "__main__":
    app.add_url_rule('/', 'webio_view', webio_view(main), methods=['GET', 'POST', 'OPTIONS'])
    app.run(host='0.0.0.0', port=80)
