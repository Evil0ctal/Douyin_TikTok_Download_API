# welcome`TikTokDownloader_PyWebIO`(Douyin online analysis)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

content:[API](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8)[screenshot](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E6%88%AA%E5%9B%BE)[deploy](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E9%83%A8%E7%BD%B2)

Language:  \[[English](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.en.md)]  \[[Simplified Chinese](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.md)]

> Note: Words "TikTok" in this readme file mentioned stands for  the Chinese version of TikTok.
> AKA \[[douin](https://www.douyin.com/)] or \[[Tik Tok](https://www.douyin.com/)] , The US TikTok is now supported! (no gallery analysis function)

## 👻Introduction

🚀Demo address:<https://douyin.wtf/>

🛰API demo:<https://douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/>

💾iOS Shortcuts:[Click to get instructions](https://www.icloud.com/shortcuts/e8243369340548efa0d4c1888dd3c170)Updated on 2022/02/06

This project uses[PyWebIO](https://github.com/pywebio/PyWebIO)、[Requests](https://github.com/psf/requests)、[Flask](https://github.com/pallets/flask), using Python to implement online batch parsing of Douyin's watermark-free video/atlas.

It can be used to download videos that the author prohibits to download, and can be used with[iOS Shortcuts APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with this project API to realize in-app download.

The shortcut command needs to be in the Douyin or TikTok app, select the video you want to save, click the share button, and then find the option "Douyin TikTok without watermark download", if you encounter a notification asking whether to allow the shortcut command to access xxxx (domain name) or server), you need to click Allow to use it normally.

## 💯 Supported features:

-   Support Douyin video/atlas parsing
-   Support overseas TikTok video analysis (no atlas analysis)
-   Support batch parsing (support Douyin/TikTok hybrid parsing)
-   Support API calls
-   support[iOS Shortcuts](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Realize in-app download of video/atlas without watermark

* * *

## 🤦‍♂️Follow-up features:

-   [ ] Support input (Tik Tok/TikTok) author homepage link to achieve batch parsing

* * *

## 🧭How to use:

-   Install dependent libraries:

```text
pip install -r requirements.txt
```

-   Run TikTok_ZH.py (Python version 3.9 or above)

```text
python3 TikTok_ZH.py
# python3 TikTok_EN.py - English interface
```

-   go to homepage

```text
http://localhost(服务器IP):80/
```

## 🗺️ Supported submission formats:

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

## 🛰️API usage

The API can convert the request parameters into a watermark-free video/picture straight link that needs to be extracted, and can be downloaded in-app with the IOS shortcut.

-   Parse request parameters

```text
http://localhost(服务器IP):80/api?url="复制的(抖音/TikTok)口令/链接"
```

-   return parameter

> Douyin video

```json
{
"Status": "Success",
"Type": "Video",
"video_author": "花花花菜",
"video_author_id": "Wobukunxixi",
"video_music": "https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/6906830659719383822.mp3",
"video_title": "~猫跟你都想了解",
"video_url": "https://v3-dy-o.zjcdn.com/93e3a68e365ae83f4ce2b2bb9c253489/6191c9c3/video/tos/cn/tos-cn-ve-15/083012c589c842e69f5267803eb8e3a5/?a=1128&br=2262&bt=2262&cd=0%7C0%7C0&ch=96&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=StecAhgM6BMM8b8NDtPDWodpeaQ&l=202111150945070102121380392D1AC2F5&lr=all&mime_type=video_mp4&net=0&pl=0&qs=0&rc=ajh5aTRseW95eTMzNGkzM0ApNjk1OTU6OWVlN2Q7ODo0N2cpaHV2fWVuZDFwekBvbTJjMDVrbmBfLS1eLS9zczRhXi9iLmFgYGBfLy1iLi46Y29zYlxmK2BtYmJeYA%3D%3D&vl=&vr="
}
```

> Douyin Atlas

```json
{
"Status": "Success",
"Type": "Image",
"image_author": "三石壁纸(收徒)",
"image_author_id": "782972562",
"image_music": "https://sf6-cdn-tos.douyinstatic.com/obj/tos-cn-ve-2774/635efafc32694ffbb73fbe60eca4a99d",
"image_title": "#壁纸 #炫酷壁纸 #图集 每一张都是精选",
"image_url": [
"https://p3-sign.douyinpic.com/tos-cn-i-0813/4af91199ca154074a8a5a63c3c749c6f~noop.webp?x-expires=1639530000&x-signature=P446eJEt2yuyhf2yb58Be29UpBA%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&sh=&sc=&l=202111150954330102120702320620C75E&biz_tag=aweme_images"
]
}
```

> TikTok videos

```JSON
{
   "Status":"Success",
   "Type":"Video",
   "followerCount":18,
   "followingCount":18,
   "likes_recived":3000000,
   "music_author":"❁ちゅらる❁",
   "music_title":"オリジナル楽曲 - ♛",
   "original_url":"https://vm.tiktok.com/TTPdkQvKjP/",
   "video_author":"nemi__goro",
   "video_author_id":"78903680178",
   "video_count":203,
   "video_music":"https://sf16-ies-music-sg.tiktokcdn.com/obj/tiktok-obj/6967616110887701250.mp3",
   "video_title":"#ベルメイク",
   "video_url":"https://v16m.tiktokcdn.com/65824a4bba45fbf4691d1ea2d040d2cc/6200e22c/video/tos/alisg/tos-alisg-pve-0037/6799cebe4a2248b98828788c94964a57/?a=1233&br=4118&bt=2059&cd=0%7C0%7C0%7C3&ch=0&cr=3&cs=0&cv=1&dr=0&ds=3&er=&ft=CvjiQnB4TJBS6BMyjOYNVKP&l=20220207031102010223065036144769B6&lr=all&mime_type=video_mp4&net=0&pl=0&qs=0&rc=M3NtaTo6Zjc5OTMzODgzNEApOWVmaTtlZDs7N2VlNjc8N2dzMjAzcjRfXzZgLS1kLy1zcy8wMS0uXi8uLjY2YGFjYDE6Yw%3D%3D&vl=&vr=",
   "water_mark_url":"https://v16-webapp.tiktok.com/233cec8c26b1a7d46fb6caaf5b354621/6200efc0/video/tos/alisg/tos-alisg-pve-0037/a00cfbcc79f54b66824aac6a871777c8/?a=1988&br=3506&bt=1753&cd=0%7C0%7C1%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=XOQ9-3E7nz7ThxPVoDXq&l=202202070408580102231230340B4C6876&lr=tiktok&mime_type=video_mp4&net=0&pl=0&qs=0&rc=M3NtaTo6Zjc5OTMzODgzNEApO2k8aTw0M2Q0N2VoZ2VoOWdzMjAzcjRfXzZgLS1kLy1zc19eYWJgY2E0MmFjMjY2MWE6Yw%3D%3D&vl=&vr="
}
```

-   Download video request parameters

```text
http://localhost(服务器IP):80/video?url="复制的(抖音/TikTok)口令/链接"
#返回mp4文件
```

-   Download audio request parameters

```text
http://localhost(服务器IP):80/bgm?url="复制的(抖音/TikTok)口令/链接"
#返回mp3文件
```

* * *

## 💾Deploy

> It is best to deploy this project to an overseas server, otherwise strange problems may occur

For example: the project is deployed on a domestic server, and the person is in the United States, click the link of the result page and report an error 403, which is visually related to Douyin CDN.

> Deploy using the Pagoda Linux panel

-   First go to the security group to open port 80 (default 80, which can be modified at the bottom of the file.)
-   Search for python in the pagoda app store and install the project manager

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_1.png)

* * *

-   Create a project with an arbitrary name
-   Path select the path where you upload the file
-   The Python version needs to be at least 3.9 or above (install it by yourself in the version management on the left)
-   The frame is modified to`Flask`
-   The startup method is changed to`python`
-   Startup file selection`TikTok_ZH.py`
-   Check install module dependencies
-   Start at will
-   If the pagoda is installed`Nginx`wait for the application to stop it or`TikTok_ZH.py`Modify the port at the bottom (the default port is 80)

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)

* * *

## 🎉 Screenshot

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
