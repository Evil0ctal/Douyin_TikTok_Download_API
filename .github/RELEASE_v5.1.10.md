## What's new

**Fixes for what the demo's traffic turned up.** Under load an instance could
run out of database connections and answer with 500s; guest identities the
platforms had flagged were never retired, and the risk-control answers they kept
collecting tripped the circuit breakers for everyone; comments on a post hidden
from all but its author came back as a 502; and TikTok's post search, which now
refuses guests, answered "not found". All four are fixed, and the console's
overview no longer polls an endpoint the demo account cannot read.

### Upgrading

Installed with the script on 5.1.6 or later, in the install directory:

```bash
./dtkctl upgrade
```

On 5.1.5 or earlier, `dtkctl` has no `upgrade` yet, so this once:

```bash
cd /opt/dtk && git pull           # or ~/dtk - wherever you installed it
bash install/install.sh --upgrade # install/install.zh.sh for the Chinese one
```

It checks out `v5.1.10`, pulls 5.1.10, backs the database up, migrates, restarts
and writes `DTK_IMAGE_TAG=5.1.10` into `.env` itself; there is nothing to edit.
The postgres container is recreated once, because it now starts with
`max_connections=100`; its data volume is untouched. CloakBrowser and the
browser image are unchanged.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit; a pinned one becomes 5.1.10 - no `v`, see the tag table
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

**One thing to know.** Within five minutes of starting, the worker retires the
minted guest identities that have failed ten or more times in a row - on a busy
pool that can be hundreds at once - and the pool refills with fresh ones. If you
would rather keep every identity until you retire it yourself, set
`pool.retire_fail_streak` to 0 on the Scheduler page. Imported identities are
never retired this way.

No new migrations, and API keys need nothing. Your data is untouched.

### Added

- **`DTK_DB_POOL_SIZE` and `DTK_DB_MAX_OVERFLOW`** (5 and 5): how many database
  connections each of the api and the worker keeps open, and how many more it
  may open under load before a request waits for one. Defaults suit every
  install; change them only if the diagnosis tells you to.
- **A connection check in the diagnosis.** Step 1 now compares what the api and
  the worker can open between them with Postgres' `max_connections`, and warns
  with both numbers when they do not fit.
- **`pool.retire_fail_streak`** (10): the failure streak at which a minted guest
  identity is retired automatically. See Changed.

### Changed

- **Minted guests a platform keeps refusing are retired.** Nothing retired an
  identity on its own: one the platform had flagged cooled, came back, failed
  and cooled again for ever. On the demo, 1273 of 1288 live Douyin identities
  had failed three or more times in a row and about 600 were still used each
  day, and their risk-control answers tripped the circuit breakers for the
  requests healthy identities could have served. The worker now retires minted
  guests at a streak of `pool.retire_fail_streak`, every five minutes - never an
  imported identity, and never on a platform with no healthy identity left,
  since then the platform is refusing everyone. 0 turns it off.
- **TikTok's post search needs a signed-in identity.** Since 2026-11-02 it
  refuses a guest outright (`status_code 2483`, "Please login your account
  first"). With no login in rotation, `/api/v1/tiktok/search/posts` answers
  `IDENTITY_LOGIN_REQUIRED` at once instead of spending a request. The account
  search still answers guests and is unchanged. To search posts, import a
  logged-in TikTok identity.

### Fixed

- **"too many clients already" under load.** Each process's pool could grow to
  30 connections, and TimescaleDB's image tunes Postgres to 25 inside the 1 GB
  an installer gives a small host. The pools are smaller and configurable, and
  `docker/compose.yml` now sets `max_connections=100` on the postgres command
  line, which overrides the tuned value for an existing data directory as well.
- **Comments on a post hidden from all but its author.** Douyin answers those
  with `comments: null`, which was reported as a changed schema - a 502. It is
  an empty page now.
- **A login wall read as a missing post.** A platform's 2483 ("log in first") -
  TikTok's post search and Douyin's search both use it - is reported as
  `IDENTITY_LOGIN_REQUIRED` instead of `NOT_FOUND`, and is not held against the
  guest that met it.
- **The console's overview polled the watchlist for the demo account**, which
  cannot read it, and was refused on every poll. The tile now shows only to
  viewers and up, like the transcriptions tile beside it.

---

## 本次更新

**修复演示站流量里暴露出来的问题。** 高负载时实例会用光数据库连接、返回 500；被平台识破的游客身份从来不被退休，它们不断拿到的风控结果会把整个接口熔断，连累所有人；仅作者可见的视频，评论接口返回 502；TikTok 作品搜索现在拒绝游客，却被报成"不存在"。这四个问题都修好了，控制台总览页也不再轮询 demo 账号无权访问的接口。

### 升级方式

用脚本装的 5.1.6 及以后的版本，在安装目录里：

```bash
./dtkctl upgrade
```

5.1.5 及更早的版本，`dtkctl` 里还没有 `upgrade`，这一次要这样：

```bash
cd /opt/dtk && git pull           # 或 ~/dtk，装在哪就在哪
bash install/install.zh.sh --upgrade # 英文版用 install/install.sh
```

它会 checkout `v5.1.10`、拉取 5.1.10、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.10` 写进 `.env`，
不需要手改任何东西。postgres 容器会重建一次，因为它现在带着 `max_connections=100` 启动；数据卷不受影响。
CloakBrowser 和浏览器镜像都没有变化。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.10 —— 不带 v，见文末标签表
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

**有一件事要知道。** 启动后五分钟内，worker 会把连续失败 10 次以上的机器铸造游客身份退休——繁忙的身份池可能一下子退休几百个——之后补充任务会铸造新的补上。如果你想保留所有身份、等自己手动退休，请在调度器页把 `pool.retire_fail_streak` 设为 0。导入的身份不会这样被退休。

没有新的迁移，API Key 也不用动。数据不受影响。

### 新增

- **`DTK_DB_POOL_SIZE` 和 `DTK_DB_MAX_OVERFLOW`**（默认 5 和 5）：api 和 worker 各自常驻的数据库连接数，以及高负载时最多再多开多少个，再多就排队等连接。默认值适合所有安装，只有自检提示时才需要改。
- **自检新增连接检查。** 第 1 步会比较 api 和 worker 合计能开的连接数与 Postgres 的 `max_connections`，超出时给出警告并写明两个数字。
- **`pool.retire_fail_streak`**（默认 10）：机器铸造的游客身份连续失败多少次后自动退休。见「变更」。

### 变更

- **被平台持续拒绝的游客身份会被自动退休。** 以前系统从不自动退休身份：被平台识破的身份冷却、回来、失败、再冷却，永远循环。演示站上 1288 个存活的抖音身份里有 1273 个连续失败了 3 次以上，每天仍有约 600 个在被使用，它们拿到的风控结果把接口熔断，连累了本来可以由健康身份服务的请求。现在 worker 每五分钟会退休连续失败达到 `pool.retire_fail_streak` 次的机器铸造游客身份——从不碰导入的身份；某个平台已经没有健康身份时也一个都不退休，因为那说明平台在拒绝所有人。设为 0 关闭。
- **TikTok 作品搜索需要已登录身份。** 2026-11-02 起它直接拒绝游客（`status_code 2483`，"Please login your account first"）。轮换里没有登录身份时，`/api/v1/tiktok/search/posts` 会立即返回 `IDENTITY_LOGIN_REQUIRED`，不再白白发一次请求。账号搜索游客照样能用，没有变化。要搜索作品，请导入一个已登录的 TikTok 身份。

### 修复

- **高负载时报 "too many clients already"。** 每个进程的连接池最多能涨到 30 个连接，而 TimescaleDB 镜像在安装脚本给小机器的 1 GB 内存里会把 Postgres 调成 25 个连接。现在连接池更小、可配置，`docker/compose.yml` 也在 postgres 的启动命令里设了 `max_connections=100`，对已有的数据目录同样生效。
- **仅作者可见的视频的评论。** 抖音对这类视频返回 `comments: null`，以前被报成接口结构变化（502），现在返回空页。
- **"需要登录"被当成"不存在"。** 平台返回的 2483（"请先登录"，TikTok 作品搜索和抖音搜索都用它）现在报 `IDENTITY_LOGIN_REQUIRED`，不再报 `NOT_FOUND`，也不会算在碰到它的游客身份头上。
- **控制台总览页替 demo 账号轮询 watchlist**，而 demo 账号无权读取，每次轮询都被拒绝。这张卡片现在只对 viewer 及以上显示，和旁边的转写卡片一样。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.10` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.10` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-SHA_FILLED_IN_AFTER_TAGGING` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.10` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.10`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.9...v5.1.10
