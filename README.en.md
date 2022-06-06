# welcome`Douyin_TikTok_Download_API`(Douyin/TikTok no watermark parsing API)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

Language:  \[[English](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.en.md)]  \[[Simplified Chinese](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md)]  \[[traditional Chinese](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.zh-TW.md)]

> Note: This API is applicable to Douyin and TikTok. Douyin is TikTok in China. You can distribute or modify the code at will, but please mark the original author.

> Note: This API is suitable for Douyin and TikTok. Douyin is TikTok in China. You can distribute or modify the code at will, but please mark the original author.

## 👻Introduction

> 因为演示站的机子配置很拉胯，动不动就CPU吃满然后进程结束(其实主要是我代码写的很拉胯)，出于稳定性的考虑，暂时关闭演示站的/video和/music这两个API，同时结果页面的批量下载功能也暂时不可用，如有需求请自行部署或本地运行，其他功能在演示站上仍正常使用，在我没想到更好的解决方法之前请右键手动保存视频文件，在跑演示站的两台的小鸡都只有0.5G内存和一个CPU核心，顶不住了╥﹏╥...

🚀Demo address:<https://douyin.wtf/>

🛰API demo:<https://api.douyin.wtf/>

💾iOS Shortcuts:[点击获取指令](https://www.icloud.com/shortcuts/126820d2783748d1bdec95a223a02639)(Updated on 2022/04/16, the shortcut command can automatically check for updates, just install it once.)

This project uses[PyWebIO](https://github.com/pywebio/PyWebIO)、[Flask](https://github.com/pallets/flask), using Python to implement online batch parsing of Douyin's watermark-free video/atlas.

It can be used to download videos that the author prohibits to download, or to perform data crawling, etc., and can be matched with[Shortcut APP that comes with iOS](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with this project API to realize in-app download.

The shortcut command needs to be in the Douyin or TikTok app, select the video you want to save, click the share button, and then find the option "Douyin TikTok without watermark download", if you encounter a notification asking whether to allow the shortcut command to access xxxx (domain name) or server), you need to click Allow to use it normally, and the successfully downloaded video or gallery will be saved in a special album for easy browsing.

## 💡Project file structure

    # 请根据需要自行修改config.ini中的内容
    .
    └── Douyin_TikTok_Download_API/
        ├── /static(静态前端资源)
        ├── web_zh.py(网页入口)
        ├── web_api.py(API)
        ├── scraper.py(解析库)
        ├── config.ini(网页入口和API的配置文件，请自行修改该文件。)
        ├── logs.txt(错误日志，自动生成。)
        └── API_logs.txt(API调用日志，自动生成。)

## 💯 Supported features:

-   Support Douyin video/atlas parsing
-   Support overseas TikTok video analysis
-   Support batch parsing (support Douyin/TikTok hybrid parsing)
-   Parse the result page to download watermark-free videos in batches
-   Support API calls
-   support[iOS Shortcuts](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Realize in-app download of video/atlas without watermark

* * *

## 🤦‍♂️Follow-up features:

-   [ ] Support input (Tik Tok/TikTok) author homepage link to achieve batch parsing

* * *

## 🧭 Running instructions (tested Python version is 3.8):

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
# 运行web_zh.py
python3 web_zh.py
```

-   API

```console
# 运行web_api.py
python3 web_api.py
```

-   call parsing library

```python
# 将scraper.py拷贝至你的项目目录
# 在该项目中导入scraper.py 
from scraper import Scraper
api = Scraper()
# 解析Douyin视频/图集
douyin_data = api.douyin(input('在此输入抖音分享口令/链接：' ))
# 返回字典
print(douyin_data)
# 解析TikTok视频/图集
tiktok_data = api.tiktok(input('在此输入TikTok分享口令/链接：' ))
# 返回字典
print(tiktok_data)
```

-   Entrance

```text
网页入口:
http://localhost(服务器IP):5000/
API入口:
http://localhost(服务器IP):2333/
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

-   TikTok Short URL (In-App Copy)

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

## 🛰️API usage

The API can convert the request parameters into a watermark-free video/picture straight link that needs to be extracted, and can be downloaded in-app with the IOS shortcut.

-   Parse request parameters

```text
http://localhost(服务器IP):2333/api?url="复制的(抖音/TikTok)口令/链接"
```

-   return parameter

> Douyin video

```json
{
   "analyze_time":"1.9043s",
   "api_url":"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=6918273131559881997",
   "nwm_video_url":"http://v3-dy-o.zjcdn.com/23f0dec312ede563bef881af9a88bdc7/624dd965/video/tos/cn/tos-cn-ve-15/eccedcf4386948f5b5a1f0bcfb3dcde9/?a=1128&br=2537&bt=2537&cd=0%7C0%7C0%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=sYGC~3E7nz7Th1PZSDXq&l=202204070118030102080650132A21E31F&lr=&mime_type=video_mp4&net=0&pl=0&qs=0&rc=M3hleDRsODlkMzMzaGkzM0ApODpmNWc4ODs5N2lmNzg5aWcpaGRqbGRoaGRmLi4ybnBrbjYuYC0tYy0wc3MtYmJjNTM2NjAtNDFjMzJgOmNwb2wrbStqdDo%3D&vl=&vr=",
   "original_url":"https://v.douyin.com/L4FJNR3/",
   "platform":"douyin",
   "status":"success",
   "url_type":"video",
   "video_author":"Real机智张",
   "video_author_id":"Rea1yaoyue",
   "video_author_signature":"",
   "video_author_uid":"59840491348",
   "video_aweme_id":"6918273131559881997",
   "video_comment_count":"89145",
   "video_create_time":"1610786002",
   "video_digg_count":"2968195",
   "video_hashtags":[
      "百万转场变身"
   ],
   "video_music":"https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/6910889805266504461.mp3",
   "video_music_author":"梅尼耶",
   "video_music_id":"6910889820861451000",
   "video_music_mid":"6910889820861451021",
   "video_music_title":"@梅尼耶创作的原声",
   "video_play_count":"0",
   "video_share_count":"74857",
   "video_title":"骑白马的也可以是公主#百万转场变身",
   "wm_video_url":"https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0300ffe0000c01a96q5nis1qu5b1u10&ratio=720p&line=0"
}
```

> Douyin Atlas

```json
{
   "album_author":"治愈图集",
   "album_author_id":"ZYTJ2002",
   "album_author_signature":"取无水印图",
   "album_author_uid":"449018054867063",
   "album_aweme_id":"7015137063141920030",
   "album_comment_count":"5436",
   "album_create_time":"1633338878",
   "album_digg_count":"193734",
   "album_hashtags":[
      "晚霞",
      "治愈系",
      "落日余晖",
      "日落🌄"
   ],
   "album_list":[
      "https://p26-sign.douyinpic.com/tos-cn-i-0813/5223757a7bef4f8480cd25d0fa2d2d94~noop.webp?x-expires=1651856400&x-signature=K1VjJdWTHCAaYSz14y6NumjjtfI%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47",
      "https://p26-sign.douyinpic.com/tos-cn-i-0813/d99467672da840908acccf2d2b4b7ef7~noop.webp?x-expires=1651856400&x-signature=ncBb8Tt7z4PmpUyiCNr%2FJYnwRSA%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47",
      "https://p26-sign.douyinpic.com/tos-cn-i-0813/5c2562210b1a4d4c99d6d4dbd2f23f2b~noop.webp?x-expires=1651856400&x-signature=Rsmplb53IKfvKd3mmIb4iQNhlIE%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47",
      "https://p26-sign.douyinpic.com/tos-cn-i-0813/9bb74c0c6aff4217bd1491a077b2c817~noop.webp?x-expires=1651856400&x-signature=BLRyHoKP0ybIci57yneOca62dxI%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47"
   ],
   "album_music":"https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/6978805801733442341.mp3",
   "album_music_author":"魏同学",
   "album_music_id":"6978805810365271000",
   "album_music_mid":"6978805810365270791",
   "album_music_title":"@魏同学创作的原声",
   "album_play_count":"0",
   "album_share_count":"30717",
   "album_title":"“山海自有归期 风雨自有相逢 意难平终将和解 万事终将如意”#晚霞 #治愈系 #落日余晖 #日落🌄",
   "analyze_time":"1.0726s",
   "api_url":"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=7015137063141920030",
   "original_url":"https://v.douyin.com/Nb8jysN/",
   "platform":"douyin",
   "status":"success",
   "url_type":"album"
}
```

> TikTok videos

```JSON
{
   "analyze_time":"5.0863s",
   "nwm_video_url":"https://v19.tiktokcdn-us.com/cfa357dadd8f913f013a6d0b0dca293f/624e20fa/video/tos/useast5/tos-useast5-ve-0068c003-tx/3296231486014755a1b81aa70c349a53/?a=1233&br=6498&bt=3249&cd=0%7C0%7C0%7C3&ch=0&cr=3&cs=0&cv=1&dr=0&ds=6&er=&ft=bY1KJnB4TJBS6BMy-L1iVKP&l=20220406172333010113135214232FAB56&lr=all&mime_type=video_mp4&net=0&pl=0&qs=0&rc=MzpsaGY6Zjo7PDMzZzczNEApNjY6ZTtkOzxpN2Q3PDo5OmdgZ2BtcjQwai9gLS1kMS9zczJhLTEzYjEuMTJeXzQyLmM6Yw%3D%3D&vl=&vr=",
   "original_url":"https://www.tiktok.com/@oregonzoo/video/7080938094823738666",
   "platform":"tiktok",
   "status":"success",
   "url_type":"video",
   "video_author":"oregonzoo",
   "video_author_SecId":"MS4wLjABAAAArWNQ8-AZN6CxWOkqdeWsMBUuLDmJt8TWUAk0S4aWDW5V5EoqRbuczhaLnxJHCGob",
   "video_author_diggCount":94,
   "video_author_followerCount":1800000,
   "video_author_followingCount":39,
   "video_author_heartCount":29700000,
   "video_author_id":"6699816060206171141",
   "video_author_nickname":"Oregon Zoo",
   "video_author_videoCount":264,
   "video_aweme_id":"7080938094823738666",
   "video_comment_count":61,
   "video_create_time":"1648659375",
   "video_digg_count":11800,
   "video_hashtags":[
      "redpanda",
      "boop",
      "sunshine"
   ],
   "video_music":"https://sf16.tiktokcdn-us.com/obj/ies-music-tx/7075363935741856558.mp3",
   "video_music_author":"Gilderoy Dauterive",
   "video_music_id":"7075363884613356330",
   "video_music_title":"Be the Sunshine",
   "video_music_url":"https://sf16.tiktokcdn-us.com/obj/ies-music-tx/7075363935741856558.mp3",
   "video_play_count":60100,
   "video_ratio":"720p",
   "video_share_count":298,
   "video_title":"Moshu ✨ #redpanda #boop #sunshine",
   "wm_video_url":"https://v16m-webapp.tiktokcdn-us.com/0394b9183a5852d4392a7e804bf78c55/624e20f6/video/tos/useast5/tos-useast5-ve-0068c001-tx/fc63ae232e70466398b55ccf97eb3c67/?a=1988&br=6468&bt=3234&cd=0%7C0%7C1%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=XY53A3E7nz7Th-pZSDXq&l=202204061723290101131351171341B9BB&lr=tiktok_m&mime_type=video_mp4&net=0&pl=0&qs=0&rc=MzpsaGY6Zjo7PDMzZzczNEApOjo4aDMzZmRlN2loOWk6ZWdgZ2BtcjQwai9gLS1kMS9zczBhNGA0LTIwNjNiYDQ2YmE6Yw%3D%3D&vl=&vr="
}
```

-   Download video request parameters

```text
http://localhost(服务器IP):2333/video?url="复制的(抖音/TikTok)口令/链接"
# 返回无水印mp4文件
```

-   Download audio request parameters

```text
http://localhost(服务器IP):2333/music?url="复制的(抖音/TikTok)口令/链接"
# 返回mp3文件
```

* * *

## 💾Deploy

> Note:
> The screenshots may not match the text due to update problems, please refer to the text description first.

> It is best to deploy this project to an overseas server (preferably a server in the United States), otherwise strange problems may occur.

example:
The project is deployed on a domestic server, and the person is in the United States. Clicking the link on the result page reports an error 403, which is visually related to the Douyin CDN.
The project is deployed on a South Korean server, parsing TikTok errors, and visually TikTok restricts certain regions or IPs.

> Use the pagoda Linux panel for deployment (the Chinese pagoda must be bound to the mobile phone number, which is very rogue and cannot be bypassed. It is recommended to use the international version of the pagoda, Google search keyword aapanel to install it yourself, and the deployment steps are similar.)

-   First go to the security group to open ports 5000 and 2333 (default 5000 for Web, 2333 for API default, which can be modified in the file config.ini.)
-   Search for python in the Pagoda app store and install the project manager (version 1.9 is recommended)

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_1.png)

* * *

-   Create a project with an arbitrary name
-   Path select the path where you upload the file
-   Python version needs to be at least 3 or more (install it yourself in the version management on the left)
-   The frame is modified to`Flask`
-   The startup method is changed to`python`
-   Web startup file selection`web_zh.py`
-   API startup file selection`web_api.py`
-   Check install module dependencies
-   Start at will
-   If the pagoda runs`Nginx`When waiting for other services, please judge by yourself whether the port is occupied. The running port can be modified in the file config.ini.

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)

* * *

## 🎉 Screenshot

> Note:
> The screenshots may not match the text due to update problems, please refer to the text description first.

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
