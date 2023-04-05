<div align="center">
<a href="https://douyin.wtf/" alt="logo" ><img src="./logo/logo192.png" width="120"/></a>
</div>
<h1 align="center">Douyin_TikTok_Download_API(抖音/TikTok API)</h1>

<div align="center">

[English](./README.en.md)\|[Simplified Chinese](./README.md)

🚀「Douyin_TikTok_Download_API」is an out-of-the-box high-performance asynchronous[Tik Tok](https://www.douyin.com)\|[TikTok](https://www.tiktok.com)A data crawling tool that supports API calls, online batch analysis and downloading.

[![GitHub license](https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](LICENSE)[![Release Version](https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/latest)[![GitHub Star](https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/stargazers)[![GitHub Fork](https://img.shields.io/github/forks/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/network/members)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)[![GitHub closed issues](https://img.shields.io/github/issues-closed/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues?q=is%3Aissue+is%3Aclosed)![GitHub Repo size](https://img.shields.io/github/repo-size/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&color=3cb371)<br>[![PyPI v](https://img.shields.io/pypi/v/douyin-tiktok-scraper?style=flat-square&color=%23a8e6cf)](https://pypi.org/project/douyin-tiktok-scraper/)[![PyPI wheel](https://img.shields.io/pypi/wheel/douyin-tiktok-scraper?style=flat-square&color=%23dcedc1)](https://pypi.org/project/douyin-tiktok-scraper/#files)[![PyPI dm](https://img.shields.io/pypi/dm/douyin-tiktok-scraper?style=flat-square&color=%23ffd3b6)](https://pypi.org/project/douyin-tiktok-scraper/)[![PyPI pyversions](https://img.shields.io/pypi/pyversions/douyin-tiktok-scraper?color=%23ffaaa5&style=flat-square)](https://pypi.org/project/douyin-tiktok-scraper/)<br>[![API status](https://img.shields.io/website?down_color=lightgrey&label=API%20Status&down_message=API%20offline&style=flat-square&up_color=%23dfb9ff&up_message=online&url=https%3A%2F%2Fapi.douyin.wtf%2Fdocs)](https://api.douyin.wtf/docs)[![TikHub-API status](https://img.shields.io/website?down_color=lightgrey&label=TikHub-API%20Status&down_message=API%20offline&style=flat-square&up_color=%23dfb9ff&up_message=online&url=https%3A%2F%2Fapi.tikhub.io%2Fdocs)](https://api.tikhub.io/docs)<br>[![爱发电](https://img.shields.io/badge/爱发电-evil0ctal-blue.svg?style=flat-square&color=ea4aaa&logo=github-sponsors)](https://afdian.net/@evil0ctal)[![Kofi](https://img.shields.io/badge/Kofi-evil0ctal-orange.svg?style=flat-square&logo=kofi)](https://ko-fi.com/evil0ctal)[![Patreon](https://img.shields.io/badge/Patreon-evil0ctal-red.svg?style=flat-square&logo=patreon)](https://www.patreon.com/evil0ctal)

</div>

## 👻 Introduction

> 🚨If you need to use a private server to run this project, please refer to the deployment method\[[Docker deployment](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker),[One-click deployment](./README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-linux)]

This project is based on[PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[AIOHTTP](https://docs.aiohttp.org/), fast asynchronous[Tik Tok](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)It is a data crawling tool, and realizes online batch analysis and download of video or atlas without watermark through the web terminal, data crawling API, iOS shortcut command without watermark download and other functions. You can deploy or transform this project yourself to achieve more functions, or you can call it directly in your project[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)or install an existing[pip package](https://pypi.org/project/douyin-tiktok-scraper/)As a parsing library, it is easy to crawl data, etc...

_Some simple usage scenarios:_

_Download prohibited videos for data analysis, download without watermark for iOS (with[iOS built-in shortcut command APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with this project API to realize in-app download or read clipboard download), etc....._

## 🖥Public site: I'm vulnerable...don't stress test (••᷄ࡇ•᷅ ）

> **Tikhub-Opi:**support`Douyin|TikTok`Crawl the author's home page video data (remove watermark link, liked video list (permissions need to be public), video comment data, background music video list data, etc...), for details, please check TikHub- API documentation. In addition, TikHub-API is faster than this project's API when capturing TikTok data.

🍔Web APP:<https://douyin.wtf/>

🍟API Document:<https://api.douyin.wtf/docs>

🌭TikHub API Document:<https://api.tikhub.io/docs>

💾iOS Shortcut:[Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/discussions/104?sort=top)

📦️Desktop downloader (recommended by warehouse):

-   [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
-   [HFrost0/bilix](https://github.com/HFrost0/bilix)
-   [Tairraos/TikDown - \[Needs update\]](https://github.com/Tairraos/TikDown/)

🛸Other repositories based on this project

-   [TikHubIO/TikHub_API_PyPi](https://github.com/TikHubIO/TikHub_API_PyPi)
-   [Evil0ctal/Douyin_Tiktok_Scraper_PyPi](https://github.com/Evil0ctal/Douyin_Tiktok_Scraper_PyPi)

## ⚗️Technology stack

-   [web_app.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/web_app.py)-[PyWebIO](https://www.pyweb.io/)
-   [web_api.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/web_api.py)-[FastAPI](https://fastapi.tiangolo.com/)
-   [scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/scraper.py)-[AIOHTTP](https://docs.aiohttp.org/)

> **_scraper.py:_**

-   Towards[Douyin|TikTok]The API submits requests and retrieves data, returns a dictionary (dict) after processing, and supports asynchronous.

> **_web_api.py:_**

-   Get request parameters and use`Scraper()`The class processes the data and returns it in the form of JSON, video downloads, quick calls with iOS shortcuts, and asynchronous support.

> **_web_app.py:_**

-   for`web_api.py`as well as`scraper.py`A simple web program made to process the value entered in the web page and then use it`Scraper()`class processing and matching`web_api.py`The interface output is on the web page (similar to the separation of front and back ends)

**_Most of the parameters of the above files can be found in[config. ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)make changes in_**

## 💡Project file structure

    .
    └── Douyin_TikTok_Download_API/
        ├── /static -> (PyWebIO static resources)
        ├── web_app.py -> (Web APP)
        ├── web_api.py -> (API)
        ├── scraper.py -> (Parsing library)
        ├── config.ini -> (Configuration file)
        ├── install.sh -> (Installation bash script)

## ✨Features:

-   Douyin (overseas version of Douyin: TikTok) video/picture analysis
-   Batch analysis on the web page (supports Douyin/TikTok mixed submission)
-   Batch download of non-watermarked videos from the analysis result page on the web page (removed in V3.X and later versions, please deploy V2.X version by yourself)
-   API call to get link data
-   make[pip package](https://pypi.org/project/douyin-tiktok-scraper/)Easily and quickly import your projects
-   [iOS shortcut command to quickly call API](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Realize in-app download without watermark video/photo gallery
-   Parse all videos on the author's homepage ([Tikhub-opy](https://api.tikhub.io/docs)Support Douyin/TikTok)
-   Parse all comment information in the video ([Tikhub-opy](https://api.tikhub.io/docs)Support Douyin/TikTok)

* * *

## 🤦‍To-do list:

> 💡Suggestions or PRs are welcome to this repository ♪(･ω･)ﾉ)

-   [ ] Write an asynchronous downloader on the desktop to achieve local batch download
-   [ ] TikHub-API adds data crawling for hash_tag pages[#101](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/101)
-   [ ] Add support for other short video platforms, such as: Douyin Volcano, Kuaishou, Watermelon Video, Bilibili

* * *

## 📦Calling the parsing library:

> 💡PyPi：<https://pypi.org/project/douyin-tiktok-scraper/>

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

asyncio.run(hybrid_parsing(url=input("Paste Douyin/TikTok share URL here: ")))
```

## 🗺️Supported submission formats:

> 💡Tips: Including but not limited to the following examples, if you encounter link parsing failures, please open a new one[issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)

-   Douyin sharing password (copy in APP)

```text
7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

-   Douyin short URL (copy in APP)

```text
https://v.douyin.com/L4FJNR3/
```

-   Douyin normal URL (web version copy)

```text
https://www.douyin.com/video/6914948781100338440
```

-   Douyin Discovery Page URL (APP Copy)

```text
https://www.douyin.com/discover?modal_id=7069543727328398622
```

-   TikTok short URL (copy in APP)

```text
https://www.tiktok.com/t/ZTR9nDNWq/
```

-   TikTok normal URL (web version copy)

```text
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

-   Douyin/TikTok bulk URLs (no need to use symbols to separate)

```text
https://v.douyin.com/L4NpDJ6/
https://www.douyin.com/video/7126745726494821640
2.84 nqe:/ 骑白马的也可以是公主%%百万转场变身https://v.douyin.com/L4FJNR3/ 复制此链接，打开Dou音搜索，直接观看视频！
https://www.tiktok.com/t/ZTR9nkkmL/
https://www.tiktok.com/t/ZTR9nDNWq/
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

## 🛰️API Documentation

> 💡Tip: You can also view the interface documentation in the code comments of web_api.py

**_API documentation:_**

local:<http://localhost:8000/docs>

online:<https://api.douyin.wtf/docs>

**_TikHub-API documentation:_**

online:<https://api.tikhub.io/docs>

**_API demo:_**

-   Crawl video data (TikTok or Douyin hybrid analysis)`https://api.douyin.wtf/api?url=[视频链接/Video URL]&minimal=false`
-   Download video/photo gallery (TikTok or Douyin hybrid analysis)`https://api.douyin.wtf/download?url=[视频链接/Video URL]&prefix=true&watermark=false`
-   Replace domain name to download video/photo gallery


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

**_For more demos, please check the content of the document..._**

## 💻 Deployment (method 1 Linux)

> 💡Tips: It is best to deploy this project to a server in the United States, otherwise strange bugs may appear.

recommend everyone to use[Digitalocean](https://www.digitalocean.com/)The server, mainly because of free.

Sign up with my invitation link, you can get a $200 credit, and when you spend $25 on it, I can also get a $25 reward.

My invite link:

<https://m.do.co/c/9f72a27dec35>

> Use the script to deploy this project with one click

-   Download using wget command[install.sh](https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh)to the server and run


    wget -O install.sh https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh && sudo bash install.sh

-   Automatically used after running the Bash script[config.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.py)to help you modify[config. ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)

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

-   The script will then ask you which services you want to start

api: started separately`web_api.py`

web: started separately`web_app.py`

all: start at the same time`web_api.py`and`web_app.py`

```console
Run API or Web? [api/web/all/quit] api
Do you want to start the api service when system boot? [y/n] y
Created symlink /etc/systemd/system/multi-user.target.wants/web_api.service → /etc/systemd/system/web_api.service.
API service will start when system boot!
Starting API...
API is running! You can visit http://your_ip:port
You can stop the api service by running: systemctl stop web_api.service
```

> Start/stop service

-   web service:`systemctl start/stop web_app.service`
-   api service:`systemctl start/stop web_api.service`

> Turn on/off autorun at startup

-   web service:`systemctl enable/disable web_app.service`
-   api service:`systemctl enable/disable web_api.service`

> update project

-   `cd /www/wwwroot/Douyin_TikTok_Download_API/bash`
-   `sudo sh update.sh`

## 💽Deployment (Method 2 Docker)

> 💡Docker Image repo:[Docker Hub](https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api)

-   install docker

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

-   delete container

```yaml
docker rm -f douyin_tiktok_download_api
```

-   renew

```yaml
docker-compose pull && docker-compose down && docker-compose up -d
```

## ❤️ Contributors

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
