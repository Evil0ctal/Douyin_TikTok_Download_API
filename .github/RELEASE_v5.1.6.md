## What's new

**Bilibili is back.** v4 had it and the v5 rewrite left it out, because it
shares neither the signing nor the identity machinery with Douyin and TikTok.
This release brings it back with both: Bilibili's WBI signature in pure
Python, and guest identities minted over plain HTTP - so a Bilibili pool fills
itself with no browser container at all. Videos come back with a playable MP4,
and downloading or transcribing one works like any other post - one part of a
multi-part video included. Most console pages were reworked around what the
last few releases added, and the release also hardens three things the 5.1.5
security review found next to the fix it shipped.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.6 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or update the checkout first, then run the installer and pick Upgrade:

```bash
cd /opt/dtk && git pull && bash install/install.sh --manage
```

Three new migrations: the identity pool can hold Bilibili identities, a
download can be one part of a multi-part video, and the request log is indexed
by proxy for the Logs page's proxy filter. The browser image is unchanged since
5.1.4; nothing to rebuild. Your data is untouched.

Two things do not happen by themselves:

- **API keys do not gain the new scope.** A key that should read Bilibili needs
  `bilibili:read`; edit it or mint a new one under **Access → API keys**.
  Console sessions need nothing.
- **A demo instance's published key** is minted with the scopes of the release
  that minted it. Rotate the demo pair once (Settings → Demo mode) and the published
  key reads Bilibili too.

### Added

- **Bilibili.** `/api/v1/bilibili/...`, MCP and the console, with the same
  normalized output as the other two platforms:
  - `video` - one video, with its playable file. Bilibili's detail has no
    stream in it, so the instance asks `playurl` for one as a second request,
    logged under its own name, `bilibili.content_streams`; if that request
    fails the detail still comes back. The stream is one progressive MP4, up to
    1080p for a guest on older uploads and 720p on current ones.
  - `video/comments` and `video/comments/replies`. A guest is shown the three
    hottest comments and no further page, so comments prefer an imported
    signed-in identity, and need one past the first page. Replies are the same
    one level down: a guest reads the first page, up to 20, and later pages
    need a signed-in identity.
  - `user`, `user/posts`, `user/collections` (an uploader's public folders) and
    `collection/posts`. `/parse` answers a folder link with the folder's first
    page of videos.
  - Links: `bilibili.com/video/BV...` and `av...`, `b23.tv` and `bili2233.cn`
    short links, `space.bilibili.com/<mid>`, folder links and the embedded
    player. An av number is converted to the bvid it stands for, so either
    spelling reaches the same post.
  - Minting needs no browser anywhere: the pool's own refill, **Mint
    identities**, the setup wizard, the identity tool and
    `dtk identity mint --platform bilibili`.
  - The signing tools: `/tools/sign` signs with WBI, and `/tools/decode` checks
    a `w_rid` against the query it came on.

  - `popular?board=hot|weekly|precious|rank` - the four lists the site curates:
    what is popular now (paged), the weekly selection (one issue a page, newest
    first, the cursor naming the next older issue), the editors' all-time
    selection and the top 100. All four answer a guest identity.
  - `video/parts` - the parts of a video uploaded in several, which `video`
    now also lists as `parts`. `part=N` on `video` returns that part's stream,
    and a link's `?p=N` means the same.
  - Comments `sort=hot|new` - newest first as well as hottest. Douyin and
    TikTok rank their own way: `hot` is accepted there, `new` is refused rather
    than answered with the hottest. Also on the MCP `list_comments` tool.

  The other endpoints - likes, reposts, bookmarks, followers, mixes - answer
  `UNSUPPORTED_CONTENT` on Bilibili. Its pool has its own marks,
  `pool.bilibili.min_size` and `pool.bilibili.target_size`; set the first to 0
  if you do not want Bilibili identities minted.
- **Downloads show their progress while it happens.** A running download is
  drawn in bytes against its size, with the percentage, the speed and the time
  left, and the page polls every 2 seconds while anything is in flight. The
  drawer follows the row, file by file. Until now a file's size was reported
  only once it had finished, so a 100 MB video sat at "1/2 files" until it
  landed. `GET /api/v1/downloads` carries the same numbers in `progress`:
  `bytes`, `bytes_expected` and `bytes_per_second`, overall and per file. They
  come from the 5.1.6 downloader image; with an older one the bar still counts
  files.
- **Download one part of a multi-part video.** `part` on `POST
  /api/v1/downloads` and the MCP `download_media` tool, or a pasted link's
  `?p=`. Each part is its own download in a directory beside the post's
  (`<bvid>-p3/`), so two parts can download at once and removing one never
  takes the other, and it is transcribed as its own video. A plain download
  still stores the first part, the one the post's link plays.
- **Transcribe from the Library.** A saved video post's drawer has
  **Transcribe video**, as the downloads drawer does. On a post not saved yet it
  is disabled and says to store it first, because the transcriber reads the
  stored file. A demo account still cannot transcribe: the route is not on its
  write allowlist and the published key holds no transcription scope.
- **README sections** on offline transcription and on driving the instance
  from an AI agent over MCP, and a user-guide section on transcribing a stored
  video: starting one from the console, REST or MCP, the Transcriptions page,
  and what it costs.

- **The console, page by page.** Most pages were reworked around what the
  last few releases added:
  - **Identities** - mint and import follow the platform (import detects it
    from the cookies, and the server says which cookie does what), each
    platform's logged-in coverage with a warning at none, login expiry, a
    drawer that stays live and acts, filters in the URL, paging that no longer
    hides older logins behind retired rows.
  - **Downloads, Library, Transcriptions, Watchlist** - they link to each
    other (`?id=`, `?author=`, `?tag=`), page and search past the first 100,
    an author run reports its progress and can be stopped, the Library filters
    by author and tag and acts on a selection, the Watchlist takes links and
    names, a Bilibili post can be downloaded part by part, and a Transcribe
    button that cannot work says how to turn the transcriber on.
  - **Playground, Overview, sidebar** - the Playground greys what a platform
    does not serve and fills real examples, a guest's partial answer is
    flagged, Overview's tiles link and its pool is per platform, and the
    sidebar's Tools group is now Content and Developer.
  - **Operator pages** - API keys can be edited (and keys missing
    `bilibili:read` are flagged), role changes and demo actions confirm,
    Settings links the keys that have their own page, Logs filters by endpoint,
    proxy and actor with the filters in the URL, Proxies test in bulk and open
    a drawer, Diagnose takes a platform and the System page reports the
    downloader and transcriber, the Scheduler counts per platform, and every
    page hides what the role cannot do.
- **API additions behind those pages:** `PATCH /api/v1/admin/api-keys/{id}`,
  `GET /api/v1/system/capabilities`, `q` on `GET /api/v1/downloads` and
  `/transcriptions`, `POST /api/v1/archive/export` for a selection,
  `proxy_id` on the request log, `platform` on `POST /admin/diagnose`, and the
  downloader and transcriber in `GET /api/v1/system/status`.

### Changed

- **The uploader video list is the endpoint Bilibili guards hardest.** A burst
  of requests from one egress is answered with HTTP 412 pages for a few minutes,
  whatever the request looks like; it is reported as `UPSTREAM_RISK_CONTROL` and
  has the slowest token bucket of any Bilibili endpoint. Spread requests out,
  or add proxies.
- **Minting backs off per route.** A browser container that is down no longer
  stops the Bilibili pool from refilling. The identities pool panel says per
  platform whether it can be minted here (`can_mint` on each platform in
  `GET /api/v1/admin/identities/pool`).
- **The archive recheck and backfill count against the queue ceiling.** They
  were exempt as rare operator jobs while any read key could queue them; a
  bulk run of them can now answer `QUEUE_FULL` like every other read.

### Fixed

- **MCP calls made with the published demo key** were archived and logged like
  an operator's and kept past the demo retention window. They are demo tasks
  now, as the REST routes and the media tools already made them.
- **MCP checked `count` only against its own 50.** A TikTok page of 36 to 50
  reached TikTok, which answers it as if the author had no posts. MCP now
  refuses a count above the platform's ceiling with `INVALID_PARAM`, as REST
  does.
- **Reissuing the demo pair answered 500** on any instance where anybody had
  logged in, and rolled back, so the pair could not be rotated at all; turning
  demo mode off left the demo's console sessions alive behind a logged error.
  The demo's logout scanned every `session:*` key and read each as a string,
  and every session keeps a hash under the same prefix. It now reads the
  account's own session index. Upgrade before you rotate, as Upgrading above
  asks you to.
- **Saving a TikTok post's comments from the Downloads page** asked for 50 a
  page, above TikTok's 35, and failed. It stays under each platform's ceiling
  now.
- **Bilibili downloads failed as `partial` with an empty video** whenever the
  CDN cut the transfer short - measured, an Akamai edge that had not cached the
  video yet stopped every time after exactly 2 MiB, and three part downloads in
  five failed that way. The downloader now asks the same mirror for the rest
  of the file from where it stopped, only accepting the rest of the same file,
  and waits out an edge that answers 504 while it fetches from its origin. A
  cold Akamai edge then refused the rest for over a minute, so an Akamai link
  is now tried on Bilibili's own hosts first, where the same file came back
  whole.
- **A Bilibili post archived from a list downloaded only its cover.** Lists
  carry no stream, and inside the mirror window the download was planned from
  the list's row and finished "done". Such a post is now fetched first.
- **The demo account could be made an administrator** with one click on the
  Users page, which saved a role change on select. Any change to the demo
  account is now refused by the server, and role changes confirm first.
- **Saving a notification channel subscribed it to everything** when it was
  narrowed to an alert the page did not know (`capacity_warning`,
  `capacity_paused`, `media_evicted`), and the Settings page showed the
  channels as `[object Object]`.

### Security

- **Queueing past the ceiling.** Any key with a platform read scope, or any
  viewer, could stack archive rechecks and backfills past `sched.queue_max`,
  each a run of real requests against the identity pool. See Changed above.

---

## 本次更新

**B 站回来了。** v4 支持过它，v5 重写时把它放下了，因为它和抖音 / TikTok 不共用签名和身份
体系。这个版本把这两样都补上了：纯 Python 的 B 站 WBI 签名，以及通过普通 HTTP 铸造的
游客身份——所以 B 站的身份池不需要浏览器容器就能自己补齐。视频会带上可播放的 MP4，下载和
转写都和其他平台的作品一样，多 P 视频的某一 P 也可以。控制台的大部分页面也围绕最近几个版本新增的功能重新整理了一遍，
这个版本还加固了 5.1.5 安全审查在那次修复之外发现的三处问题。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.6 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者先更新代码，再运行安装脚本并选择「升级」：

```bash
cd /opt/dtk && git pull && bash install/install.sh --manage
```

新增三个数据库迁移：身份池可以存放 B 站身份，一条下载可以是多 P 视频的某一 P，请求日志按代理
建立索引，供日志页按代理筛选。浏览器镜像自 5.1.4 起没有变化，不需要重新构建。数据不受影响。

有两件事不会自动发生：

- **已有的 API Key 不会自动获得新的权限范围。** 要读取 B 站的 Key 需要 `bilibili:read`；在
  **访问 → API 密钥** 里修改，或者新建一把。控制台会话不需要任何操作。
- **演示实例公开的那把 Key** 带的是生成它的那个版本的权限范围。轮换一次演示账号（设置 → 演示模式），
  公开的 Key 就能读取 B 站了。

### 新增

- **B 站。** `/api/v1/bilibili/...`、MCP 和控制台都支持，输出和另外两个平台是同一套归一化
  结构：
  - `video`——单个视频，带可播放文件。B 站的详情里没有视频流，所以实例会再请求一次 `playurl`，
    这次请求以它自己的名字 `bilibili.content_streams` 记入日志；如果这次请求失败，详情照常
    返回。视频流是单个渐进式 MP4，游客在较早的投稿上最高 1080p，当前的投稿一般是 720p。
  - `video/comments` 和 `video/comments/replies`。游客只能看到最热的三条评论，也没有下一页，
    所以评论接口优先使用导入的已登录身份，翻页则必须用它。回复也一样：游客能读第一页（最多
    20 条），后续页需要已登录身份。
  - `user`、`user/posts`、`user/collections`（UP 主的公开收藏夹）和 `collection/posts`。
    `/parse` 对收藏夹链接返回这个收藏夹第一页的视频。
  - 链接：`bilibili.com/video/BV...` 和 `av...`、`b23.tv` 和 `bili2233.cn` 短链、
    `space.bilibili.com/<mid>`、收藏夹链接和嵌入式播放器。av 号会换算成对应的 BV 号，两种
    写法拿到的是同一个作品。
  - 铸造在任何地方都不需要浏览器：身份池自己的补充、**铸造身份**、初始化向导、身份工具，
    以及 `dtk identity mint --platform bilibili`。
  - 签名工具：`/tools/sign` 用 WBI 签名，`/tools/decode` 会把 `w_rid` 和它所在的查询串
    对照校验。

  - `popular?board=hot|weekly|precious|rank`——站点维护的四个榜单：综合热门（分页）、每周必看
    （每页一期，最新的在前，游标是上一期的期号）、入站必刷和排行榜 Top 100。四个榜单游客身份都能拿到。
  - `video/parts`——多 P 视频的分 P 列表，`video` 现在也会在 `parts` 里列出。`video` 加上
    `part=N` 返回那一 P 的视频流，链接里的 `?p=N` 是同一个意思。
  - 评论 `sort=hot|new`——除了最热，也能按最新排。抖音和 TikTok 按自己的方式排序：接受 `hot`，
    `new` 会被拒绝，而不是悄悄返回最热的。MCP 的 `list_comments` 工具也支持。

  其余接口——喜欢、转发、收藏、粉丝、合集——在 B 站上返回 `UNSUPPORTED_CONTENT`。B 站的
  身份池有自己的水位 `pool.bilibili.min_size` 和 `pool.bilibili.target_size`；不想铸造 B 站
  身份，把前者设为 0 即可。
- **下载过程中实时显示进度。** 正在进行的下载按字节显示已下载量和总大小，并显示百分比、速度和
  剩余时间；有任务进行中时页面每 2 秒刷新一次。抽屉会跟着这一行更新，逐个文件显示进度。之前
  一个文件要下载完才会报告大小，所以一个 100 MB 的视频在下完之前一直停在「1/2 个文件」。
  `GET /api/v1/downloads` 的 `progress` 里也有同样的数字：`bytes`、`bytes_expected` 和
  `bytes_per_second`，整体和每个文件各一份。这些数字来自 5.1.6 的下载器镜像；用旧镜像时，
  进度条仍按文件数计算。
- **下载多 P 视频的某一 P。** 在 `POST /api/v1/downloads` 和 MCP 的 `download_media` 工具里传
  `part`，或者粘贴带 `?p=` 的链接。每一 P 都是独立的下载，存在作品目录旁边的单独目录里
  （`<bvid>-p3/`），所以两个分 P 可以同时下载，删掉其中一个也不会带走另一个，转写时也按独立的视频
  处理。普通下载仍然只存第一 P，也就是作品链接播放的那一 P。
- **在资料库里转写。** 已保存的视频作品的抽屉里有 **转写视频**，和下载抽屉一样。作品还没保存时
  按钮是灰的，并提示先保存，因为转写读取的是已保存的文件。演示账号仍然不能转写：这个接口不在它的
  写操作白名单里，公开的 Key 也没有转写权限。
- **README 新增两节**：离线转写，以及让 AI agent 通过 MCP 使用这个实例；用户指南里新增
  「把已存的视频转成文字」一节：从控制台、REST 或 MCP 发起转写，视频转写页面，以及它的代价。

- **控制台逐页整理。** 大部分页面围绕最近几个版本新增的功能重新整理了一遍：
  - **身份**——铸造和导入按平台适配（导入时根据 cookie 识别平台，并由服务端说明每个 cookie 的
    作用），按平台显示已登录身份的覆盖情况、为 0 时提醒，显示登录有效期，详情抽屉实时刷新并
    可直接操作，筛选写进 URL，分页不再让已退役的行把较早的登录身份挤出列表。
  - **下载、资料库、视频转写、关注列表**——页面之间互相跳转（`?id=`、`?author=`、`?tag=`），
    超过 100 条也能翻页和搜索，下载作者作品时显示进度并可停止，资料库可按作者和标签筛选并批量
    操作，关注列表可以粘贴链接、起名，B 站作品可以按分 P 下载，转写服务没开时转写按钮会说明
    怎么开启。
  - **Playground、概览、侧栏**——Playground 会灰掉平台不支持的接口并填入真实示例，游客兜底的
    不完整结果会被标出，概览的卡片可以点击跳转，身份池按平台显示，侧栏的「工具」组拆成了
    「内容」和「开发者」。
  - **管理页**——API Key 可以编辑（缺少 `bilibili:read` 的 Key 会被标出），改角色和演示模式操作
    会先确认，设置页里有专门页面的项改为链接，日志可按接口、代理和操作人筛选且筛选写进 URL，
    代理可以批量测试并打开详情，诊断可以选平台，系统页显示下载器和转写服务的状态，调度页按平台
    统计，每个页面都会隐藏当前角色用不了的操作。
- **这些页面背后新增的 API：** `PATCH /api/v1/admin/api-keys/{id}`、
  `GET /api/v1/system/capabilities`、`GET /api/v1/downloads` 和 `/transcriptions` 的 `q`、
  按选择导出的 `POST /api/v1/archive/export`、请求日志的 `proxy_id`、`POST /admin/diagnose`
  的 `platform`，以及 `GET /api/v1/system/status` 里的下载器和转写服务。

### 变更

- **UP 主投稿列表是 B 站防护最严的接口。** 同一个出口的突发请求会在几分钟内收到 HTTP 412
  拦截页，和请求长什么样无关；这会被报告为 `UPSTREAM_RISK_CONTROL`，它也是 B 站所有接口里
  令牌桶最慢的一个。请把请求分散开，或者加代理。
- **铸造按途径分别退避。** 浏览器容器挂掉不会再让 B 站的身份池停止补充。身份池面板会按平台
  说明能否在这里铸造（`GET /api/v1/admin/identities/pool` 里每个平台的 `can_mint`）。
- **归档重新检查和历史回溯计入队列上限。** 它们原来作为少见的运维任务被豁免，但任何读取 Key
  都能发起；批量发起时现在也会和其他读取一样收到 `QUEUE_FULL`。

### 修复

- **用公开的演示 Key 通过 MCP 发起的调用**，原来会像运维人员的调用一样被归档、记录，并且保留
  得比演示保留期更久。现在它们是演示任务，和 REST 路由及媒体工具的处理一致。
- **MCP 原来只用它自己的 50 检查 `count`。** 36 到 50 的 TikTok 分页会被发到 TikTok，而
  TikTok 对它的回答就像这个作者没有作品。现在 MCP 和 REST 一样，超过平台上限的 `count`
  直接返回 `INVALID_PARAM`。
- **重新生成演示账号和 Key 会返回 500**：只要有人登录过实例就会这样，并且整个操作回滚，所以演示
  账号根本无法轮换；关闭演示模式时，演示账号的控制台会话也不会被结束，只在日志里留下一条错误。
  原因是演示账号的登出逻辑扫描所有 `session:*` 键并把它们都当成字符串读取，而每个会话在同一前缀
  下还有一个哈希。现在改为读取该账号自己的会话索引。请先升级再轮换，正如上面「升级方式」所说。
- **在下载页保存 TikTok 作品的评论**时每页请求 50 条，超过了 TikTok 的 35，所以会失败。现在
  它不会超过各平台的上限。
- **B 站下载会以 `partial` 结束、视频为空**：每当 CDN 提前切断传输就会这样——实测还没缓存这个
  视频的 Akamai 节点每次都在恰好 2 MiB 处断开，五次分 P 下载里有三次因此失败。现在下载器会从断开的
  位置向同一个镜像请求文件的剩余部分，只接受同一个文件的剩余部分；节点在从源站拉取时返回 504 的，
  也会稍等再问。冷启动的 Akamai 节点之后会在一分多钟内一直拒绝剩余部分，所以现在 Akamai 链接会
  先换到 B 站自己的节点上尝试，同一个文件在那里能完整下载。
- **从列表归档的 B 站作品下载时只存了封面。** 列表里没有视频流，而在镜像有效期内下载是按列表那一行
  规划的，结果以「完成」结束。现在这种作品会先抓取详情。
- **演示账号可以被一键提升为管理员**：用户页的角色下拉框一选就保存。现在服务端拒绝对演示账号的
  任何修改，改角色也会先确认。
- **保存通知渠道会把它改成订阅全部事件**：当它只订阅了页面不认识的告警（`capacity_warning`、
  `capacity_paused`、`media_evicted`）时就会这样；设置页还把渠道显示成 `[object Object]`。

### 安全

- **绕过队列上限。** 任何带平台读取权限的 Key，或任何 viewer，都能把归档重新检查和历史回溯
  堆过 `sched.queue_max`，每一个都是一串真实请求，消耗身份池。见上面的「变更」。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.6` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.6` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-<40-char commit>` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.6` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.6`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.5...v5.1.6
