#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2022/02/04
# @Function:
# 基于 PyWebIO、Requests、Flask，可实现在线批量解析抖音的无水印视频/图集。
# 可用于下载作者禁止下载的视频，同时可搭配iOS的快捷指令APP配合本项目API实现应用内下载。
# API请求参考
# 抖音/TikTok解析请求参数
# http://localhost(服务器IP):80/api?url="复制的(抖音/TikTok)的(分享文本/链接)" - 返回JSON数据
# 抖音/TikTok视频下载请求参数
# http://localhost(服务器IP):80/download_video?url="复制的抖音/TikTok链接" - 返回mp4文件下载请求
# 抖音视频/图集音频下载请求参数
# http://localhost(服务器IP):80/download_bgm?url="复制的抖音/TikTok链接" - 返回mp3文件下载请求

from pywebio import config, session
from pywebio.input import *
from pywebio.output import *
from pywebio.platform.flask import webio_view
from retrying import retry
from werkzeug.urls import url_quote
from flask import Flask, request, jsonify, make_response
import re
import json
import time
import requests
import unicodedata

app = Flask(__name__)
title = "抖音/TikTok在线解析"
description = "在线批量解析下载抖音/TikTok的无水印视频/图集。"
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


def find_url(string):
    # 解析抖音分享口令中的链接并返回列表
    url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
    return url


def valid_check(kou_ling):
    # 校验输入的内容
    url_list = find_url(kou_ling)
    # 对每一个链接进行校验
    if url_list:
        for i in url_list:
            if 'douyin.com' in i[:31]:
                if i == url_list[-1]:
                    return None
            elif 'tiktok.com' in i[:31]:
                if i == url_list[-1]:
                    return None
            else:
                return '请确保输入链接均为有效的抖音/TikTok链接!'
    elif kou_ling == 'wyn':
        return None
    else:
        return '抖音分享口令有误!'


def clean_filename(string, author_name):
    # 替换不能用于文件名的字符
    rstr = r"[\/\\\:\*\?\"\<\>\|]"  # '/ \ : * ? " < > |'
    new_title = re.sub(rstr, "_", string)  # 替换为下划线
    filename = 'douyin.wtf_' + new_title + '_' + author_name
    return filename


def error_do(e, func_name):
    # 输出一个毫无用处的信息
    put_html("<hr>")
    put_error("出现了意料之的错误，请检查输入值是否有效！")
    put_html('<h3>⚠详情</h3>')
    put_table([
        ['函数名', '原因'],
        [func_name, str(e)]])
    put_html("<hr>")
    put_markdown(
        '大量解析TikTok可能导致其防火墙限流!\n请稍等1-2分钟后再次尝试!\n如果多次尝试后仍失败,请点击[反馈](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues).\n你可以在右上角的关于菜单中查看本站错误日志:)')
    put_link('返回主页', '/')
    # 将错误记录在logs.txt中
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('logs.txt', 'a') as f:
        f.write(date + " " + func_name + ': ' + str(e) + '\n')


def loading(url_lists):
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
        r = requests.get(url=original_url, allow_redirects=False)
        try:
            # 2021/12/11 发现抖音做了限制，会自动重定向网址，不能用以前的方法获取视频ID了，但是还是可以从请求头中获取。
            long_url = r.headers['Location']
        except:
            # 报错后判断为长链接，直接截取视频id
            long_url = original_url
        key = re.findall('video/(\d+)?', long_url)[0]
        api_url = f'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={key}'
        print("Sending request to: " + '\n' + api_url)
        js = json.loads(requests.get(url=api_url, headers=headers).text)
        # 判断是否为图集
        if js['item_list'][0]['images'] is not None:
            print("Type = images")
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
            return image_info, 'image', api_url
        else:
            print("Type = video")
            # 去水印后视频链接(2022年1月1日抖音APi获取到的URL会进行跳转，需要在Location中获取直链)
            video_url = str(js['item_list'][0]['video']['play_addr']['url_list'][0]).replace('playwm', 'play')
            r = requests.get(url=video_url, headers=headers, allow_redirects=False)
            video_url = r.headers['Location']
            print(video_url)
            # 视频背景音频
            if js['item_list'][0]['music']['play_url']['url_list']:
                print("Getting music from playlist")
                video_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
                print(video_music)
            else:
                video_music = "None"
            print(video_music)
            # 视频标题
            video_title = str(js['item_list'][0]['desc'])
            print(video_title)
            # 视频作者昵称
            video_author = str(js['item_list'][0]['author']['nickname'])
            print(video_author)
            # 视频作者抖音号
            video_author_id = str(js['item_list'][0]['author']['unique_id'])
            print(video_author_id)
            if video_author_id == "":
                # 如果作者未修改过抖音号，应使用此值以避免无法获取其抖音ID
                video_author_id = str(js['item_list'][0]['author']['short_id'])
            # 返回包含数据的列表
            video_info = [video_url, video_music, video_title, video_author, video_author_id, original_url]
            return video_info, 'video', api_url
    except Exception as e:
        # 异常捕获
        error_do(e, 'get_video_info')


@retry(stop_max_attempt_number=3)
def get_video_info_tiktok(tiktok_url):
    # 对TikTok视频进行解析
    try:
        tiktok_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "authority": "www.tiktok.com",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Host": "www.tiktok.com",
            "User-Agent": "Mozilla/5.0  (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) coc_coc_browser/86.0.170 Chrome/80.0.3987.170 Safari/537.36",
        }
        html = requests.get(url=tiktok_url, headers=tiktok_headers)
        res = re.search('<script id="sigi-persisted-data">(.*)</script><script', html.text).group(1)
        resp = re.findall(r'^window\[\'SIGI_STATE\']=(.*)?;window', res)[0]
        result = json.loads(resp)
        author_id = result["ItemList"]["video"]["list"][0]
        video_info = result["ItemModule"][author_id]
        # print("The author_id is: ", author_id)
        # print(video_info)
        # 格式很乱 要忍一下
        return video_info
    except Exception as e:
        # 异常捕获
        error_do(e, 'get_video_info_tiktok')


@retry(stop_max_attempt_number=3)
def tiktok_nwm(tiktok_url):
    # 使用第三方API获取无水印视频链接（不保证稳定）
    try:
        s = requests.Session()
        api_url = "https://ttdownloader.com/req/"
        source = s.get("https://ttdownloader.com/")
        token = re.findall(r'value=\"([0-9a-z]+)\"', source.text)
        result = s.post(
            api_url,
            data={'url': tiktok_url, 'format': '', 'token': token[0]}
        )
        nwm, wm, audio = re.findall(
            r'(https?://.*?.php\?v\=.*?)\"', result.text
        )
        r = requests.get(nwm, allow_redirects=False)
        true_link = r.headers['Location']
        return true_link
    except Exception as e:
        error_do(e, "tiktok_nwm")


@app.route("/api")
def webapi():
    # 创建一个Flask应用获取POST参数并返回结果
    try:
        post_content = request.args.get("url")
        if post_content:
            # 将API记录在API_logs.txt中
            date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open('API_logs.txt', 'a') as f:
                f.write(date + " : " + post_content + '\n')
            # 校验是否为TikTok链接
            if 'tiktok.com' in post_content:
                try:
                    js = get_video_info_tiktok(post_content)
                    return js
                except Exception:
                    return jsonify(Status='Failed!', Reason='Check the link!')
            # 如果关键字不存在则判断为抖音链接
            elif 'douyin.com' in post_content:
                try:
                    response_data, result_type, api_url = get_video_info(post_content)
                    if result_type == 'image':
                        # 返回图集信息json
                        return jsonify(Status='Success', Type='Image', image_url=response_data[0],
                                       image_music=response_data[1],
                                       image_title=response_data[2], image_author=response_data[3],
                                       image_author_id=response_data[4], original_url=response_data[5])
                    else:
                        # 返回视频信息json
                        return jsonify(Status='Success', Type='Video', video_url=response_data[0],
                                       video_music=response_data[1],
                                       video_title=response_data[2], video_author=response_data[3],
                                       video_author_id=response_data[4], original_url=response_data[5])
                except:
                    return jsonify(Status='Failed!', Reason='Check the link!')
            else:
                return jsonify(Status='Failed!', Reason='Check the link!')

    except Exception as e:
        # 异常捕获
        error_do(e, 'webapi')
        return jsonify(Message="解析失败", Reason=str(e), Result=False)


@app.route("/download_video", methods=["POST", "GET"])
def download_video_url():
    # 返回视频下载请求
    input_url = request.args.get("url")
    try:
        if 'douyin.com' in input_url:
            video_info, result_type, api_url = get_video_info(input_url)
            video_url = video_info[0]
            # 视频标题
            video_title = video_info[2]
            # 作者昵称
            author_name = video_info[3]
            # 清理文件名
            file_name = clean_filename(video_title, author_name)
        elif 'tiktok.com' in input_url:
            video_info = get_video_info_tiktok(input_url)
            nwm = tiktok_nwm(input_url)
            # 无水印地址
            video_url = nwm
            # 视频标题
            video_title = video_info['desc']
            # 作者昵称
            author_name = video_info['author']
            # 清理文件名
            file_name = clean_filename(video_title, author_name)
        else:
            return jsonify(Status='Failed!', Reason='Check the link!')
        # video_title = 'video_title'
        video_mp4 = requests.get(video_url, headers).content
        # 将video字节流封装成response对象
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
        error_do(e, 'download_video_url')
        return jsonify(Status='Failed!', Reason='Check the link!')


@app.route("/download_bgm", methods=["POST", "GET"])
def download_bgm_url():
    # 返回视频下载请求
    input_url = request.args.get("url")
    try:
        if 'douyin.com' in input_url:
            video_info, result_type, api_url = get_video_info(input_url)
            if video_info[1] == "None":
                print('Failed')
                return jsonify(Status='Failed', Reason='This link has no music to get!')
            else:
                print("Success")
                # 音频链接
                bgm_url = video_info[1]
                # 视频标题
                bgm_title = video_info[2]
                # 作者昵称
                author_name = video_info[3]
                # 清理文件名
                file_name = clean_filename(bgm_title, author_name)
        elif 'tiktok.com' in input_url:
            video_info = get_video_info_tiktok(input_url)
            bgm_url = video_info['music']['playUrl']
            # 视频标题
            bgm_title = video_info['music']['album']
            print('album: ', bgm_title)
            # 作者昵称
            author_name = video_info['music']['authorName']
            print('authorName: ', author_name)
            # 清理文件名
            file_name = clean_filename(bgm_title, author_name)
            print(file_name)
        else:
            return jsonify(Status='Failed', Reason='Check the link!')
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
        error_do(e, 'download_bgm_url')
        return jsonify(Status='Failed!', Reason='Check the link!')


def put_result(item):
    # 根据解析格式向前端输出表格
    video_info, result_type, api_url = get_video_info(item)
    short_api_url = '/api?url=' + item
    if result_type == 'video':
        download_video = '/download_video?url=' + video_info[5]
        download_bgm = '/download_bgm?url=' + video_info[5]
        put_table([
            ['类型', '内容'],
            ['格式:', result_type],
            ['视频直链: ', put_link('点击打开视频', video_info[0], new_window=True)],
            ['视频下载：', put_link('点击下载', download_video, new_window=True)],
            ['背景音乐直链: ', put_link('点击打开音频', video_info[1], new_window=True)],
            ['背景音乐下载：', put_link('点击下载', download_bgm, new_window=True)],
            ['视频标题: ', video_info[2]],
            ['作者昵称: ', video_info[3]],
            ['作者抖音ID: ', video_info[4]],
            ['原视频链接: ', put_link('点击打开原视频', video_info[5], new_window=True)],
            ['当前视频API链接: ', put_link('点击浏览API数据', api_url, new_window=True)],
            ['当前视频精简API链接: ', put_link('点击浏览API数据', short_api_url, new_window=True)]
        ])
    else:
        download_bgm = '/download_bgm?url=' + video_info[5]
        put_table([
            ['类型', '内容'],
            ['格式:', result_type],
            ['背景音乐直链: ', put_link('点击打开音频', video_info[1], new_window=True)],
            ['背景音乐下载：', put_link('点击下载', download_bgm, new_window=True)],
            ['视频标题: ', video_info[2]],
            ['作者昵称: ', video_info[3]],
            ['作者抖音ID: ', video_info[4]],
            ['原视频链接: ', put_link('点击打开原视频', video_info[5], new_window=True)],
            ['当前视频API链接: ', put_link('点击浏览API数据', api_url, new_window=True)],
            ['当前视频精简API链接: ', put_link('点击浏览API数据', short_api_url, new_window=True)]
        ])
        for i in video_info[0]:
            put_table([
                ['图片直链: ', put_link('点击打开图片', i, new_window=True), put_image(i)]
            ])


def put_tiktok_result(item):
    # 将TikTok结果显示在前端
    video_info = get_video_info_tiktok(item)
    nwm = tiktok_nwm(item)
    download_url = '/download_video?url=' + item
    api_url = '/api?url=' + item
    put_table([
        ['类型', '内容'],
        ['视频标题: ', video_info['desc']],
        ['视频直链(有水印): ', put_link('点击打开视频', video_info['video']['playAddr'], new_window=True)],
        ['视频直链(无水印): ', put_link('点击打开视频', nwm, new_window=True)],
        ['视频下载(无水印)：', put_link('点击下载', download_url, new_window=True)],
        ['音频(名称-作者)：', video_info['music']['album'] + " - " + video_info['music']['authorName']],
        ['音频播放：', put_link('点击播放', video_info['music']['playUrl'], new_window=True)],
        ['作者昵称: ', video_info['author']],
        ['作者ID: ', video_info['authorId']],
        ['粉丝数量: ', video_info['authorStats']['followerCount']],
        ['关注他人数量: ', video_info['authorStats']['followingCount']],
        ['获赞总量: ', video_info['authorStats']['heart']],
        ['视频总量: ', video_info['authorStats']['videoCount']],
        ['原视频链接: ', put_link('点击打开原视频', item, new_window=True)],
        ['当前视频API链接: ', put_link('点击浏览API数据', api_url, new_window=True)]
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
        put_markdown("💽API文档")
        put_markdown("API可将请求参数转换为需要提取的无水印视频/图片直链，配合IOS捷径可实现应用内下载。")
        put_link('[中文文档]', 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8',
                 new_window=True)
        put_html('<br>')
        put_link('[英文文档]',
                 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README-EN.md#%EF%B8%8Fapi-usage',
                 new_window=True)
        put_html('<hr>')
        put_markdown("🛰️API参考")
        put_markdown('抖音/TikTok解析请求参数')
        put_code('http://localhost(服务器IP):80/api?url="复制的(抖音/TikTok)的(分享文本/链接)"\n#返回JSON')
        put_markdown('抖音/TikTok视频下载请求参数')
        put_code('http://localhost(服务器IP):80/download_video?url="复制的抖音/TikTok链接"\n#返回mp4文件下载请求')
        put_markdown('抖音视频/图集音频下载请求参数')
        put_code('http://localhost(服务器IP):80/download_bgm?url="复制的抖音/TikTok链接"\n#返回mp3文件下载请求')


def error_log_popup_window():
    with popup('错误日志'):
        content = open(r'./logs.txt', 'rb').read()
        put_file('logs.txt', content=content)
        with open('./logs.txt', 'r') as f:
            content = f.read()
            put_text(str(content))


def about_popup_window():
    with popup('更多信息'):
        put_html('<h3>⚠️关于解析失败</h3>')
        put_text('目前已知短时间大量访问抖音API可能触发其验证码。')
        put_text('若多次解析失败后，请等待一段时间再尝试。')
        put_button("错误日志", onclick=lambda: error_log_popup_window(), link_style=True, small=True)
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
    with popup('Select Site Language'):
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
             put_button("API", onclick=lambda: api_document_pop_window(), link_style=True, small=True),
             put_button("关于", onclick=lambda: about_popup_window(), link_style=True, small=True),
             put_button("Language", onclick=lambda: language_pop_window(), link_style=True, small=True),
             put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg',
                       title='访问记录')
             ])
    placeholder = "批量解析请直接粘贴多个口令或链接，无需使用符号分开，支持抖音和TikTok链接混合。"
    kou_ling = textarea('请将抖音或TikTok的分享口令或网址粘贴于此', type=TEXT, validate=valid_check, required=True,
                        placeholder=placeholder,
                        position=0)
    if kou_ling:
        if kou_ling == 'wyn':
            # 好想你
            with popup('给 WYN💖'):
                put_text('常见朋友们发一些浪漫的文案。')
                put_text('我想，')
                put_text('浪慢的话我也会写，')
                put_text('但是让谁来听呢？')
                put_text('或者又能给谁看呢？')
                put_text('我想，')
                put_text('这大抵是安慰自己罢了...')
                put_text('新年快乐🧨')
                put_text('2022/02/01')
                put_text('-Evil0ctal')
                put_link('返回主页', '/')
        else:
            url_lists = find_url(kou_ling)
            # 解析开始时间
            start = time.time()
            try:
                loading(url_lists)
                for url in url_lists:
                    if 'douyin.com' in url:
                        put_result(url)
                    else:
                        put_tiktok_result(url)
                clear('bar')
                # 解析结束时间
                end = time.time()
                put_html("<br><hr>")
                put_link('返回主页', '/')
                put_text('解析完成! 耗时: %.4f秒' % (end - start))
            except Exception as e:
                # 异常捕获
                clear('bar')
                error_do(e, 'main')
                end = time.time()
                put_text('解析完成! 耗时: %.4f秒' % (end - start))


if __name__ == "__main__":
    app.add_url_rule('/', 'webio_view', webio_view(main), methods=['GET', 'POST', 'OPTIONS'])
    app.run(host='0.0.0.0', port=80)
