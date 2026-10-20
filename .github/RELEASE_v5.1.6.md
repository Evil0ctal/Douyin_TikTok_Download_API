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

Installed with the script? This release is the one that adds `./dtkctl upgrade`,
so this once it is two commands in the install directory:

```bash
cd /opt/dtk && git pull           # or ~/dtk - wherever you installed it
bash install/install.sh --upgrade # install/install.zh.sh for the Chinese one
```

It pulls 5.1.6, rebuilds the browser image, migrates, restarts and writes
`DTK_IMAGE_TAG=5.1.6` into `.env` itself; there is nothing to edit. From the
next release on, the whole upgrade is `./dtkctl upgrade`.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit. A pinned one becomes 5.1.6 - no `v`, see the tag table.
# Name what you run: drop downloader or transcriber if you do not, and keep the
# --profile flags you start the stack with.
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Pull the downloader too: the download progress and the Bilibili resume below
are in the 5.1.6 downloader image, and an API on 5.1.6 next to an older one
counts files and loses cut-short Bilibili videos. The browser image changed only
in its version string, but it is tagged with `DTK_IMAGE_TAG`, so under a new tag
`up` would build it anyway - build it first, as above.

Four new migrations: the identity pool can hold Bilibili identities, a
download can be one part of a multi-part video, the request log is indexed by
proxy for the Logs page's proxy filter, and an identity keeps a note and the
account its last session check named. Your data is untouched.

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

  The other endpoints - likes, reposts, bookmarks, followers, following, mixes
  and a collection's detail - answer `UNSUPPORTED_CONTENT` on Bilibili. Its pool has its own marks,
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
- **`./dtkctl upgrade [VERSION]`.** An install made by the script upgrades with
  one command, to the latest release or to the one named: it updates the
  checkout, hands over to the installer that came with it, pulls (or, on an
  install built from source, rebuilds), migrates, restarts, and writes the
  version into `.env` itself. Upgrading used to mean editing `DTK_IMAGE_TAG` by
  hand and remembering the rest in order. `bash install.sh --upgrade` is the
  same without dtkctl; an install from before 5.1.6 gets the command on its
  first upgrade.
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
- **Notes on identities, and rotation per endpoint.** An identity keeps a
  note - whose account it is, where the cookie came from - set at import or in
  the drawer, shown in its own column of the identity table, in the Playground's
  picker and in the request log, carried by an export. The session check
  remembers the account id the platform named. The drawer's Rotation card
  chooses every endpoint, only the ticked ones (the endpoints that need a
  login are marked, and one button ticks just those), or only requests that
  name it; selected identities take one choice at once. The Scheduler page
  shows, per endpoint, how many identities can serve it and how many of them
  are logins.
- **API additions behind those pages:** `PATCH /api/v1/admin/api-keys/{id}`,
  `GET /api/v1/system/capabilities`, `q` on `GET /api/v1/downloads` and
  `/transcriptions`, `POST /api/v1/archive/export` for a selection,
  `proxy_id` on the request log, `platform` on `POST /admin/diagnose`, the
  downloader and transcriber in `GET /api/v1/system/status`,
  `PATCH /api/v1/admin/identities/{id}` for the note,
  `GET /api/v1/admin/identities/coverage`, `serves` and `ready` on the
  identity list, `identity_note` on the request log, and `login` /
  `paging_login` / `session_bound` on each capability. Also:
  `GET /api/v1/admin/proxies/{id}`; `reserved_active` on `/system/status`;
  `missing_scopes` on `GET /admin/demo` and `demo_owned` on API keys;
  `username` on audit rows and `before_id` to page them; `transcription` on
  every download and archived post and `stored.saved_at`;
  `PATCH /api/v1/admin/watchlist/{id}` for the `label`; and on the identity
  list `include_retired`, `offset`, `meta.total` and `expires_at`, with
  `reserved: true` on import.

### Changed

- **The uploader video list is the endpoint Bilibili guards hardest.** A burst
  of requests from one egress is answered with HTTP 412 pages for a few minutes,
  whatever the request looks like; it is reported as `UPSTREAM_RISK_CONTROL` and
  has the slowest token bucket of any Bilibili endpoint. Spread requests out,
  or add proxies.
- **Logins are kept for the endpoints that need them.** Endpoints a guest
  answers just as well now go to guest identities first, and to a login only
  when no guest can take the request (`pool.<platform>.guests_first`, on by
  default, a switch on the Scheduler page). Endpoints that need a login still
  prefer or require one. Turn it off to have logins share that traffic again.
- **Minting backs off per route.** A browser container that is down no longer
  stops the Bilibili pool from refilling. The identities pool panel says per
  platform whether it can be minted here (`can_mint` on each platform in
  `GET /api/v1/admin/identities/pool`).
- **The archive recheck and backfill count against the queue ceiling.** They
  were exempt as rare operator jobs while any read key could queue them; a
  bulk run of them can now answer `QUEUE_FULL` like every other read.
- **A Douyin author's likes go only to a login.** Measured on fifteen authors,
  the one with public likes gave a logged-in identity 20 posts and a next page,
  and three guests the zero-byte answer Douyin gives a private list. With no
  login in rotation the endpoint now answers `IDENTITY_LOGIN_REQUIRED` at once.
- **The install script explains running out of Docker address pools** on the
  first start, instead of leaving one line about subnets: what it means, and
  `docker network prune` or `default-address-pools` as the way out.

### Fixed

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
- **An instance without the browser container** was told on every Overview
  visit that it had no Douyin and no TikTok identity, with a hint to mint one it
  could not mint. A platform nothing will mint gets no alert now; the pool
  card says why it is empty.
- **"Keep it for requests that name it"** was the only rotation choice the
  console had, and turning it on to keep a login for the author feed took the
  login off the author feed. See the per-endpoint choice above.
- **A Douyin login imported from a browser was hidden as a spent session.**
  The Credential column read only `UIFID_TEMP`, which a mint yields; a browser's
  jar spells it `UIFID`, which the signer was already signing with. It now
  reads every spelling the signer does.
- **A Douyin author's likes read as private for every author.** Lookups went
  to guests, which Douyin tells nothing; see Changed. A login asking about a
  private list was answered `UPSTREAM_CHANGED` (502), asking to be reported as
  a schema change; it is `NOT_FOUND` now, as a guest's answer was.
- **An install built from source could not upgrade from the script's menu.**
  It pulled `dtk-app` from Docker Hub, which does not exist, and stopped. It now
  rebuilds its images from the updated checkout.
- **A watchlist entry's name lasted one interval.** Every run overwrote the
  label with the platform's nickname; a run now fills it only while it is empty.
- **The archive export ignored `collection` and `stored`**, which the console
  sent, and reading one archived post answered `stored: null` for everything,
  so a post opened from a link offered to save what was already saved.
- **Paging the audit trail skipped rows** that shared a timestamp - every row
  one request writes does - and every actor showed as a UUID.
- **The Settings demo card showed a running demo as off** to operators and
  viewers, who got a 403 it read as "off".
- **Bulk enable, disable and delete on Proxies stopped at the first failure**
  with a generic message and no word about the rows already changed. They run
  every row and report what was refused and why.
- **`dtk diagnose` called the downloader and transcriber "not configured"** on
  a deployment that runs both: the CLI built its context without them.
- **The coverage card's Can serve opened a longer list than its number**: the
  link also listed cooling and minting identities, and on Douyin's author likes,
  which only a login is sent to, the number counted every guest as well. Both
  now hold what rotation would send, and Cooling links to its own.
- **Example paths in Chinese read 未知 ("unknown")**: the API document's
  `DELETE /api/v1/downloads/{download_id}/files` and the `api.public_endpoints`
  setting's `GET /api/v1/{platform}/video` were filled in as template arguments,
  and every read of the settings logged one missing.

### Security

- **Queueing past the ceiling.** Any key with a platform read scope, or any
  viewer, could stack archive rechecks and backfills past `sched.queue_max`,
  each a run of real requests against the identity pool. See Changed above.
- **MCP calls made with the published demo key** were archived and logged like
  an operator's and kept past the demo retention window. They are demo tasks
  now, as the REST routes and the media tools already made them.
- **Every admin route is held to a role-aware guard by a test** that walks the
  mounted app: an admin route without one, or an admin write on anything but
  `manage_pool` or `admin_only`, fails the build. Each assertion was checked by
  reverting it.

---

## 本次更新

**B 站回来了。** v4 支持过它，v5 重写时把它放下了，因为它和抖音 / TikTok 不共用签名和身份
体系。这个版本把这两样都补上了：纯 Python 的 B 站 WBI 签名，以及通过普通 HTTP 铸造的
游客身份——所以 B 站的身份池不需要浏览器容器就能自己补齐。视频会带上可播放的 MP4，下载和
转写都和其他平台的作品一样，多 P 视频的某一 P 也可以。控制台的大部分页面也围绕最近几个版本新增的功能重新整理了一遍，
这个版本还加固了 5.1.5 安全审查在那次修复之外发现的三处问题。

### 升级方式

用脚本装的？`./dtkctl upgrade` 就是这个版本加的，所以只有这一次是在安装目录里跑两条命令：

```bash
cd /opt/dtk && git pull              # 或 ~/dtk —— 你装在哪就是哪
bash install/install.zh.sh --upgrade # 用英文脚本装的就是 install/install.sh
```

它会拉取 5.1.6、重建浏览器镜像、跑迁移、重启，并且自己把 `DTK_IMAGE_TAG=5.1.6` 写进 `.env`，
不需要手动改任何东西。从下个版本起，升级就只是 `./dtkctl upgrade`。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.6 —— 不带 v，见文末标签表。
# 写上你在跑的服务：没开下载器或转写的就去掉 downloader 或 transcriber，
# --profile 参数和启动时保持一致。
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

下载器也要拉：下面的下载进度和 B 站断点续传都在 5.1.6 的下载器镜像里，5.1.6 的 API 配旧下载器，
进度只会按文件数算，被截断的 B 站视频也救不回来。浏览器镜像只改了版本号字符串，但它的 tag 跟着
`DTK_IMAGE_TAG` 走，换了 tag 之后 `up` 反正也会构建它——所以像上面那样先构建。

新增四个数据库迁移：身份池可以存放 B 站身份，一条下载可以是多 P 视频的某一 P，请求日志按代理
建立索引供日志页按代理筛选，身份可以保存备注以及上一次检查登录时平台给出的账号。数据不受影响。

有两件事不会自动发生：

- **已有的 API Key 不会自动获得新的权限范围。** 要读取 B 站的 Key 需要 `bilibili:read`；在
  **访问控制 → API Key** 里修改，或者新建一把。控制台会话不需要任何操作。
- **演示实例公开的那把 Key** 带的是生成它的那个版本的权限范围。轮换一次演示账号（设置 → 演示模式），
  公开的 Key 就能读取 B 站了。

### 新增

- **B 站。** `/api/v1/bilibili/...`、MCP 和控制台都支持，输出和另外两个平台是同一套归一化
  结构：
  - `video`——单个视频，带可播放文件。B 站的详情里没有视频流，所以实例会再请求一次 `playurl`，
    这次请求以它自己的名字 `bilibili.content_streams` 记入日志；如果这次请求失败，详情照常
    返回。视频流是单个渐进式 MP4，游客在较早的投稿上最高 1080p，当前的投稿是 720p。
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

  其余接口——喜欢、转发、收藏、粉丝、关注、合集作品和合集详情——在 B 站上返回 `UNSUPPORTED_CONTENT`。B 站的
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
- **`./dtkctl upgrade [版本]`。** 用脚本装的实例一条命令就能升级，升到最新的 Release 或者指定的版本：
  它更新代码，交给随代码来的安装脚本接着做，拉取镜像（源码构建的安装则是重新构建）、跑迁移、重启，
  并且自己把版本号写进 `.env`。以前升级要手动改 `DTK_IMAGE_TAG`，其余步骤还得自己记着按顺序做。
  `bash install.zh.sh --upgrade` 是不经过 dtkctl 的同一件事；5.1.6 之前装的实例，第一次升级之后就有这条命令。
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
  - **调试台、总览、侧栏**——调试台会灰掉平台不支持的接口并填入真实示例，游客兜底的
    不完整结果会被标出，总览的卡片可以点击跳转，身份池按平台显示，侧栏的「工具」组拆成了
    「内容」和「开发者」。
  - **管理页**——API Key 可以编辑（缺少 `bilibili:read` 的 Key 会被标出），改角色和演示模式操作
    会先确认，设置页里有专门页面的项改为链接，日志可按接口、代理和操作人筛选且筛选写进 URL，
    代理可以批量测试并打开详情，诊断可以选平台，系统页显示下载器和转写服务的状态，调度页按平台
    统计，每个页面都会隐藏当前角色用不了的操作。
- **身份备注，以及按接口设置轮换。** 身份可以保存备注——是谁的账号、Cookie 从哪里来——导入时或在
  详情里填写，显示在身份表格的备注列、调试台的身份下拉框和请求日志里，导出时一并带走。检查登录时会记下
  平台给出的账号 ID。详情里的轮换卡片可以选择所有接口、只用于勾选的接口（需要登录的接口会标出来，
  一个按钮就能只勾选它们）或只服务点名它的请求；也可以选中多个身份一次设置。调度页按接口显示有多少
  身份可以服务它、其中多少是登录态。
- **这些页面背后新增的 API：** `PATCH /api/v1/admin/api-keys/{id}`、
  `GET /api/v1/system/capabilities`、`GET /api/v1/downloads` 和 `/transcriptions` 的 `q`、
  按选择导出的 `POST /api/v1/archive/export`、请求日志的 `proxy_id`、`POST /admin/diagnose`
  的 `platform`、`GET /api/v1/system/status` 里的下载器和转写服务、修改备注的
  `PATCH /api/v1/admin/identities/{id}`、`GET /api/v1/admin/identities/coverage`、身份列表的
  `serves` 和 `ready`、请求日志的 `identity_note`，以及每个能力项的 `login` / `paging_login` /
  `session_bound`。另外还有：`GET /api/v1/admin/proxies/{id}`；`/system/status` 的
  `reserved_active`；`GET /admin/demo` 的 `missing_scopes` 和 API Key 的 `demo_owned`；审计记录的
  `username` 以及翻页用的 `before_id`；每条下载和每个归档作品上的 `transcription`，以及
  `stored.saved_at`；修改名称的 `PATCH /api/v1/admin/watchlist/{id}` 的 `label`；身份列表的
  `include_retired`、`offset`、`meta.total` 和 `expires_at`，以及导入时的 `reserved: true`。

### 变更

- **UP 主投稿列表是 B 站防护最严的接口。** 同一个出口的突发请求会在几分钟内收到 HTTP 412
  拦截页，和请求长什么样无关；这会被报告为 `UPSTREAM_RISK_CONTROL`，它也是 B 站所有接口里
  令牌桶最慢的一个。请把请求分散开，或者加代理。
- **登录态留给需要它的接口。** 游客也能拿到同样结果的接口现在先用游客身份，只有没有游客能接下请求
  时才用登录态（`pool.<platform>.guests_first`，默认开启，开关在调度页）。需要登录的接口照旧优先或
  只用登录态。想让登录态重新分担这类流量，关掉它即可。
- **铸造按途径分别退避。** 浏览器容器挂掉不会再让 B 站的身份池停止补充。身份池面板会按平台
  说明能否在这里铸造（`GET /api/v1/admin/identities/pool` 里每个平台的 `can_mint`）。
- **归档重新检查和历史回溯计入队列上限。** 它们原来作为少见的运维任务被豁免，但任何读取 Key
  都能发起；批量发起时现在也会和其他读取一样收到 `QUEUE_FULL`。
- **抖音作者的喜欢列表只交给登录身份。** 实测十五个作者，唯一公开了喜欢列表的那个，登录身份拿到 20
  条和下一页，三个游客拿到的都是抖音回复私密列表时的零字节。轮换里没有登录身份时，这个接口现在
  立即返回 `IDENTITY_LOGIN_REQUIRED`。
- **安装脚本在第一次启动时会解释 Docker 地址池用完的情况**，而不是只留下一行讲子网的报错：说明
  是怎么回事，并给出 `docker network prune` 或 `default-address-pools` 两条出路。

### 修复

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
- **没有浏览器容器的实例**每次打开概览都会被告知「还没有抖音身份」「还没有 TikTok 身份」，还提示去
  铸造一个它根本铸造不了的身份。现在不会为任何不会被铸造的平台告警，身份池卡片会说明它为什么是空的。
- **「只服务点名它的请求」原来是控制台里唯一的轮换选项**，为了把登录态留给作者作品列表而打开它，反而
  让作者作品列表用不上这个登录态。见上面按接口设置轮换。
- **从浏览器导入的抖音登录态被当成会话已失效隐藏。**「会话凭据」一列只认铸造出来的 `UIFID_TEMP`，
  而浏览器导出的 Cookie 里叫 `UIFID`——签名器一直在用它签名。现在这一列和签名器认同样的几种写法。
- **每个抖音作者的喜欢列表看起来都是私密的。** 这类请求交给了游客，而抖音什么都不给游客；见「变更」。
  登录身份查一个私密列表时返回 `UPSTREAM_CHANGED`（502），要求当成平台结构变化去反馈；现在和以前
  游客拿到的一样，是 `NOT_FOUND`。
- **源码构建的安装没法从脚本菜单升级。** 它去 Docker Hub 拉 `dtk-app`，那个仓库根本不存在，于是停在
  那里。现在它用更新后的代码重新构建镜像。
- **关注列表条目的名称只能保持一个周期。** 每次运行都会用平台昵称覆盖它；现在只在名称为空时才填。
- **归档导出忽略了控制台传的 `collection` 和 `stored`**，读取单个归档作品时对所有作品都返回
  `stored: null`，所以从链接打开的作品会提示保存一个早就存过的东西。
- **审计记录翻页会漏行**：同一时间戳的记录——一次请求写的所有记录都是——会被跳过；操作人也都显示成 UUID。
- **设置页的演示卡片把正在运行的演示显示成关闭**：operator 和 viewer 拿到的是 403，卡片把它读成了「关闭」。
- **代理页的批量启用、停用和删除遇到第一个失败就停下**，只弹一个笼统的提示，也不说已经改了哪些行。
  现在每一行都会执行，并报告哪些被拒、为什么。
- **`dtk diagnose` 把下载器和转写服务说成「未配置」**，哪怕部署里两个都在跑：CLI 构建上下文时没带上它们。
- **覆盖卡片的「可分配」点进去的列表比数字长**：链接把冷却中和铸造中的身份也列了出来；抖音作者的喜欢列表只会派给登录态，
  数字却把所有游客也算了进去。现在数字和列表都只算轮换真正会派去的身份，「冷却中」也有自己的链接。
- **中文里的示例路径显示成「未知」**：API 文档里的 `DELETE /api/v1/downloads/{download_id}/files` 和设置项
  `api.public_endpoints` 说明里的 `GET /api/v1/{platform}/video` 被当成模板参数填了值，每次读取设置都会记一条缺参数的警告。

### 安全

- **绕过队列上限。** 任何带平台读取权限的 Key，或任何 viewer，都能把归档重新检查和历史回溯
  堆过 `sched.queue_max`，每一个都是一串真实请求，消耗身份池。见上面的「变更」。
- **用公开的演示 Key 通过 MCP 发起的调用**，原来会像运维人员的调用一样被归档、记录，并且保留
  得比演示保留期更久。现在它们是演示任务，和 REST 路由及媒体工具的处理一致。
- **每个管理路由都由一个测试守着角色守卫**：它遍历挂载好的应用，管理路由没有按角色判断的守卫，或者
  管理写操作用的不是 `manage_pool` 或 `admin_only`，构建就会失败。每一条断言都通过撤掉对应修改验证过。

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
