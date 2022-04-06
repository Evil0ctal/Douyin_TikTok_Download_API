# 欢迎使用 `Douyin_TikTok_Download_API` (抖音/TikTok无水印解析API)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)
[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)
[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)
[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

Language:  [[English](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.en.md)]  [[简体中文](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md)]

> Note: This API is applicable to Douyin and TikTok. Douyin is TikTok in China. The code of this repository cannot be used for any commercial purpose. You can distribute or modify the code at will, but please mark the original author.
> 注: 此API适用于Douyin和TikTok，Douyin为中国区域的TikTok，此仓库的代码不得用于任何商业目的，你可以随意分发或修改代码，但请标注原作者。

## 👻介绍

🚀演示地址：[https://douyin.wtf/](https://douyin.wtf/)

🛰API演示：[https://api.douyin.wtf/](https://douyin.wtf/)

💾iOS快捷指令: [点击获取指令](https://www.icloud.com/shortcuts/e8243369340548efa0d4c1888dd3c170) 更新于2022/02/06

本项目使用 [PyWebIO](https://github.com/pywebio/PyWebIO)、[Flask](https://github.com/pallets/flask)，利用Python实现在线批量解析抖音的无水印视频/图集。

可用于下载作者禁止下载的视频，同时可搭配[iOS快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)配合本项目API实现应用内下载。

快捷指令需要在抖音或TikTok的APP内，选择你想要保存的视频，点击分享按钮，然后找到 "抖音TikTok无水印下载" 这个选项，如遇到通知询问是否允许快捷指令访问xxxx (域名或服务器)，需要点击允许才可以正常使用。


## 💡项目文件结构

```
.
└── Douyin_TikTok_Download_API/
    ├── Web/
    │   ├── web_zh.py(网页入口)
    │   ├── scraper.py(解析库)
    │   └── logs.txt(错误日志)
    └── API/
        ├── web_api.py(API)
        ├── scraper.py(解析库)
        └── API_logs.txt(API调用日志)
```

## 💯已支持功能：

- 支持抖音视频/图集解析
- 支持海外TikTok视频解析(无图集解析)
- 支持批量解析(支持抖音/TikTok混合解析)
- 支持API调用
- 支持[iOS快捷指令](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)实现应用内下载无水印视频/图集

---

## 🤦‍♂️后续功能：

- [ ] 支持输入(抖音/TikTok)作者主页链接实现批量解析

---

## 🧭如何使用:

- 安装依赖库：

```text
pip install -r requirements.txt
```

- 网页解析

```text
# 运行web_zh.py (测试过的Python版本为3.8)
python3 web_zh.py
```

- API

```text
# 运行web_api.py (测试过的Python版本为3.8)
python3 web_api.py
```

- 调用解析库

```text
# 将scraper.py拷贝至你的项目目录(测试过的Python版本为3.8)
# 在该项目中导入scraper.py 
from scraper import Scraper
api = Scraper()
# 解析Douyin视频/图集(返回字典)
douyin_data = api.douyin('抖音分享口令/链接')
print(douyin_data )
# 解析TikTok视频/图集(返回字典)
tiktok_data = api.tiktok('TikTok分享口令/链接')
print(tiktok_data )
```

- 入口

```text
网页入口:
http://localhost(服务器IP):5000/
API入口:
http://localhost(服务器IP):2333/
```

## 🗺️支持的提交格式：

- 抖音分享口令  (APP内复制)

```text
例子：7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

- 抖音短网址 (APP内复制)

```text
例子：https://v.douyin.com/L4FJNR3/
```

- 抖音正常网址 (网页版复制)

```text
例子：
https://www.douyin.com/video/6914948781100338440
```

- TikTok短网址 (APP内复制)

```text
例子：
https://vm.tiktok.com/TTPdkQvKjP/
```

- TikTok正常网址 (网页版复制)

```text
例子：
https://www.tiktok.com/@tvamii/video/7045537727743380782
```

- 抖音/TikTok批量网址(无需使用符合隔开)

```text
例子：
2.84 nqe:/ 骑白马的也可以是公主%%百万转场变身  https://v.douyin.com/L4FJNR3/ 复制此链接，打开Dou音搜索，直接观看视频！
8.94 mDu:/ 让你在几秒钟之内记住我  https://v.douyin.com/L4NpDJ6/ 复制此链接，打开Dou音搜索，直接观看视频！
9.94 LWz:/ ok我坦白交代 %%knowknow  https://v.douyin.com/L4NEvNn/ 复制此链接，打开Dou音搜索，直接观看视频！
https://www.tiktok.com/@gamer/video/7054061777033628934
https://www.tiktok.com/@off.anime_rei/video/7059609659690339586
https://www.tiktok.com/@tvamii/video/7045537727743380782
```

## 🛰️API使用

API可将请求参数转换为需要提取的无水印视频/图片直链，配合IOS捷径可实现应用内下载。

- 解析请求参数

```text
http://localhost(服务器IP):2333/api?url="复制的(抖音/TikTok)口令/链接"
```

- 返回参数

> 抖音视频

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

> 抖音图集

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

> TikTok视频

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

- 下载视频请求参数

```text
http://localhost(服务器IP):2333/video?url="复制的(抖音/TikTok)口令/链接"
# 返回无水印mp4文件
# 大量请求时很吃服务器内存，容易崩，慎用。
```

- 下载音频请求参数

```text
http://localhost(服务器IP):2333/music?url="复制的(抖音/TikTok)口令/链接"
# 返回mp3文件
# 大量请求时很吃服务器内存，容易崩，慎用。
```

---

## 💾部署

> 注：
截图可能因更新问题与文字不符，一切请优先参照文字叙述。

> 最好将本项目部署至海外服务器，否则可能会出现奇怪的问题。

如：项目部署在国内服务器，而人在美国，点击结果页面链接报错403 ，目测与抖音CDN有关系。

> 使用宝塔Linux面板进行部署

- 首先要去安全组开放5000和2333端口（Web默认5000，API默认2333，可以在文件底部修改。）
- 在宝塔应用商店内搜索python并安装项目管理器 (推荐使用1.9版本)

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_1.png)

---

- 创建一个项目名字随意
- 路径选择你上传文件的路径
- Python版本需要至少3以上(在左侧版本管理中自行安装)
- 框架修改为`Flask`
- 启动方式修改为`python`
- Web启动文件选择`web_zh.py`
- API启动文件选择`web_api.py`
- 勾选安装模块依赖
- 开机启动随意
- 如果宝塔运行了`Nginx`等其他服务时请自行判断端口是否被占用，运行端口可在文件底部修改。

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)

---

## 🎉截图

> 注：
截图可能因更新问题与文字不符，一切请优先参照文字叙述。

- 主界面

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/home.png)

---

- 解析完成

> 单个

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/single_result.png)

---

> 批量

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/multi_results.png)

---

- API提交/返回

> 视频返回值

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_video_result.png)

> 图集返回值

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_image_result.png)

> TikTok返回值

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/tiktok_API.png)

---
