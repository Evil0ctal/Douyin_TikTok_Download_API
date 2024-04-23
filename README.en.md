<div align="center">
<a href="https://douyin.wtf/" alt="logo" ><img src="./logo/logo192.png" width="120"/></a>
</div>
<h1 align="center">Douyin_TikTok_Download_API(抖音/TikTok API)</h1>

<div align="center">

[English](./README.en.md)\|[Simplified Chinese](./README.md)

🚀"Douyin_TikTok_Download_API" is a high-performance asynchronous API that can be used out of the box[Tik Tok](https://www.douyin.com)\|[TikTok](https://www.tiktok.com)\|[Bilibili](https://www.bilibili.com)Data crawling tool supports API calling, online batch analysis and downloading.

[![GitHub license](https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](LICENSE)[![Release Version](https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/latest)[![GitHub Star](https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/stargazers)[![GitHub Fork](https://img.shields.io/github/forks/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/network/members)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)[![GitHub closed issues](https://img.shields.io/github/issues-closed/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues?q=is%3Aissue+is%3Aclosed)![GitHub Repo size](https://img.shields.io/github/repo-size/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&color=3cb371)
<br>[![PyPI v](https://img.shields.io/pypi/v/douyin-tiktok-scraper?style=flat-square&color=%23a8e6cf)](https://pypi.org/project/douyin-tiktok-scraper/)[![PyPI wheel](https://img.shields.io/pypi/wheel/douyin-tiktok-scraper?style=flat-square&color=%23dcedc1)](https://pypi.org/project/douyin-tiktok-scraper/#files)[![PyPI dm](https://img.shields.io/pypi/dm/douyin-tiktok-scraper?style=flat-square&color=%23ffd3b6)](https://pypi.org/project/douyin-tiktok-scraper/)[![PyPI pyversions](https://img.shields.io/pypi/pyversions/douyin-tiktok-scraper?color=%23ffaaa5&style=flat-square)](https://pypi.org/project/douyin-tiktok-scraper/)<br>[![API status](https://img.shields.io/website?down_color=lightgrey&label=API%20Status&down_message=API%20offline&style=flat-square&up_color=%23dfb9ff&up_message=online&url=https%3A%2F%2Fapi.douyin.wtf%2Fdocs)](https://api.douyin.wtf/docs)[![TikHub-API status](https://img.shields.io/website?down_color=lightgrey&label=TikHub-API%20Status&down_message=API%20offline&style=flat-square&up_color=%23dfb9ff&up_message=online&url=https%3A%2F%2Fapi.tikhub.io%2Fdocs)](https://api.tikhub.io/docs)<br>[![爱发电](https://img.shields.io/badge/爱发电-evil0ctal-blue.svg?style=flat-square&color=ea4aaa&logo=github-sponsors)](https://afdian.net/@evil0ctal)[![Kofi](https://img.shields.io/badge/Kofi-evil0ctal-orange.svg?style=flat-square&logo=kofi)](https://ko-fi.com/evil0ctal)[![Patreon](https://img.shields.io/badge/Patreon-evil0ctal-red.svg?style=flat-square&logo=patreon)](https://www.patreon.com/evil0ctal)

</div>

## 🔊 V4.0.0 version refactoring

> ALL:

-   Removed outdated bilibili code and needs someone to rewrite it.
-   Someone in the group wants to add the analysis of Kuaishou and Xigua videos.
-   The readme is outdated and needs to be rewritten.
-   Make PyPi package
-   The config.yaml file needs to be trimmed.
-   Add parsing of user homepage.
-   iOS shortcuts need to be updated to be compatible with the latest API responses and paths.
-   Desktop downloaders or browser plug-ins can be developed if necessary.
-   Solve the problem of crawler cookie risk control.

> Change

-   Run Pywebio as a sub-APP of FastAPI.
-   Rewritten the interfaces of Douyin and TikTok, thank you[@johnserf-seed](https://github.com/Johnserf-Seed)
-   The file download endpoint has been rewritten and now uses asynchronous file IO.
-   Annotations and demonstration values ​​were added to all endpoints.
-   Organize the project file structure.

> Remark

If you are interested in writing this project together, please add us on WeChat`Evil0ctal`Note: Github project reconstruction, everyone can communicate and learn from each other in the group. Advertising and illegal things are not allowed. It is purely for making friends and technical exchanges.

> Private interface service

Discord:[Tikhub discord](https://discord.com/invite/aMEAS8Xsvz)

Free Douyin/TikTok API:[Tikhub Beta Opi](https://beta.tikhub.io/)

## 👻Introduction

> 🚨If you need to use a private server to run this project, please refer to the deployment method\[[Docker deployment](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker),[One-click deployment](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-linux)]

This project is based on[PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[HTTPX](https://www.python-httpx.org/), fast and asynchronous[Tik Tok](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)Data crawling tool, and realizes online batch analysis and downloading of videos or photo albums without watermarks through the Web, data crawling API, iOS shortcut command without watermark downloads and other functions. You can deploy or modify this project yourself to achieve more functions, or you can call it directly in your project[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)or install an existing[pip package](https://pypi.org/project/douyin-tiktok-scraper/)As a parsing library, it is easy to crawl data, etc.....

_Some simple application scenarios:_

_Download prohibited videos, perform data analysis, download without watermark on iOS (with[Shortcut command APP that comes with iOS](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with the API of this project to achieve in-app downloads or read clipboard downloads), etc....._

## 🖥Demo site: I am very vulnerable...please do not stress test (·•᷄ࡇ•᷅ )

🍔Web APP: <https://douyin.wtf/>

🍟API Document:<https://douyin.wtf/docs>

🌭TikHub API Document:<https://api.tikhub.io/docs>

💾iOS Shortcut (shortcut command):[Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/discussions/104?sort=top)

📦️Desktop downloader (recommended by warehouse):

-   [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
-   [HFrost0/bilix](https://github.com/HFrost0/bilix)
-   [Tairraos/TikDown - \[needs update\]](https://github.com/Tairraos/TikDown/)

## ⚗️Technology stack

-   [/app/web](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/app/web)-[PyWebIO](https://www.pyweb.io/)
-   [/app/api](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/app/api)-[FastAPI](https://fastapi.tiangolo.com/)
-   [/crawlers](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/crawlers)-[HTTPX](https://www.python-httpx.org/)

> **_/crawlers_**

-   Submit requests to APIs on different platforms and retrieve data. After processing, a dictionary (dict) is returned, and asynchronous support is supported.

> **_/app/api_**

-   Get request parameters and use`Crawlers`The related classes process the data and return it in JSON form, download the video, and cooperate with iOS shortcut commands to achieve fast calling and support asynchronous.

> **_/app/web_**

-   use`PyWebIO`A simple web program created to process the values ​​entered on the web page and then use them`Crawlers`The related class processing interface outputs related data on the web page.

**_Most of the parameters of the above files can be found in the corresponding`config.yaml`Modify in_**

## 💡Project file structure

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

## ✨Features:

-   Douyin Web Most API
-   TikTok WebMost APIs
-   Batch analysis on the web page (supports Douyin/TikTok mixed submission)
-   Download videos or photo albums online.
-   API call to get link data
-   make[pip package](https://pypi.org/project/douyin-tiktok-scraper/)Conveniently and quickly import your projects
-   [iOS shortcut commands to quickly call API](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Achieve in-app download of watermark-free videos/photo albums
-   Analyze all videos on the author's homepage ([Tikhub-opy](https://api.tikhub.io/docs)Support Douyin/TikTok)
-   Parse all comment information in the video ([Tikhub-opy](https://api.tikhub.io/docs)Support Douyin/TikTok)

* * *

## 📦Call the parsing library (to be updated):

> 💡PyPi:<https://pypi.org/project/douyin-tiktok-scraper/>

Install the parsing library:`pip install douyin-tiktok-scraper`

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

## 🗺️Supported submission formats:

> 💡Tip: Including but not limited to the following examples, if you encounter link parsing failure, please open a new one[issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)

-   Kuaishou video link

```text
https://www.kuaishou.com/short-video/3xiqjrezhqjyzxw/
https://v.kuaishou.com/75kDOJ/
```

-   Watermelon video link

```text
https://www.ixigua.com/7270448082586698281/
https://m.ixigua.com/video/7274710134306112054/
```

-   Bilibili video link

```text
https://www.bilibili.com/video/BV1Th411x7ii/
```

-   Douyin sharing password (copy in APP)

```text
7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

-   Douyin short URL (copy within APP)

```text
https://v.douyin.com/L4FJNR3/
```

-   Douyin normal URL (copy from web version)

```text
https://www.douyin.com/video/6914948781100338440
```

-   Douyin discovery page URL (APP copy)

```text
https://www.douyin.com/discover?modal_id=7069543727328398622
```

-   TikTok short URL (copy within APP)

```text
https://www.tiktok.com/t/ZTR9nDNWq/
```

-   TikTok normal URL (copy from web version)

```text
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

-   Douyin/TikTok batch URL (no need to use matching separation)

```text
https://v.douyin.com/L4NpDJ6/
https://www.douyin.com/video/7126745726494821640
2.84 nqe:/ 骑白马的也可以是公主%%百万转场变身https://v.douyin.com/L4FJNR3/ 复制此链接，打开Dou音搜索，直接观看视频！
https://www.tiktok.com/t/ZTR9nkkmL/
https://www.tiktok.com/t/ZTR9nDNWq/
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

## 🛰️API documentation

**_API documentation:_**

local:[http://localhost:8000/docs](http://localhost:80/docs)

Online:<https://api.douyin.wtf/docs>

**_API demo:_**

-   Crawl video data (TikTok or Douyin hybrid analysis)`https://api.douyin.wtf/api/hybrid/video_data?url=[视频链接/Video URL]&minimal=false`
-   Download videos/photo albums (TikTok or Douyin hybrid analysis)`https://api.douyin.wtf/api/download?url=[视频链接/Video URL]&prefix=true&with_watermark=false`

**_For more demonstrations, please see the documentation..._**

## 💻Deployment (Method 1 Linux)

> 💡Tips: It is best to deploy this project to a server in the United States, otherwise strange BUGs may occur.

Recommended for everyone to use[Digitalocean](https://www.digitalocean.com/)servers, mainly because they are free.

Use my invitation link to sign up and you can get a $200 credit, and when you spend $25 on it, I can also get a $25 reward.

My invitation link:

<https://m.do.co/c/9f72a27dec35>

> Use script to deploy this project with one click

-   Download using wget command[install.sh](https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh)to the server and run


    wget -O install.sh https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh && sudo bash install.sh

> Start/stop service

-   `systemctl start/stop Douyin_TikTok_Download_API.service`

> Turn on/off automatic operation at startup

-   `systemctl enable/disable Douyin_TikTok_Download_API.service`

> Update project

-   `cd /www/wwwroot/Douyin_TikTok_Download_API/bash && sudo bash update.sh`

## 💽Deployment (Method 2 Docker)

> 💡Docker Image repo:[Docker Hub](https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api)

-   Install docker

```yaml
curl -fsSL get.docker.com -o get-docker.sh&&sh get-docker.sh &&systemctl enable docker&&systemctl start docker
```

-   Just leave the config.ini and docker-compose.yml files
-   Run the command to let the container run in the background

```yaml
docker-compose up -d
```

-   View container logs

```yaml
docker logs -f douyin_tiktok_download_api
```

-   Delete container

```yaml
docker rm -f douyin_tiktok_download_api
```

-   renew

```yaml
docker-compose pull && docker-compose down && docker-compose up -d
```

## ❤️Contributor

[![](https://github.com/Evil0ctal.png?size=50)](https://github.com/Evil0ctal)[![](https://github.com/jw-star.png?size=50)](https://github.com/jw-star)[![](https://github.com/Jeffrey-deng.png?size=50)](https://github.com/Jeffrey-deng)[![](https://github.com/chris-ss.png?size=50)](https://github.com/chris-ss)[![](https://github.com/weixuan00.png?size=50)](https://github.com/weixuan00)[![](https://github.com/Tairraos.png?size=50)](https://github.com/Tairraos)

## 📸Screenshot

**_API speed test (compared to official API)_**

<details><summary>🔎点击展开截图</summary>

Douyin official API:![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/Douyin_API.png?raw=true)

API of this project:![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/Douyin_API_Douyin_wtf.png?raw=true)

TikTok official API:![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/TikTok_API.png?raw=true)

API of this project:![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/benchmarks/TikTok_API_Douyin_wtf.png?raw=true)

</details>
<hr>

**_Project interface_**

<details><summary>🔎点击展开截图</summary>

Web main interface:

![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/v3_screenshots/Home.png?raw=true)

Web main interface:

![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/v3_screenshots/Home_en.png?raw=true)

</details>
<hr>

## 📜 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Evil0ctal/Douyin_TikTok_Download_API&type=Timeline)](https://star-history.com/#Evil0ctal/Douyin_TikTok_Download_API&Timeline)

[MY License](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/LICENSE)

> Start: 2021/11/06
> GitHub:[@Evil0ctal](https://github.com/Evil0ctal)Contact:[Evil0ctal1985@gmail.com](mailto:Evil0ctal1985@gmail.com)
