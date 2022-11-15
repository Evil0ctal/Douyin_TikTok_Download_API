<h1 align="center">
 Douyin_TikTok_Download_API(抖音/TikTok无水印解析API)
 <br><br>
 <a href="https://douyin.wtf/" alt="logo" ><img src="https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/logo/logo192.png" width="120"/></a>
</h1>
<p align="center">
 <a href="https://github.com/Evil0ctal/Douyin_TikTok_Download_API#%E8%BF%90%E8%A1%8C%E8%AF%B4%E6%98%8E%E7%BB%8F%E8%BF%87%E6%B5%8B%E8%AF%95%E8%BF%87%E7%9A%84python%E7%89%88%E6%9C%AC%E4%B8%BA38">📝运行说明</a> •
  <a href="https://github.com/Evil0ctal/Douyin_TikTok_Download_API/#%EF%B8%8Fapi使用">👽️API使用</a> •
  <a href="https://github.com/Evil0ctal/Douyin_TikTok_Download_API#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-%E6%89%8B%E5%8A%A8%E9%83%A8%E7%BD%B2">🔧手动部署</a> •
  <a href="https://github.com/Evil0ctal/Douyin_TikTok_Download_API#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker">🚧Docker部署</a> •
  <a href="https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api">📦️Docker镜像</a> •
  <a href="https://github.com/Evil0ctal/Douyin_TikTok_Download_API#%EF%B8%8F-贡献者">🧑‍💻贡献者</a>
</p>
<br>

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)[![Docker Image size](https://img.shields.io/docker/image-size/evil0ctal/douyin_tiktok_download_api?style=flat-square)](https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api)

Language:  \[[English](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.en.md)]  \[[Simplified Chinese](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md)]

## 👻Introduction

> 🚨If you need to use a private server to run this project, please refer to the deployment method\[[Docker deployment](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker),[Manual deployment](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-%E6%89%8B%E5%8A%A8%E9%83%A8%E7%BD%B2)]

This project is based on[PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[AIOHTTP](https://docs.aiohttp.org/), fast asynchronous[Tik Tok](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)Data crawling tool, and realize online batch parsing and download of watermark-free videos or atlases through the web terminal, data crawling API, iOS shortcut command without watermark download and other functions. You can deploy or transform this project yourself to achieve more functions, or you can call it directly in your project[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)or install an existing[pip package](https://pypi.org/project/douyin-tiktok-scraper/)As a parsing library to easily crawl data, etc...

_Some simple application scenarios:_

_Download prohibited videos, perform data analysis, and download without watermark on iOS (with[Shortcut APP that comes with iOS](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with the API of this project to realize in-app download or read clipboard download), etc..._

## 🖥Public Site: I'm vulnerable...please don't hit me at will ‎(·•᷄ࡇ•᷅ ）

> **API-V2:**support input`Douyin|TikTok`The user's homepage crawls the author \[homepage video data (remove watermark link, liked video list (permission must be public), video comment data, background music video list data, etc...), please check the V2 document for details , the server response time may sometimes become longer, please`timeout`Set the value high.

🍔Web APP:<https://douyin.wtf/>

🍟API-V1:<https://api.douyin.wtf/docs>

🌭API-V2:<https://api-v2.douyin.wtf/docs>

💾iOS Shortcut:[Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/discussions/104?sort=top)

📦️ Desktop downloader (recommended warehouse):

-   [Tairraos/TikToon](https://github.com/Tairraos/TikDown/)
-   [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
-   [HFrost0/bilix](https://github.com/HFrost0/bilix)

## ⚗️Technology stack

-   [web_app.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/web_app.py)-[PyWebIO](https://www.pyweb.io/)
-   [web_api.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/web_api.py)-[FastAPI](https://fastapi.tiangolo.com/)
-   [scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)-[AIOHTTP](https://docs.aiohttp.org/)

> **_scraper.py:_**

-   Towards[Douyin|TikTok]The API submits requests and retrieves data, returns a dictionary (dict) after processing, and supports asynchronous.

> **_web_api.py:_**

-   get request parameters and use`Scraper()`After the class processes the data, it returns it in the form of JSON, and the video is downloaded. It can be called quickly with the iOS shortcut command, and supports asynchronous.

> **_web_app.py:_**

-   for`web_api.py`as well as`scraper.py`Created a simple web program that processes the value entered on the web page and uses it`Scraper()`Class handling and coordination`web_api.py`The interface output is on the web page (similar to front-end and back-end separation)

**_Most of the parameters of the above files can be found in[config.ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)make changes in_**

## 💡Project file structure

    .
    └── Douyin_TikTok_Download_API/
        ├── /static -> (PyWebIO static resources)
        ├── web_app.py -> (Web APP)
        ├── web_api.py -> (API)
        ├── scraper.py -> (Parsing library)
        ├── config.ini -> (configuration file)

## 💯 Supported features:

-   Douyin (Douyin overseas version: TikTok) video/picture analysis
-   Batch parsing on the webpage (support Douyin/TikTok mixed submission)
-   Batch download of watermark-free videos on the web-side parsing result page (Temporarily removed for V3.0.0)
-   API call to get link data
-   make[pip package](https://pypi.org/project/douyin-tiktok-scraper/)Easily and quickly import your projects
-   [iOS shortcut command to quickly call API](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Realize in-app download of video/atlas without watermark
-   Parse all videos in the author's homepage ([API-V2](https://api-v2.douyin.wtf/docs)Support Douyin/TikTok)
-   Parse all comments in the video ([API-V2](https://api-v2.douyin.wtf/docs)Support Douyin/TikTok)

* * *

## 🤦‍Follow-up features:

-   [ ] Welcome to make new suggestions or share your ideas with me in the issue
-   [ ] Welcome to submit PR to[Development branch](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/Development)♪(･ω･)ﾉ)

* * *

## 📦 Call parsing library:

> 💡PyPi:<https://pypi.org/project/douyin-tiktok-scraper/>

Install the parsing library:`pip install douyin-tiktok-scraper`

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

## 🗺️ Supported submission formats:

> 💡Tip: Including but not limited to the following examples, if you encounter link resolution failure, please open a new one[issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)

-   Douyin share password (copy in APP)

```text
7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

-   Douyin Short URL (Copy in APP)

```text
https://v.douyin.com/L4FJNR3/
```

-   Douyin normal URL (web version copy)

```text
https://www.douyin.com/video/6914948781100338440
```

-   Douyin discovery page URL (APP copy)

```text
https://www.douyin.com/discover?modal_id=7069543727328398622
```

-   TikTok Short URL (In-App Copy)

```text
https://www.tiktok.com/t/ZTR9nDNWq/
```

-   TikTok normal URL (copy from web version)

```text
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
```

-   Douyin/TikTok bulk URLs (no need to separate them)

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

**_API-V1 Documentation:_**local:<http://localhost:8000/docs>Online:<https://api.douyin.wtf/docs>

**_API-V2 Documentation:_**Online:<https://api-v2.douyin.wtf/docs>

**_API demo:_**

-   Crawl video data (TikTok or Douyin mixed analysis)`https://api.douyin.wtf/api?url=[视频链接/Video URL]&minimal=false`
-   Download video/photo gallery (TikTok or Douyin mixed analysis)`https://api.douyin.wtf/download?url=[视频链接/Video URL]&prefix=true&watermark=false`
-   Replace the domain name to download videos/atlas


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

**_Check out the documentation for more demos..._**

## 💻Deployment (Method 1 Linux)

> 💡Tip: It is best to deploy this project to a server in the United States, otherwise strange bugs may occur.

-   First go to the security group to open ports 8080 (Web) and 8000 (API).
-   Search in the Pagoda Panel App Store`进程守护`or install manually`supervisord`：


    [宝塔面板]
    https://www.bt.cn/new/download.html
    [aapanel]
    https://www.aapanel.com/new/download.html
    [Supervisor]
    http://supervisord.org/installing.html

-   configuration item[config.ini](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/config.ini)document
-   Install dependencies`pip install -r requirements.txt`
-   set up`supervisord`daemon
-   Start command:

```console
[Web]
python3 web_app.py
[API]
python3 web_api.py
```

-   Program entry:

```text
[Web]
http://localhost:8080
[API]
http://localhost:8000
```

## 💽Deployment (Method 2 Docker)

> 💡Docker Image repo:[Docker Hub](https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api)

-   install docker

```yaml
curl -fsSL get.docker.com -o get-docker.sh&&sh get-docker.sh &&systemctl enable docker&&systemctl start docker
```

-   Just leave the config.int and docker-compose.yml files
-   Run the command to let the container run in the background

```yaml
docker compose up -d
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
docker compose pull && docker compose down && docker compose up -d
```

## ❤️ Contributors

[![](https://github.com/Evil0ctal.png?size=50)](https://github.com/Evil0ctal)[![](https://github.com/jw-star.png?size=50)](https://github.com/jw-star)[![](https://github.com/Jeffrey-deng.png?size=50)](https://github.com/Jeffrey-deng)[![](https://github.com/chris-ss.png?size=50)](https://github.com/chris-ss)[![](https://github.com/weixuan00.png?size=50)](https://github.com/weixuan00)[![](https://github.com/Tairraos.png?size=50)](https://github.com/Tairraos)

## 📸 Screenshot

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
Web主界面:
![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/v3_screenshots/Home.png?raw=true)

Web main interface:![](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/Screenshots/v3_screenshots/Home_en.png?raw=true)

</details>
<hr>

## 📜 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Evil0ctal/Douyin_TikTok_Download_API&type=Timeline)](https://star-history.com/#Evil0ctal/Douyin_TikTok_Download_API&Timeline)

[MY License](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/LICENSE)

> Start: 2021/11/06
> GitHub:[@Evil0ctal](https://github.com/Evil0ctal)Contact:[Evil0ctal1985@gmail.com](mailto:Evil0ctal1985@gmail.com)
