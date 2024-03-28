<div align="center">
<a href="https://douyin.wtf/" alt="logo" ><img src="./logo/logo192.png" width="120"/></a>
</div>
<h1 align="center">Douyin_TikTok_Download_API(抖音/TikTok API)</h1>

<div align="center">

[English](./README.en.md) | [简体中文](./README.md)

🚀「Douyin_TikTok_Download_API」是一个开箱即用的高性能异步[抖音](https://www.douyin.com)|[TikTok](https://www.tiktok.com)|[Bilibili](https://www.bilibili.com)数据爬取工具，支持API调用，在线批量解析及下载。

[![GitHub license](https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](LICENSE)
[![Release Version](https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/latest)
[![GitHub Star](https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/stargazers)
[![GitHub Fork](https://img.shields.io/github/forks/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/network/members)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)
[![GitHub closed issues](https://img.shields.io/github/issues-closed/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues?q=is%3Aissue+is%3Aclosed)
![GitHub Repo size](https://img.shields.io/github/repo-size/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&color=3cb371)
<br>
[![PyPI v](https://img.shields.io/pypi/v/douyin-tiktok-scraper?style=flat-square&color=%23a8e6cf)](https://pypi.org/project/douyin-tiktok-scraper/)
[![PyPI wheel](https://img.shields.io/pypi/wheel/douyin-tiktok-scraper?style=flat-square&color=%23dcedc1)](https://pypi.org/project/douyin-tiktok-scraper/#files)
[![PyPI dm](https://img.shields.io/pypi/dm/douyin-tiktok-scraper?style=flat-square&color=%23ffd3b6)](https://pypi.org/project/douyin-tiktok-scraper/)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/douyin-tiktok-scraper?color=%23ffaaa5&style=flat-square)](https://pypi.org/project/douyin-tiktok-scraper/)
<br>
[![API status](https://img.shields.io/website?down_color=lightgrey&label=API%20Status&down_message=API%20offline&style=flat-square&up_color=%23dfb9ff&up_message=online&url=https%3A%2F%2Fapi.douyin.wtf%2Fdocs)](https://api.douyin.wtf/docs)
[![TikHub-API status](https://img.shields.io/website?down_color=lightgrey&label=TikHub-API%20Status&down_message=API%20offline&style=flat-square&up_color=%23dfb9ff&up_message=online&url=https%3A%2F%2Fapi.tikhub.io%2Fdocs)](https://api.tikhub.io/docs)
<br>
[![爱发电](https://img.shields.io/badge/爱发电-evil0ctal-blue.svg?style=flat-square&color=ea4aaa&logo=github-sponsors)](https://afdian.net/@evil0ctal)
[![Kofi](https://img.shields.io/badge/Kofi-evil0ctal-orange.svg?style=flat-square&logo=kofi)](https://ko-fi.com/evil0ctal)
[![Patreon](https://img.shields.io/badge/Patreon-evil0ctal-red.svg?style=flat-square&logo=patreon)](https://www.patreon.com/evil0ctal)

</div>

## 🔊 本项目计划在V4.0.0版本进行重构。
感兴趣的给请加微信`Evil0ctal`备注github项目重构，目前需要有爬虫/后端/全栈开发，如果你不具备相关技术栈也可以进来，主要是想着拉一个群然后大家可以在群里互相交流学习，不允许发广告以及违法的东西，纯粹交朋友和技术交流。

Discord: [TikHub Discord](https://discord.com/invite/aMEAS8Xsvz)

## 👻介绍

> 🚨如需使用私有服务器运行本项目，请参考部署方式[[Docker部署](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker), [一键部署](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-linux)]

本项目是基于 [PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[AIOHTTP](https://docs.aiohttp.org/)，快速异步的[抖音](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)/[Bilibili](https://www.bilibili.com)数据爬取工具，并通过Web端实现在线批量解析以及下载无水印视频或图集，数据爬取API，iOS快捷指令无水印下载等功能。你可以自己部署或改造本项目实现更多功能，也可以在你的项目中直接调用[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)或安装现有的[pip包](https://pypi.org/project/douyin-tiktok-scraper/)作为解析库轻松爬取数据等.....

*一些简单的运用场景：*

*下载禁止下载的视频，进行数据分析，iOS无水印下载（搭配[iOS自带的快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)
配合本项目API实现应用内下载或读取剪贴板下载）等.....*

## 🖥公共站点: 我很脆弱...请勿压测(·•᷄ࡇ•᷅ ）

> **TikHub-API:** 支持`Douyin|TikTok`用户主页爬取该作者[主页视频数据(去水印链接, 已点赞视频列表(权限需为公开), 视频评论数据, 背景音乐视频列表数据, 等等...), 详细信息请查看TikHub-API文档，此外TikHub-API对比本项目API在抓取TikTok数据时，TikHub-API速度更快。

🍔Web APP: [https://douyin.wtf/](https://douyin.wtf/)

🍟API Document: [https://api.douyin.wtf/docs](https://api.douyin.wtf/docs)

🌭TikHub API Document: [https://api.tikhub.io/docs](https://api.tikhub.io/docs)

💾iOS Shortcut(快捷指令): [Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/discussions/104?sort=top)

🎉Chrome Plugin (浏览器插件) [README](./tools/README.md)

📦️桌面端下载器(仓库推荐)：

- [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
- [HFrost0/bilix](https://github.com/HFrost0/bilix)
- [Tairraos/TikDown - [需更新]](https://github.com/Tairraos/TikDown/)

🛸基于本项目的其他仓库

- [TikHubIO/TikHub_API_PyPi](https://github.com/TikHubIO/TikHub_API_PyPi)
- [Evil0ctal/Douyin_Tiktok_Scraper_PyPi](https://github.com/Evil0ctal/Douyin_Tiktok_Scraper_PyPi)

## ⚗️技术栈

* [web_app.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/web_app.py) - [PyWebIO](https://www.pyweb.io/)
* [web_api.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/web_api.py) - [FastAPI](https://fastapi.tiangolo.com/)
* [scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/scraper.py) - [AIOHTTP](https://docs.aiohttp.org/)

> ***scraper.py:***

- 向[Douyin|TikTok]的API提交请求并取回数据，处理后返回字典(dict)，支持异步。

> ***web_api.py:***

- 获得请求参数并使用`Scraper()`类处理数据后以JSON形式返回，视频下载，配合iOS快捷指令实现快速调用，支持异步。

> ***web_app.py:***

- 为`web_api.py`以及`scraper.py`制作的简易Web程序，将网页输入的值进行处理后使用`Scraper()`类处理并配合`web_api.py`的接口输出在网页上(类似前后端分离)

***以上文件的参数大多可在[config.ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)中进行修改***

## 💡项目文件结构

```
.
└── Douyin_TikTok_Download_API/
    ├── /static -> (PyWebIO static resources)
    ├── web_app.py -> (Web APP)
    ├── web_api.py -> (API)
    ├── scraper.py -> (Parsing library)
    ├── config.ini -> (Configuration file)
    ├── install.sh -> (Installation bash script)
```

## ✨功能：

- 抖音（抖音海外版: TikTok）视频/图片解析
- Bilibili视频解析
- 西瓜视频解析
- 快手视频解析
- 网页端批量解析(支持抖音/TikTok混合提交)
- 网页端解析结果页批量下载无水印视频(V3.X以上版本移除，请自行部署V2.X版本)
- API调用获取链接数据
- 制作[pip包](https://pypi.org/project/douyin-tiktok-scraper/)方便快速导入你的项目
- [iOS快捷指令快速调用API](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)实现应用内下载无水印视频/图集
- 解析作者主页内所有视频([TikHub-API](https://api.tikhub.io/docs) 支持抖音/TikTok)
- 解析视频内所有评论信息([TikHub-API](https://api.tikhub.io/docs) 支持抖音/TikTok)

---

## 🤦‍待办清单：

> 💡欢迎提出建议或直接提交PR至此仓库 ♪(･ω･)ﾉ)

- [ ] 编写一个桌面端的异步下载器实现本地批量下载
- [ ] TikHub-API添加对hash_tag页面的数据爬取 [#101](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/101)
- [ ] 对其他短视频平台添加支持，如：抖音火山版，快手，西瓜视频，哔哩哔哩

---

## 📦调用解析库:

> 💡PyPi：[https://pypi.org/project/douyin-tiktok-scraper/](https://pypi.org/project/douyin-tiktok-scraper/)

安装解析库：`pip install douyin-tiktok-scraper`

```python
import asyncio
from douyin_tiktok_scraper.scraper import Scraper

api = Scraper()

async def hybrid_parsing(url: str) -> dict:
    # Hybrid parsing(Douyin/TikTok URL)
    result = await api.hybrid_parsing(url)
    print(f"The hybrid parsing result:\n {result}")
    return result

asyncio.run(hybrid_parsing(url=input("Paste Douyin/TikTok/Bilibili share URL here: ")))
```

## 🗺️支持的提交格式：

> 💡提示：包含但不仅限于以下例子，如果遇到链接解析失败请开启一个新 [issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)

- 快手视频链接

```text
https://www.kuaishou.com/short-video/3xiqjrezhqjyzxw/
https://v.kuaishou.com/75kDOJ/
```

- 西瓜视频链接

```text
https://www.ixigua.com/7270448082586698281/
https://m.ixigua.com/video/7274710134306112054/
```

- Bilibili视频链接

```text
https://www.bilibili.com/video/BV1Th411x7ii/
```

- 抖音分享口令  (APP内复制)

```text
7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

- 抖音短网址 (APP内复制)

```text
https://v.douyin.com/L4FJNR3/
```

- 抖音正常网址 (网页版复制)

```text
https://www.douyin.com/video/6914948781100338440
```

- 抖音发现页网址 (APP复制)

```text
https://www.douyin.com/discover?modal_id=7069543727328398622
```

- TikTok短网址 (APP内复制)

```text
https://www.tiktok.com/t/ZTR9nDNWq/
```

- TikTok正常网址 (网页版复制)

```text
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

- 抖音/TikTok批量网址(无需使用符合隔开)

```text
https://v.douyin.com/L4NpDJ6/
https://www.douyin.com/video/7126745726494821640
2.84 nqe:/ 骑白马的也可以是公主%%百万转场变身https://v.douyin.com/L4FJNR3/ 复制此链接，打开Dou音搜索，直接观看视频！
https://www.tiktok.com/t/ZTR9nkkmL/
https://www.tiktok.com/t/ZTR9nDNWq/
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

## 🛰️API文档

> 💡提示：也可以在web_api.py的代码注释中查看接口文档

***API文档：***

本地：[http://localhost:8000/docs](http://localhost:8000/docs)

在线：[https://api.douyin.wtf/docs](https://api.douyin.wtf/docs)

***TikHub-API文档：***

在线：[https://api.tikhub.io/docs](https://api.tikhub.io/docs)

***API演示：***

- 爬取视频数据(TikTok或Douyin混合解析)
  `https://api.douyin.wtf/api?url=[视频链接/Video URL]&minimal=false`
- 下载视频/图集(TikTok或Douyin混合解析)
  `https://api.douyin.wtf/download?url=[视频链接/Video URL]&prefix=true&watermark=false`
- 替换域名下载视频/图集

```
[抖音]
原始链接:
https://www.douyin.com/video/7159502929156705567
替换域名:
https://api.douyin.wtf/video/7159502929156705567
# 返回无水印视频下载响应
[TikTok]
original link:
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
Replace Domain:
https://api.douyin.wtf/@evil0ctal/video/7156033831819037994
# Return No Watermark Video Download Response
```

***更多演示请查看文档内容......***

## 💻部署(方式一 Linux)

> 💡提示：最好将本项目部署至美国地区的服务器，否则可能会出现奇怪的BUG。

推荐大家使用[Digitalocean](https://www.digitalocean.com/)的服务器，主要是因为免费。

使用我的邀请链接注册，你可以获得$200的credit，当你在上面消费$25时，我也可以获得$25的奖励。

我的邀请链接：

[https://m.do.co/c/9f72a27dec35](https://m.do.co/c/9f72a27dec35)

> 使用脚本一键部署本项目

- 使用wget命令下载[install.sh](https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh)至服务器并运行

```
wget -O install.sh https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh && sudo bash install.sh
```

- 运行Bash脚本后会自动使用[config.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.py)来帮助你修改[config.ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)

```console
Please edit config.ini, all input must be numbers!
Default API port: 8000
If you want use different port input new API port here: 80
Use new port for web_api.py: 80
Default API rate limit: 10/minute
If you want use different rate limit input new rate limit here: 60
Use new rate limit: 60/minute
Default App port: 80
If you want use different port input new App port here: 8080
Use new port: 8080
```

- 随后脚本会询问你要启动的服务

api：单独启动`web_api.py`

web：单独启动`web_app.py`

all：同时启动`web_api.py`和`web_app.py`

```console
Run API or Web? [api/web/all/quit] api
Do you want to start the api service when system boot? [y/n] y
Created symlink /etc/systemd/system/multi-user.target.wants/web_api.service → /etc/systemd/system/web_api.service.
API service will start when system boot!
Starting API...
API is running! You can visit http://your_ip:port
You can stop the api service by running: systemctl stop web_api.service
```

> 开启/停止服务

- web服务：`systemctl start/stop web_app.service`
- api服务：`systemctl start/stop web_api.service`

> 开启/关闭开机自动运行

- web服务：`systemctl enable/disable web_app.service`
- api服务：`systemctl enable/disable web_api.service`

> 更新项目

- `cd /www/wwwroot/Douyin_TikTok_Download_API/bash`
- `sudo sh update.sh`

## 💽部署(方式二 Docker)

> 💡Docker Image repo: [Docker Hub](https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api)

- 安装docker

```yaml
curl -fsSL get.docker.com -o get-docker.sh&&sh get-docker.sh &&systemctl enable docker&&systemctl start docker
```

- 留下config.ini和docker-compose.yml文件即可
- 运行命令,让容器在后台运行

```yaml
docker-compose up -d
```

- 查看容器日志

```yaml
docker logs -f douyin_tiktok_download_api
```

- 删除容器

```yaml
docker rm -f douyin_tiktok_download_api
```

- 更新

```yaml
docker-compose pull && docker-compose down && docker-compose up -d
```

## ❤️ 贡献者

[![](https://github.com/Evil0ctal.png?size=50)](https://github.com/Evil0ctal)
[![](https://github.com/jw-star.png?size=50)](https://github.com/jw-star)
[![](https://github.com/Jeffrey-deng.png?size=50)](https://github.com/Jeffrey-deng)
[![](https://github.com/chris-ss.png?size=50)](https://github.com/chris-ss)
[![](https://github.com/weixuan00.png?size=50)](https://github.com/weixuan00)
[![](https://github.com/Tairraos.png?size=50)](https://github.com/Tairraos)

## 📸截图

***API速度测试(对比官方API)***

<details><summary>🔎点击展开截图</summary>

抖音官方API:
![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/Douyin_API.png?raw=true)

本项目API:
![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/Douyin_API_Douyin_wtf.png?raw=true)

TikTok官方API:
![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/TikTok_API.png?raw=true)

本项目API:
![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/TikTok_API_Douyin_wtf.png?raw=true)

</details>
<hr>

***项目界面***

<details><summary>🔎点击展开截图</summary>

Web主界面:

![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/v3_screenshots/Home.png?raw=true)

Web main interface:

![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/v3_screenshots/Home_en.png?raw=true)

</details>
<hr>

## 📜 Star历史

[![Star History Chart](https://api.star-history.com/svg?repos=Evil0ctal/Douyin_TikTok_Download_API&type=Timeline)](https://star-history.com/#Evil0ctal/Douyin_TikTok_Download_API&Timeline)

[MIT License](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/LICENSE)

> Start: 2021/11/06
> GitHub: [@Evil0ctal](https://github.com/Evil0ctal)
> Contact: Evil0ctal1985@gmail.com


