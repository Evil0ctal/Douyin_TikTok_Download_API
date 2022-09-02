# 歡迎使用`Douyin_TikTok_Download_API`(抖音/TikTok無水印解析API)

![](https://views.whatilearened.today/views/github/Evil0ctal/TikTokDownloader_PyWebIO.svg)[![GitHub license](https://img.shields.io/github/license/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/LICENSE)[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/issues)[![GitHub forks](https://img.shields.io/github/forks/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/network)[![GitHub stars](https://img.shields.io/github/stars/Evil0ctal/TikTokDownloader_PyWebIO)](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/stargazers)

語：  \[[英語](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.en.md)]  \[[簡體中文](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md)]  \[[繁體中文](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.zh-TW.md)]

> 注：此API適用於抖音和抖音。抖音是中國的TikTok。您可以在以下位置分發或修改代碼
> 會，但請註明原作者。

## 👻介紹

> 出於穩定性的考慮，暫時關閉演示站的/video(返回mp4文件)和/music(返回mp3文件)
> 這兩個功能，同時結果頁面的批量下載功能也暫時不可用，如有需求請自行部署，其他功能在演示站上仍正常使用，API服務器保證99%的時間正常運行，但不保證解析100%成功，如果解析失敗請等一兩分鐘後重試。

🚀演示地址：<https://douyin.wtf/>

🛰API演示：<https://api.douyin.wtf/>

💾iOS快捷指令(中文):[點擊獲取](https://www.icloud.com/shortcuts/331073aca78345cf9ab4f73b6a457f97)(
更新於2022/07/18，快捷指令可自動檢查更新，安裝一次即可。 )

🌎iOS快捷方式（英文）：[點擊獲取](https://www.icloud.com/shortcuts/83548306bc0c4f8ea563108f79c73f8d)（更新於
2022/07/18，這個快捷方式會自動檢查更新，只需要安裝一次。）

🗂快捷指令歷史版本:[快捷方式發布](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/53)

📦️Tiktok/抖音下載器(桌面應用)：[抖音](https://github.com/Tairraos/TikDown/)

本項目使用[PyWebIO](https://github.com/pywebio/PyWebIO)、[燒瓶](https://github.com/pallets/flask)，利用Python實現在線批量解析抖音的無水印視頻/圖集。

可用於下載作者禁止下載的視頻，或者進行數據爬取等等，同時可搭配[iOS自帶的快捷指令APP](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)配合本項目API實現應用內下載。

快捷指令需要在抖音或TikTok的APP內，選擇你想要保存的視頻，點擊分享按鈕，然後找到 "抖音TikTok無水印下載"
這個選項，如遇到通知詢問是否允許快捷指令訪問xxxx (域名或服務器)，需要點擊允許才可以正常使用，下載成功的視頻或圖集會保存在一個專門的相冊中以方便瀏覽。

## 💡項目文件結構

    # 请根据需要自行修改config.ini中的内容
    .
    └── Douyin_TikTok_Download_API/
        ├── /static(静态前端资源)
        ├── web_zh.py(网页入口)
        ├── web_api.py(API)
        ├── scraper.py(解析库)
        ├── config.ini(所有项目的配置文件，包含端口及代理等，如需请自行修改该文件。)
        ├── logs.txt(错误日志，自动生成。)
        └── API_logs.txt(API调用日志，自动生成。)

## 💯已支持功能：

-   支持抖音視頻/圖集解析
-   支持海外TikTok視頻解析
-   支持批量解析(支持抖音/TikTok混合解析)
-   解析結果頁批量下載無水印視頻
-   製作[pip包](https://pypi.org/project/DT-Scraper/)方便使用
-   支持API調用
-   支持使用代理解析
-   支持[iOS快捷指令](https://apps.apple.com/cn/app/%E5%BF%AB%E6%8D%B7%E6%8C%87%E4%BB%A4/id915249334)實現應用內下載無水印視頻/圖集

* * *

## 🤦‍後續功能：

-   [ ] 支持輸入(抖音/TikTok)作者主頁鏈接實現批量解析

* * *

## 🧭運行說明(經過測試過的Python版本為3.8):

> 🚨如果你要部署本項目，請參考部署方式([Docker部署](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%BA%8C-docker "Docker部署"),[手動部署](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/README.md#%E9%83%A8%E7%BD%B2%E6%96%B9%E5%BC%8F%E4%B8%80-%E6%89%8B%E5%8A%A8%E9%83%A8%E7%BD%B2 "手动部署"))

-   克隆本倉庫：

```console
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
```

-   移動至倉庫目錄：

```console
cd Douyin_TikTok_Download_API
```

-   安裝依賴庫：

```console
pip install -r requirements.txt
```

-   修改config.ini(可選)：

```console
vim config.ini
```

-   網頁解析

```console
# 运行web_zh.py
python3 web_zh.py
```

-   API

```console
# 运行web_api.py
python3 web_api.py
```

-   調用解析庫

```python
# pip install DT-Scraper
from DT_scraper.scraper import Scraper

api = Scraper()

# 解析Douyin视频/图集
douyin_data = api.douyin(input('抖音视频链接：'))
# 返回字典
print(douyin_data)

# Parsing TikTok Videos/Galleries
tiktok_data = api.tiktok(input('TikTok video URL：'))
# return dictionary
print(tiktok_data)

# 使用代理进行解析(Parse using a proxy)
api.tiktok(input('TikTok video URL：'), proxies = {'all': 127.0.0.1:2333})

```

-   入口(端口可在config.ini文件中修改)

```text
网页入口:
http://localhost(服务器IP):5000/
API入口:
http://localhost(服务器IP):2333/
```

## 🗺️支持的提交格式(包含但不僅限於以下例子)：

-   抖音分享口令  (APP內復制)

```text
例子：7.43 pda:/ 让你在几秒钟之内记住我  https://v.douyin.com/L5pbfdP/ 复制此链接，打开Dou音搜索，直接观看视频！
```

-   抖音短網址 (APP內復制)

```text
例子：https://v.douyin.com/L4FJNR3/
```

-   抖音正常網址 (網頁版複製)

```text
例子：
https://www.douyin.com/video/6914948781100338440
```

-   抖音發現頁網址 (APP複製)

```text
例子：
https://www.douyin.com/discover?modal_id=7069543727328398622
```

-   TikTok短網址 (APP內復制)

```text
例子：
https://vm.tiktok.com/TTPdkQvKjP/
```

-   TikTok正常網址 (網頁版複製)

```text
例子：
https://www.tiktok.com/@tvamii/video/7045537727743380782
```

-   抖音/TikTok批量網址(無需使用符合隔開)

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

API可將請求參數轉換為需要提取的無水印視頻/圖片直鏈，配合IOS捷徑可實現應用內下載。

-   解析請求參數

```text
http://localhost(服务器IP):2333/api?url="复制的(抖音/TikTok)口令/链接"
```

-   返回參數

> 抖音視頻

```json
{
  "analyze_time": "1.9043s",
  "api_url": "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=6918273131559881997",
  "nwm_video_url": "http://v3-dy-o.zjcdn.com/23f0dec312ede563bef881af9a88bdc7/624dd965/video/tos/cn/tos-cn-ve-15/eccedcf4386948f5b5a1f0bcfb3dcde9/?a=1128&br=2537&bt=2537&cd=0%7C0%7C0%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=sYGC~3E7nz7Th1PZSDXq&l=202204070118030102080650132A21E31F&lr=&mime_type=video_mp4&net=0&pl=0&qs=0&rc=M3hleDRsODlkMzMzaGkzM0ApODpmNWc4ODs5N2lmNzg5aWcpaGRqbGRoaGRmLi4ybnBrbjYuYC0tYy0wc3MtYmJjNTM2NjAtNDFjMzJgOmNwb2wrbStqdDo%3D&vl=&vr=",
  "original_url": "https://v.douyin.com/L4FJNR3/",
  "platform": "douyin",
  "status": "success",
  "url_type": "video",
  "video_author": "Real机智张",
  "video_author_id": "Rea1yaoyue",
  "video_author_signature": "",
  "video_author_uid": "59840491348",
  "video_aweme_id": "6918273131559881997",
  "video_comment_count": "89145",
  "video_create_time": "1610786002",
  "video_digg_count": "2968195",
  "video_hashtags": [
    "百万转场变身"
  ],
  "video_music": "https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/6910889805266504461.mp3",
  "video_music_author": "梅尼耶",
  "video_music_id": "6910889820861451000",
  "video_music_mid": "6910889820861451021",
  "video_music_title": "@梅尼耶创作的原声",
  "video_play_count": "0",
  "video_share_count": "74857",
  "video_title": "骑白马的也可以是公主#百万转场变身",
  "wm_video_url": "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0300ffe0000c01a96q5nis1qu5b1u10&ratio=720p&line=0"
}
```

> 抖音圖集

```json
{
  "album_author": "治愈图集",
  "album_author_id": "ZYTJ2002",
  "album_author_signature": "取无水印图",
  "album_author_uid": "449018054867063",
  "album_aweme_id": "7015137063141920030",
  "album_comment_count": "5436",
  "album_create_time": "1633338878",
  "album_digg_count": "193734",
  "album_hashtags": [
    "晚霞",
    "治愈系",
    "落日余晖",
    "日落🌄"
  ],
  "album_list": [
    "https://p26-sign.douyinpic.com/tos-cn-i-0813/5223757a7bef4f8480cd25d0fa2d2d94~noop.webp?x-expires=1651856400&x-signature=K1VjJdWTHCAaYSz14y6NumjjtfI%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47",
    "https://p26-sign.douyinpic.com/tos-cn-i-0813/d99467672da840908acccf2d2b4b7ef7~noop.webp?x-expires=1651856400&x-signature=ncBb8Tt7z4PmpUyiCNr%2FJYnwRSA%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47",
    "https://p26-sign.douyinpic.com/tos-cn-i-0813/5c2562210b1a4d4c99d6d4dbd2f23f2b~noop.webp?x-expires=1651856400&x-signature=Rsmplb53IKfvKd3mmIb4iQNhlIE%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47",
    "https://p26-sign.douyinpic.com/tos-cn-i-0813/9bb74c0c6aff4217bd1491a077b2c817~noop.webp?x-expires=1651856400&x-signature=BLRyHoKP0ybIci57yneOca62dxI%3D&from=4257465056&s=PackSourceEnum_DOUYIN_REFLOW&se=false&biz_tag=aweme_images&l=202204070120460102101050412A210A47"
  ],
  "album_music": "https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/6978805801733442341.mp3",
  "album_music_author": "魏同学",
  "album_music_id": "6978805810365271000",
  "album_music_mid": "6978805810365270791",
  "album_music_title": "@魏同学创作的原声",
  "album_play_count": "0",
  "album_share_count": "30717",
  "album_title": "“山海自有归期 风雨自有相逢 意难平终将和解 万事终将如意”#晚霞 #治愈系 #落日余晖 #日落🌄",
  "analyze_time": "1.0726s",
  "api_url": "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=7015137063141920030",
  "original_url": "https://v.douyin.com/Nb8jysN/",
  "platform": "douyin",
  "status": "success",
  "url_type": "album"
}
```

> TikTok視頻

```JSON
{
  "analyze_time": "5.0863s",
  "nwm_video_url": "https://v19.tiktokcdn-us.com/cfa357dadd8f913f013a6d0b0dca293f/624e20fa/video/tos/useast5/tos-useast5-ve-0068c003-tx/3296231486014755a1b81aa70c349a53/?a=1233&br=6498&bt=3249&cd=0%7C0%7C0%7C3&ch=0&cr=3&cs=0&cv=1&dr=0&ds=6&er=&ft=bY1KJnB4TJBS6BMy-L1iVKP&l=20220406172333010113135214232FAB56&lr=all&mime_type=video_mp4&net=0&pl=0&qs=0&rc=MzpsaGY6Zjo7PDMzZzczNEApNjY6ZTtkOzxpN2Q3PDo5OmdgZ2BtcjQwai9gLS1kMS9zczJhLTEzYjEuMTJeXzQyLmM6Yw%3D%3D&vl=&vr=",
  "original_url": "https://www.tiktok.com/@oregonzoo/video/7080938094823738666",
  "platform": "tiktok",
  "status": "success",
  "url_type": "video",
  "video_author": "oregonzoo",
  "video_author_SecId": "MS4wLjABAAAArWNQ8-AZN6CxWOkqdeWsMBUuLDmJt8TWUAk0S4aWDW5V5EoqRbuczhaLnxJHCGob",
  "video_author_diggCount": 94,
  "video_author_followerCount": 1800000,
  "video_author_followingCount": 39,
  "video_author_heartCount": 29700000,
  "video_author_id": "6699816060206171141",
  "video_author_nickname": "Oregon Zoo",
  "video_author_videoCount": 264,
  "video_aweme_id": "7080938094823738666",
  "video_comment_count": 61,
  "video_create_time": "1648659375",
  "video_digg_count": 11800,
  "video_hashtags": [
    "redpanda",
    "boop",
    "sunshine"
  ],
  "video_music": "https://sf16.tiktokcdn-us.com/obj/ies-music-tx/7075363935741856558.mp3",
  "video_music_author": "Gilderoy Dauterive",
  "video_music_id": "7075363884613356330",
  "video_music_title": "Be the Sunshine",
  "video_music_url": "https://sf16.tiktokcdn-us.com/obj/ies-music-tx/7075363935741856558.mp3",
  "video_play_count": 60100,
  "video_ratio": "720p",
  "video_share_count": 298,
  "video_title": "Moshu ✨ #redpanda #boop #sunshine",
  "wm_video_url": "https://v16m-webapp.tiktokcdn-us.com/0394b9183a5852d4392a7e804bf78c55/624e20f6/video/tos/useast5/tos-useast5-ve-0068c001-tx/fc63ae232e70466398b55ccf97eb3c67/?a=1988&br=6468&bt=3234&cd=0%7C0%7C1%7C0&ch=0&cr=0&cs=0&cv=1&dr=0&ds=3&er=&ft=XY53A3E7nz7Th-pZSDXq&l=202204061723290101131351171341B9BB&lr=tiktok_m&mime_type=video_mp4&net=0&pl=0&qs=0&rc=MzpsaGY6Zjo7PDMzZzczNEApOjo4aDMzZmRlN2loOWk6ZWdgZ2BtcjQwai9gLS1kMS9zczBhNGA0LTIwNjNiYDQ2YmE6Yw%3D%3D&vl=&vr="
}
```

-   下載視頻請求參數

```text
http://localhost(服务器IP):2333/video?url="复制的(抖音/TikTok)口令/链接"
# 返回无水印mp4文件
```

-   下載音頻請求參數

```text
http://localhost(服务器IP):2333/music?url="复制的(抖音/TikTok)口令/链接"
# 返回mp3文件
```

* * *

## 💾部署(方式一 手動部署)

> 注：
> 截圖可能因更新問題與文字不符，一切請優先參照文字敘述。

> 最好將本項目部署至海外服務器(優先選擇美國地區的服務器)，否則可能會出現奇怪的問題。

例子：
項目部署在國內服務器，而人在美國，點擊結果頁面鏈接報錯403 ，目測與抖音CDN有關係。
項目部署在韓國服務器，解析TikTok報錯 ，目測TikTok對某些地區或IP進行了限制。

> 使用寶塔Linux面板進行部署(
> 中文寶塔要強制綁定手機號了，很流氓且無法繞過，建議使用寶塔國際版，谷歌搜索關鍵字aapanel自行安裝，部署步驟相似。 )

-   首先要去安全組開放5000和2333端口（Web默認5000，API默認2333，可以在文件config.ini中修改。）
-   在寶塔應用商店內搜索python並安裝項目管理器 (推薦使用1.9版本)

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_1.png)

* * *

-   創建一個項目名字隨意
-   路徑選擇你上傳文件的路徑
-   Python版本需要至少3以上(在左側版本管理中自行安裝)
-   框架修改為`Flask`
-   啟動方式修改為`python`
-   Web啟動文件選擇`web_zh.py`
-   API啟動文件選擇`web_api.py`
-   勾選安裝模塊依賴
-   開機啟動隨意
-   如果寶塔運行了`Nginx`等其他服務時請自行判斷端口是否被佔用，運行端口可在文件config.ini中修改。

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/BT_Linux_Panel_Deploy_2.png)

-   如果有大量請求請使用進程守護啟動防止進程關閉

* * *

## 💾部署(方式二 Docker)

-   安裝docker

```yaml
curl -fsSL get.docker.com -o get-docker.sh&&sh get-docker.sh &&systemctl enable docker&&systemctl start docker
```

-   留下config.int和docker-compose.yml文件即可
-   運行命令,讓容器在後台運行

```yaml
docker compose up -d
```

-   查看容器日誌

```yaml
docker logs -f douyin_tiktok_download_api
```

-   刪除容器

```yaml
docker rm -f douyin_tiktok_download_api
```

-   更新

```yaml
docker compose pull && docker compose down && docker compose up -d
```

## 🎉截圖

> 注：
> 截圖可能因更新問題與文字不符，一切請優先參照文字敘述。

-   主界面

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/home.png)

* * *

-   解析完成

> 單個

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/single_result.png)

* * *

> 批量

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/multi_results.png)

* * *

-   API提交/返回

> 視頻返回值

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_video_result.png)

> 圖集返回值

![](https://github.com/Evil0ctal/TikTokDownloader_PyWebIO/blob/main/Screenshots/api_image_result.png)

> TikTok返回值

![](https://raw.githubusercontent.com/Evil0ctal/TikTokDownloader_PyWebIO/main/Screenshots/tiktok_API.png)

* * *
