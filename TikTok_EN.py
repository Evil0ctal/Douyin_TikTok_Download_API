#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2022/01/31
# @Function:
"""
@Function:
This project uses PyWebIO, Requests, Flask as Python libraries
to download Douyin/TikTok's videos/gallery without watermark.
It can be used to download videos/gallery that the author has forbidden to download.
At the same time, it can be used with iOS shortcut APP
to cooperate with this project's API to realize internal download.
"""

from pywebio import config, session
from pywebio.input import *
from pywebio.output import *
from pywebio.platform.flask import webio_view
from retrying import retry
from werkzeug.urls import url_quote
from tiktok_downloader import info_post, tikmate
from flask import Flask, request, jsonify, make_response
import re
import json
import time
import requests
import unicodedata

app = Flask(__name__)
title = "Douyin/TikTok Downloader"
description = "Douyin/TikTok video/gallery analysis online"
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


def find_url(string):
    # Parse the link in the Douyin share password and return to the list
    url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
    return url


def valid_check(kou_ling):
    # Verify the content of the input
    url_list = find_url(kou_ling)
    # Verify each item in the content of the input
    if url_list:
        for i in url_list:
            if 'douyin.com' in i[:31]:
                if i == url_list[-1]:
                    return None
            elif 'tiktok.com' in i[:31]:
                if i == url_list[-1]:
                    return None
            else:
                return 'Please make sure that the input links are all valid Douyin/TikTok links!'
    elif kou_ling == 'wyn':
        return None
    else:
        return 'Douyin or TikTok share link is wrong!'


def clean_filename(string, author_name):
    # Replace characters that cannot be used in file names
    rstr = r"[\/\\\:\*\?\"\<\>\|]"  # '/ \ : * ? " < > |'
    new_title = re.sub(rstr, "_", string)  # Replace with underscore
    filename = 'douyin.wtf_TikTok_online_analysis' + new_title + '_' + author_name
    return filename


def error_do(e, func_name):
    # Output a useless message
    put_html("<hr>")
    put_error("An unexpected error occurred. \nplease check whether the input value is valid!")
    put_html('<h3>⚠Details</h3>')
    put_table([
        ['Function name', '原因'],
        [func_name, str(e)]])
    put_html("<hr>")
    put_markdown(
        'A lot of analysis of TikTok may cause its firewall to limit current!\nPlease wait 1-2 minutes and try again!\nIf you still fail after multiple attempts, please click [issues](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues).\nYou can view the error log of this site in the About menu in the upper right corner.)')
    put_link('return to home page', '/')
    # 将错误记录在logs.txt中
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('logs.txt', 'a') as f:
        f.write(date + " " + func_name + ': ' + str(e) + '\n')


def loading(url_lists):
    # Write a progress bar and pretend it :)
    total_len = len(url_lists)
    set_scope('bar', position=3)
    with use_scope('bar'):
        put_processbar('bar')
        for i in range(1, 4):
            set_processbar('bar', i / 3)
            time.sleep(0.1)


@retry(stop_max_attempt_number=3)
def get_video_info(original_url):
    # Use the official interface to parse the link information
    try:
        # Original video link
        r = requests.get(url=original_url, allow_redirects=False)
        try:
            # 2021/12/11 It is found that Douyin has restricted it, and it will automatically redirect the URL. The video ID cannot be obtained by the previous method, but it can still be obtained from the request header.
            long_url = r.headers['Location']
        except:
            # After the error is reported, it is judged as a long link, and the video id is directly intercepted
            long_url = original_url
        key = re.findall('video/(\d+)?', long_url)[0]
        api_url = f'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={key}'
        print("Sending request to: " + '\n' + api_url)
        js = json.loads(requests.get(url=api_url, headers=headers).text)
        # Determine whether it is an atlas
        try:
            image_data = js['item_list'][0]['images']
            # Gallery background audio
            image_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
            # Gallery title
            image_title = str(js['item_list'][0]['desc'])
            # Atlas author's nickname
            image_author = str(js['item_list'][0]['author']['nickname'])
            # Atlas Author Tik Tok
            image_author_id = str(js['item_list'][0]['author']['unique_id'])
            if image_author_id == "":
                # If the author has not modified the Douyin ID, this value should be used to avoid being unable to obtain its Douyin ID
                image_author_id = str(js['item_list'][0]['author']['short_id'])
            # Remove watermark atlas link
            images_url = []
            for data in image_data:
                images_url.append(data['url_list'][0])
            image_info = [images_url, image_music, image_title, image_author, image_author_id, original_url]
            return image_info, 'image', api_url
        # Determined as a video after reporting an error
        except:
            # After removing the watermark, the video link (the URL obtained by Douyin APi on January 1, 2022 will be redirected, and the direct link needs to be obtained in the Location)
            video_url = str(js['item_list'][0]['video']['play_addr']['url_list'][0]).replace('playwm', 'play')
            r = requests.get(url=video_url, headers=headers, allow_redirects=False)
            video_url = r.headers['Location']
            # Video background audio
            video_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
            # Video title
            video_title = str(js['item_list'][0]['desc'])
            # Video author nickname
            video_author = str(js['item_list'][0]['author']['nickname'])
            # Video author Douyin
            video_author_id = str(js['item_list'][0]['author']['unique_id'])
            if video_author_id == "":
                # If the author has not modified the Douyin ID, this value should be used to avoid being unable to obtain its Douyin ID
                video_author_id = str(js['item_list'][0]['author']['short_id'])
            # Return a list containing data
            video_info = [video_url, video_music, video_title, video_author, video_author_id, original_url]
            return video_info, 'video', api_url
    except Exception as e:
        # Exception capture
        error_do(e, 'get_video_info')


@retry(stop_max_attempt_number=3)
def get_video_info_tiktok(tiktok_url):
    # Analyze TikTok video
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
        print("The author_id is: ", author_id)
        print(video_info)
        # The format is messy, please bear with it
        return video_info
    except Exception as e:
        # Exception capture
        error_do(e, 'get_video_info_tiktok')


@retry(stop_max_attempt_number=3)
def tiktok_nowm(tiktok_url):
    # Use 3rd party API to get watermark-free video link (not guaranteed to be stable)
    try:
        api_url = "https://api.reiyuura.me/api/dl/tiktok?url="
        no_water_mark = api_url + tiktok_url
        res = requests.get(no_water_mark, headers=headers)
        print(res)
        result = json.loads(res.text)
        nowm = result['result']['nowm']
        return nowm
    except Exception as e:
        error_do(e, "tiktok_nwm")


@app.route("/api")
def webapi():
    # Create a Flask application to get POST parameters and return the results
    try:
        post_content = request.args.get("url")
        if post_content:
            # Record the API uses in API_logs.txt
            date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open('API_logs.txt', 'a') as f:
                f.write(date + " : " + post_content + '\n')
            # Verify whether it is a TikTok link
            if 'tiktok.com' in post_content:
                try:
                    js = get_video_info_tiktok(post_content)
                    return js
                except Exception:
                    return jsonify(Status='Failed!', Reason='Check the link!')
            # If the keyword does not exist, it is judged as a Douyin link
            elif 'douyin.com' in post_content:
                try:
                    response_data, result_type, api_url = get_video_info(post_content)
                    if result_type == 'image':
                        # Return atlas information json
                        return jsonify(Status='Success', Type='Image', image_url=response_data[0],
                                       image_music=response_data[1],
                                       image_title=response_data[2], image_author=response_data[3],
                                       image_author_id=response_data[4], original_url=response_data[5])
                    else:
                        # Return video information json
                        return jsonify(Status='Success', Type='Video', video_url=response_data[0],
                                       video_music=response_data[1],
                                       video_title=response_data[2], video_author=response_data[3],
                                       video_author_id=response_data[4], original_url=response_data[5])
                except:
                    return jsonify(Status='Failed!', Reason='Check the link!')
            else:
                return jsonify(Status='Failed!', Reason='Check the link!')

    except Exception as e:
        # Exception capture
        error_do(e, 'webapi')
        return jsonify(Message="Parsing failed", Reason=str(e), Result=False)


@app.route("/download_video", methods=["POST", "GET"])
def download_video_url():
    # Return to video download request
    input_url = request.args.get("url")
    try:
        if 'douyin.com' in input_url:
            video_info, result_type, api_url = get_video_info(input_url)
            video_url = video_info[0]
            # Video title
            video_title = video_info[2]
            # Author's nickname
            author_name = video_info[3]
            # Clean up the file name
            file_name = clean_filename(video_title, author_name)
        elif 'tiktok.com' in input_url:
            download_url = find_url(tikmate().get_media(input_url)[1].json)[0]
            return jsonify(Status='Success! Click to download!', No_WaterMark_Link=download_url)
        else:
            return jsonify(Status='Failed!', Reason='Check the link!')
        # video_title = 'video_title'
        video_mp4 = requests.get(video_url, headers).content
        # Encapsulate the video byte stream into a response object
        response = make_response(video_mp4)
        # Add response header information
        response.headers['Content-Type'] = "video/mp4"
        # Fuck, it took my boss to solve the file Chinese name problem
        try:
            filename = file_name.encode('latin-1')
        except UnicodeEncodeError:
            filenames = {
                'filename': unicodedata.normalize('NFKD', file_name).encode('latin-1', 'ignore'),
                'filename*': "UTF-8''{}".format(url_quote(file_name) + '.mp4'),
            }
        else:
            filenames = {'filename': file_name}
        # attachment means to download as an attachment
        response.headers.set('Content-Disposition', 'attachment', **filenames)
        return response
    except Exception as e:
        error_do(e, 'download_video_url')
        return jsonify(Status='Failed!', Reason='Check the link!')


@app.route("/download_bgm", methods=["POST", "GET"])
def download_bgm_url():
    # Return to video download request
    input_url = request.args.get("url")
    try:
        if 'douyin.com' in input_url:
            video_info, result_type, api_url = get_video_info(input_url)
            bgm_url = video_info[1]
            # Video title
            bgm_title = video_info[2]
            # Author's nickname
            author_name = video_info[3]
            # Clean up the file name
            file_name = clean_filename(bgm_title, author_name)
        else:
            return jsonify(Status='Failed', Reason='Coming soon!')
        video_title = 'video_bgm'
        video_bgm = requests.get(bgm_url, headers).content
        # Encapsulate the bgm byte stream into a response object
        response = make_response(video_bgm)
        # Add response header information
        response.headers['Content-Type'] = "video/mp3"
        # Fuck, it took my boss to solve the file Chinese name problem
        try:
            filename = file_name.encode('latin-1')
        except UnicodeEncodeError:
            filenames = {
                'filename': unicodedata.normalize('NFKD', file_name).encode('latin-1', 'ignore'),
                'filename*': "UTF-8''{}".format(url_quote(file_name) + '.mp3'),
            }
        else:
            filenames = {'filename': file_name}
        # attachment means to download as an attachment
        response.headers.set('Content-Disposition', 'attachment', **filenames)
        return response
    except Exception as e:
        error_do(e, 'download_bgm_url')
        return jsonify(Status='Failed!', Reason='Check the link!')


def put_result(item):
    # Output the form to the front end according to the parsed format
    video_info, result_type, api_url = get_video_info(item)
    short_api_url = '/api?url=' + item
    if result_type == 'video':
        download_video = '/download_video?url=' + video_info[5]
        download_bgm = '/download_bgm?url=' + video_info[5]
        put_table([
            ['type', 'content'],
            ['Format:', result_type],
            ['Video straight link: ', put_link('Click to open the video', video_info[0], new_window=True)],
            ['Video download：', put_link('click to download', download_video, new_window=True)],
            ['Background music straight link: ', put_link('Click to open audio', video_info[1], new_window=True)],
            ['Background music download：', put_link('click to download', download_bgm, new_window=True)],
            ['Video title: ', video_info[2]],
            ['Author nickname: ', video_info[3]],
            ['Author Douyin ID: ', video_info[4]],
            ['Original video link: ', put_link('Click to open the original video', video_info[5], new_window=True)],
            ['Current video API link: ', put_link('Click to browse API data', api_url, new_window=True)],
            ['Current video streamline API link: ', put_link('Click to browse API data', short_api_url, new_window=True)]
        ])
    else:
        download_bgm = '/download_bgm?url=' + video_info[5]
        put_table([
            ['type', 'content'],
            ['Format:', result_type],
        ])
        for i in video_info[0]:
            put_table([
                ['Picture straight link: ', put_link('Picture straight link', i, new_window=True)]
            ])
        put_table([
            ['Background music straight link: ', put_link('Click to open audio', video_info[1], new_window=True)],
            ['Background music download：', put_link('click to download', download_bgm, new_window=True)],
            ['Video title: ', video_info[2]],
            ['Author nickname: ', video_info[3]],
            ['Author Douyin ID: ', video_info[4]],
            ['Original video link: ', put_link('Click to open the original video', video_info[5], new_window=True)],
            ['Current video API link: ', put_link('Click to browse API data', api_url, new_window=True)],
            ['Current video streamline API link: ', put_link('Click to browse API data', short_api_url, new_window=True)]
        ])


def put_tiktok_result(item):
    # Display TikTok results on the front end
    video_info = get_video_info_tiktok(item)
    download_url = find_url(tikmate().get_media(item)[1].json)[0]
    nowm = tiktok_nowm(item)
    api_url = '/api?url=' + item
    put_table([
        ['type', 'content'],
        ['video title: ', video_info['desc']],
        ['Video direct link (with watermark): ', put_link('Click to open video', video_info['video']['playAddr'], new_window=True)],
        ['Video direct link (no watermark): ', put_link('Click to open video', nowm, new_window=True)],
        ['Video download (no watermark)：', put_link('Click to download', download_url, new_window=True)],
        ['audio(name-author)：', video_info['music']['album'] + " - " + video_info['music']['authorName']],
        ['Music link：', put_link('Click to play', video_info['music']['playUrl'], new_window=True)],
        ['Author Nickname: ', video_info['author']],
        ['Author ID: ', video_info['authorId']],
        ['Number of fans: ', video_info['authorStats']['followerCount']],
        ['Follow others amount: ', video_info['authorStats']['followingCount']],
        ['Total likes get: ', video_info['authorStats']['heart']],
        ['Total videos: ', video_info['authorStats']['videoCount']],
        ['Original video link: ', put_link('Click to open the original video', item, new_window=True)],
        ['Current Video API Link: ', put_link('Click to browse API data', api_url, new_window=True)]
    ])


def github_pop_window():
    with popup("Github"):
        put_html('<h3>⭐Welcome Star</h3>')
        put_markdown('[TikTokDownloader_PyWebIO](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO)')


def feedback_pop_window():
    with popup("You can give feedback in the following ways"):
        put_html('<h3>🎯Github</h3>')
        put_markdown('submit：[issues](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)')
        put_html('<hr>')
        put_html('<h3>🤖WeChat</h3>')
        put_markdown('WeChat：[Evil0ctal](https://mycyberpunk.com/)')
        put_html('<hr>')


def api_document_pop_window():
    with popup("API documentation"):
        put_markdown("💽API documentation")
        put_markdown("The API can convert the request parameters into the non-watermarked video/picture direct link that needs to be extracted, and it can be downloaded in-app with the IOS shortcut.")
        put_link('[Chinese document]', 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8',
                 new_window=True)
        put_html('<br>')
        put_link('[English document]',
                 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.en.md#%EF%B8%8Fapi-usage',
                 new_window=True)
        put_html('<hr>')
        put_markdown("🛰️API reference")
        put_markdown('Douyin/TikTok parsing request parameters')
        put_code('http://localhost(Server IP):80/api?url="Copied (TikTok/TikTok) (Share text/link)"\n#Return JSON')
        put_markdown('Douyin/TikTok video download request parameters')
        put_code('http://localhost(Server IP):80/download_video?url="Duplicated Douyin/TikTok link"\n#Return to mp4 file download request')
        put_markdown('TikTok video/atlas audio download request parameters')
        put_code('http://localhost(Server IP):80/download_bgm?url="Duplicated Douyin/TikTok link"\n#Return to mp3 file download request')


def error_log_popup_window():
    with popup('Error log'):
        content = open(r'./logs.txt', 'rb').read()
        put_file('logs.txt', content=content)
        with open('./logs.txt', 'r') as f:
            content = f.read()
            put_text(str(content))


def about_popup_window():
    with popup('More information'):
        put_html('<h3>⚠️About parsing failure</h3>')
        put_text('It is currently known that a large number of visits to the TikTok API in a short period of time may trigger its verification code.')
        put_text('If the analysis fails several times, please wait for a while and then try again.')
        put_button("Error logs", onclick=lambda: error_log_popup_window(), link_style=True, small=True)
        put_html('<hr>')
        put_html('<h3>🌐Video/Atlas batch download</h3>')
        put_markdown('You can use tools such as [IDM](https://www.internetdownloadmanager.com/) to sniff the links to the results page.')
        put_html('<hr>')
        put_html('<h3>📣About this project</h3>')
        put_markdown('I have limited skills, so be welcome to [GitHub](https://github.com/Evil0ctal/TikTokDownload_PyWebIO/pulls) submit a pull request.')
        put_html('<hr>')
        put_html('<h3>💖Make friends</h3>')
        put_markdown('WeChat：[Evil0ctal](https://mycyberpunk.com/)')


def language_pop_window():
    with popup('Select Site Language'):
        put_link('[Chinese Language]', 'https://douyin.wtf')
        put_html('<br>')
        put_link('[English Language]', 'https://en.douyin.wtf')


@config(title=title, description=description)
def main():
    # Set favicon
    favicon_url = "https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/favicon/android-chrome-512x512.png"
    session.run_js("""
    $('#favicon32,#favicon16').remove(); 
    $('head').append('<link rel="icon" type="image/png" href="%s">')
    """ % favicon_url)
    # 修改footer
    session.run_js("""$('footer').remove()""")
    put_markdown("""<div align='center' ><font size='20'>😼Welcome to use Douyin/TikTok online analysis</font></div>""")
    put_html('<hr>')
    put_row([put_button("Github", onclick=lambda: github_pop_window(), link_style=True, small=True),
             put_button("Feedback", onclick=lambda: feedback_pop_window(), link_style=True, small=True),
             put_button("API", onclick=lambda: api_document_pop_window(), link_style=True, small=True),
             put_button("About", onclick=lambda: about_popup_window(), link_style=True, small=True),
             put_button("Language", onclick=lambda: language_pop_window(), link_style=True, small=True),
             put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg',
                       title='Access to records')
             ])
    placeholder = "For batch resolution, please paste multiple content or links directly, without using symbols to separate them, and support the mixing of Douyin/TikTok links."
    kou_ling = textarea('Please paste the share content or URL of Douyin/TikTok here', type=TEXT, validate=valid_check, required=True,
                        placeholder=placeholder,
                        position=0)
    if kou_ling:
        if kou_ling == 'wyn':
            # really miss you
            with popup('For WYN💖'):
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
                put_link('return to home page', '/')
        else:
            url_lists = find_url(kou_ling)
            # Analysis start time
            start = time.time()
            try:
                loading(url_lists)
                for url in url_lists:
                    if 'douyin.com' in url:
                        put_result(url)
                    else:
                        put_tiktok_result(url)
                clear('bar')
                # Analysis end time
                end = time.time()
                put_html("<br><hr>")
                put_link('return to home page', '/')
                put_text('Parsing is complete! time consuming: %.4fs' % (end - start))
            except Exception as e:
                # exception catch
                clear('bar')
                error_do(e, 'main')
                end = time.time()
                put_text('Parsing is complete! time consuming: %.4fs' % (end - start))


if __name__ == "__main__":
    app.add_url_rule('/', 'webio_view', webio_view(main), methods=['GET', 'POST', 'OPTIONS'])
    app.run(host='0.0.0.0', port=80)
