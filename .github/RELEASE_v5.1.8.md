## What's new

**Three more live reads, and an installer that can clone through a mirror you
choose.** A live room can now be asked for by its broadcast's own id, and a
Douyin app share link to a live room is understood; up to 100 rooms can be
checked for being on air in one request on Douyin and TikTok; and a Douyin
room's audience ranking can be read. For servers that cannot reach GitHub, the
installer takes an `https://` mirror prefix - GitHub stays the default, and it
asks before cloning from anywhere else. The console also says, before a login
is imported, that the account could be banned.

### Upgrading

Installed with the script on 5.1.6 or 5.1.7, in the install directory:

```bash
./dtkctl upgrade
```

On 5.1.5 or earlier, `dtkctl` has no `upgrade` yet, so this once:

```bash
cd /opt/dtk && git pull           # or ~/dtk - wherever you installed it
bash install/install.sh --upgrade # install/install.zh.sh for the Chinese one
```

It checks out `v5.1.8`, pulls 5.1.8, backs the database up, migrates, restarts
and writes `DTK_IMAGE_TAG=5.1.8` into `.env` itself; there is nothing to edit.
CloakBrowser stays at the version 5.1.7 pinned.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit; a pinned one becomes 5.1.8 - no `v`, see the tag table
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

No new migrations, no new settings, and API keys need nothing: the new reads
are under each platform's existing `<platform>:read` scope. Your data is
untouched.

### Added

- **A live room by its broadcast's own id, and Douyin app share links.**
  `GET /api/v1/{platform}/live` takes `live_id` beside `url` and `room_id`, on
  Douyin and TikTok. A Douyin app share link to a live room - the
  `v.douyin.com` form, or what it expands to - is answered by `/live`,
  `/parse`, `dtk fetch` and MCP `parse_url`. Every live room answer that finds
  the room on air now carries its `live_id`, which is what the two reads below
  take. Bilibili numbers a room only once and answers `live_id` with
  `UNSUPPORTED_CONTENT`.
- **Whether up to 100 rooms are on air, in one request.**
  `GET /api/v1/{platform}/live/status?live_ids=<a>,<b>,...` on Douyin and
  TikTok answers a `{live_id, is_live}` for each broadcast, each in its own
  place in the order asked. A broadcast that has ended and an id that never
  was one are both `is_live: false`. It costs one request however many are
  asked about: poll it, and ask `/live` for the room that has just come on.
- **A Douyin room's audience ranking.**
  `GET /api/v1/douyin/live/ranking?live_id=` answers up to 200 viewers, top
  first, as `{rank, author}`. No score is returned: a guest is sent 0 for
  every one, and a 0 would read as a fact. A viewer who chose not to be listed,
  or whom the platform names to a guest only as a masked nickname, is a place
  with a null `author`.
- All three answer a guest identity (measured 2026-10-26) and are in the
  console's Playground. Their per-identity rate limits are in the identities
  guide.
- **`DTK_GH_PROXY` for the installer**, for servers in mainland China that
  cannot reach GitHub: one `https://` prefix put in front of the GitHub
  addresses the installer uses. GitHub stays the default and no mirror is
  built in. The script you download first does not go through it - prefix that
  download by hand. The installation guide has a new section,
  "Cloning from a mirror, and what it risks", on what a mirror can change.

### Changed

- **The installer asks before cloning from anywhere but GitHub.** A mirror,
  whether set with `DTK_GH_PROXY` or `DTK_REPO_URL`, hands over all of the
  code, `docker/compose.yml` and so which images run, and every later upgrade,
  since the checkout keeps it as its origin. The installer now says the full
  address and what it hands over, and a bare Enter means no. Under `--yes`,
  setting the variable is the answer. An existing checkout is not asked: it
  fetches from its own origin, as before.
- **The console says before a login is imported that the account could be
  banned.** Platforms rarely ban an account for reading data with it, but a ban
  cannot be ruled out. The checkbox a logged-in cookie jar already needed now
  accepts that risk as well. A guest jar still asks for nothing. The import
  endpoint's docs, the identities guide, the FAQ and the README say the same.

---

## 本次更新

**又多了三个直播读取接口，安装脚本也能走你自己选的镜像了。** 现在可以按一场直播自己的 ID 查直播间，也能识别抖音 App
分享出来的直播链接；抖音和 TikTok 上一次请求能查最多 100 个直播间是否在播；还能读抖音直播间的观众榜。连不上 GitHub
的服务器可以给安装脚本一个 `https://` 镜像前缀——默认仍然直连 GitHub，换成别的来源之前会先问你。控制台在导入登录态之前，
也会说明账号有被封的可能。

### 升级方式

用脚本装的 5.1.6 或 5.1.7，在安装目录里：

```bash
./dtkctl upgrade
```

5.1.5 及更早的版本，`dtkctl` 里还没有 `upgrade`，这一次要这样：

```bash
cd /opt/dtk && git pull           # 或 ~/dtk，装在哪就在哪
bash install/install.zh.sh --upgrade # 英文版用 install/install.sh
```

它会 checkout `v5.1.8`、拉取 5.1.8、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.8` 写进 `.env`，
不需要手改任何东西。CloakBrowser 的版本和 5.1.7 钉的一样。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.8 —— 不带 v，见文末标签表
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

没有新的迁移，没有新的设置项，API Key 也不用动：新的读取接口都在各平台原有的 `<平台>:read` 权限下。数据不受影响。

### 新增

- **按一场直播自己的 ID 查直播间，以及抖音 App 分享链接。** `GET /api/v1/{platform}/live` 在 `url` 和 `room_id`
  之外多了 `live_id`，抖音和 TikTok 可用。抖音 App 分享出来的直播链接——`v.douyin.com` 的短链，或者它展开后的地址——
  `/live`、`/parse`、`dtk fetch` 和 MCP 的 `parse_url` 都能识别。直播间正在直播时，返回里现在都带 `live_id`，
  下面两个接口要的就是它。B 站的直播间只有一个房间号，传 `live_id` 会返回 `UNSUPPORTED_CONTENT`。
- **一次请求查最多 100 个直播间是否在播。** `GET /api/v1/{platform}/live/status?live_ids=<a>,<b>,...` 在抖音和
  TikTok 上为每场直播返回一条 `{live_id, is_live}`，按请求的顺序、各在各的位置。已结束的直播和根本不存在的 ID
  都是 `is_live: false`。不管查多少个都只算一次请求：定时轮询它，发现哪个刚开播，再用 `/live` 去查那一个。
- **抖音直播间的观众榜。** `GET /api/v1/douyin/live/ranking?live_id=` 按名次返回最多 200 名观众，格式是
  `{rank, author}`。不返回分值：游客拿到的分值全是 0，返回一个 0 会被当成事实。选择不上榜的观众，以及平台只给游客
  打码昵称的观众，那一名的 `author` 是 null。
- 这三个接口用游客身份都能拿到数据（2026-10-26 实测），控制台的 Playground 里都有。每个身份的限流见身份文档。
- **安装脚本支持 `DTK_GH_PROXY`**，给连不上 GitHub 的国内服务器用：一个 `https://` 前缀，加在安装脚本用到的 GitHub
  地址前面。默认仍然直连 GitHub，脚本里不内置任何镜像。第一步下载脚本本身不经过它，那一步要手动加前缀。安装文档新增了
  "用镜像克隆，以及它的风险"一节，讲清楚镜像能改动什么。

### 变更

- **从 GitHub 以外的地方克隆之前，安装脚本会先问你。** 不管是用 `DTK_GH_PROXY` 还是 `DTK_REPO_URL` 设的镜像，
  它拿到的都是全部代码——包括 `docker/compose.yml`，也就是跑哪些镜像——以及之后的每一次升级，因为 checkout 会一直
  把它当作 origin。现在脚本会说出完整的地址和它会拿到什么，直接回车就是不换。用 `--yes` 时，设了这个变量就算同意。
  已有的 checkout 不会问：它照旧从自己的 origin 拉取。
- **控制台在导入登录态之前，会说明账号有被封的可能。** 平台很少因为用账号读取数据而封号，但不能排除。导入已登录的
  Cookie 本来就要勾选的那个确认框，现在也包括接受这个风险。游客 Cookie 仍然不需要勾选。导入接口的说明、身份文档、
  FAQ 和 README 里也写了同样的话。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.8` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.8` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-3be29eaaa3bf558166e464503687da5d4c25674c` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.8` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.8`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Thanks / 感谢**: @ccsimplyspolit (#798, #800) and @tianxingjian2406 (#799).

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.7...v5.1.8
