#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author: https://github.com/Evil0ctal/
# @Time: 2021/11/06
# @Update: 2023/03/08
# @Version: 3.1.5
# @Function:
# 创建一个接受提交参数的FastAPi应用程序。
# 将scraper.py返回的内容以JSON格式返回。


import os
import time
import json
import aiohttp
import uvicorn
import zipfile
import threading
import configparser

from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse, FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from pydantic import BaseModel
from starlette.responses import RedirectResponse

from scraper import Scraper

# 读取配置文件
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
# 运行端口
port = int(config["Web_API"]["Port"])
# 域名
domain = config["Web_API"]["Domain"]
# 限制器/Limiter
Rate_Limit = config["Web_API"]["Rate_Limit"]

# 创建FastAPI实例
title = "Douyin TikTok Download/Scraper API-V1"
version = '3.1.3'
update_time = "2023/02/19"
description = """
#### Description/说明
<details>
<summary>点击展开/Click to expand</summary>
> [中文/Chinese]
- 爬取Douyin以及TikTok的数据并返回，更多功能正在开发中。
- 如果需要更多接口，请查看[https://api.tikhub.io/docs](https://api.tikhub.io/docs)。
- 本项目开源在[GitHub：Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)。
- 全部端点数据均来自抖音以及TikTok的官方接口，如遇到问题或BUG或建议请在[issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)中反馈。
- 本项目仅供学习交流使用，严禁用于违法用途，如有侵权请联系作者。
> [英文/English]
- Crawl the data of Douyin and TikTok and return it. More features are under development.
- If you need more interfaces, please visit [https://api.tikhub.io/docs](https://api.tikhub.io/docs).
- This project is open source on [GitHub: Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API).
- All endpoint data comes from the official interface of Douyin and TikTok. If you have any questions or BUGs or suggestions, please feedback in [issues](
- This project is for learning and communication only. It is strictly forbidden to be used for illegal purposes. If there is any infringement, please contact the author.
</details>
#### Contact author/联系作者
<details>
<summary>点击展开/Click to expand</summary>
- WeChat: Evil0ctal
- Email: [Evil0ctal1985@gmail.com](mailto:Evil0ctal1985@gmail.com)
- Github: [https://github.com/Evil0ctal](https://github.com/Evil0ctal)
</details>
"""
tags_metadata = [
    {
        "name": "Root",
        "description": "Root path info.",
    },
    {
        "name": "API",
        "description": "Hybrid interface, automatically determine the input link and return the simplified data/混合接口，自动判断输入链接返回精简后的数据。",
    },
    {
        "name": "Douyin",
        "description": "All Douyin API Endpoints/所有抖音接口节点",
    },
    {
        "name": "TikTok",
        "description": "All TikTok API Endpoints/所有TikTok接口节点",
    },
    {
        "name": "Download",
        "description": "Enter the share link and return the download file response./输入分享链接后返回下载文件响应",
    },
    {
        "name": "iOS_Shortcut",
        "description": "Get iOS shortcut info/获取iOS快捷指令信息",
    },
]

# 创建Scraper对象
api = Scraper()

# 创建FastAPI实例
app = FastAPI(
    title=title,
    description=description,
    version=version,
    openapi_tags=tags_metadata
)

# 创建Limiter对象
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

""" ________________________⬇️端点响应模型(Endpoints Response Model)⬇️________________________"""


# API Root节点
class APIRoot(BaseModel):
    API_status: str
    Version: str = version
    Update_time: str = update_time
    Request_Rate_Limit: str = Rate_Limit
    Web_APP: str
    API_V1_Document: str
    TikHub_API_Document: str
    GitHub: str


# API获取视频基础模型
class iOS_Shortcut(BaseModel):
    version: str = None
    update: str = None
    link: str = None
    link_en: str = None
    note: str = None
    note_en: str = None


# API获取视频基础模型
class API_Video_Response(BaseModel):
    status: str = None
    platform: str = None
    endpoint: str = None
    message: str = None
    total_time: float = None
    aweme_list: list = None


# 混合解析API基础模型:
class API_Hybrid_Response(BaseModel):
    status: str = None
    message: str = None
    endpoint: str = None
    url: str = None
    type: str = None
    platform: str = None
    aweme_id: str = None
    total_time: float = None
    official_api_url: dict = None
    desc: str = None
    create_time: int = None
    author: dict = None
    music: dict = None
    statistics: dict = None
    cover_data: dict = None
    hashtags: list = None
    video_data: dict = None
    image_data: dict = None


# 混合解析API精简版基础模型:
class API_Hybrid_Minimal_Response(BaseModel):
    status: str = None
    message: str = None
    platform: str = None
    type: str = None
    wm_video_url: str = None
    wm_video_url_HQ: str = None
    nwm_video_url: str = None
    nwm_video_url_HQ: str = None
    no_watermark_image_list: list or None = None
    watermark_image_list: list or None = None


""" ________________________⬇️端点日志记录(Endpoint logs)⬇️________________________"""


# 记录API请求日志
async def api_logs(start_time, input_data, endpoint, error_data: dict = None):
    if config["Web_API"]["Allow_Logs"] == "True":
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        total_time = float(format(time.time() - start_time, '.4f'))
        file_name = "API_logs.json"
        # 写入日志内容
        with open(file_name, "a", encoding="utf-8") as f:
            data = {
                "time": time_now,
                "endpoint": f'/{endpoint}/',
                "total_time": total_time,
                "input_data": input_data,
                "error_data": error_data if error_data else "No error"
            }
            f.write(json.dumps(data, ensure_ascii=False) + ",\n")
        print('日志记录成功！')
        return 1
    else:
        print('日志记录已关闭！')
        return 0


""" ________________________⬇️Root端点(Root endpoint)⬇️________________________"""


# Root端点
@app.get("/", response_class=ORJSONResponse, response_model=APIRoot, tags=["Root"])
async def root():
    """
    Root path info.
    """
    data = {
        "API_status": "Running",
        "Version": version,
        "Update_time": update_time,
        "Request_Rate_Limit": Rate_Limit,
        "Web_APP": "https://www.douyin.wtf/",
        "API_V1_Document": "https://api.douyin.wtf/docs",
        "TikHub_API_Document": "https://api.tikhub.io/docs",
        "GitHub": "https://github.com/Evil0ctal/Douyin_TikTok_Download_API",
    }
    return ORJSONResponse(data)


""" ________________________⬇️混合解析端点(Hybrid parsing endpoints)⬇️________________________"""


# 混合解析端点,自动判断输入链接返回精简后的数据
# Hybrid parsing endpoint, automatically determine the input link and return the simplified data.
@app.get("/api", tags=["API"], response_class=ORJSONResponse, response_model=API_Hybrid_Response)
@limiter.limit(Rate_Limit)
async def hybrid_parsing(request: Request, url: str, minimal: bool = False):
    """
        ## 用途/Usage
        - 获取[抖音|TikTok]单个视频数据，参数是视频链接或分享口令。
        - Get [Douyin|TikTok] single video data, the parameter is the video link or share code.
        ## 参数/Parameter
        #### url(必填/Required)):
        - 视频链接。| 分享口令
        - The video link.| Share code
        - 例子/Example:
        `https://www.douyin.com/video/7153585499477757192`
        `https://v.douyin.com/MkmSwy7/`
        `https://vm.tiktok.com/TTPdkQvKjP/`
        `https://www.tiktok.com/@tvamii/video/7045537727743380782`
        #### minimal(选填/Optional Default:False):
        - 是否返回精简版数据。
        - Whether to return simplified data.
        - 例子/Example:
        `True`
        `False`
        ## 返回值/Return
        - 用户当个视频数据的列表，列表内包含JSON数据。
        - List of user single video data, list contains JSON data.
    """
    print("正在进行混合解析...")
    # 开始时间
    start_time = time.time()
    # 获取数据
    data = await api.hybrid_parsing(url)
    # 是否精简
    if minimal:
        result = api.hybrid_parsing_minimal(data)
    else:
        # 更新数据
        result = {
            'url': url,
            "endpoint": "/api/",
            "total_time": float(format(time.time() - start_time, '.4f')),
        }
        # 合并数据
        result.update(data)
    # 记录API调用
    await api_logs(start_time=start_time,
                   input_data={'url': url},
                   endpoint='api')
    return ORJSONResponse(result)


""" ________________________⬇️抖音视频解析端点(Douyin video parsing endpoint)⬇️________________________"""


# 获取抖音单个视频数据/Get Douyin single video data
@app.get("/douyin_video_data/", response_class=ORJSONResponse, response_model=API_Video_Response, tags=["Douyin"])
@limiter.limit(Rate_Limit)
async def get_douyin_video_data(request: Request, douyin_video_url: str = None, video_id: str = None):
    """
    ## 用途/Usage
    - 获取抖音用户单个视频数据，参数是视频链接|分享口令
    - Get the data of a single video of a Douyin user, the parameter is the video link.
    ## 参数/Parameter
    #### douyin_video_url(选填/Optional):
    - 视频链接。| 分享口令
    - The video link.| Share code
    - 例子/Example:
    `https://www.douyin.com/video/7153585499477757192`
    `https://v.douyin.com/MkmSwy7/`
    #### video_id(选填/Optional):
    - 视频ID，可以从视频链接中获取。
    - The video ID, can be obtained from the video link.
    - 例子/Example:
    `7153585499477757192`
    #### s_v_web_id(选填/Optional):
    - s_v_web_id，可以从浏览器访问抖音然后从cookie中获取。
    - s_v_web_id, can be obtained from the browser to access Douyin and then from the cookie.
    - 例子/Example:
    `s_v_web_id=verify_leytkxgn_kvO5kOmO_SdMs_4t1o_B5ml_BUqtWM1mP6BF;`
    #### 备注/Note:
    - 参数`douyin_video_url`和`video_id`二选一即可，如果都填写，优先使用`video_id`以获得更快的响应速度。
    - The parameters `douyin_video_url` and `video_id` can be selected, if both are filled in, the `video_id` is used first to get a faster response speed.
    ## 返回值/Return
    - 用户当个视频数据的列表，列表内包含JSON数据。
    - List of user single video data, list contains JSON data.
    """
    if video_id is None or video_id == '':
        # 获取视频ID
        video_id = await api.get_douyin_video_id(douyin_video_url)
        if video_id is None:
            result = {
                "status": "failed",
                "platform": "douyin",
                "message": "video_id获取失败/Failed to get video_id",
            }
            return ORJSONResponse(result)
    if video_id is not None and video_id != '':
        # 开始时间
        start_time = time.time()
        print('获取到的video_id数据:{}'.format(video_id))
        if video_id is not None:
            video_data = await api.get_douyin_video_data(video_id=video_id)
            if video_data is None:
                result = {
                    "status": "failed",
                    "platform": "douyin",
                    "endpoint": "/douyin_video_data/",
                    "message": "视频API数据获取失败/Failed to get video API data",
                }
                return ORJSONResponse(result)
            # print('获取到的video_data:{}'.format(video_data))
            # 记录API调用
            await api_logs(start_time=start_time,
                           input_data={'douyin_video_url': douyin_video_url, 'video_id': video_id},
                           endpoint='douyin_video_data')
            # 结束时间
            total_time = float(format(time.time() - start_time, '.4f'))
            # 返回数据
            result = {
                "status": "success",
                "platform": "douyin",
                "endpoint": "/douyin_video_data/",
                "message": "获取视频数据成功/Got video data successfully",
                "total_time": total_time,
                "aweme_list": [video_data]
            }
            return ORJSONResponse(result)
        else:
            print('获取抖音video_id失败')
            result = {
                "status": "failed",
                "platform": "douyin",
                "endpoint": "/douyin_video_data/",
                "message": "获取视频ID失败/Failed to get video ID",
                "total_time": 0,
                "aweme_list": []
            }
            return ORJSONResponse(result)


@app.get("/douyin_live_video_data/", response_class=ORJSONResponse, response_model=API_Video_Response, tags=["Douyin"])
@limiter.limit(Rate_Limit)
async def get_douyin_live_video_data(request: Request, douyin_live_video_url: str = None, web_rid: str = None):
    """
    ## 用途/Usage
    - 获取抖音直播视频数据，参数是视频链接|分享口令
    - Get the data of a Douyin live video, the parameter is the video link.
    ## 失效待修复/Waiting for repair
    """
    if web_rid is None or web_rid == '':
        # 获取视频ID
        web_rid = await api.get_douyin_video_id(douyin_live_video_url)
        if web_rid is None:
            result = {
                "status": "failed",
                "platform": "douyin",
                "message": "web_rid获取失败/Failed to get web_rid",
            }
            return ORJSONResponse(result)
    if web_rid is not None and web_rid != '':
        # 开始时间
        start_time = time.time()
        print('获取到的web_rid:{}'.format(web_rid))
        if web_rid is not None:
            video_data = await api.get_douyin_live_video_data(web_rid=web_rid)
            if video_data is None:
                result = {
                    "status": "failed",
                    "platform": "douyin",
                    "endpoint": "/douyin_live_video_data/",
                    "message": "直播视频API数据获取失败/Failed to get live video API data",
                }
                return ORJSONResponse(result)
            # print('获取到的video_data:{}'.format(video_data))
            # 记录API调用
            await api_logs(start_time=start_time,
                           input_data={'douyin_video_url': douyin_live_video_url, 'web_rid': web_rid},
                           endpoint='douyin_live_video_data')
            # 结束时间
            total_time = float(format(time.time() - start_time, '.4f'))
            # 返回数据
            result = {
                "status": "success",
                "platform": "douyin",
                "endpoint": "/douyin_live_video_data/",
                "message": "获取直播视频数据成功/Got live video data successfully",
                "total_time": total_time,
                "aweme_list": [video_data]
            }
            return ORJSONResponse(result)
        else:
            print('获取抖音video_id失败')
            result = {
                "status": "failed",
                "platform": "douyin",
                "endpoint": "/douyin_live_video_data/",
                "message": "获取直播视频ID失败/Failed to get live video ID",
                "total_time": 0,
                "aweme_list": []
            }
            return ORJSONResponse(result)


@app.get("/douyin_profile_videos/", response_class=ORJSONResponse, response_model=None, tags=["Douyin"])
async def get_douyin_user_profile_videos(tikhub_token: str, douyin_user_url: str = None):
    """
    ## 用途/Usage
    - 获取抖音用户主页数据，参数是用户链接|ID
    - Get the data of a Douyin user profile, the parameter is the user link or ID.
    ## 参数/Parameter
    tikhub_token: https://api.tikhub.io/#/Authorization/login_for_access_token_user_login_post
    """
    response = await api.get_douyin_user_profile_videos(tikhub_token=tikhub_token, profile_url=douyin_user_url)
    return response


@app.get("/douyin_profile_liked_videos/", response_class=ORJSONResponse, response_model=None, tags=["Douyin"])
async def get_douyin_user_profile_liked_videos(tikhub_token: str, douyin_user_url: str = None):
    """
    ## 用途/Usage
    - 获取抖音用户喜欢的视频数据，参数是用户链接|ID
    - Get the data of a Douyin user profile liked videos, the parameter is the user link or ID.
    ## 参数/Parameter
    tikhub_token: https://api.tikhub.io/#/Authorization/login_for_access_token_user_login_post
    """
    response = await api.get_douyin_profile_liked_data(tikhub_token=tikhub_token, profile_url=douyin_user_url)
    return response


@app.get("/douyin_video_comments/", response_class=ORJSONResponse, response_model=None, tags=["Douyin"])
async def get_douyin_video_comments(tikhub_token: str, douyin_video_url: str = None):
    """
    ## 用途/Usage
    - 获取抖音视频评论数据，参数是视频链接|分享口令
    - Get the data of a Douyin video comments, the parameter is the video link.
    ## 参数/Parameter
    tikhub_token: https://api.tikhub.io/#/Authorization/login_for_access_token_user_login_post
    """
    response = await api.get_douyin_video_comments(tikhub_token=tikhub_token, video_url=douyin_video_url)
    return response


""" ________________________⬇️TikTok视频解析端点(TikTok video parsing endpoint)⬇️________________________"""


# 获取TikTok单个视频数据/Get TikTok single video data
@app.get("/tiktok_video_data/", response_class=ORJSONResponse, response_model=API_Video_Response, tags=["TikTok"])
@limiter.limit(Rate_Limit)
async def get_tiktok_video_data(request: Request, tiktok_video_url: str = None, video_id: str = None):
    """
        ## 用途/Usage
        - 获取单个视频数据，参数是视频链接| 分享口令。
        - Get single video data, the parameter is the video link.
        ## 参数/Parameter
        #### tiktok_video_url(选填/Optional):
        - 视频链接。| 分享口令
        - The video link.| Share code
        - 例子/Example:
        `https://www.tiktok.com/@evil0ctal/video/7156033831819037994`
        `https://vm.tiktok.com/TTPdkQvKjP/`
        #### video_id(选填/Optional):
        - 视频ID，可以从视频链接中获取。
        - The video ID, can be obtained from the video link.
        - 例子/Example:
        `7156033831819037994`
        #### 备注/Note:
        - 参数`tiktok_video_url`和`video_id`二选一即可，如果都填写，优先使用`video_id`以获得更快的响应速度。
        - The parameters `tiktok_video_url` and `video_id` can be selected, if both are filled in, the `video_id` is used first to get a faster response speed.
        ## 返回值/Return
        - 用户当个视频数据的列表，列表内包含JSON数据。
        - List of user single video data, list contains JSON data.
        """
    # 开始时间
    start_time = time.time()
    if video_id is None or video_id == "":
        video_id = await api.get_tiktok_video_id(tiktok_video_url)
        if video_id is None:
            return ORJSONResponse({"status": "fail", "platform": "tiktok", "endpoint": "/tiktok_video_data/",
                                   "message": "获取视频ID失败/Get video ID failed"})
    if video_id is not None and video_id != '':
        print('开始解析单个TikTok视频数据')
        video_data = await api.get_tiktok_video_data(video_id)
        # TikTok的API数据如果为空或者返回的数据中没有视频数据，就返回错误信息
        # If the TikTok API data is empty or there is no video data in the returned data, an error message is returned
        if video_data is None or video_data.get('aweme_id') != video_id:
            print('视频数据获取失败/Failed to get video data')
            result = {
                "status": "failed",
                "platform": "tiktok",
                "endpoint": "/tiktok_video_data/",
                "message": "视频数据获取失败/Failed to get video data"
            }
            return ORJSONResponse(result)
        # 记录API调用
        await api_logs(start_time=start_time,
                       input_data={'tiktok_video_url': tiktok_video_url, 'video_id': video_id},
                       endpoint='tiktok_video_data')
        # 结束时间
        total_time = float(format(time.time() - start_time, '.4f'))
        # 返回数据
        result = {
            "status": "success",
            "platform": "tiktok",
            "endpoint": "/tiktok_video_data/",
            "message": "获取视频数据成功/Got video data successfully",
            "total_time": total_time,
            "aweme_list": [video_data]
        }
        return ORJSONResponse(result)
    else:
        print('视频链接错误/Video link error')
        result = {
            "status": "failed",
            "platform": "tiktok",
            "endpoint": "/tiktok_video_data/",
            "message": "视频链接错误/Video link error"
        }
        return ORJSONResponse(result)


# 获取TikTok用户视频数据/Get TikTok user video data
@app.get("/tiktok_profile_videos/", response_class=ORJSONResponse, response_model=None, tags=["TikTok"])
async def get_tiktok_profile_videos(tikhub_token: str, tiktok_video_url: str = None):
    """
    ## 用途/Usage
    - 获取抖音用户主页数据，参数是用户链接|ID
    - Get the data of a Douyin user profile, the parameter is the user link or ID.
    ## 参数/Parameter
    tikhub_token: https://api.tikhub.io/#/Authorization/login_for_access_token_user_login_post
    """
    response = await api.get_tiktok_user_profile_videos(tikhub_token=tikhub_token, tiktok_video_url=tiktok_video_url)
    return response


# 获取TikTok用户主页点赞视频数据/Get TikTok user profile liked video data
@app.get("/tiktok_profile_liked_videos/", response_class=ORJSONResponse, response_model=None, tags=["TikTok"])
async def get_tiktok_profile_liked_videos(tikhub_token: str, tiktok_video_url: str = None):
    """
    ## 用途/Usage
    - 获取抖音用户主页点赞视频数据，参数是用户链接|ID
    - Get the data of a Douyin user profile liked video, the parameter is the user link or ID.
    ## 参数/Parameter
    tikhub_token: https://api.tikhub.io/#/Authorization/login_for_access_token_user_login_post
    """
    response = await api.get_tiktok_user_profile_liked_videos(tikhub_token=tikhub_token, tiktok_video_url=tiktok_video_url)
    return response


""" ________________________⬇️iOS快捷指令更新端点(iOS Shortcut update endpoint)⬇️________________________"""


@app.get("/ios", response_model=iOS_Shortcut, tags=["iOS_Shortcut"])
async def Get_Shortcut():
    data = {
        'version': config["Web_API"]["iOS_Shortcut_Version"],
        'update': config["Web_API"]['iOS_Shortcut_Update_Time'],
        'link': config["Web_API"]['iOS_Shortcut_Link'],
        'link_en': config["Web_API"]['iOS_Shortcut_Link_EN'],
        'note': config["Web_API"]['iOS_Shortcut_Update_Note'],
        'note_en': config["Web_API"]['iOS_Shortcut_Update_Note_EN'],
    }
    return ORJSONResponse(data)


""" ________________________⬇️下载文件端点/函数(Download file endpoints/functions)⬇️________________________"""


# 下载文件端点/Download file endpoint
@app.get("/download", tags=["Download"])
@limiter.limit(Rate_Limit)
async def download_file_hybrid(request: Request, url: str, prefix: bool = True, watermark: bool = False):
    """
        ## 用途/Usage
        ### [中文]
        - 将[抖音|TikTok]链接作为参数提交至此端点，返回[视频|图片]文件下载请求。
        ### [English]
        - Submit the [Douyin|TikTok] link as a parameter to this endpoint and return the [video|picture] file download request.
        # 参数/Parameter
        - url:str -> [Douyin|TikTok] [视频|图片] 链接/ [Douyin|TikTok] [video|image] link
        - prefix: bool -> [True/False] 是否添加前缀/Whether to add a prefix
        - watermark: bool -> [True/False] 是否添加水印/Whether to add a watermark
        """
    # 是否开启此端点/Whether to enable this endpoint
    if config["Web_API"]["Download_Switch"] != "True":
        return ORJSONResponse({"status": "endpoint closed",
                               "message": "此端点已关闭请在配置文件中开启/This endpoint is closed, please enable it in the configuration file"})
    # 开始时间
    start_time = time.time()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    data = await api.hybrid_parsing(url)
    if data is None:
        return ORJSONResponse(data)
    else:
        # 记录API调用
        await api_logs(start_time=start_time,
                       input_data={'url': url},
                       endpoint='download')
        url_type = data.get('type')
        platform = data.get('platform')
        aweme_id = data.get('aweme_id')
        file_name_prefix = config["Web_API"]["File_Name_Prefix"] if prefix else ''
        root_path = config["Web_API"]["Download_Path"]
        # 查看目录是否存在，不存在就创建
        if not os.path.exists(root_path):
            os.makedirs(root_path)
        if url_type == 'video':
            file_name = file_name_prefix + platform + '_' + aweme_id + '.mp4' if not watermark else file_name_prefix + platform + '_' + aweme_id + '_watermark' + '.mp4'
            url = data.get('video_data').get('nwm_video_url_HQ') if not watermark else data.get('video_data').get(
                'wm_video_url_HQ')
            print('url: ', url)
            file_path = root_path + "/" + file_name
            print('file_path: ', file_path)
            # 判断文件是否存在，存在就直接返回
            if os.path.exists(file_path):
                print('文件已存在，直接返回')
                return FileResponse(path=file_path, media_type='video/mp4', filename=file_name)
            else:
                if platform == 'douyin':
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url=url, headers=headers, allow_redirects=False) as response:
                            r = response.headers
                            cdn_url = r.get('location')
                            async with session.get(url=cdn_url) as res:
                                r = await res.content.read()
                elif platform == 'tiktok':
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url=url, headers=headers) as res:
                            r = await res.content.read()
                with open(file_path, 'wb') as f:
                    f.write(r)
                return FileResponse(path=file_path, media_type='video/mp4', filename=file_name)
        elif url_type == 'image':
            url = data.get('image_data').get('no_watermark_image_list') if not watermark else data.get(
                'image_data').get('watermark_image_list')
            print('url: ', url)
            zip_file_name = file_name_prefix + platform + '_' + aweme_id + '_images.zip' if not watermark else file_name_prefix + platform + '_' + aweme_id + '_images_watermark.zip'
            zip_file_path = root_path + "/" + zip_file_name
            print('zip_file_name: ', zip_file_name)
            print('zip_file_path: ', zip_file_path)
            # 判断文件是否存在，存在就直接返回、
            if os.path.exists(zip_file_path):
                print('文件已存在，直接返回')
                return FileResponse(path=zip_file_path, media_type='zip', filename=zip_file_name)
            file_path_list = []
            for i in url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url=i, headers=headers) as res:
                        content_type = res.headers.get('content-type')
                        file_format = content_type.split('/')[1]
                        r = await res.content.read()
                index = int(url.index(i))
                file_name = file_name_prefix + platform + '_' + aweme_id + '_' + str(
                    index + 1) + '.' + file_format if not watermark else \
                    file_name_prefix + platform + '_' + aweme_id + '_' + str(
                        index + 1) + '_watermark' + '.' + file_format
                file_path = root_path + "/" + file_name
                file_path_list.append(file_path)
                print('file_path: ', file_path)
                with open(file_path, 'wb') as f:
                    f.write(r)
                if len(url) == len(file_path_list):
                    zip_file = zipfile.ZipFile(zip_file_path, 'w')
                    for f in file_path_list:
                        zip_file.write(os.path.join(f), f, zipfile.ZIP_DEFLATED)
                    zip_file.close()
                    return FileResponse(path=zip_file_path, media_type='zip', filename=zip_file_name)
        else:
            return ORJSONResponse(data)


# 批量下载文件端点/Batch download file endpoint
@app.get("/batch_download", tags=["Download"])
async def batch_download_file(url_list: str, prefix: bool = True):
    """
    批量下载文件端点/Batch download file endpoint
    未完工/Unfinished
    """
    print('url_list: ', url_list)
    return ORJSONResponse({"status": "failed",
                           "message": "嘿嘿嘿，这个功能还没做呢，等我有空再做吧/Hehehe, this function hasn't been done yet, I'll do it when I have time"})


# 抖音链接格式下载端点(video)/Douyin link format download endpoint(video)
@app.get("/video/{aweme_id}", tags=["Download"])
async def download_douyin_video(aweme_id: str, prefix: bool = True, watermark: bool = False):
    """
    ## 用途/Usage
    ### [中文]
    - 将抖音域名改为当前服务器域名即可调用此端点，返回[视频|图片]文件下载请求。
    - 例如原链接：https://douyin.com/video/1234567890123456789 改成 https://api.douyin.wtf/video/1234567890123456789 即可调用此端点。
    ### [English]
    - Change the Douyin domain name to the current server domain name to call this endpoint and return the video file download request.
    - For example, the original link: https://douyin.com/video/1234567890123456789 becomes https://api.douyin.wtf/video/1234567890123456789 to call this endpoint.
    # 参数/Parameter
    - aweme_id:str -> 抖音视频ID/Douyin video ID
    - prefix: bool -> [True/False] 是否添加前缀/Whether to add a prefix
    - watermark: bool -> [True/False] 是否添加水印/Whether to add a watermark
    """
    # 是否开启此端点/Whether to enable this endpoint
    if config["Web_API"]["Download_Switch"] != "True":
        return ORJSONResponse({"status": "endpoint closed",
                               "message": "此端点已关闭请在配置文件中开启/This endpoint is closed, please enable it in the configuration file"})
    video_url = f"https://www.douyin.com/video/{aweme_id}"
    download_url = f"{domain}/download?url={video_url}&prefix={prefix}&watermark={watermark}"
    return RedirectResponse(download_url)


# 抖音链接格式下载端点(video)/Douyin link format download endpoint(video)
@app.get("/note/{aweme_id}", tags=["Download"])
async def download_douyin_video(aweme_id: str, prefix: bool = True, watermark: bool = False):
    """
    ## 用途/Usage
    ### [中文]
    - 将抖音域名改为当前服务器域名即可调用此端点，返回[视频|图片]文件下载请求。
    - 例如原链接：https://douyin.com/video/1234567890123456789 改成 https://api.douyin.wtf/video/1234567890123456789 即可调用此端点。
    ### [English]
    - Change the Douyin domain name to the current server domain name to call this endpoint and return the video file download request.
    - For example, the original link: https://douyin.com/video/1234567890123456789 becomes https://api.douyin.wtf/video/1234567890123456789 to call this endpoint.
    # 参数/Parameter
    - aweme_id:str -> 抖音视频ID/Douyin video ID
    - prefix: bool -> [True/False] 是否添加前缀/Whether to add a prefix
    - watermark: bool -> [True/False] 是否添加水印/Whether to add a watermark
    """
    # 是否开启此端点/Whether to enable this endpoint
    if config["Web_API"]["Download_Switch"] != "True":
        return ORJSONResponse({"status": "endpoint closed",
                               "message": "此端点已关闭请在配置文件中开启/This endpoint is closed, please enable it in the configuration file"})
    video_url = f"https://www.douyin.com/video/{aweme_id}"
    download_url = f"{domain}/download?url={video_url}&prefix={prefix}&watermark={watermark}"
    return RedirectResponse(download_url)


# 抖音链接格式下载端点/Douyin link format download endpoint
@app.get("/discover", tags=["Download"])
async def download_douyin_discover(modal_id: str, prefix: bool = True, watermark: bool = False):
    """
    ## 用途/Usage
    ### [中文]
    - 将抖音域名改为当前服务器域名即可调用此端点，返回[视频|图片]文件下载请求。
    - 例如原链接：https://www.douyin.com/discover?modal_id=1234567890123456789 改成 https://api.douyin.wtf/discover?modal_id=1234567890123456789 即可调用此端点。
    ### [English]
    - Change the Douyin domain name to the current server domain name to call this endpoint and return the video file download request.
    - For example, the original link: https://douyin.com/discover?modal_id=1234567890123456789 becomes https://api.douyin.wtf/discover?modal_id=1234567890123456789 to call this endpoint.
    # 参数/Parameter
    - modal_id: str -> 抖音视频ID/Douyin video ID
    - prefix: bool -> [True/False] 是否添加前缀/Whether to add a prefix
    - watermark: bool -> [True/False] 是否添加水印/Whether to add a watermark
    """
    # 是否开启此端点/Whether to enable this endpoint
    if config["Web_API"]["Download_Switch"] != "True":
        return ORJSONResponse({"status": "endpoint closed",
                               "message": "此端点已关闭请在配置文件中开启/This endpoint is closed, please enable it in the configuration file"})
    video_url = f"https://www.douyin.com/discover?modal_id={modal_id}"
    download_url = f"{domain}/download?url={video_url}&prefix={prefix}&watermark={watermark}"
    return RedirectResponse(download_url)


# Tiktok链接格式下载端点(video)/Tiktok link format download endpoint(video)
@app.get("/{user_id}/video/{aweme_id}", tags=["Download"])
async def download_tiktok_video(user_id: str, aweme_id: str, prefix: bool = True, watermark: bool = False):
    """
        ## 用途/Usage
        ### [中文]
        - 将TikTok域名改为当前服务器域名即可调用此端点，返回[视频|图片]文件下载请求。
        - 例如原链接：https://www.tiktok.com/@evil0ctal/video/7156033831819037994 改成 https://api.douyin.wtf/@evil0ctal/video/7156033831819037994 即可调用此端点。
        ### [English]
        - Change the TikTok domain name to the current server domain name to call this endpoint and return the video file download request.
        - For example, the original link: https://www.tiktok.com/@evil0ctal/video/7156033831819037994 becomes https://api.douyin.wtf/@evil0ctal/video/7156033831819037994 to call this endpoint.
        # 参数/Parameter
        - user_id: str -> TikTok用户ID/TikTok user ID
        - aweme_id: str -> TikTok视频ID/TikTok video ID
        - prefix: bool -> [True/False] 是否添加前缀/Whether to add a prefix
        - watermark: bool -> [True/False] 是否添加水印/Whether to add a watermark
        """
    # 是否开启此端点/Whether to enable this endpoint
    if config["Web_API"]["Download_Switch"] != "True":
        return ORJSONResponse({"status": "endpoint closed",
                               "message": "此端点已关闭请在配置文件中开启/This endpoint is closed, please enable it in the configuration file"})
    video_url = f"https://www.tiktok.com/{user_id}/video/{aweme_id}"
    download_url = f"{domain}/download?url={video_url}&prefix={prefix}&watermark={watermark}"
    return RedirectResponse(download_url)


# 定期清理[Download_Path]文件夹
# Periodically clean the [Download_Path] folder
def cleanup_path():
    while True:
        root_path = config["Web_API"]["Download_Path"]
        timer = int(config["Web_API"]["Download_Path_Clean_Timer"])
        # 查看目录是否存在，不存在就跳过
        if os.path.exists(root_path):
            time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"{time_now}: Cleaning up the download folder...")
            for file in os.listdir("./download"):
                file_path = os.path.join("./download", file)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                except Exception as e:
                    print(e)
        else:
            time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"{time_now}: The download folder does not exist, skipping...")
        time.sleep(timer)


""" ________________________⬇️项目启动执行函数(Project start execution function)⬇️________________________"""


# 程序启动后执行/Execute after program startup
@app.on_event("startup")
async def startup_event():
    # 创建一个清理下载目录定时器线程并启动
    # Create a timer thread to clean up the download directory and start it
    download_path_clean_switches = True if config["Web_API"]["Download_Path_Clean_Switch"] == "True" else False
    if download_path_clean_switches:
        # 启动清理线程/Start cleaning thread
        thread_1 = threading.Thread(target=cleanup_path)
        thread_1.start()


if __name__ == '__main__':
    # 建议使用gunicorn启动，使用uvicorn启动时请将debug设置为False
    # It is recommended to use gunicorn to start, when using uvicorn to start, please set debug to False
    # uvicorn web_api:app --host '0.0.0.0' --port 8000 --reload
    uvicorn.run("web_api:app", host='0.0.0.0', port=port, reload=True, access_log=False)
