## What's new

**A pool with proxies now refills past one identity per platform, and the
browser image can be built where GitHub is out of reach.** Adding a proxy no
longer takes the machine's own IP out of minting: it counts as one more exit,
and the refill card stops drawing a wait for a free exit as a row of failures.
TikTok author counters now come from the exact copy TikTok sends. The browser
image's Chromium download can go through a mirror you name - with what that
costs spelled out - and the install README now says how to run an install day
to day.

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

It checks out `v5.1.9`, pulls 5.1.9, backs the database up, migrates, restarts
and writes `DTK_IMAGE_TAG=5.1.9` into `.env` itself; there is nothing to edit.
CloakBrowser stays at the version pinned since 5.1.7. If rebuilding the browser
image stops at `Downloading from https://cloakbrowser.dev/...`, see the mirror
option under Added.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit; a pinned one becomes 5.1.9 - no `v`, see the tag table
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

**One thing to check.** If you have proxies configured and this machine cannot
reach a platform directly - TikTok from mainland China is the usual case - turn
that platform's `pool.<platform>.direct_egress` off on the Scheduler page after
upgrading. It is on by default, and without a direct route every direct mint
fails and puts automatic refill into backoff. It takes effect without a
restart.

No new migrations, and API keys need nothing. Your data is untouched.

### Added

- **A download mirror for the browser's Chromium.** Building the browser image
  downloads Chromium from `cloakbrowser.dev`, which redirects to a GitHub
  release; a build host that reaches neither stopped there (#803), and
  `DTK_GH_PROXY` does not cover that step. A build-time proxy is still the
  first choice. Where there is none, `CLOAKBROWSER_DOWNLOAD_MIRROR` in `.env`
  points the download at a mirror - but CloakBrowser does not check the
  release signature on a mirror, so a mirror can hand over any browser. Use
  only one you run or trust. The installation guide's new section, "A download
  mirror for the browser binary, and what it risks", gives the value to set.
- **Day-to-day `dtkctl` in the install README:** the everyday commands, why a
  bare `docker compose` gets them wrong, what comes back on its own after a
  reboot, and how to reach the console from another machine (#784).

### Changed

- **With proxies configured, the machine's own IP is one more exit.** Up to
  5.1.8, adding a single proxy took the direct egress out of minting
  altogether, so automatic refill stopped at one identity per platform per
  proxy (#807). Now, once every proxy carries an identity of a platform,
  refill mints one more on the machine's IP; like a proxy it carries only one,
  and an identity imported with "No proxy" already occupies it. It is switched
  per platform by `pool.douyin.direct_egress`, `pool.tiktok.direct_egress` and
  `pool.bilibili.direct_egress` on the Scheduler page, all on by default. With
  no proxies at all, nothing changes.
- **Dependencies:** FastAPI 0.162, Pydantic 2.14, SQLAlchemy 2.1.6, wreq 0.13
  and the MCP SDK 2.3; the console now builds with TypeScript 6. Nothing to do.

### Fixed

- **TikTok author counters come from `statsV2`.** A profile and the follower
  and following lists read the numeric `stats` block whenever it was present -
  which is always - so the exact copy TikTok sends beside it in `statsV2` was
  never read (#804). They now read `statsV2` first, field by field, with
  `stats` as the fallback, as posts and hashtags already did. Nothing to do.
- **The refill card no longer draws a wait as failures.** When every exit
  already carried an identity of a platform, each minute's skip was recorded
  as a failed mint, and the card showed "20 of the last 20 failed" in red for
  a pool that had simply run out of exits. A skip now shows as a grey "refill
  paused" line with the reason on hover, and the real attempts keep their
  place in the history. Entries an older worker wrote are left out of the bars.

### Security

- **`CLOAKBROWSER_DOWNLOAD_MIRROR` turns a verification step off.** Unset - the
  default - nothing changes and the Chromium download is checked against
  CloakBrowser's signature. Set, the archive is compared only with whatever
  `SHA256SUMS` the mirror serves, or with nothing; the build log says so when
  it happens.

---

## 本次更新

**配了代理的身份池不再卡在每个平台一个身份，连不上 GitHub 的机器也能构建浏览器镜像了。** 加了代理之后，本机 IP 不再被排除在铸造之外，
而是多算一个出口；自动补充卡片也不再把"等出口空出来"画成一排失败。TikTok 作者的计数改为读取 TikTok 给出的精确值。浏览器镜像下载
Chromium 这一步可以改走你指定的镜像（代价写清楚了），安装 README 也补上了日常怎么管理一套安装。

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

它会 checkout `v5.1.9`、拉取 5.1.9、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.9` 写进 `.env`，
不需要手改任何东西。CloakBrowser 的版本仍是 5.1.7 起钉的那个。如果重建浏览器镜像停在
`Downloading from https://cloakbrowser.dev/...`，见「新增」里的镜像选项。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.9 —— 不带 v，见文末标签表
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

**有一件事要检查。** 如果你配了代理，而这台机器直连不到某个平台——最常见的是国内服务器上的 TikTok——升级后请在调度器页把
这个平台的 `pool.<平台>.direct_egress` 关掉。它默认开启；没有直连的路，每次直连铸造都会失败，并让自动补充进入退避。
改完立即生效，不用重启。

没有新的迁移，API Key 也不用动。数据不受影响。

### 新增

- **浏览器的 Chromium 可以走下载镜像。** 构建浏览器镜像时要从 `cloakbrowser.dev` 下载 Chromium，它会跳转到 GitHub
  Release；两处都连不上的构建机会停在这一步（#803），而 `DTK_GH_PROXY` 管不到它。首选仍然是构建时代理。没有代理时，
  可以在 `.env` 里设 `CLOAKBROWSER_DOWNLOAD_MIRROR`，让这次下载走镜像——但走镜像时 CloakBrowser 不再校验发布签名，
  镜像想给你什么浏览器都行。只用你自己运营或信得过的镜像。安装文档新增的「浏览器二进制的下载镜像，以及它的风险」一节
  写了该填什么值。
- **安装 README 补上了日常用 `dtkctl` 的说明：** 常用命令、为什么别直接敲 `docker compose`、机器重启后会不会自己起来，
  以及怎么从别的机器访问控制台（#784）。

### 变更

- **配了代理时，本机 IP 也算一个出口。** 5.1.8 及以前，只要加一个代理，直连出口就整个不参与铸造了，所以自动补充每个平台、
  每个代理只能补到一个身份（#807）。现在每个代理后面都已经有一个该平台的身份之后，补充会再在本机 IP 上铸造一个；和代理一样
  只放一个，导入时选了「不用代理」的身份已经占着它。按平台开关：调度器页的 `pool.douyin.direct_egress`、
  `pool.tiktok.direct_egress` 和 `pool.bilibili.direct_egress`，默认都开着。完全没配代理的安装没有任何变化。
- **依赖更新：** FastAPI 0.162、Pydantic 2.14、SQLAlchemy 2.1.6、wreq 0.13、MCP SDK 2.3；控制台改用 TypeScript 6
  构建。不用做任何事。

### 修复

- **TikTok 作者的计数改读 `statsV2`。** 作者主页、粉丝列表和关注列表只要有数字版的 `stats` 就用它——而它总是在——所以 TikTok
  放在旁边 `statsV2` 里的精确值从来没被读到（#804）。现在按字段先读 `statsV2`，读不到再用 `stats`，和作品、话题一样。
  不用做任何事。
- **自动补充卡片不再把"等待"画成失败。** 每个出口都已经有该平台的身份时，每分钟一次的跳过都被记成一次铸造失败，卡片上
  红色地显示「最近 20 次中有 20 次失败」，其实只是出口用完了。现在跳过显示为一行灰色的「暂停补充」，悬停能看原因，真正的
  铸造记录也不会被挤掉。旧版本 worker 写下的这类记录不再画进图里。

### 安全

- **`CLOAKBROWSER_DOWNLOAD_MIRROR` 会关掉一道校验。** 不设（默认）时一切照旧，Chromium 的下载要通过 CloakBrowser 的签名
  校验。设了之后，压缩包只和镜像自己给的 `SHA256SUMS` 比对，镜像不给就什么都不比；构建日志里会写明这一点。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.9` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.9` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-SHA_FILLED_IN_AFTER_TAGGING` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.9` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.9`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Thanks / 感谢**: @huynk-04 (#804), @815074104 (#807), @duiaic (#803) and @Readedd (#784), for reports that pointed straight at the problem.

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.8...v5.1.9
