#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2022/10/17
# @Function:
# 用于在线批量解析Douyin/TikTok的无水印视频/图集。
# 基于 PyWebIO、Flask, 将scraper.py返回的内容显示在网页上。
# 默认运行端口5000, 请自行在config.ini中修改。


import os
import re
import time
import json
import tarfile
import requests
import configparser
from scraper import Scraper
from pywebio import config, session
from pywebio.input import *
from pywebio.output import *
from pywebio.platform.flask import webio_view
from pywebio.session import info as session_info
from flask import Flask


app = Flask(__name__, static_url_path='')
app_config = configparser.ConfigParser()
app_config.read('config.ini', encoding='utf-8')
web_config = app_config['Web_ZH']
title = web_config['Web_Title']
description = web_config['Web_Description']
keywords = web_config['Keywords']
api_config = app_config['Web_API']
api_url = api_config['URL']
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


def t(chinese, eng):
    """return English or Chinese text according to the user's browser language"""
    return chinese if 'zh' in session_info.user_language else eng


def loading():
    # 写一个进度条装装样子吧 :)
    set_scope('bar', position=3)
    with use_scope('bar'):
        put_processbar('bar')
        for i in range(1, 4):
            set_processbar('bar', i / 3)
            time.sleep(0.1)


def find_url(string):
    # 解析抖音分享口令中的链接并返回列表
    url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
    return url


def valid_check(kou_ling):
    # 校验输入的内容
    url_list = find_url(kou_ling)
    # 对每一个链接进行校验
    if url_list:
        total_urls = len(url_list)
        # 最大接受提交URL的数量
        max_urls = web_config['Max_Take_URLs']
        if total_urls > int(max_urls):
            warn_info = t('URL数量过多，只会处理前{}个URL。'.format(max_urls), 'Too many URLs, only the first {} URLs will be processed.'.format(max_urls))
            return warn_info
        else:
            for i in url_list:
                if 'douyin.com' in i[:31]:
                    if i == url_list[-1]:
                        return None
                elif 'tiktok.com' in i[:31]:
                    if i == url_list[-1]:
                        return None
                else:
                    warn_info = t('请确保输入链接均为有效的抖音/TikTok链接!', 'Please make sure that the input links are all valid Douyin/TikTok links!')
                    return warn_info
    elif kou_ling == 'wyn':
        return None
    else:
        warn_info = t('抖音/TikTok分享口令有误!', 'Douyin or TikTok share cotent!')
        return warn_info


def error_do(reason, function, value):
    # 输出一个毫无用处的信息
    put_html("<hr>")
    put_error(t("发生了了意料之外的错误，输入值已被记录。", "An unexpected error occurred, the input value has been recorded."))
    put_html('<h3>⚠{}</h3>'.format(t('详情', 'Details')))
    put_table([
        [t('函数名', 'function name'), t('原因', 'reason'), t('输入值', 'input value')],
        [function, str(reason), value]])
    put_markdown(t('可能的原因:', 'Possible reasons:'))
    put_markdown(t('服务器可能被目标主机的防火墙限流(稍等片刻后再次尝试)', 'The server may be limited by the target host firewall (try again after a while)'))
    put_markdown(t('输入了错误的链接(API-V1暂不支持主页链接解析)', 'Entered the wrong link (the home page link is not supported for parsing with API-V1)'))
    put_markdown(t('如果需要解析个人主页，请使用API-V2', 'If you need to parse the personal homepage, please use API-V2'))
    put_markdown(t('API-V2 文档: [https://api-v2.douyin.wtf/docs](https://api-v2.douyin.wtf/docs)', 'API-V2 Documentation: [https://api-v2.douyin.wtf/docs](https://api-v2.douyin.wtf/docs)'))
    put_markdown(t('该视频已经被删除或屏蔽(你看的都是些啥(⊙_⊙)?)', 'The video has been deleted or blocked (what are you watching (⊙_⊙)?)'))
    put_markdown(t('其他原因(请联系作者)', 'Other reasons (please contact the author)'))
    put_html("<hr>")
    put_markdown(t('你可以在右上角的关于菜单中查看本站错误日志。', 'You can view the error log of this site in the about menu in the upper right corner.'))
    put_markdown('[{}](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)'.format(t('点击此处在GayHub上进行反馈', 'Click here to give feedback on GayHub')))
    put_html("<hr>")
    if web_config['Allow_Logs'] == 'True':
        # 将错误记录在logs.txt中
        error_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open('logs.txt', 'a') as f:
            f.write(error_date + ":\n" + function + ': ' + str(reason) + '\n' + "Input value: " + value + '\n')


def clean_filename(string, author_name):
    # 替换不能用于文件名的字符('/ \ : * ? " < > |')
    rstr = r"[\/\\\:\*\?\"\<\>\|]"
    # 将上述字符替换为下划线
    new_title = re.sub(rstr, "_", string)
    # 新文件名
    filename = (new_title + '_' + author_name).replace('\n', '')
    return filename


def compress_file(tar_file, target_file):
    # tar_file是输出压缩包名字以及目录("./output/mp4.tar")，target_file是要打包的目录或文件名("./files")
    if os.path.isfile(target_file):
        with tarfile.open(tar_file, 'w', encoding='utf-8') as tar:
            tar.add(target_file)
            return 'finished'
    else:
        with tarfile.open(tar_file, 'w', encoding='utf-8') as tar:
            for root, dirs, files in os.walk(target_file):
                for single_file in files:
                    filepath = os.path.join(root, single_file)
                    tar.add(filepath)
            return 'finished'


def clean_file(path):
    # 清理下载文件夹
    while True:
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
                # print("%s文件删除成功 %s" % (name, (time.strftime("%d/%m/%Y%H:%M:%S"))))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
                # print("%s子文件夹下文件删除成功 %s" % (name, (time.strftime("%d/%m/%Y%H:%M:%S"))))
        # 每30分钟(1800秒)清理一次
        time.sleep(1800)


def video_download_window(result_dict):
    try:
        # result_dict = {'文件名': '链接'}
        total_amount = len(result_dict)
        download_time = (time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime()))
        # 存储根目录
        save_path = './web/saved_videos/' + (download_time + '_total_' + str(total_amount) + '_videos')
        # 判断目录是否存在
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        # 弹出窗口
        with popup(t("正在服务器后台下载视频(共{}个下载任务)", "Downloading videos in the background of the server.({} download tasks in total)").format(str(len(result_dict)))):
            # 下载索引计数
            download_count = 0
            # 遍历字典的键和值
            for file_name, url in result_dict.items():
                try:
                    put_info(t('正在下载第{}个视频:\n{}', 'Downloading video {}:\n{}').format(download_count+1, file_name))
                    response = requests.get(url, headers=headers)
                    data = response.content
                    if data:
                        file_path = '{}/{}.{}'.format(save_path, file_name, 'mp4')
                        if not os.path.exists(file_path):
                            with open(file_path, 'wb') as f:
                                f.write(data)
                                f.close()
                                put_success(t('{}下载成功', '{}download successful').format(file_name))
                                download_count += 1
                except Exception as e:
                    download_count += 1
                    put_error(t('视频下载失败，将跳过该视频。', 'Video download failed, this video will be skipped. '))
                    continue
            if download_count == total_amount:
                put_html('<hr>')
                put_html('<h3>💾{}</h3>'.format(t('结果页视频合集下载完成', 'Result page video collection download complete')))
                output_path = save_path + '/output'
                tarfile_name = download_time + '_total_' + str(total_amount) + '_videos.tar'
                output_file = output_path + '/' + tarfile_name
                put_info(t('正在压缩视频文件，请勿关闭当前弹窗，完成后会在下方显示按钮...', 'Compressing video files, please do not close the current pop-up window, and a button will be displayed below after completion...'))
                # 判断目录是否存在
                if not os.path.exists(output_path):
                    os.mkdir(output_path)
                if compress_file(tar_file=output_file, target_file=save_path) == 'finished':
                    tar = open(output_file, "rb").read()
                    put_file(tarfile_name, tar, t('点击下载视频合集压缩包(不包含图集)', 'Click to download video collection compression package (not including gallery)'))
    except Exception as e:
        print(str(e))


def put_douyin_result(item):
    # 向前端输出表格
    api = Scraper()
    # 抖音数据
    douyin_date = api.douyin(item)
    if douyin_date['status'] == 'success':
        # API链接
        short_api_url = api_url + '/api?url=' + item
        download_video = api_url + '/video?url=' + item
        download_bgm = api_url + '/music?url=' + item
        if douyin_date['url_type'] == 'video':
            put_table([
                [t('类型', 'type'), t('内容', 'content')],
                [t('格式:', 'format'), douyin_date['url_type']],
                [t('视频直链: ', 'No watermark URL: '), put_link(t('点击打开视频', 'Click to open video'), douyin_date['nwm_video_url'], new_window=True)],
                [t('视频直链1080p: ', 'No watermark URL 1080p: '), put_link(t('点击打开视频', 'Click to open video'), douyin_date['nwm_video_url_1080p'], new_window=True)],
                [t('视频下载：', 'Video download: '), put_link(t('点击下载视频', 'Click to download video'), download_video, new_window=True)],
                [t('背景音乐直链: ', 'Background music URL: '), put_link(t('点击打开音频', 'Click to open audio'), douyin_date['video_music'], new_window=True)],
                [t('背景音乐下载：', 'Background music download: '), put_link(t('点击下载', 'Click to download'), download_bgm, new_window=True)],
                [t('视频标题: ', 'Video title: '), douyin_date['video_title']],
                [t('作者昵称: ', 'Author Nickname: '), douyin_date['video_author']],
                [t('作者抖音ID: ', 'Author Douyin ID: '), douyin_date['video_author_id']],
                [t('原视频链接: ', 'Original video link: '), put_link(t('点击打开原视频', 'Click to open the original video'), item, new_window=True)],
                [t('当前视频API链接: ', 'Current video API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), douyin_date['api_url'], new_window=True)],
                [t('当前视频精简API链接: ', 'Current video streamlining API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), short_api_url, new_window=True)]
            ])
            return {'status': 'success',
                    'type': 'video',
                    'video_title': douyin_date['video_title'],
                    'video_author': douyin_date['video_author'],
                    'nwm_video_url': douyin_date['nwm_video_url'],
                    'video_music': douyin_date['video_music'],
                    'original_url': douyin_date['original_url']}
        else:
            put_table([
                [t('类型', 'type'), t('内容', 'content')],
                [t('格式:', 'format'), douyin_date['url_type']],
                [t('背景音乐直链: ', 'Background music URL: '), put_link(t('点击打开音频', 'Click to open audio'), douyin_date['album_music'], new_window=True)],
                [t('背景音乐下载：', 'Background music download: '), put_link(t('点击下载', 'Click to download'), download_bgm, new_window=True)],
                [t('图集标题: ', 'Album video title: '), douyin_date['album_title']],
                [t('作者昵称: ', 'Author Nickname: '), douyin_date['album_author']],
                [t('作者抖音ID: ', 'Author Douyin ID: '), douyin_date['album_author_id']],
                [t('原视频链接: ', 'Original video link: '), put_link(t('点击打开原视频', 'Click to open the original video'), douyin_date['original_url'], new_window=True)],
                [t('当前视频API链接: ', 'Current video API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), douyin_date['api_url'], new_window=True)],
                [t('当前视频精简API链接: ', 'Current video streamlining API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), short_api_url, new_window=True)]
            ])
            for i in douyin_date['album_list']:
                put_table([
                    [t('图片直链: ', 'Picture link:'), put_link(t('点击打开图片', 'Click to open image'), i, new_window=True), put_image(i)]
                ])
            return {'status': 'success',
                    'type': 'album',
                    'album_title': douyin_date['album_title'],
                    'album_author': douyin_date['album_author'],
                    'album_list': douyin_date['album_list'],
                    'album_music': douyin_date['album_music'],
                    'original_url': douyin_date['original_url']}
    else:
        # {'status': 'failed', 'reason': e, 'function': 'API.tiktok()', 'value': original_url}
        reason = douyin_date['reason']
        function = douyin_date['function']
        value = douyin_date['value']
        error_do(reason, function, value)
        return 'failed'


def put_tiktok_result(item):
    # 将TikTok结果显示在前端
    api = Scraper()
    # TikTok数据
    tiktok_data = api.tiktok(item)
    if tiktok_data['status'] == 'success':
        # API链接
        short_api_url = api_url + '/api?url=' + item
        download_video = api_url + '/video?url=' + item
        download_bgm = api_url + '/music?url=' + item
        if tiktok_data['url_type'] == 'video':
            put_table([
                [t('类型', 'type'), t('内容', 'content')],
                [t('格式:', 'format'), tiktok_data['url_type']],
                [t('视频标题: ', 'Video title: '), tiktok_data['video_title']],
                [t('视频直链(有水印): ', 'Video direct link (with watermark): '), put_link(t('点击打开视频', 'Click to open video'), tiktok_data['wm_video_url'], new_window=True)],
                [t('视频直链(无水印): ', 'Video direct link (no watermark): '), put_link(t('点击打开视频', 'Click to open video'), tiktok_data['nwm_video_url'], new_window=True)],
                [t('视频下载(无水印)：', 'Video download (no watermark): '), put_link(t('点击下载', 'Click to download'), download_video, new_window=True)],
                [t('音频(名称-作者)：', 'Audio (name-author): '), tiktok_data['video_music_title'] + " - " + tiktok_data['video_music_author']],
                [t('音频链接：', 'Audio link: '), put_link(t('点击播放', 'Click to play'), tiktok_data['video_music_url'], new_window=True)],
                [t('音频下载：', 'Audio download: '), put_link(t('点击下载', 'Click to download'), download_bgm, new_window=True)],
                [t('作者昵称: ', 'Author Nickname: '), tiktok_data['video_author_nickname']],
                [t('作者ID: ', 'Author ID: '), tiktok_data['video_author_id']],
                [t('粉丝数量: ', 'Number of fans: '), tiktok_data['video_author_followerCount']],
                [t('关注他人数量: ', 'Follow others: '), tiktok_data['video_author_followingCount']],
                [t('获赞总量: ', 'Total likes: '), tiktok_data['video_author_heartCount']],
                [t('视频总量: ', 'Total videos: '), tiktok_data['video_author_videoCount']],
                [t('原视频链接: ', 'Original video link: '), put_link(t('点击打开原视频', 'Click to open the original video'), item, new_window=True)],
                [t('当前视频API链接: ', 'Current video API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), tiktok_data['api_url'], new_window=True)],
                [t('当前视频API链接: ', 'Current video API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), short_api_url, new_window=True)]
            ])
            return {'status': 'success',
                    'type': 'video',
                    'video_title': tiktok_data['video_title'],
                    'video_author': tiktok_data['video_author_nickname'],
                    'nwm_video_url': tiktok_data['nwm_video_url'],
                    'video_music_url': tiktok_data['video_music_url'],
                    'original_url': item}
        else:
            put_table([
                [t('类型', 'type'), t('内容', 'content')],
                [t('格式:', 'format'), tiktok_data['url_type']],
                [t('背景音乐直链: ', 'Background music URL: '), put_link(t('点击打开音频', 'Click to open audio'), tiktok_data['album_music_url'], new_window=True)],
                [t('背景音乐下载：', 'Background music download: '), put_link(t('点击下载', 'Click to download'), download_bgm, new_window=True)],
                [t('视频标题: ', 'Video title: '), tiktok_data['album_title']],
                [t('作者昵称: ', 'Author Nickname: '), tiktok_data['album_author_nickname']],
                [t('作者ID: ', 'Author ID: '), tiktok_data['album_author_id']],
                [t('原视频链接: ', 'Original video link: '), put_link(t('点击打开原视频', 'Click to open the original video'), tiktok_data['original_url'], new_window=True)],
                [t('当前视频API链接: ', 'Current video API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), tiktok_data['api_url'], new_window=True)],
                [t('当前视频精简API链接: ', 'Current video streamlining API link: '), put_link(t('点击浏览API数据', 'Click to browse API data'), 'short_api_url', new_window=True)]
            ])
            for i in tiktok_data['album_list']:
                put_table([
                    [t('图片直链: ', 'Picture link:'), put_link(t('点击打开图片', 'Click to open image'), i, new_window=True), put_image(i)]
                ])
            return {'status': 'success',
                    'type': 'album',
                    'album_title': tiktok_data['album_title'],
                    'album_author': tiktok_data['album_author_nickname'],
                    'album_list': tiktok_data['album_list'],
                    'album_music': tiktok_data['album_music_url'],
                    'original_url': tiktok_data['original_url']}
    else:
        # {'status': 'failed', 'reason': e, 'function': 'API.tiktok()', 'value': original_url}
        reason = tiktok_data['reason']
        function = tiktok_data['function']
        value = tiktok_data['value']
        error_do(reason, function, value)
        return 'failed'


def ios_pop_window():
    with popup(t("iOS快捷指令", "iOS Shortcut")):
        try:
            shortcut = json.loads(requests.get(url='https://api.douyin.wtf/ios', headers=headers).text)
            shortcut_link_zh = shortcut['link']
            shortcut_link_en = shortcut['link_en']
            shortcut_note = shortcut['note']
            shortcut_update = shortcut['update']
            shortcut_version = shortcut['version']
        except Exception as e:
            shortcut_link = t('无法获取快捷指令信息,请到Github上进行反馈。', 'Unable to get shortcut information, please provide feedback on Github.')
            shortcut_note = t('无法获取快捷指令信息,请到Github上进行反馈。', 'Unable to get shortcut information, please provide feedback on Github.')
            shortcut_update = t('无法获取快捷指令信息,请到Github上进行反馈。', 'Unable to get shortcut information, please provide feedback on Github.')
            shortcut_version = t('无法获取快捷指令信息,请到Github上进行反馈。', 'Unable to get shortcut information, please provide feedback on Github.')
        put_text(t('快捷指令需要在抖音或TikTok的APP内，浏览你想要无水印保存的视频或图集。', 'The shortcut needs to be used in the Douyin or TikTok app, browse the video or album you want to save without watermark.'))
        put_text(t('然后点击右下角分享按钮，选择更多，然后下拉找到 "抖音TikTok无水印下载" 这个选项。', 'Then click the share button in the lower right corner, select more, and then scroll down to find the "Douyin TikTok No Watermark Download" option.'))
        put_text(t('如遇到通知询问是否允许快捷指令访问xxxx (域名或服务器)，需要点击允许才可以正常使用。', 'If you are asked whether to allow the shortcut to access xxxx (domain name or server), you need to click Allow to use it normally.'))
        put_text(t('该快捷指令会在你相册创建一个新的相薄方便你浏览保存的内容。', 'The shortcut will create a new album in your photo album to help you browse the saved content.'))
        put_html('<hr>')
        put_text(t('最新快捷指令版本: {}', 'Latest shortcut version: {}').format(shortcut_version))
        put_text(t('快捷指令更新时间: {}', 'Shortcut update time: {}').format(shortcut_update))
        put_text(t('快捷指令更新内容: {}', 'Shortcut update content: {}').format(shortcut_note))
        put_link('[点击获取快捷指令 - 中文]', shortcut_link_zh, new_window=True)
        put_html('<br>')
        put_link('[Click get Shortcut - English]', shortcut_link_en, new_window=True)


def api_document_pop_window():
    with popup(t("API文档", "API Document")):
        put_markdown(t("💾API-V2文档", "💾API-V2 Document"))
        put_markdown(t('API-V2已经上线啦，支持更多功能，具体请查看接口文档。',
                       'API-V2 has been launched and supports more functions. For details, please refer to the API documentation.'))
        put_link('[API-V2 Docs]', 'https://api-v2.douyin.wtf/docs', new_window=True)
        put_html('<hr>')
        put_markdown(t("💽API-V1文档", "💽API-V1 Document"))
        put_markdown(t("API可将请求参数转换为需要提取的无水印视频/图片直链，配合IOS捷径可实现应用内下载。", "API can convert the request parameters to the direct link of the watermark-free video/picture, which can be used with IOS shortcut to achieve download within the application."))
        put_link('[中文文档]', 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8',
                 new_window=True)
        put_html('<br>')
        put_link('[English doc]',
                 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.en.md#%EF%B8%8Fapi-usage',
                 new_window=True)
        put_html('<hr>')
        put_markdown(t("🛰️API参考", "🛰️API Reference"))
        put_markdown(t('抖音/TikTok解析请求参数', 'Douyin/TikTok parsing request parameters'))
        put_code('https://api.douyin.wtf/api?url="复制的抖音链接/TikTok URL"\n#Response JSON')
        put_markdown(t('抖音/TikTok视频下载请求参数', 'Douyin/TikTok video download request parameters'))
        put_code('https://api.douyin.wtf/video?url="复制的抖音链接/TikTok URL"\n# Response MP4 file')
        put_markdown(t('抖音视频/图集音频下载请求参数', 'Douyin video/album audio download request parameters'))
        put_code('https://api.douyin.wtf/music?url="复制的抖音链接/TikTok URL"\n# Response MP3 file')


def log_popup_window():
    with popup(t('错误日志', 'Error Log')):
        put_html('<h3>⚠️{}</h3>'.format('关于解析失败可能的原因', 'About the possible reasons for parsing failure'))
        put_markdown(t('服务器可能被目标主机的防火墙限流(稍等片刻后再次尝试)', 'The server may be limited by the target host firewall (try again after a while)'))
        put_markdown(t('输入了错误的链接(暂不支持主页链接解析)', 'Entered the wrong link (not supported for home page link parsing)'))
        put_markdown(t('该视频已经被删除或屏蔽(你看的都是些啥(⊙_⊙)?)', 'The video has been deleted or blocked (what are you watching(⊙_⊙)?)'))
        put_markdown(t('[点击此处在GayHub上进行反馈](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)', '[Click here to feedback on GayHub](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)'))
        put_html('<hr>')
        put_text(t('点击logs.txt可下载日志:', 'Click logs.txt to download the log:'))
        content = open(r'./logs.txt', 'rb').read()
        put_file('logs.txt', content=content)
        with open('./logs.txt', 'r') as f:
            content = f.read()
            put_text(str(content))


def about_popup_window():
    with popup(t('更多信息', 'More Information')):
        put_html('<h3>👀{}</h3>'.format(t('访问记录', 'Visit Record')))
        put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg',
                  title='访问记录')
        put_html('<hr>')
        put_html('<h3>⭐Github</h3>')
        put_markdown('[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)')
        put_html('<hr>')
        put_html('<h3>🎯{}</h3>'.format(t('反馈', 'Feedback')))
        put_markdown('{}：[issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)'.format(t('Bug反馈', 'Bug Feedback')))
        put_html('<hr>')
        put_html('<h3>💖WeChat</h3>')
        put_markdown('WeChat：[Evil0ctal](https://mycyberpunk.com/)')
        put_html('<hr>')


@config(title=title, description=description)
def main():
    # 设置favicon
    favicon_url = "https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/favicon/android-chrome-512x512.png"
    session.run_js("""
    $('head').append('<meta name=referrer content=no-referrer>');
    $('#favicon32,#favicon16').remove(); 
    $('head').append('<link rel="icon" type="image/png" href="%s">')
    """ % favicon_url)
    # 设置Keywords
    session.run_js("""
        $('head').append('<meta name="keywords" content={}>')
        """.format(keywords))
    # 修改footer
    session.run_js("""$('footer').remove()""")
    # 访问记录
    view_amount = requests.get("https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg")
    put_markdown("""<div align='center' ><font size='20'>😼{}</font></div>""".format(t('抖音/TikTok无水印在线解析', 'Douyin/TikTok online analysis without watermark')))
    put_html('<hr>')
    put_row([put_button(t("iOS快捷指令", 'iOS Shortcuts'), onclick=lambda: ios_pop_window(), link_style=True, small=True),
             put_button("API", onclick=lambda: api_document_pop_window(), link_style=True, small=True),
             put_button(t("日志", "Log"), onclick=lambda: log_popup_window(), link_style=True, small=True),
             put_button(t("关于", 'About'), onclick=lambda: about_popup_window(), link_style=True, small=True)
             ])
    placeholder = t("批量解析请直接粘贴多个口令或链接，无需使用符号分开，支持抖音和TikTok链接混合，暂时不支持作者主页链接批量解析。", "Batch parsing, please paste multiple passwords or links directly, no need to use symbols to separate, support for mixing Douyin and TikTok links, temporarily not support for author home page link batch parsing.")
    kou_ling = textarea(t('请将抖音或TikTok的分享口令或网址粘贴于此', "Please paste the share password or URL of Douyin or TikTok here"), type=TEXT, validate=valid_check, required=True,
                        placeholder=placeholder,
                        position=0)
    if kou_ling:
        if kou_ling == 'wyn':
            # 好想你(小彩蛋)
            with popup('给 WYN💖'):
                put_text('我大约真的没有什么才华，只是因为有幸见着了你，于是这颗庸常的心中才凭空生出好些浪漫。')
                put_text('真的好爱你呀！')
                put_link('WYN&THB', 'https://www.wynthb.com/')
        else:
            url_lists = find_url(kou_ling)
            total_urls = len(url_lists)
            # 解析开始时间
            start = time.time()
            # 放一个毫无意义的进度条
            loading()
            # 成功/失败统计
            success_count = 0
            failed_count = 0
            # 解析成功的url
            success_list = []
            # 解析失败的url
            failed_list = []
            # 成功解析的视频标题/视频直链
            nwm_success_list = {}
            # 遍历链接
            for url in url_lists:
                if 'douyin.com' in url:
                    result = put_douyin_result(url)
                    if result == 'failed':
                        failed_count += 1
                        # 将url添加到失败列表内
                        failed_list.append(url)
                        continue
                    else:
                        success_count += 1
                        # 将url添加到成功列表内
                        success_list.append(url)
                        if result['type'] == 'video':
                            filename = clean_filename(string=result['video_title'], author_name=result['video_author'])
                            nwm_success_list.update({filename: result['nwm_video_url']})
                else:
                    result = put_tiktok_result(url)
                    if result == 'failed':
                        failed_count += 1
                        # 将url添加到失败列表内
                        failed_list.append(url)
                        continue
                    else:
                        success_count += 1
                        # 将url添加到成功列表内
                        success_list.append(url)
                        if result['type'] == 'video':
                            filename = clean_filename(string=result['video_title'], author_name=result['video_author'])
                            nwm_success_list.update({filename: result['nwm_video_url']})
            clear('bar')
            # 解析结束时间
            end = time.time()
            put_html("<br><hr>")
            put_text(t('总共收到', "Total received") + str(total_urls) + t('个链接', 'URLs'))
            put_text(t('成功: ', "Success: ") + str(success_count) + ' ' + t('失败: ', 'Failed: ') + str(failed_count))
            put_text(t('解析共耗时: %.4f秒', 'Total parsing time: %.4f seconds') % (end - start))
            if web_config['Allow_Batch_Download'] == 'True':
                put_button(t("下载结果页中的所有视频", "Download all videos in the results page"), onclick=lambda: video_download_window(nwm_success_list))
            put_link(t('返回主页', 'Back to home'), '/')
            time.sleep(300)
            # 清理文件夹
            clean_file('./web/saved_videos')


if __name__ == "__main__":
    # 初始化logs.txt
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('logs.txt', 'a') as f:
        f.write("Time: " + date + " " + "Program reload completed!" + '\n')
    # 判断是否使用CDN加载前端资源
    if web_config['PyWebIO_CDN'] == 'True':
        cdn = True
    else:
        cdn = False
    app.add_url_rule('/', 'webio_view', webio_view(main, cdn=cdn), methods=['GET', 'POST', 'OPTIONS'])
    # 获取空闲端口
    if os.environ.get('PORT'):
        port = int(os.environ.get('PORT'))
    else:
        # 在这里修改默认端口(记得在防火墙放行该端口)
        port = web_config['Port']
    app.run(host='0.0.0.0', port=port)
