# ==============================================================================
# Copyright (C) 2021 Evil0ctal
#
# This file is part of the Douyin_TikTok_Download_API project.
#
# This project is licensed under the Apache License 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================
#
# Contributor Link:
# - https://github.com/Evil0ctal
# - https://github.com/Johnserf-Seed
#
# ==============================================================================


# FastAPI APP
import uvicorn
from fastapi import FastAPI
from app.api.router import router as api_router

# PyWebIO APP
from app.web.app import MainView
from pywebio.platform.fastapi import asgi_app

# OS
import os

# YAML
import yaml

# Load Config

# 读取上级再上级目录的配置文件
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
with open(config_path, 'r', encoding='utf-8') as file:
    config = yaml.safe_load(file)


Host_IP = config['API']['Host_IP']
Host_Port = config['API']['Host_Port']

# API Tags
tags_metadata = [
    {
        "name": "Hybrid-API",
        "description": "**(混合数据接口/Hybrid-API data endpoints)**",
    },
    {
        "name": "Douyin-Web-API",
        "description": "**(抖音Web数据接口/Douyin-Web-API data endpoints)**",
    },
    {
        "name": "TikTok-Web-API",
        "description": "**(TikTok-Web-API数据接口/TikTok-Web-API data endpoints)**",
    },
    {
        "name": "TikTok-App-API",
        "description": "**(TikTok-App-API数据接口/TikTok-App-API data endpoints)**",
    },
    {
        "name": "Bilibili-Web-API",
        "description": "**(Bilibili-Web-API数据接口/Bilibili-Web-API data endpoints)**",
    },
    {
        "name": "iOS-Shortcut",
        "description": "**(iOS快捷指令数据接口/iOS-Shortcut data endpoints)**",
    },
    {
        "name": "Download",
        "description": "**(下载数据接口/Download data endpoints)**",
    },
]

version = config['API']['Version']
update_time = config['API']['Update_Time']
environment = config['API']['Environment']

description = f"""
### [中文]

#### 关于
- **Github**: [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
- **版本**: `{version}`
- **更新时间**: `{update_time}`
- **环境**: `{environment}`
- **文档**: [API Documentation](https://douyin.wtf/docs)
#### 备注
- 本项目仅供学习交流使用，不得用于违法用途，否则后果自负。
- 如果你不想自己部署，可以直接使用我们的在线API服务：[Douyin_TikTok_Download_API](https://douyin.wtf/docs)
- 如果你需要更稳定以及更多功能的API服务，可以使用付费API服务：[TikHub API](https://api.tikhub.io/)

### [English]

#### About
- **Github**: [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
- **Version**: `{version}`
- **Last Updated**: `{update_time}`
- **Environment**: `{environment}`
- **Documentation**: [API Documentation](https://douyin.wtf/docs)
#### Note
- This project is for learning and communication only, and shall not be used for illegal purposes, otherwise the consequences shall be borne by yourself.
- If you do not want to deploy it yourself, you can directly use our online API service: [Douyin_TikTok_Download_API](https://douyin.wtf/docs)
- If you need a more stable and feature-rich API service, you can use the paid API service: [TikHub API](https://api.tikhub.io)
"""

docs_url = config['API']['Docs_URL']
redoc_url = config['API']['Redoc_URL']

app = FastAPI(
    title="Douyin TikTok Download API",
    description=description,
    version=version,
    openapi_tags=tags_metadata,
    docs_url=docs_url,  # 文档路径
    redoc_url=redoc_url,  # redoc文档路径
)

# API router
app.include_router(api_router, prefix="/api")

# PyWebIO APP
if config['Web']['PyWebIO_Enable']:
    webapp = asgi_app(lambda: MainView().main_view())
    app.mount("/", webapp)

if __name__ == '__main__':
    uvicorn.run(app, host=Host_IP, port=Host_Port)
