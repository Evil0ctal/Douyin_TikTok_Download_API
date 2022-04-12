#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2022/04/05
# @Function:
# 用于在线批量解析Douyin/TikTok的无水印视频/图集。
# 基于 PyWebIO、Flask, 将scraper.py返回的内容显示在网页上。
# 默认运行端口5000, 请自行在文件底部修改。


import os
import re
import time
import tarfile
import requests
from scraper import Scraper
from pywebio import config, session
from pywebio.input import *
from pywebio.output import *
from pywebio.platform.flask import webio_view
from flask import Flask, request, jsonify

app = Flask(__name__)
title = "抖音/TikTok无水印在线解析"
description = "支持在线批量解析下载无水印抖音/TikTok的无水印视频/图集。支持API调用，开源，免费，无广告。"
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


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
        if total_urls > 30:
            return '为了避免资源占用过多请确保输入链接少于30个！'
        else:
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


def error_do(reason, function, value):
    # 输出一个毫无用处的信息
    put_html("<hr>")
    put_error("发生了了意料之外的错误，输入值已被记录。")
    put_html('<h3>⚠详情</h3>')
    put_table([
        ['函数名', '原因', '输入值'],
        [function, str(reason), value]])
    put_markdown('可能的原因:')
    put_markdown('服务器可能被目标主机的防火墙限流(稍等片刻后再次尝试)')
    put_markdown('输入了错误的链接(暂不支持主页链接解析)')
    put_markdown('该视频已经被删除或屏蔽(你看的都是些啥(⊙_⊙)?)')
    put_markdown('你可以在右上角的关于菜单中查看本站错误日志。')
    put_markdown('[点击此处在GayHub上进行反馈](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)')
    put_html("<hr>")
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
    filename = (author_name + '_' + new_title).replace('\n', '')
    return filename


def compress_file(tar_file, target_file):
    # tar_file是输出压缩包名字以及目录("./output/mp4.tar")，target_file是要打包的目录或文件名("./files")
    if os.path.isfile(target_file):
        with tarfile.open(tar_file, 'w') as tar:
            tar.add(target_file)
    else:
        with tarfile.open(tar_file, 'w') as tar:
            for root, dirs, files in os.walk(target_file):
                for single_file in files:
                    filepath = os.path.join(root, single_file)
                    tar.add(filepath)


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
        with popup("正在服务器后台下载视频(共{}个下载任务)".format(str(len(result_dict)))):
            # 下载索引计数
            download_count = 0
            # 遍历字典的键和值
            for file_name, url in result_dict.items():
                try:
                    download_count += 1
                    put_info('正在下载第{}个视频:\n{}'.format(download_count, file_name))
                    response = requests.get(url, headers=headers)
                    data = response.content
                    if data:
                        file_path = '{}/{}.{}'.format(save_path, file_name, 'mp4')
                        if not os.path.exists(file_path):
                            with open(file_path, 'wb') as f:
                                f.write(data)
                                f.close()
                                put_success('{}下载成功'.format(file_name))
                except Exception as e:
                    download_count += 1
                    put_error('视频下载失败，将跳过该视频。')
                    continue
            if download_count == total_amount:
                put_html('<hr>')
                put_html('<h3>💾结果页视频合集下载完成</h3>')
                output_path = save_path + '/output'
                tarfile_name = download_time + '_total_' + str(total_amount) + '_videos.tar'
                output_file = output_path + '/' + tarfile_name
                # 判断目录是否存在
                if not os.path.exists(output_path):
                    os.mkdir(output_path)
                compress_file(tar_file=output_file, target_file=save_path)
                tar = open(output_file, "rb").read()
                put_file(tarfile_name, tar, '点击下载视频合集压缩包')
    except Exception as e:
        print(str(e))


def put_douyin_result(item):
    # 向前端输出表格
    api = Scraper()
    # 抖音数据
    douyin_date = api.douyin(item)
    # API链接
    short_api_url = 'https://api.douyin.wtf/api?url=' + item
    download_video = 'https://api.douyin.wtf/video?url=' + item
    download_bgm = 'https://api.douyin.wtf/music?url=' + item
    if douyin_date['status'] == 'success':
        if douyin_date['url_type'] == 'video':
            put_table([
                ['类型', '内容'],
                ['格式:', douyin_date['url_type']],
                ['视频直链: ', put_link('点击打开视频', douyin_date['nwm_video_url'], new_window=True)],
                ['视频下载：', put_link('点击下载', download_video, new_window=True)],
                ['背景音乐直链: ', put_link('点击打开音频', douyin_date['video_music'], new_window=True)],
                ['背景音乐下载：', put_link('点击下载', download_bgm, new_window=True)],
                ['视频标题: ', douyin_date['video_title']],
                ['作者昵称: ', douyin_date['video_author']],
                ['作者抖音ID: ', douyin_date['video_author_id']],
                ['原视频链接: ', put_link('点击打开原视频', item, new_window=True)],
                ['当前视频API链接: ', put_link('点击浏览API数据', douyin_date['api_url'], new_window=True)],
                ['当前视频精简API链接: ', put_link('点击浏览API数据', short_api_url, new_window=True)]
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
                ['类型', '内容'],
                ['格式:', douyin_date['url_type']],
                ['背景音乐直链: ', put_link('点击打开音频', douyin_date['album_music'], new_window=True)],
                ['背景音乐下载：', put_link('点击下载', download_bgm, new_window=True)],
                ['视频标题: ', douyin_date['album_title']],
                ['作者昵称: ', douyin_date['album_author']],
                ['作者抖音ID: ', douyin_date['album_author_id']],
                ['原视频链接: ', put_link('点击打开原视频', douyin_date['original_url'], new_window=True)],
                ['当前视频API链接: ', put_link('点击浏览API数据', douyin_date['api_url'], new_window=True)],
                ['当前视频精简API链接: ', put_link('点击浏览API数据', 'short_api_url', new_window=True)]
            ])
            for i in douyin_date['album_list']:
                put_table([
                    ['图片直链: ', put_link('点击打开图片', i, new_window=True), put_image(i)]
                ])
            return {'status': 'success',
                    'type': 'album',
                    'album_title': douyin_date['album_title'],
                    'video_author': douyin_date['video_author'],
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
    tiktok_date = api.tiktok(item)
    if tiktok_date['status'] == 'success':
        # API链接
        short_api_url = 'https://api.douyin.wtf/api?url=' + item
        download_video = 'https://api.douyin.wtf/video?url=' + item
        download_bgm = 'https://api.douyin.wtf/music?url=' + item
        put_table([
            ['类型', '内容'],
            ['视频标题: ', tiktok_date['video_title']],
            ['视频直链(有水印): ', put_link('点击打开视频', tiktok_date['wm_video_url'], new_window=True)],
            ['视频直链(无水印): ', put_link('点击打开视频', tiktok_date['nwm_video_url'], new_window=True)],
            ['视频下载(无水印)：', put_link('点击下载', download_video, new_window=True)],
            ['音频(名称-作者)：', tiktok_date['video_music_title'] + " - " + tiktok_date['video_music_author']],
            ['音频播放：', put_link('点击播放', tiktok_date['video_music_url'], new_window=True)],
            ['作者昵称: ', tiktok_date['video_author_nickname']],
            ['作者ID: ', tiktok_date['video_author']],
            ['粉丝数量: ', tiktok_date['video_author_followerCount']],
            ['关注他人数量: ', tiktok_date['video_author_followingCount']],
            ['获赞总量: ', tiktok_date['video_author_heartCount']],
            ['视频总量: ', tiktok_date['video_author_videoCount']],
            ['原视频链接: ', put_link('点击打开原视频', item, new_window=True)],
            ['当前视频API链接: ', put_link('点击浏览API数据', short_api_url, new_window=True)]
        ])
        return {'status': 'success',
                'type': 'video',
                'video_title': tiktok_date['video_title'],
                'video_author': tiktok_date['video_author'],
                'nwm_video_url': tiktok_date['nwm_video_url'],
                'video_music_url': tiktok_date['video_music_url'],
                'original_url': item}
    else:
        # {'status': 'failed', 'reason': e, 'function': 'API.tiktok()', 'value': original_url}
        reason = tiktok_date['reason']
        function = tiktok_date['function']
        value = tiktok_date['value']
        error_do(reason, function, value)
        return 'failed'


def ios_pop_window():
    with popup("iOS快捷指令"):
        put_text('快捷指令需要在抖音或TikTok的APP内，浏览你想要无水印保存的视频或图集。')
        put_text('点击分享按钮，然后下拉找到 "抖音TikTok无水印下载" 这个选项。')
        put_text('如遇到通知询问是否允许快捷指令访问xxxx (域名或服务器)，需要点击允许才可以正常使用。')
        put_text('该快捷指令会在你相册创建一个新的相薄方便你浏览保存到内容。')
        put_html('<br>')
        put_link('[点击获取快捷指令]', 'https://www.icloud.com/shortcuts/e8243369340548efa0d4c1888dd3c170',
                 new_window=True)


def api_document_pop_window():
    with popup("API文档"):
        put_markdown("💽API文档")
        put_markdown("API可将请求参数转换为需要提取的无水印视频/图片直链，配合IOS捷径可实现应用内下载。")
        put_link('[中文文档]', 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8',
                 new_window=True)
        put_html('<br>')
        put_link('[English doc]',
                 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.en.md#%EF%B8%8Fapi-usage',
                 new_window=True)
        put_html('<hr>')
        put_markdown("🛰️API参考")
        put_markdown('抖音/TikTok解析请求参数')
        put_code('https://api.douyin.wtf/api?url="复制的(抖音/TikTok)的(分享文本/链接)"\n#返回JSON')
        put_markdown('抖音/TikTok视频下载请求参数')
        put_code('https://api.douyin.wtf/video?url="复制的抖音/TikTok链接"\n'
                 '# 返回mp4文件下载请求\n'
                 '# 大量请求时很吃服务器内存，容易崩，慎用。')
        put_markdown('抖音视频/图集音频下载请求参数')
        put_code('https://api.douyin.wtf/music?url="复制的抖音/TikTok链接"\n'
                 '# 返回mp3文件下载请求\n'
                 '# 大量请求时很吃服务器内存，容易崩，慎用。')


def log_popup_window():
    with popup('错误日志'):
        put_html('<h3>⚠️关于解析失败可能的原因</h3>')
        put_markdown('服务器可能被目标主机的防火墙限流(稍等片刻后再次尝试)')
        put_markdown('输入了错误的链接(暂不支持主页链接解析)')
        put_markdown('该视频已经被删除或屏蔽(你看的都是些啥(⊙_⊙)?)')
        put_markdown('[点击此处在GayHub上进行反馈](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)')
        put_html('<hr>')
        put_text('点击logs.txt可下载日志:')
        content = open(r'./logs.txt', 'rb').read()
        put_file('logs.txt', content=content)
        with open('./logs.txt', 'r') as f:
            content = f.read()
            put_text(str(content))


def about_popup_window():
    with popup('更多信息'):
        put_html('<h3>👀访问记录</h3>')
        put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg',
                  title='访问记录')
        put_html('<hr>')
        put_html('<h3>⭐Github</h3>')
        put_markdown('[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)')
        put_html('<hr>')
        put_html('<h3>🎯反馈</h3>')
        put_markdown('提交：[issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)')
        put_html('<hr>')
        put_html('<h3>🌐视频/图集批量下载</h3>')
        put_markdown('可以使用[IDM](https://www.zhihu.com/topic/19746283/hot)之类的工具对结果页面的链接进行嗅探。')
        put_markdown('如果你有更好的想法欢迎PR')
        put_html('<hr>')
        put_html('<h3>💖WeChat</h3>')
        put_markdown('微信：[Evil0ctal](https://mycyberpunk.com/)')
        put_html('<hr>')


@config(title=title, description=description)
def main():
    # 设置favicon
    favicon_url = "https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/favicon/android-chrome-512x512.png"
    session.run_js("""
    $('#favicon32,#favicon16').remove(); 
    $('head').append('<link rel="icon" type="image/png" href="%s">')
    """ % favicon_url)
    # 修改footer
    session.run_js("""$('footer').remove()""")
    # 访问记录
    view_amount = requests.get("https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg")
    put_markdown("""<div align='center' ><font size='20'>😼抖音/TikTok无水印在线解析</font></div>""")
    put_html('<hr>')
    put_row([put_button("快捷指令", onclick=lambda: ios_pop_window(), link_style=True, small=True),
             put_button("API", onclick=lambda: api_document_pop_window(), link_style=True, small=True),
             put_button("日志", onclick=lambda: log_popup_window(), link_style=True, small=True),
             put_button("关于", onclick=lambda: about_popup_window(), link_style=True, small=True)
             ])
    placeholder = "批量解析请直接粘贴多个口令或链接，无需使用符号分开，支持抖音和TikTok链接混合，暂时不支持作者主页链接批量解析。"
    kou_ling = textarea('请将抖音或TikTok的分享口令或网址粘贴于此', type=TEXT, validate=valid_check, required=True,
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
            put_text('总共收到' + str(total_urls) + '个链接')
            put_text('成功: ' + str(success_count) + ' ' + '失败: ' + str(failed_count))
            put_text('解析共耗时: %.4f秒' % (end - start))
            put_button("下载结果页中的所有视频", onclick=lambda: video_download_window(nwm_success_list))
            put_link('返回主页', '/')
            time.sleep(300)
            # 清理文件夹
            clean_file('./web/saved_videos')


if __name__ == "__main__":
    # 初始化logs.txt
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('logs.txt', 'a') as f:
        f.write("时间: " + date + " " + "程序重载完毕!" + '\n')
    app.add_url_rule('/', 'webio_view', webio_view(main), methods=['GET', 'POST', 'OPTIONS'])
    # 获取空闲端口
    if os.environ.get('PORT'):
        port = int(os.environ.get('PORT'))
    else:
        # 在这里修改默认端口(记得在防火墙放行该端口)
        port = 5000
    app.run(host='0.0.0.0', port=port)
