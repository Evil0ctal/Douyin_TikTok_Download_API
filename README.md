<h1 align="center">
 <a href="https://douyin.wtf/" alt="logo" ><img src="https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/logo/logo192.png" width="120"/></a>
 <br>
 Douyin_TikTok_Download_API(抖音/TikTok无水印解析API)
</h1>

<div align="center">

[English](./README.en.md) | [简体中文](./README.md)

[![Release Version](https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/latest)
[![GitHub license](https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](LICENSE)
[![GitHub Star](https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)
[![GitHub Fork](https://img.shields.io/github/forks/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/network/members)
![GitHub Repo size](https://img.shields.io/github/repo-size/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&color=3cb371)
[![GitHub Repo Languages](https://img.shields.io/github/languages/top/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/search?l=python)
[![PyPI](https://img.shields.io/pypi/v/douyin-tiktok-scraper?style=flat-square)](https://pypi.org/project/douyin-tiktok-scraper/)
[![PyPI](https://img.shields.io/pypi/dm/douyin-tiktok-scraper?style=flat-square)](https://pypi.org/project/douyin-tiktok-scraper/)
<br>
[![API status](https://img.shields.io/website?down_color=lightgrey&down_message=API-V1%20offline&style=flat-square&up_color=blue&up_message=API-V1%20online&url=https%3A%2F%2Fapi.douyin.wtf%2Fdocs)](https://api.douyin.wtf/docs)
[![API status](https://img.shields.io/website?down_color=lightgrey&down_message=API-V2%20offline&style=flat-square&up_color=blue&up_message=API-V2%20online&url=https%3A%2F%2Fapi-v2.douyin.wtf%2Fdocs)](https://api-v2.douyin.wtf/docs)

</div>

## 👻介绍

> 🚨如需使用私有服务器运行本项目，请参考部署方式[[Docker部署](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker), [手动部署](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-%E6%89%8B%E5%8A%A8%E9%83%A8%E7%BD%B2)]

本项目是基于 [PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[AIOHTTP](https://docs.aiohttp.org/)，快速异步的[抖音](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)数据爬取工具，并通过Web端实现在线批量解析以及下载无水印视频或图集，数据爬取API，iOS快捷指令无水印下载等功能。你可以自己部署或改造本项目实现更多功能，也可以在你的项目中直接调用[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)或安装现有的[pip包](https://pypi.org/project/douyin-tiktok-scraper/)作为解析库轻松爬取数据等.....

*一些简单的运用场景：*

*下载禁止下载的视频，进行数据分析，iOS无水印下载（搭配[iOS自带的快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)
配合本项目API实现应用内下载或读取剪贴板下载）等.....*

## 🖥公共站点: 我很脆弱...请不要随意打我 ‎(·•᷄ࡇ•᷅ ）

> **API-V2:** 支持输入`Douyin|TikTok`用户主页爬取该作者[主页视频数据(去水印链接, 已点赞视频列表(权限需为公开), 视频评论数据, 背景音乐视频列表数据, 等等...), 详细信息请查看V2文档, 服务器响应时间有时会变长, 使用时请将`timeout`值设高.

🍔Web APP: [https://douyin.wtf/](https://douyin.wtf/)

🍟API-V1: [https://api.douyin.wtf/docs](https://api.douyin.wtf/docs)

🌭API-V2: [https://api-v2.douyin.wtf/docs](https://api-v2.douyin.wtf/docs)

💾iOS Shortcut(快捷指令): [Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/discussions/104?sort=top)

📦️桌面端下载器(仓库推荐)：

- [Tairraos/TikDown](https://github.com/Tairraos/TikDown/)
- [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
- [HFrost0/bilix](https://github.com/HFrost0/bilix)

## ⚗️技术栈

* [web_app.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/web_app.py) - [PyWebIO](https://www.pyweb.io/)
* [web_api.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/web_api.py) - [FastAPI](https://fastapi.tiangolo.com/)
* [scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py) - [AIOHTTP](https://docs.aiohttp.org/)

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
    ├── config.ini -> (configuration file)
```

## 💯已支持功能：

- 抖音（抖音海外版: TikTok）视频/图片解析
- 网页端批量解析(支持抖音/TikTok混合提交)
- 网页端解析结果页批量下载无水印视频(V3.0.0暂时移除)
- API调用获取链接数据
- 制作[pip包](https://pypi.org/project/douyin-tiktok-scraper/)方便快速导入你的项目
- [iOS快捷指令快速调用API](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)实现应用内下载无水印视频/图集
- 解析作者主页内所有视频([API-V2](https://api-v2.douyin.wtf/docs) 支持抖音/TikTok)
- 解析视频内所有评论信息([API-V2](https://api-v2.douyin.wtf/docs) 支持抖音/TikTok)

---

## 🤦‍后续功能：

- [ ] 欢迎提出新的建议或将你的思路在issue中与我分享
- [ ] 欢迎提交PR至[Development分支](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/Development) ♪(･ω･)ﾉ)

---

## 📦调用解析库:

>💡PyPi：[https://pypi.org/project/douyin-tiktok-scraper/](https://pypi.org/project/douyin-tiktok-scraper/)

安装解析库：`pip install douyin-tiktok-scraper`

```python
import asyncio
from douyin_tiktok_scraper.scraper import Scraper

api = Scraper()

async def async_test(url: str) -> dict:
    # Hybrid parsing(Douyin/TikTok URL)
    hybrid_data = await api.hybrid_parsing(url)
    print(f"The hybrid parsing result:\n {hybrid_data}")
    return hybrid_data

asyncio.run(async_test(url=input("Paste Douyin/TikTok share URL here: ")))
```

## 🗺️支持的提交格式：
>💡提示：包含但不仅限于以下例子，如果遇到链接解析失败请开启一个新[issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)

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

***API-V1文档：***
本地：[http://localhost:8000/docs](http://localhost:8000/docs)
在线：[https://api.douyin.wtf/docs](https://api.douyin.wtf/docs)

***API-V2文档：***
在线：[https://api-v2.douyin.wtf/docs](https://api-v2.douyin.wtf/docs)

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

- 首先要去安全组开放8080(Web)和8000(API)端口。
- 在宝塔面板应用商店内搜索`进程守护`或手动安装`supervisord`：
```
[宝塔面板]
https://www.bt.cn/new/download.html
[aapanel]
https://www.aapanel.com/new/download.html
[Supervisor]
http://supervisord.org/installing.html
```
- 配置项目[config.ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)文件
- 安装依赖文件`pip install -r requirements.txt`
- 设置`supervisord`守护进程
- 启动命令:
```console
[Web]
python3 web_app.py
[API]
python3 web_api.py
```
- 程序入口:
```text
[Web]
http://localhost:8080
[API]
http://localhost:8000
```

## 💽部署(方式二 Docker)

>  💡Docker Image repo: [Docker Hub](https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api)

- 安装docker

```yaml
curl -fsSL get.docker.com -o get-docker.sh&&sh get-docker.sh &&systemctl enable docker&&systemctl start docker
```

- 留下config.int和docker-compose.yml文件即可
- 运行命令,让容器在后台运行

```yaml
docker compose up -d
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
docker compose pull && docker compose down && docker compose up -d
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


