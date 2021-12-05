# 欢迎使用 `TikTokDownloader_PyWebIO` (抖音在线解析)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)
[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)
[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)
[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

Language:  [[English](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README-EN.md)]  [[简体中文](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.md)]

> Note: Words "TikTok" in this readme file mentioned stands for  the Chinese version of TikTok.
AKA [[Douyin](https://www.douyin.com/)] or [[抖音](https://www.douyin.com/)] , The US TikTok will be supported soon! (no gallery analysis function)

目录: [API](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%EF%B8%8Fapi%E4%BD%BF%E7%94%A8) [截图](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E6%88%AA%E5%9B%BE) [部署](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO#%E9%83%A8%E7%BD%B2)

## 👻介绍

🚀演示地址：[https://douyin.wtf/](https://douyin.wtf/)

🛰API演示：[https://douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/](https://douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/)

本项目使用 [PyWebIO](https://github.com/pywebio/PyWebIO)、[Requests](https://github.com/psf/requests)、[Flask](https://github.com/pallets/flask)，利用Python实现在线批量解析抖音的无水印视频/图集。

可用于下载作者禁止下载的视频，同时可搭配[iOS快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)配合本项目API实现应用内下载。

## 💯已支持功能：

- 视频/图集解析

- 支持批量解析

- 开放API

- 将本项目部署至在线服务器

---

## 🤦‍♂️后续功能：

- [ ] 搭配[iOS快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)实现应用内下载 (懒，来个人帮忙写一下呗)

- [ ] 添加对美区TikTok的支持(无图集解析)

---

## 🧭如何使用:

- 安装依赖库：

```text
pip install -r requirements.txt
```

- 运行TikTok.py

```text
python3 TikTok.py
```

- 进入主页

```text
http://localhost(服务器IP):80/
```

## 🗺️支持的提交格式：

- 分享口令

```text
例子：8.79 vSy:/ %壁纸 %炫酷壁纸 %图集 每一张都是精选  https://v.douyin.com/RH7Gvmr/复淛佌lian接，打开Dou音搜索，直接观kan视频！
```

- 短网址

```text
例子：https://v.douyin.com/RHnWEng/
```

- 正常网址

```text
例子：
https://www.douyin.com/video/6997729432244866341&previous_page=video_detail
```

- 批量网址(无需使用符合隔开)

```text
例子：
1.20 rEu:/ ~猫跟你都想了解  https://v.douyin.com/RCjCE1D/ 复制此链接，打开Dou音搜索，直接观看视频！
5.17 dnq:/ 《黑猫警长》吃猫鼠也太强了，不仅把猫当食物，连鳄鱼也害怕它!  https://v.douyin.com/RCjVQwh/ 复制此链接，打开Dou音搜索，直接观看视频！
8.43 and:/ 一家人不听道士的劝，搬进了鬼别墅，诡异的事情接连发生 %%恐怖  %%热门  %%电影解说   https://v.douyin.com/RCj5pyh/ 复制此链接，打开Dou音搜索，直接观看视频！
3.84 FHI:/ 晚上好，蹦迪人，蹦迪魂，蹦迪都是人上人 能蹦几分是几分%%小姐姐蹦迪 %%美不美看大腿 @DOU+小助手  https://v.douyin.com/RCjqkow/ 复制此链接，打开Dou音搜索，直接观看视频！
6.61 mQk:/ biu～%%爱心发射 %%日常%%宿舍%%变妆  https://v.douyin.com/RCj7VV9/ 复制此链接，打开Dou音搜索，直接观看视频！
4.12 vse:/ 更该被人看到的古城超级英雄%%超级英雄无缝转场 %%复仇者联盟  https://v.douyin.com/RCjGAjG/ 复制此链接，打开Dou音搜索，直接观看视频！

```

## 🛰️API使用

API可将请求参数转换为需要提取的无水印视频/图片直链，配合IOS捷径可实现应用内下载。

- 请求参数

```text
http://localhost(服务器IP):80/api?url="复制的抖音链接"
```

- 返回参数

> 视频

```json
{
"Type": "video",
"video_author": "花花花菜",
"video_author_id": "Wobukunxixi",
"video_music": "https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/6906830659719383822.mp3",
"video_title": "~猫跟你都想了解",
"video_url": "https://v3-dy-o.zjcdn.com/93e3a68e365ae83f4ce2b2bb9c253489/6191c9c3/video/tos/cn/tos-cn-ve-15/083012c589c842e69f5267803eb8e3a5/?a=1128&br=2262&bt=2262&cd=0%7C0%7C0&ch=96&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=StecAhgM6BMM8b8NDtPDWodpeaQ&l=202111150945070102121380392D1AC2F5&lr=all&mime_type=video_mp4&net=0&pl=0&qs=0&rc=ajh5aTRseW95eTMzNGkzM0ApNjk1OTU6OWVlN2Q7ODo0N2cpaHV2fWVuZDFwekBvbTJjMDVrbmBfLS1eLS9zczRhXi9iLmFgYGBfLy1iLi46Y29zYlxmK2BtYmJeYA%3D%3D&vl=&vr="
}
```

> 图集

```json
{
"Type": "image",
"image_author": "三石壁纸(收徒)",
"image_author_id": "782972562",
"image_music": "https://sf6-cdn-tos.douyinstatic.com/obj/tos-cn-ve-2774/635efafc32694ffbb73fbe60eca4a99d",
"image_title": "#壁纸 #炫酷壁纸 #图集 每一张都是精选",
"image_url": [
"https://p3-sign.douyinpic.com/tos-cn-i-0813/4af91199ca154074a8a5a63c3c749c6f~noop.webp?x-expires=1639530000&x-signature=P446eJEt2yuyhf2yb58Be29UpBA%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&sh=&sc=&l=202111150954330102120702320620C75E&biz_tag=aweme_images"
]
}
```

## 🎉截图

- 主界面

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/home.png)

---

- 解析完成

>  单个

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

- Python版本默认

- 框架修改为`Flask`

- 启动方式修改为`python`

- 启动文件选择`TikTok_ZH.py`

- 勾选安装模块依赖

- 开机启动随意

- 如果宝塔安装了`Nginx`请将其停止或修改代码端口

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)



