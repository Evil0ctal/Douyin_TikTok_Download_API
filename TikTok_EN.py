#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2021/11/19
"""
@Function:
This project uses PyWebIO, Requests, Flask as Python libraries
to download TikTok's videos/gallery without watermark.
It can be used to download videos/gallery that the author has forbidden to download.
At the same time, it can be used with iOS shortcut APP
to cooperate with this project's API to realize internal download.
"""


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
title = "TikTok Downloader"
description = "TikTok video/gallery analysis online"
headers = {
    'user-agent': 'Mozilla/5.0 (Linux; Android 8.0; Pixel 2 Build/OPD3.170816.012) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.66'
}


def find_url(string):
    # Parse the link in the Douyin share password and return to the list
    url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
    return url


def valid_check(kou_ling):
    # Verify the content of the input
    if find_url(kou_ling):
        return None
    else:
        return 'TikTok share format is not correct!'


def error_msg():
    # Output a useless message
    put_html("<hr>")
    put_text("The input content could not be parsed. Please check the input or try again later. If multiple attempts still fail, please go issues.")
    put_html("<hr>")


def error_log(e):
    # Log the error in the logs.txt
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open('logs.txt', 'a') as f:
        f.write(date + ": " + str(e) + '\n')


def loading():
    # Write a progress bar to pretend :)
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
        r = requests.get(url=original_url)
        key = re.findall('video/(\d+)?', str(r.url))[0]
        api_url = f'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={key}'
        js = json.loads(requests.get(url=api_url, headers=headers).text)
        # Determine whether it is an atlas
        try:
            image_data = js['item_list'][0]['images']
            # Gallery background audio
            image_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
            # Gallery title
            image_title = str(js['item_list'][0]['desc'])
            # Author's nickname
            image_author = str(js['item_list'][0]['author']['nickname'])
            # Atlas Author TikTok ID
            image_author_id = str(js['item_list'][0]['author']['unique_id'])
            if image_author_id == "":
                # If the author has not modified the Douyin ID, this value should be used to avoid being unable to obtain its Douyin ID
                image_author_id = str(js['item_list'][0]['author']['short_id'])
            # De-watermarked atlas link
            images_url = []
            for data in image_data:
                images_url.append(data['url_list'][0])
            image_info = [images_url, image_music, image_title, image_author, image_author_id, original_url]
            return image_info, 'image'
        # Determined as a video after reporting an Exception
        except:
            # Video link after removing watermark
            video_url = str(js['item_list'][0]['video']['play_addr']['url_list'][0]).replace('playwm', 'play')
            # Video background audio
            video_music = str(js['item_list'][0]['music']['play_url']['url_list'][0])
            # Video title
            video_title = str(js['item_list'][0]['desc'])
            # Video author nickname
            video_author = str(js['item_list'][0]['author']['nickname'])
            # Video Author TikTok ID
            video_author_id = str(js['item_list'][0]['author']['unique_id'])
            if video_author_id == "":
                # If the author has not modified the Douyin ID, this value should be used to avoid being unable to obtain its Douyin ID
                video_author_id = str(js['item_list'][0]['author']['short_id'])
            # Return a list containing data
            video_info = [video_url, video_music, video_title, video_author, video_author_id, original_url]
            return video_info, 'video'
    except Exception as e:
        # Exception catch
        error_log(e)


@app.route("/api")
def webapi():
    # Create a Flask application to get POST parameters and return the results
    try:
        post_content = request.args.get("url")
        if post_content:
            response_data, result_type = get_video_info(post_content)
            if result_type == 'image':
                # Return atlas information json
                return jsonify(Type=result_type, image_url=response_data[0], image_music=response_data[1],
                               image_title=response_data[2], image_author=response_data[3],
                               image_author_id=response_data[4], original_url=response_data[5])
            else:
                # Return video information json
                return jsonify(Type=result_type, video_url=response_data[0], video_music=response_data[1],
                               video_title=response_data[2], video_author=response_data[3],
                               video_author_id=response_data[4], original_url=response_data[5])
    except Exception as e:
        # Exception catch
        error_log(e)
        return jsonify(Message="Parsing Failed", Reason=str(e), Result=False)


def put_result(item):
    # Output the form to the front end according to the parsed format
    video_info, result_type = get_video_info(item)
    if result_type == 'video':
        put_table([
            ['type', 'content'],
            ['Format:', result_type],
            ['Video link: ', put_link('Click to open the video', video_info[0], new_window=True)],
            ['BGM link: ', put_link('Click to open audio', video_info[1], new_window=True)],
            ['Video title: ', video_info[2]],
            ['Author nickname: ', video_info[3]],
            ['Author TikTok ID: ', video_info[4]],
            ['Original video link: ', put_link('Click to open the original video', video_info[5], new_window=True)]
        ])
    else:
        put_table([
            ['type', 'content'],
            ['Format:', result_type],
        ])
        for i in video_info[0]:
            put_table([
                ['Picture link: ', put_link('Click to open the picture', i, new_window=True)]
            ])
        put_table([
            ['BGM link: ', put_link('Click to open audio', video_info[1], new_window=True)],
            ['Video title: ', video_info[2]],
            ['Author nickname: ', video_info[3]],
            ['Author TikTok ID: ', video_info[4]],
            ['Gallery  link: ', put_link('Click to open the original gallery', video_info[5], new_window=True)]
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
        put_markdown("🛰️API usage")
        put_markdown("The API can convert the request parameters into a non-watermarked video/picture direct link that needs to be extracted, and can be used with IOS shortcuts to achieve in-app download.")
        put_link('[Chinese document]', 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8', new_window=True)
        put_html('<br>')
        put_link('[English document]', 'https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README-EN.md#%EF%B8%8Fapi-usage', new_window=True)


def error_log_popup_window():
    with popup('Error logs'):
        content = open(r'./logs.txt', 'rb').read()
        put_file('Download logs.txt', content=content)
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
    # set favicon
    favicon_url = "https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/favicon/android-chrome-512x512.png"
    session.run_js("""
    $('#favicon32,#favicon16').remove(); 
    $('head').append('<link rel="icon" type="image/png" href="%s">')
    """ % favicon_url)
    # remove footer
    session.run_js("""$('footer').remove()""")
    put_markdown("""<div align='center' ><font size='20'>😼Welcome to TikTok online Downloader</font></div>""")
    put_html('<hr>')
    put_row([put_button("Github", onclick=lambda: github_pop_window(), link_style=True, small=True),
             put_button("Feedback", onclick=lambda: feedback_pop_window(), link_style=True, small=True),
             put_button("API", onclick=lambda: api_document_pop_window(), link_style=True, small=True),
             put_button("About", onclick=lambda: about_popup_window(), link_style=True, small=True),
             put_button("Language", onclick=lambda: language_pop_window(), link_style=True, small=True),
             put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg', title='Browsing records')
             ])
    placeholder = "For batch analysis, please paste multiple passwords or links directly without using symbols to separate them."
    kou_ling = textarea('Please paste the shared words or URL of TikTok (Douyin) here', type=TEXT, validate=valid_check, required=True, placeholder=placeholder, position=0)
    if kou_ling:
        url_lists = find_url(kou_ling)
        # Analysis start time
        start = time.time()
        try:
            loading()
            for url in url_lists:
                put_result(url)
            clear('bar')
            # Analysis end time
            end = time.time()
            put_html("<br><hr>")
            put_text('Parsing is complete: time consuming: %.4fs' % (end - start))
        except Exception as e:
            # Exception catch
            clear('bar')
            error_msg()
            end = time.time()
            put_text('Parsing is complete: time consuming: %.4fs' % (end - start))
            error_log(e)


if __name__ == "__main__":
    app.add_url_rule('/', 'webio_view', webio_view(main), methods=['GET', 'POST', 'OPTIONS'])
    app.run(host='0.0.0.0', port=80)
