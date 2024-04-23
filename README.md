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

## 🔊 V4.0.0版本重构

> TODO:

- 移除了过时的bilibili代码，需要有人重写。
- 群里有人想添加快手以及西瓜视频的解析。
- 自述文件已经过时，需要进行重写。
- 进行PyPi包制作
- config.yaml文件需要进行修整。
- 添加对用户主页的解析。
- iOS快捷指令需要更新兼容最新的API响应和路径。
- 桌面端下载器或浏览器插件有需要可以进行开发。
- 解决爬虫Cookie风控问题。

> 更改

- 将Pywebio作为FastAPI的子APP一起运行。
- 重写了抖音以及TikTok的接口，感谢 [@johnserf-seed](https://github.com/Johnserf-Seed)
- 重写了文件下载的端点，现在使用异步文件IO。
- 对所有端点进行了注解和演示值的添加。
- 整理项目文件结构。

> 备注

感兴趣一起写这个项目的给请加微信`Evil0ctal`备注github项目重构，大家可以在群里互相交流学习，不允许发广告以及违法的东西，纯粹交朋友和技术交流。

> 私有接口服务

Discord: [TikHub Discord](https://discord.com/invite/aMEAS8Xsvz)

Free Douyin/TikTok API: [TikHub Beta API](https://beta.tikhub.io/)

## 👻介绍

> 🚨如需使用私有服务器运行本项目，请参考部署方式[[Docker部署](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker), [一键部署](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-linux)]

本项目是基于 [PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[HTTPX](https://www.python-httpx.org/)，快速异步的[抖音](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)数据爬取工具，并通过Web端实现在线批量解析以及下载无水印视频或图集，数据爬取API，iOS快捷指令无水印下载等功能。你可以自己部署或改造本项目实现更多功能，也可以在你的项目中直接调用[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)或安装现有的[pip包](https://pypi.org/project/douyin-tiktok-scraper/)作为解析库轻松爬取数据等.....

*一些简单的运用场景：*

*下载禁止下载的视频，进行数据分析，iOS无水印下载（搭配[iOS自带的快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)
配合本项目API实现应用内下载或读取剪贴板下载）等.....*

## 🖥演示站点: 我很脆弱...请勿压测(·•᷄ࡇ•᷅ ）


🍔Web APP: [https://douyin.wtf/](https://douyin.wtf/)

🍟API Document: [https://douyin.wtf/docs](https://douyin.wtf/docs)

🌭TikHub API Document: [https://api.tikhub.io/docs](https://api.tikhub.io/docs)

💾iOS Shortcut(快捷指令): [Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/discussions/104?sort=top)

📦️桌面端下载器(仓库推荐)：

- [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
- [HFrost0/bilix](https://github.com/HFrost0/bilix)
- [Tairraos/TikDown - [需更新]](https://github.com/Tairraos/TikDown/)

## ⚗️技术栈

* [/app/web](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/app/web) - [PyWebIO](https://www.pyweb.io/)
* [/app/api](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/app/api) - [FastAPI](https://fastapi.tiangolo.com/)
* [/crawlers](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/crawlers) - [HTTPX](https://www.python-httpx.org/)

> ***/crawlers***

- 向不同平台的API提交请求并取回数据，处理后返回字典(dict)，支持异步。

> ***/app/api***

- 获得请求参数并使用`Crawlers`相关类处理数据后以JSON形式返回，视频下载，配合iOS快捷指令实现快速调用，支持异步。

> ***/app/web***

- 使用`PyWebIO`制作的简易Web程序，将网页输入的值进行处理后使用`Crawlers`相关类处理接口输出相关数据在网页上。

***以上文件的参数大多可在对应的`config.yaml`中进行修改***

## 💡项目文件结构

```
./Douyin_TikTok_Download_API
    ├─app
    │  ├─api
    │  │  ├─endpoints
    │  │  └─models
    │  ├─download
    │  └─web
    │      └─views
    └─crawlers
        ├─douyin
        │  └─web
        ├─hybrid
        ├─tiktok
        │  ├─app
        │  └─web
        └─utils
```

## ✨功能：

- 抖音Web大多数API
- TikTok Web大多数API
- 网页端批量解析(支持抖音/TikTok混合提交)
- 在线下载视频或图集。
- API调用获取链接数据
- 制作[pip包](https://pypi.org/project/douyin-tiktok-scraper/)方便快速导入你的项目
- [iOS快捷指令快速调用API](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)实现应用内下载无水印视频/图集
- 解析作者主页内所有视频([TikHub-API](https://api.tikhub.io/docs) 支持抖音/TikTok)
- 解析视频内所有评论信息([TikHub-API](https://api.tikhub.io/docs) 支持抖音/TikTok)

---

## 📦调用解析库（待更新）:

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

***API文档：***

本地：[http://localhost:8000/docs](http://localhost:80/docs)

在线：[https://api.douyin.wtf/docs](https://api.douyin.wtf/docs)

***API演示：***

- 爬取视频数据(TikTok或Douyin混合解析)
  `https://api.douyin.wtf/api/hybrid/video_data?url=[视频链接/Video URL]&minimal=false`
- 下载视频/图集(TikTok或Douyin混合解析)
  `https://api.douyin.wtf/api/download?url=[视频链接/Video URL]&prefix=true&with_watermark=false`

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

> 开启/停止服务

- `systemctl start/stop Douyin_TikTok_Download_API.service`

> 开启/关闭开机自动运行

- `systemctl enable/disable Douyin_TikTok_Download_API.service`

> 更新项目

- `cd /www/wwwroot/Douyin_TikTok_Download_API/bash && sudo bash update.sh`

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
