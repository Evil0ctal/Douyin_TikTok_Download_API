# Welcome to use `TikTokDownload_PyWebIO` (TikTok Online Downloader)

ReadMe Language:  [[English](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README-EN.md)]  [[简体中文](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/README.md)]

> Note: Words "TikTok" in this readme file mentioned stands for  the Chinese version of TikTok.
AKA [[Douyin](https://www.douyin.com/)] or [[抖音](https://www.douyin.com/)] , The US version of TikTok will be supported soon! (no gallery analysis function)

## 👻 Introduce

🚀Demo: [https://en.douyin.wtf](https://en.douyin.wtf)

🛰API demo: [https://en.douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/](https://en.douyin.wtf/api?url=https://v.douyin.com/R9bQKx4/)

This project uses [PyWebIO](https://github.com/pywebio/PyWebIO), [Requests](https://github.com/psf/requests), [Flask ](https://github.com/pallets/flask) as Python libraries to download TikTok's videos/gallery without watermark.

It can be used to download videos/gallery that the author has forbidden to download. At the same time, it can be used with [iOS shortcut APP ](https://apps.apple.com/us/app/shortcuts/id915249334)to cooperate with this project's API to realize internal download.

## 💯Achieved functions:

- Video/gallery analysis

- Support batch analysis

- Open API

- Deploy this project to the online server

---

## 🤦‍♂️Follow-up function:

- [ ] Realize the application with the [iOS shortcut APP](https://apps.apple.com/us/app/shortcuts/id915249334) (lazy, please write in personal help)

- [ ] Add support for the US version of TikTok (no gallery analysis function)

---

## 🧭How to use:

- Install dependent libraries:

```text
pip install -r requirements.txt
```

- Run TikTok.py

```text
python3 TikTok.py
```

- Home Page path:

```text
http://localhost(Server IP):80/
```

## 🗺️Supported submission format：

- Shared words

```text
Example：8.79 vSy:/ %壁纸 %炫酷壁纸 %图集 每一张都是精选  https://v.douyin.com/RH7Gvmr/复淛佌lian接，打开Dou音搜索，直接观kan视频！
```

- Short links

```text
Example：https://v.douyin.com/RHnWEng/
```

- Normal links

```text
Example：
https://www.douyin.com/video/6997729432244866341&previous_page=video_detail
```

- Bulk URLs (no need to use match separation)

```text
Example：
1.20 rEu:/ ~猫跟你都想了解  https://v.douyin.com/RCjCE1D/ 复制此链接，打开Dou音搜索，直接观看视频！
5.17 dnq:/ 《黑猫警长》吃猫鼠也太强了，不仅把猫当食物，连鳄鱼也害怕它!  https://v.douyin.com/RCjVQwh/ 复制此链接，打开Dou音搜索，直接观看视频！
8.43 and:/ 一家人不听道士的劝，搬进了鬼别墅，诡异的事情接连发生 %%恐怖  %%热门  %%电影解说   https://v.douyin.com/RCj5pyh/ 复制此链接，打开Dou音搜索，直接观看视频！
3.84 FHI:/ 晚上好，蹦迪人，蹦迪魂，蹦迪都是人上人 能蹦几分是几分%%小姐姐蹦迪 %%美不美看大腿 @DOU+小助手  https://v.douyin.com/RCjqkow/ 复制此链接，打开Dou音搜索，直接观看视频！
6.61 mQk:/ biu～%%爱心发射 %%日常%%宿舍%%变妆  https://v.douyin.com/RCj7VV9/ 复制此链接，打开Dou音搜索，直接观看视频！
4.12 vse:/ 更该被人看到的古城超级英雄%%超级英雄无缝转场 %%复仇者联盟  https://v.douyin.com/RCjGAjG/ 复制此链接，打开Dou音搜索，直接观看视频！

```

## 🛰️API usage

The API can convert the request parameters into a non-watermarked video/picture direct link that needs to be extracted, and can be used with IOS shortcuts to achieve in-app download.

- Request parameter

```text
http://localhost(Server IP):80/api?url="Supported submission format"
```

- Response parameters

> Videos

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

> Images

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

## 🎉Screenshots

- Main

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/home.png)

---

- Parsing is complete

>  Single result

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/single_result.png)

---

> Multiple results

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/multi_results.png)

---

- API request/response

> Video response

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_video_result.png)

> Gallery response

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_image_result.png)

