# welcome`TikTokDownloader_PyWebIO`(Douyin online analysis)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

content:[API](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8)[screenshot](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E6%88%AA%E5%9B%BE)[deploy](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E9%83%A8%E7%BD%B2)

Language:  \[[English](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.en.md)]  \[[Simplified Chinese](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.md)]

> Note: Words "TikTok" in this readme file mentioned stands for  the Chinese version of TikTok.
> AKA \[[douin](https://www.douyin.com/)] or \[[Tik Tok](https://www.douyin.com/)] , The US TikTok is now supported! (no gallery analysis function)

## 👻Introduction

🚀Demo address:<https://douyin.wtf/>

🛰API demo:<https://douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/>

This project uses[PyWebIO](https://github.com/pywebio/PyWebIO)、[Requests](https://github.com/psf/requests)、[Flask](https://github.com/pallets/flask), using Python to implement online batch parsing of Douyin's watermark-free video/atlas.

It can be used to download videos that the author prohibits to download, and can be used with[iOS Shortcuts APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Cooperate with this project API to realize in-app download.

## 💯 Supported features:

-   Support Douyin video/atlas parsing

-   Support overseas TikTok video analysis (no atlas analysis)

-   Support batch parsing (support Douyin/TikTok hybrid parsing)

-   Support API calls

* * *

## 🤦‍♂️Follow-up features:

-   [ ] match[iOS Shortcuts APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)Realize in-app downloads (lazy, come and write for help)

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

-   share password

```text
例子：7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

-   Short URL

```text
例子：https://v.douyin.com/RHnWEng/
```

-   normal url

```text
例子：
https://www.douyin.com/video/6914948781100338440
```

-   TikTok URL

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
   "author":"tvamii",
   "authorId":"7019018029121455109",
   "authorSecId":"MS4wLjABAAAAAdUMc4sCkhslVsCSHQfem65nh_Zz12rv15qGYzoCQ1n9UjndmhwvRS0kiZ17d8Ae",
   "authorStats":{
      "diggCount":243,
      "followerCount":4959,
      "followingCount":40,
      "heart":116400,
      "heartCount":116400,
      "videoCount":20
   },
   "avatarThumb":"https://p16-sign.tiktokcdn-us.com/tos-useast5-avt-0068-tx/b6fbb55d497f2958ee428da9b0ebfe0f~c5_100x100.jpeg?x-expires=1644134400&x-signature=%2BSJvWXv1fqhO6X30Duiod9SZggc%3D",
   "challenges":[
      {
         "coverLarger":"",
         "coverMedium":"",
         "coverThumb":"",
         "desc":"Mu\u00e9stranos tus mejores jugadas",
         "id":"337014",
         "isCommerce":false,
         "profileLarger":"",
         "profileMedium":"",
         "profileThumb":"",
         "title":"warzone"
      },
      {
         "coverLarger":"",
         "coverMedium":"",
         "coverThumb":"",
         "desc":"",
         "id":"42578",
         "isCommerce":false,
         "profileLarger":"",
         "profileMedium":"",
         "profileThumb":"",
         "title":"callofduty"
      },
      {
         "coverLarger":"",
         "coverMedium":"",
         "coverThumb":"",
         "desc":"",
         "id":"229207",
         "isCommerce":false,
         "profileLarger":"",
         "profileMedium":"",
         "profileThumb":"",
         "title":"fyp"
      },
      {
         "coverLarger":"",
         "coverMedium":"",
         "coverThumb":"",
         "desc":"",
         "id":"1677192325085189",
         "isCommerce":false,
         "profileLarger":"",
         "profileMedium":"",
         "profileThumb":"",
         "title":"rebirthisland"
      }
   ],
   "comments":[
      
   ],
   "createTime":"1640417086",
   "desc":"Nothing better than rebirth island \ud83c\udfdd #warzone #callofduty #fyp #rebirthisland",
   "digged":false,
   "diversificationLabels":[
      "Video Games",
      "Games",
      "Entertainment"
   ],
   "duetDisplay":0,
   "duetEnabled":true,
   "duetInfo":{
      "duetFromId":"0"
   },
   "effectStickers":[
      
   ],
   "forFriend":false,
   "id":"7045537727743380782",
   "indexEnabled":true,
   "isActivityItem":false,
   "isAd":false,
   "itemCommentStatus":0,
   "itemMute":false,
   "music":{
      "album":"",
      "authorName":"MAKAN",
      "coverLarge":"https://p16-sign-va.tiktokcdn.com/tos-maliva-avt-0068/2b77a1347866635e004f9a8671d2f8df~c5_1080x1080.jpeg?x-expires=1644134400&x-signature=65YBpjA0yGaQHEncDGu%2Bb6P9ixA%3D",
      "coverMedium":"https://p16-sign-va.tiktokcdn.com/tos-maliva-avt-0068/2b77a1347866635e004f9a8671d2f8df~c5_720x720.jpeg?x-expires=1644134400&x-signature=7%2B5y9PghRZMJOm3raGlCmzPXzH0%3D",
      "coverThumb":"https://p16-sign-va.tiktokcdn.com/tos-maliva-avt-0068/2b77a1347866635e004f9a8671d2f8df~c5_100x100.jpeg?x-expires=1644134400&x-signature=xw9yp1XUNNKNxJq%2BA7KG4FYI9Ew%3D",
      "duration":36,
      "id":"6995388223497259781",
      "original":true,
      "playUrl":"https://sf16-ies-music-va.tiktokcdn.com/obj/musically-maliva-obj/6995388366275611397.mp3",
      "scheduleSearchTime":0,
      "title":"son original"
   },
   "nickname":"Ami",
   "officalItem":false,
   "originalItem":false,
   "privateItem":false,
   "scheduleTime":0,
   "secret":false,
   "shareEnabled":true,
   "showNotPass":false,
   "stats":{
      "commentCount":69,
      "diggCount":56900,
      "playCount":133000,
      "shareCount":22
   },
   "stickersOnItem":[
      
   ],
   "stitchDisplay":0,
   "stitchEnabled":true,
   "takeDown":0,
   "textExtra":[
      {
         "awemeId":"",
         "end":46,
         "hashtagId":"337014",
         "hashtagName":"warzone",
         "isCommerce":false,
         "secUid":"",
         "start":38,
         "subType":0,
         "type":1,
         "userId":"",
         "userUniqueId":""
      },
      {
         "awemeId":"",
         "end":58,
         "hashtagId":"42578",
         "hashtagName":"callofduty",
         "isCommerce":false,
         "secUid":"",
         "start":47,
         "subType":0,
         "type":1,
         "userId":"",
         "userUniqueId":""
      },
      {
         "awemeId":"",
         "end":63,
         "hashtagId":"229207",
         "hashtagName":"fyp",
         "isCommerce":false,
         "secUid":"",
         "start":59,
         "subType":0,
         "type":1,
         "userId":"",
         "userUniqueId":""
      },
      {
         "awemeId":"",
         "end":78,
         "hashtagId":"1677192325085189",
         "hashtagName":"rebirthisland",
         "isCommerce":false,
         "secUid":"",
         "start":64,
         "subType":0,
         "type":1,
         "userId":"",
         "userUniqueId":""
      }
   ],
   "video":{
      "bitrate":2530304,
      "codecType":"h264",
      "cover":"https://p16-sign.tiktokcdn-us.com/obj/tos-useast5-p-0068-tx/bf17541f99cd47489050f740f2680e4e?x-expires=1644069600&x-signature=dZPl5WGnF8lwTgOoh0%2FyPp5RC7k%3D",
      "definition":"720p",
      "downloadAddr":"https://v16-webapp.tiktok.com/214ac5ed6ee8b0351c5487d6d45c0380/61fe8ece/video/tos/useast5/tos-useast5-ve-0068c003-tx/450dd55906664f5eb194d5b6212e6070/?a=1988&br=4942&bt=2471&cd=0%7C0%7C1%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=XOQ9-3E7nz7ThSm4xlXq&l=20220205085015010191055029051527A2&lr=tiktok_m&mime_type=video_mp4&net=0&pl=0&qs=0&rc=ajNnN2c6Zm5mOjMzZzczNEApZTtpNzo8OGU0NzplODM4aWcyL2lucjRfXzBgLS1kMS9zczUwYWE0NTA0XjIvXmM2NDY6Yw%3D%3D&vl=&vr=",
      "duration":39,
      "dynamicCover":"https://p16-sign.tiktokcdn-us.com/obj/tos-useast5-p-0068-tx/db58dfc00c5b43898c2bbaedfbe9f079_1640417087?x-expires=1644069600&x-signature=HkCCUrMMsd9pZExtuywu6cNVVOw%3D",
      "encodeUserTag":"",
      "encodedType":"normal",
      "format":"mp4",
      "height":576,
      "id":"7045537727743380782",
      "originCover":"https://p16-sign.tiktokcdn-us.com/obj/tos-useast5-p-0068-tx/035eb2303c2f4aa698a6cf2c200312d3_1640417087?x-expires=1644069600&x-signature=8CtiX%2F1iSTD07GOVFa32QpcAV44%3D",
      "playAddr":"https://v16-webapp.tiktok.com/214ac5ed6ee8b0351c5487d6d45c0380/61fe8ece/video/tos/useast5/tos-useast5-ve-0068c003-tx/450dd55906664f5eb194d5b6212e6070/?a=1988&br=4942&bt=2471&cd=0%7C0%7C1%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=XOQ9-3E7nz7ThSm4xlXq&l=20220205085015010191055029051527A2&lr=tiktok_m&mime_type=video_mp4&net=0&pl=0&qs=0&rc=ajNnN2c6Zm5mOjMzZzczNEApZTtpNzo8OGU0NzplODM4aWcyL2lucjRfXzBgLS1kMS9zczUwYWE0NTA0XjIvXmM2NDY6Yw%3D%3D&vl=&vr=",
      "ratio":"720p",
      "reflowCover":"https://p16-sign.tiktokcdn-us.com/obj/tos-useast5-p-0068-tx/bf17541f99cd47489050f740f2680e4e?x-expires=1644069600&x-signature=dZPl5WGnF8lwTgOoh0%2FyPp5RC7k%3D",
      "shareCover":[
         "",
         "https://p19-sign.tiktokcdn-us.com/tos-useast5-p-0068-tx/035eb2303c2f4aa698a6cf2c200312d3_1640417087~tplv-tiktok-play.jpeg?x-expires=1644652800&x-signature=ANH1qJpgs291tRRn0HCRHsxIUfo%3D",
         "https://p19-sign.tiktokcdn-us.com/tos-useast5-p-0068-tx/035eb2303c2f4aa698a6cf2c200312d3_1640417087~tplv-tiktokx-share-play.jpeg?x-expires=1644652800&x-signature=pMxB2XKFsgWE2yEdBEoEUYdExL4%3D"
      ],
      "videoQuality":"normal",
      "width":1024
   },
   "vl1":false,
   "warnInfo":[
      
   ]
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
