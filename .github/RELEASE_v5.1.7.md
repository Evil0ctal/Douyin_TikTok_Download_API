## What's new

**Ten new ways to read the platforms, and an upgrade that follows the
release.** Beside a post and its author you can now ask for the posts
recommended next to it, a sound and the posts that use it, a hashtag, keyword
search, an author's own series, a live room and the trending boards - on the
platforms that answer a guest for each. `./dtkctl upgrade` now moves the
install to the release it upgrades to, backs up before migrating, and puts
everything back if a step fails. The release also stops the browser service
spending a CPU core on autoplaying video nobody watches, and answers a deleted
Douyin post with not-found instead of risk control (#795).

### Upgrading

Installed with the script on 5.1.6, in the install directory:

```bash
./dtkctl upgrade
```

On 5.1.5 or earlier, `dtkctl` has no `upgrade` yet, so this once:

```bash
cd /opt/dtk && git pull           # or ~/dtk - wherever you installed it
bash install/install.sh --upgrade # install/install.zh.sh for the Chinese one
```

It checks out `v5.1.7`, pulls 5.1.7, rebuilds the browser image, backs the
database up, migrates, restarts and writes `DTK_IMAGE_TAG=5.1.7` into `.env`
itself; there is nothing to edit. If any step before the restart fails, the
checkout and `.env` go back to what they were and the running version keeps
running.

**With the browser profile, the browser image is rebuilt on CloakBrowser
0.6.3, which downloads its Chromium** - a few minutes on a small machine, so
pick a quiet moment. Earlier installers wrote CloakBrowser's version into
`.env`, where it stayed when the code moved on: an install from 5.1.3 or
earlier that upgraded to 5.1.6 with the script was built on CloakBrowser
0.5.10 against the Playwright 1.64 that only 0.6.3 is verified with. The
upgrade comments out the two values installers wrote (`f04c23da…` and
`fbf005bc…`), so the build takes the version `docker/compose.yml` pins. A
value you set yourself is kept, and the upgrade says so.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit. A pinned one becomes 5.1.7 - no `v`, see the tag table.
# If .env has CLOAKBROWSER_COMMIT=f04c23da... or =fbf005bc..., written by an
# older installer, delete that line: the version docker/compose.yml pins is the
# one the browser service is tested with.
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

No new migrations, and API keys need nothing: the new reads are under each
platform's existing `<platform>:read` scope. Your data is untouched.

### Added

- **Ten discovery reads**, on REST, the console's Playground and `dtk fetch`.
  Each was measured with a guest identity on 2026-10-20, and a platform that
  does not answer a guest is not offered rather than guessed at:
  - `video/related` - the posts each platform's own player lists next to a
    post, on all three. One batch, not a page.
  - `music` and `music/posts` - a sound by id or link, with how many posts use
    it and who published it, and the posts made with it. Douyin and TikTok.
  - `hashtag` - a hashtag by link or id, and on TikTok by name: its posts and
    views and the id its posts are listed by. `hashtag/posts` pages them on
    TikTok; Douyin answers a guest with nothing, so it is not offered there.
  - `search/posts` and `search/users` - keyword search, on TikTok and Bilibili.
    TikTok gives a guest a thin first page of posts and no second page of
    either, so post search prefers a signed-in identity and both need one to
    page. Douyin refuses guests search outright, so it is not offered.
  - `user/mixes` - the series an author published: Douyin's mixes, TikTok's
    playlists, Bilibili's seasons. `mix/posts` reads a Bilibili season too.
  - `live` - a live room by link or room: whether it is on, the broadcaster,
    and while it is live the viewer count and the pull URLs for your own
    player. Nothing records or proxies a stream.
  - `trending` - a whole trending board, ranked: Douyin's four (hot,
    entertainment, society, challenge) and Bilibili's hot search.
- **`/parse`, MCP `parse_url` and `dtk fetch` answer sound, hashtag and live
  links.** `dtk fetch` uses `/parse`'s own link table now, so the two answer
  the same links. The MCP tool set stays at ten.
- **`dtk config set <key> --stdin`** reads the value from standard input, so a
  secret setting never appears on a command line. The installer's settings menu
  uses it.
- **Installer presets for the version and the source.** `DTK_VERSION` installs
  or upgrades to `5.1.7`, `5.1`, `latest` or `sha-<commit>`; `DTK_REPO_URL`
  clones from a mirror. `--no-backup` skips the pre-migration backup.

### Changed

- **The installer follows releases, not `main`.** A fresh install checks out
  the latest release and pins `DTK_IMAGE_TAG` to it; an upgrade checks out the
  release it moves to and hands over to that release's own installer, so the
  steps that run are the ones written for it. A version that does not know a
  migration your database already has is refused before anything is pulled.
- **Upgrades back up first and put things back on failure**, as above.
- **The installer finds an existing install by its containers** - Docker's
  compose labels - instead of guessing at directories, and refuses a fresh
  install over volumes another one left behind, saying what to do. Before, a
  `--yes` run, a missing `python3` or stopped containers could start a second
  install with a new database password that the old volume would not accept.
- **CloakBrowser's version lives in `docker/compose.yml` only**, beside the
  Playwright it is tested with, and is no longer written into `.env`.
- **The installer's Docker advice is right for each distribution.** It named a
  package that does not exist on Debian and one Fedora rejects, and offered
  Docker's own installer on openSUSE, where it always refuses. Debian 12 and
  the RHEL family now get Docker's page for them, and openSUSE Leap 15 and
  Amazon Linux are told to add the Compose plugin their packages lack.

### Fixed

- **A deleted Douyin post is `NOT_FOUND`, not `UPSTREAM_RISK_CONTROL`** (#795).
  Douyin now answers some deleted posts with the post named and no reason, which
  was read as the platform withholding the payload - and every such request
  cooled the identity that made it. A reply naming the post the request asked
  for is an answer now, with or without a reason.
- **The browser service no longer plays the platforms' feeds to nobody.** Its
  warm signing pages are the home feeds, which autoplay, and an unused page
  played for as long as the service ran: the demo's held about one core of two
  for seven hours. A warm page now pauses its video and sits behind a blank tab
  once it can sign - around 4% for both, still signing in milliseconds - at
  the cost of one blank tab's memory each. You need do nothing beyond the
  rebuild the upgrade does.
- **Browser-signed TikTok requests match what they were signed over.** This
  only matters with `signing.mode` set to `rpc` or `auto`; the default,
  `native`, was never affected. The browser signer sent a space raw where the
  page had signed `%20`, and the query carried the page's own `msToken` while
  the Cookie header carried the identity's, so endpoints that check the seal
  answered with nothing. TikTok still refuses an author's post list signed in the
  browser - so does TikTok's own page - and native signing serves it.
- **The installer on small or unusual machines.** Hosts with about 4.6 to 7.6
  GiB of memory or a single core were given limits Docker refused to start. The
  menu's restore failed on the backup name it listed, listing settings exited
  the script, Ctrl-C answered prompts with their defaults, and freeing disk
  could remove images belonging to other projects.
- **TikTok tag links are names.** `/tag/2024` asked for hashtag number 2024
  rather than #2024. A live room that is not a number on Douyin or Bilibili,
  an empty @handle and a board the platform does not have are refused at once
  with a 400.
- **Bilibili's verified flag** read twenty ordinary accounts in twenty as
  verified in search results.

### Security

- **Secret settings stay off the command line.** The installer's settings menu
  passed values as arguments, where any local user could read them in the
  process list; it uses `dtk config set --stdin` now.
- **The installer no longer opens a home directory to everyone.** A model
  directory given as `~` had the whole home directory made world-readable,
  `.ssh` included. It now opens only the model directory and its three model
  folders, and refuses a non-empty directory that is not a model directory.
- **The installer no longer runs a `dtkctl` it merely found.** Looking for an
  install in the current directory or the script's own would run any file
  called `dtkctl` there, under `curl | bash` too. A directory it finds by itself
  now has to be a checkout of this project with a `dtkctl` owned by you or root
  that nobody else can write.

---

## 本次更新

**十种新的读取方式，以及跟着 release 走的升级。** 除了作品和作者，现在还能查一个作品旁边推荐的作品、
一段声音和用它的作品、话题、关键词搜索、作者自己的合集、直播间和热榜——每个平台只开放游客实测能拿到的那部分。
`./dtkctl upgrade` 现在会把安装切到要升级到的那个 release，迁移前先备份，任何一步失败都会恢复原样。
这个版本还让浏览器服务不再为没人看的自动播放视频占掉一个 CPU 核，并把已删除的抖音作品答成"不存在"，
不再报风控（#795）。

### 升级方式

用脚本装的 5.1.6，在安装目录里：

```bash
./dtkctl upgrade
```

5.1.5 及更早的版本，`dtkctl` 里还没有 `upgrade`，这一次要这样：

```bash
cd /opt/dtk && git pull           # 或 ~/dtk，装在哪就在哪
bash install/install.zh.sh --upgrade # 英文版用 install/install.sh
```

它会 checkout `v5.1.7`、拉取 5.1.7、重建浏览器镜像、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.7`
写进 `.env`，不需要手改任何东西。重启之前任何一步失败，代码和 `.env` 都会恢复原样，正在运行的版本继续运行。

**开了浏览器 profile 的，浏览器镜像会按 CloakBrowser 0.6.3 重建，要重新下载 Chromium**，小机器上要几分钟，
挑个空闲时段。以前的安装脚本把 CloakBrowser 的版本写进了 `.env`，代码更新后它还留在那里：5.1.3 及更早装的、
又用脚本升到 5.1.6 的，构建出来的是 CloakBrowser 0.5.10 配 Playwright 1.64，而这个 Playwright 只和 0.6.3 一起验证过。
这次升级会把安装脚本写进去的那两个值（`f04c23da…` 和 `fbf005bc…`）注释掉，构建改用 `docker/compose.yml` 钉住的版本。
你自己设的值会保留，升级时会提示。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.7 —— 不带 v，见文末标签表
# 如果 .env 里有旧安装脚本写的 CLOAKBROWSER_COMMIT=f04c23da... 或 =fbf005bc...，
# 删掉这一行：docker/compose.yml 钉住的版本才是浏览器服务测过的那个。
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

没有新的迁移，API Key 也不用动：新的读取接口都在各平台原有的 `<平台>:read` 权限下。数据不受影响。

### 新增

- **十个发现类接口**，REST、控制台的 Playground 和 `dtk fetch` 都能用。每个都在 2026-10-20 用游客身份实测过，
  对游客不返回数据的平台就不开放，不靠猜：
  - `video/related` —— 各平台自己的播放器在一个作品旁边列出的推荐作品，三个平台都有。一次一批，不分页。
  - `music` 和 `music/posts` —— 按 ID 或链接查一段声音，包括有多少作品在用、谁发布的，以及用它的作品。抖音和 TikTok。
  - `hashtag` —— 按链接或 ID 查话题，TikTok 还能按名字查：作品数、播放量，以及列它的作品要用的 ID。
    `hashtag/posts` 在 TikTok 上分页列出这些作品；抖音对游客什么都不返回，所以那边不开放。
  - `search/posts` 和 `search/users` —— 关键词搜索，TikTok 和 B 站。TikTok 给游客的作品第一页很少，两种搜索都翻不了页，
    所以作品搜索优先用已登录的身份，两种搜索翻页都需要登录。抖音直接拒绝游客搜索，所以不开放。
  - `user/mixes` —— 作者自己发布的合集：抖音的合集、TikTok 的播放列表、B 站的合集。`mix/posts` 也能读 B 站合集。
  - `live` —— 按链接或房间号查直播间：是否在播、主播，开播时还有在看人数和拉流地址，给你自己的播放器用。
    不会录制或代理任何直播流。
  - `trending` —— 整张热榜，按排名：抖音的四张（热点、娱乐、社会、挑战）和 B 站的热搜。
- **`/parse`、MCP 的 `parse_url` 和 `dtk fetch` 都能解析声音、话题和直播间链接。** `dtk fetch` 现在直接用 `/parse`
  的链接表，两边能解析的链接始终一致。MCP 工具仍然是十个。
- **`dtk config set <key> --stdin`** 从标准输入读取值，密钥类设置不会出现在命令行上。安装脚本的设置菜单用的就是它。
- **安装脚本可以预设版本和来源。** `DTK_VERSION` 指定安装或升级到 `5.1.7`、`5.1`、`latest` 或 `sha-<提交>`；
  `DTK_REPO_URL` 从镜像仓库克隆。`--no-backup` 跳过迁移前的备份。

### 变更

- **安装脚本跟着 release 走，不再跟 `main`。** 全新安装会 checkout 最新的 release，并把 `DTK_IMAGE_TAG` 钉成它；
  升级会 checkout 目标 release，再交给那个 release 自带的安装脚本接着做，所以执行的是为它写的步骤。
  如果目标版本不认识数据库已经做过的某个迁移，会在拉取任何东西之前就拒绝。
- **升级先备份，失败自动恢复**，见上文。
- **安装脚本按容器找已有的安装**（用 Docker compose 的标签），不再猜目录；遇到别的安装留下的数据卷会拒绝全新安装，
  并说明怎么处理。以前用了 `--yes`、没装 `python3` 或容器被停掉时，会开始第二次安装，生成一个旧数据卷不认的新数据库密码。
- **CloakBrowser 的版本只写在 `docker/compose.yml` 里**，和与它一起测过的 Playwright 放在一起，不再写进 `.env`。
- **安装脚本给出的 Docker 安装方法对每个发行版都对了。** 以前在 Debian 上写了一个不存在的包，Fedora 会拒绝那条命令，
  还在 openSUSE 上提议跑一个在那里必然拒绝的官方安装脚本。Debian 12 和 RHEL 系现在会指向 Docker 对应的安装页面，
  openSUSE Leap 15 和 Amazon Linux 会提示补装系统包里缺的 Compose 插件。

### 修复

- **已删除的抖音作品返回 `NOT_FOUND`，不再是 `UPSTREAM_RISK_CONTROL`**（#795）。抖音现在对部分已删除的作品
  只返回作品 ID、不给原因，以前被当成平台扣下了数据，每次这样的请求都会让发起它的身份进入冷却。
  现在只要回答里指明的就是请求的那个作品，不管有没有原因都算平台的答复。
- **浏览器服务不再给没人看的信息流放视频。** 它的预热签名页就是平台首页信息流，会自动播放，没人用的页面会一直播下去：
  demo 上的那个在 2 核里占了约一个核，持续了七个小时。现在预热页一能签名就暂停视频、并放到一个空白标签页后面——
  两个页面合计约 4%，签名照样是毫秒级——代价是每个页面多一个空白标签页的内存。除了升级时那次重建，你什么都不用做。
- **浏览器签名的 TikTok 请求和它签的内容一致了。** 只影响把 `signing.mode` 设为 `rpc` 或 `auto` 的情况，
  默认的 `native` 从来不受影响。浏览器签名把页面签成 `%20` 的空格原样发了出去，而且查询串里是页面自己的
  `msToken`，Cookie 头里却是身份的那个，所以校验签名的接口什么都不返回。TikTok 仍然拒绝在浏览器里签名的作者作品列表——
  TikTok 自己的页面也一样被拒——这个接口由 native 签名提供。
- **安装脚本在小内存或少见配置的机器上的问题。** 内存约 4.6 到 7.6 GiB、或者只有一个核的机器，拿到的资源限制
  Docker 会拒绝启动。菜单里照列出的文件名恢复备份会失败，列出设置项会让脚本退出，按 Ctrl-C 会用默认答案继续，
  清理磁盘可能删掉别的项目的镜像。
- **TikTok 话题链接按名字处理。** `/tag/2024` 以前查的是 2024 号话题，而不是 #2024。抖音和 B 站上不是数字的直播间号、
  空的 @handle 和平台没有的榜单会直接返回 400。
- **B 站认证标记**：搜索结果里二十个普通账号有二十个被读成了已认证。

### 安全

- **密钥类设置不再出现在命令行上。** 安装脚本的设置菜单以前把值当作参数传，本机任何用户都能在进程列表里看到；
  现在改用 `dtk config set --stdin`。
- **安装脚本不再把家目录开放给所有人。** 模型目录填 `~` 时，以前会把整个家目录（包括 `.ssh`）改成所有人可读。
  现在只开放模型目录和其中三个模型子目录，并且拒绝一个不是模型目录的非空目录。
- **安装脚本不再运行它碰巧找到的 `dtkctl`。** 以前在当前目录或脚本所在目录找安装时，那里任何一个叫 `dtkctl`
  的文件都会被执行，用 `curl | bash` 时也一样。现在它自己找到的目录必须是本项目的代码，而且 `dtkctl`
  的属主是你或 root、其他人不能写入。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.7` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.7` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-203e08fc33278aa3d17bfb09a06e4fcfd3e2c43c` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.7` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.7`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.6...v5.1.7
