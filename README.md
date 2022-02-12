# 欢迎使用 `TikTokDownloader_PyWebIO` (抖音在线解析)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)
[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)
[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)
[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

目录: [API](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8) [截图](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E6%88%AA%E5%9B%BE) [部署](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E9%83%A8%E7%BD%B2)

Language:  [[English](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.en.md)]  [[简体中文](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.md)]

> Note: Words "TikTok" in this readme file mentioned stands for  the Chinese version of TikTok.
> AKA [[Douyin](https://www.douyin.com/)] or [[抖音](https://www.douyin.com/)] , The US TikTok is now supported! (no gallery analysis function)

## 👻介绍

🚀演示地址：[https://douyin.wtf/](https://douyin.wtf/)

🛰API演示：[https://douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/](https://douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/)

💾iOS快捷指令: [点击获取指令](https://www.icloud.com/shortcuts/e8243369340548efa0d4c1888dd3c170) 更新于2022/02/06

本项目使用 [PyWebIO](https://github.com/pywebio/PyWebIO)、[Requests](https://github.com/psf/requests)、[Flask](https://github.com/pallets/flask)，利用Python实现在线批量解析抖音的无水印视频/图集。

可用于下载作者禁止下载的视频，同时可搭配[iOS快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)配合本项目API实现应用内下载。

快捷指令需要在抖音或TikTok的APP内，选择你想要保存的视频，点击分享按钮，然后找到 "抖音TikTok无水印下载" 这个选项，如遇到通知询问是否允许快捷指令访问xxxx (域名或服务器)，需要点击允许才可以正常使用。

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

- 运行TikTok_ZH.py (Python版本需3.9以上)

```text
python3 TikTok_ZH.py
# python3 TikTok_EN.py - English interface
```

- 进入主页

```text
http://localhost(服务器IP):80/
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
http://localhost(服务器IP):80/api?url="复制的(抖音/TikTok)口令/链接"
```

- 返回参数

> 抖音视频

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

> 抖音图集

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

> TikTok视频

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

- 下载视频请求参数

```text
http://localhost(服务器IP):80/video?url="复制的(抖音/TikTok)口令/链接"
#返回mp4文件
```

- 下载音频请求参数

```text
http://localhost(服务器IP):80/bgm?url="复制的(抖音/TikTok)口令/链接"
#返回mp3文件
```

---

## 💾部署

> 最好将本项目部署至海外服务器，否则可能会出现奇怪的问题

如：项目部署在国内服务器，而人在美国，点击结果页面链接报错403 ，目测与抖音CDN有关系。

> 使用宝塔Linux面板进行部署

- 首先要去安全组开放80端口（默认80，可以在文件底部修改。）
- 在宝塔应用商店内搜索python并安装项目管理器

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_1.png)

---

- 创建一个项目名字随意
- 路径选择你上传文件的路径
- Python版本需要至少3.9以上(在左侧版本管理中自行安装)
- 框架修改为`Flask`
- 启动方式修改为`python`
- 启动文件选择`TikTok_ZH.py`
- 勾选安装模块依赖
- 开机启动随意
- 如果宝塔安装了`Nginx`等应用请将其停止或在`TikTok_ZH.py`底部修改端口(默认端口为80)

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)

---

## 🎉截图

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

