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

This project is based on[PyWebIO](https://github.com/pywebio/PyWebIO)，[FastAPI](https://fastapi.tiangolo.com/)，[HTTPX](https://www.python-httpx.org/), fast asynchronous[Tik Tok](https://www.douyin.com/)/[TikTok](https://www.tiktok.com/)Data crawling tool, and realize online batch parsing and download of video or atlas without watermark, data crawling API, iOS shortcut command without watermark download and other functions through the Web terminal. You can deploy or transform this project yourself to achieve more functions, or you can call it directly in your project[scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)or install an existing[pip package](https://pypi.org/project/DT-Scraper/)As a parsing library to easily crawl data, etc...

_Some simple application scenarios:_

_Download prohibited videos, perform data analysis, and download without watermark on iOS (with[Shortcut APP that comes with iOS](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with the API of this project to realize in-app download or read clipboard download), etc..._

## 🖥Public Site: I'm vulnerable...please don't hit me at will ‎(·•᷄ࡇ•᷅ ）

> **API-V2:**support input`Douyin|TikTok`The user's homepage crawls the author \[homepage video data (remove watermark link, liked video list (permission must be public), video comment data, background music video list data, etc...), please check the V2 document for details , the server response time may sometimes become longer, please`timeout`Set the value high.

🍔Web APP:<https://douyin.wtf/>

🍟API-V1:<https://api.douyin.wtf/docs>

🌭API-V2:<https://api-v2.douyin.wtf/docs>

💾iOS Shortcut:[Shortcut release](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/53)

📦️ Desktop downloader (recommended warehouse):

-   [Tairraos/TikToon](https://github.com/Tairraos/TikDown/)
-   [Johnserf-Seed/TikTokDownload](https://github.com/Johnserf-Seed/TikTokDownload)
-   [HFrost0/bilix](https://github.com/HFrost0/bilix)

## ⚗️Technology stack

-   [web_app.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/web_app.py)-[PyWebIO](https://www.pyweb.io/)
-   [web_api.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/web_api.py)-[FastAPI](https://fastapi.tiangolo.com/)
-   [scraper.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/scraper.py)-[HTTPX](https://www.python-httpx.org/)

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
-   Batch parsing on the web page (support Douyin/TikTok mixed submission)
-   Batch download of watermark-free videos on the parsing result page on the webpage (V3.0.0 temporarily removed)
-   API call to get link data
-   make[pip package](https://pypi.org/project/DT-Scraper/)Easily and quickly import your projects
-   [\[iOS shortcut command to quickly call API\]](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Realize in-app download of video/atlas without watermark
-   Parse all videos in the author's homepage ([API-V2](https://api-v2.douyin.wtf/docs)Support Douyin/TikTok)
-   Parse all comments in the video ([API-V2](https://api-v2.douyin.wtf/docs)Support Douyin/TikTok)

* * *

## 🤦‍Follow-up features:

-   [ ] Welcome to make new suggestions or share your ideas with me in the issue
-   [ ] Welcome to submit PR to[Development branch](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/Development)♪(･ω･)ﾉ)

* * *

## 🧭 Running Instructions (Python version > 3.0):

> 🚨If you want to deploy this project, please refer to the deployment method ([Docker deployment](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker "Docker部署"),[Manual deployment](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-%E6%89%8B%E5%8A%A8%E9%83%A8%E7%BD%B2 "手动部署"))

-   Clone this repository:

```console
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
```

-   Move to repository directory:

```console
cd Douyin_TikTok_Download_API
```

-   Install dependent libraries:

```console
pip install -r requirements.txt
```

-   Modify config.ini (optional):

```console
vim config.ini
```

-   Web page parsing

```console
# 运行web_app.py
python3 web_app.py
```

-   API

```console
# 运行web_api.py
python3 web_api.py
```

-   call parsing library

```python
# pip install DT-Scraper
from DT_scraper.scraper import Scraper
	
api = Scraper()
	
async def async_test(url: str):
	if 'douyin' in url:
		douyin_url = await api.convert_share_urls(url)
		# Get Douyin ID and video data
		douyin_id = await api.get_douyin_video_id(douyin_url)
		douyin_data = await api.get_douyin_video_data(douyin_id)
	elif 'tiktok' in url:
		tiktok_url = await api.convert_share_urls(url)
		# Get TikTok video data
		tiktok_id = await api.get_tiktok_video_id(tiktok_url)
		tiktok_data = await api.get_tiktok_video_data(tiktok_id)
		
	# Hybrid parsing(Any platform URL)
	hybrid_data = await api.hybrid_parsing(url)
	
asyncio.run(async_test(url=input("Paste Douyin/TikTok share URL here: "))
```

-   Entry (port can be modified in the config.ini file)

```text
Web入口:
http://localhost(服务器IP):8080/
API入口:
http://localhost(服务器IP):8000/
```

## 🗺️ Supported submission formats (including but not limited to the following examples):

-   Douyin share password (copy in APP)

```text
例子：7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

-   Douyin Short URL (Copy in APP)

```text
例子：https://v.douyin.com/L4FJNR3/
```

-   Douyin normal URL (web version copy)

```text
例子：
https://www.douyin.com/video/6914948781100338440
```

-   Douyin discovery page URL (APP copy)

```text
例子：
https://www.douyin.com/discover?modal_id=7069543727328398622
```

-   TikTok URL Shortening (In-App Copy)

```text
例子：
https://vm.tiktok.com/TTPdkQvKjP/
```

-   TikTok normal URL (copy from web version)

```text
例子：
https://www.tiktok.com/@tvamii/video/7045537727743380782
```

-   Douyin/TikTok bulk URLs (no need to separate them)

```text
例子：
2.84 nqe:/ 骑白马的也可以是公主%%百万转场变身  https://v.douyin.com/L4FJNR3/ 复制此链接，打开Dou音搜索，直接观看视频！
8.94 mDu:/ 让你在几秒钟之内记住我  https://v.douyin.com/L4NpDJ6/ 复制此链接，打开Dou音搜索，直接观看视频！
9.94 LWz:/ ok我坦白交代 %%knowknow  https://v.douyin.com/L4NEvNn/ 复制此链接，打开Dou音搜索，直接观看视频！
https://www.tiktok.com/@gamer/video/7054061777033628934
https://www.tiktok.com/@off.anime_rei/video/7059609659690339586
https://www.tiktok.com/@tvamii/video/7045537727743380782
```

## 🛰️API Documentation

> You can also see the interface documentation in the code of web_api.py

**_API-V1 Documentation:_**[http://localhost (server IP):8000/docs]("http://localhost:8000/docs")or[https://api.douyin.wtf/docs]("https://api.douyin.wtf/docs")

**_API-V2 Documentation:_**[https://api-v2.douyin.wtf/docs]("https://api-v2.douyin.wtf/docs")

* * *

## 💾Deployment (method 1 manual deployment)

> Note:
> The screenshots may not match the text due to update problems, please refer to the text description first.

> It is best to deploy this project to an overseas server (preferably a server in the United States), otherwise strange problems may occur.

example:
The project is deployed on a domestic server, and the person is in the United States. Clicking the link on the result page reports an error 403, which is visually related to the Douyin CDN.
The project is deployed on a South Korean server, parsing TikTok errors, and visually TikTok restricts certain regions or IPs.

> Deploy using the Pagoda Linux panel (
> The Chinese pagoda is going to be bound to a mobile phone number, which is very rogue and cannot be bypassed. It is recommended to use the international version of the pagoda. Google search for the keyword aapanel to install it yourself, and the deployment steps are similar. )

-   First go to the security group to open ports 8080 and 8000 (default 8080 for Web, default 8000 for API, which can be modified in the file config.ini.)
-   Search for python in the Pagoda app store and install the project manager (version 1.9 is recommended)

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_1.png)

* * *

-   Create a project with an arbitrary name
-   Path select the path where you upload the file
-   Python version needs to be at least 3 or more (install it yourself in the version management on the left)
-   The frame is modified to`Uvicorn`
-   The startup method is changed to`python`
-   Web startup file selection`web_app.py`
-   API startup file selection`web_api.py`
-   Check install module dependencies
-   Start at will
-   Please judge by yourself whether the port is occupied. The running port can be modified in the file config.ini.
-   If there are a lot of requests please use_process daemon_Start preventing process from closing

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)

* * *

## 💾Deployment (Method 2 Docker)

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

## 🎉 Screenshot

> Note:
> The screenshots may not match the text due to update problems, please refer to the text description first.

<details><summary>点击展开截图</summary>

<hr>

-   Main interface

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/home.png)

* * *

-   parsing complete

> single

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/single_result.png)

* * *

> batch

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/multi_results.png)

* * *

-   API submit/return

> Video return value

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_video_result.png)

> Atlas return value

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_image_result.png)

> TikTok return value

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/tiktok_API.png)

* * *

</details>

## 📜 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Evil0ctal/Douyin_TikTok_Download_API&type=Timeline)](https://star-history.com/#Evil0ctal/Douyin_TikTok_Download_API&Timeline)

[MY License](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/Stable/LICENSE)

> Start: 2021/11/06
> GitHub:[@Evil0ctal](https://github.com/Evil0ctal)Contact:[Evil0ctal1985@gmail.com](mailto:Evil0ctal1985@gmail.com)
