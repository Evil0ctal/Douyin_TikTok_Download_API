## What's new

**Each platform now has its own refill thresholds, and either one can be switched
off.** Until now the identity pool used one "mint below / top up to" pair for
both platforms. A deployment that only serves Douyin, on a server that cannot
reach TikTok, kept trying to mint TikTok identities every minute, failed every
time, and raised `pool_empty` for a pool it never wanted
([#763](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/763)).
Each platform can now follow the global pair, set its own, or be turned off.
This release also fixes a race in first-run setup.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.1 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or run the installer again and pick Upgrade:

```bash
bash install/install.sh --manage
```

Your data is untouched, and nothing changes until you change it: every platform
starts out following the global `pool.min_size` / `pool.target_size`, exactly
as before. No new migrations in this release.

### Added

- **Per-platform refill thresholds.** Four new settings:
  `pool.douyin.min_size`, `pool.douyin.target_size`, `pool.tiktok.min_size` and
  `pool.tiktok.target_size`. They default to `-1`, which means "follow the global
  value". Setting a platform's `min_size` to `0` turns its automatic minting off,
  along with its `pool_empty` and `pool_below_min` alerts. Imported identities and
  identities you mint by hand still work. Values below `-1` are refused rather
  than read as "follow". If you only use one platform, set the other one's
  `min_size` to `0`.
- **The refill card on the Identities page has a row per platform.** Each row has
  its own **Auto** switch and its own **Mint below** / **Top up to** numbers. A
  number that is still following the global value is marked **default**, and
  **Restore default** drops a platform's own numbers in one click.

### Changed

- **A mark of 0 now also silences the pool alerts.** This applies to the global
  `pool.min_size` too: before, setting it to 0 stopped minting but an empty pool
  still raised `pool_empty` every hour. If you relied on that alert with the mark
  at 0, set the mark back to 1 or more.
- **`GET /api/v1/admin/identities/pool` reports each platform's own marks.** Every
  platform row gains `min_size`, `target_size`, `min_inherited`,
  `target_inherited` and `auto`. The top-level `min_size` / `target_size` are
  still there and are the global pair, so existing clients keep working.
- **The pool step in `dtk diagnose` compares against the highest mark of any
  platform** rather than the global `pool.min_size`. It still counts the whole
  pool, not each platform separately.

### Fixed

- **The saved-folder endpoints added in 5.1.0 were missing from the API
  reference** in `documents/`. They are documented now. Nothing to do.

### Security

- **First-run setup could create two administrators from one token.** The setup
  endpoint read the token, compared it, and only then deleted it, so two requests
  sent at the same moment with the correct token could both succeed. Using the
  token is now the same step as deleting it, and only one request can win. The
  exposure was small: the token only appears in the container log, so this let
  whoever already held it create a second admin, and never let anyone else in.
  A wrong guess still costs one of five attempts and never uses up the real
  token. Nothing to do; an instance that is already set up is not affected.

---

## 本次更新

**每个平台都有了自己的补充阈值，也可以单独关掉。** 之前身份池的「低于多少开始铸造 /
补到多少」是两个平台共用的一对。一个只用抖音、服务器又访问不到 TikTok 的部署，会每分钟
尝试铸造 TikTok 身份、每次都失败，还会为一个根本不需要的池子发 `pool_empty` 告警
（[#763](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/763)）。
现在每个平台可以沿用全局值、单独设置，或者关闭。这个版本还修复了首次初始化里的一个竞态问题。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.1 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者重新运行安装脚本并选择「升级」：

```bash
bash install/install.sh --manage
```

数据不受影响，而且在你改动之前什么都不会变：每个平台一开始都沿用全局的
`pool.min_size` / `pool.target_size`，和以前完全一样。这个版本没有新增数据库迁移。

### 新增

- **按平台的补充阈值。** 新增四个设置：`pool.douyin.min_size`、
  `pool.douyin.target_size`、`pool.tiktok.min_size` 和 `pool.tiktok.target_size`。
  默认值是 `-1`，表示「沿用全局值」。把某个平台的 `min_size` 设为 `0` 会关闭它的自动
  铸造，同时不再发出它的 `pool_empty` 和 `pool_below_min` 告警。导入的身份和手动铸造的
  身份照常可用。小于 `-1` 的值会被拒绝，而不是被当成「沿用」。如果你只用一个平台，
  把另一个平台的 `min_size` 设成 `0` 即可。
- **身份页的「自动补充」卡片改成每个平台一行。** 每行有自己的**自动**开关和自己的
  **低于** / **补到**两个数字。仍在沿用全局值的数字旁边标着**默认**，点**恢复默认**
  可以一次清掉这个平台单独设置的数字。

### 变更

- **下限为 0 时，身份池告警也一并关闭。** 这对全局的 `pool.min_size` 同样适用：以前把它
  设成 0 会停止铸造，但池子空了仍然每小时发一次 `pool_empty`。如果你在下限为 0 的情况下
  依赖这条告警，请把下限改回 1 或以上。
- **`GET /api/v1/admin/identities/pool` 会返回每个平台自己的阈值。** 每个平台行新增
  `min_size`、`target_size`、`min_inherited`、`target_inherited` 和 `auto`。顶层的
  `min_size` / `target_size` 仍然保留，表示全局那一对，已有的客户端不受影响。
- **`dtk diagnose` 的身份池一步改为和所有平台里最高的下限比较**，而不是全局的
  `pool.min_size`。它统计的仍然是整个池子，没有按平台分别检查。

### 修复

- **5.1.0 新增的收藏夹接口没有写进 `documents/` 里的接口参考。** 现在已经补上。
  不用做任何事。

### 安全

- **首次初始化可能用同一个 token 创建出两个管理员。** 初始化接口先读取 token、比较，
  之后才删除它，所以两个同时发出、都带着正确 token 的请求可以都成功。现在使用 token 和
  删除 token 是同一步，只有一个请求能胜出。影响范围很小：token 只出现在容器日志里，所以
  这只能让已经拿到 token 的人多建一个管理员，从来不会让别人进来。猜错 token 仍然只消耗
  五次机会中的一次，不会用掉真正的 token。不用做任何事；已经初始化过的实例不受影响。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.1` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.1` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-SHA_PLACEHOLDER` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.1` was never pushed. One value names both published images. `latest`
and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.1`
这个标签从来没有被推送过。同一个值同时对应两个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。
