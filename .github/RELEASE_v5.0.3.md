## What's new

**If you are on 5.0.2, upgrade.** The TikTok fix it shipped worked for about a
minute per author and then silently stopped, which is worse than the bug it
replaced.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.0.3 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or run the installer again and pick Upgrade:

```bash
bash install/install.sh --manage
```

Your data is untouched: the named volumes survive a rebuild, so the identity
pool, the archive, the settings and the API keys stay where they are. No new
migrations in this release.

### Fixed

- **5.0.2's TikTok handle lookup only worked on a cold cache.** Asking for a
  TikTok author's posts by profile link resolves the `@handle` to the `secUid`
  those endpoints need. That lookup read its answer out of a parse callback —
  and `fetch` answers a cache hit from storage without calling one. So the first
  request for an author succeeded and every request in the next fifteen minutes
  fell back to the same `INVALID_PARAM` the fix was meant to remove, with
  nothing in the log to say why. It reads the returned payload now, which is
  populated on both paths.
- **A session's `403` listed scopes that could never have refused it.** A console
  session is bounded by its role, never by its scopes, so printing them was
  noise — and actively misleading on the demo account, whose scope list contains
  `admin`. The refusal read as "I hold admin and still cannot read this".
  `have_scopes` now appears only for a caller that scopes actually bind.

### Changed

- Two new worker log events, `worker.author_handle.resolved` (with `cached`) and
  `worker.author_handle.no_id`. The branch that gave up used to say nothing at
  all, which is why the bug above was invisible from the outside.

---

## 本次更新

**如果你在用 5.0.2，请升级。** 它带的那个 TikTok 修复，每个作者只能用大约一分钟就会
悄悄失效——比它想修的那个问题更糟。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.0.3 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者再跑一次安装脚本，选「升级」：

```bash
bash install/install.zh.sh --manage
```

数据不受影响：命名卷不随容器重建而消失，身份池、归档、设置和 API Key 都在原地。
本次没有新的迁移脚本。

### 修复

- **5.0.2 的 TikTok handle 解析只在缓存是冷的时候有效。** 用主页链接取 TikTok 作者的
  作品时，会把 `@handle` 解析成那些接口需要的 `secUid`。而这个查询是从 `parse` 回调里
  取结果的——`fetch` 命中缓存时直接从存储返回，**根本不调回调**。于是对一个作者的第一次
  请求成功，之后十五分钟内的每一次都退回到它本该消除的那条 `INVALID_PARAM`，日志里还
  什么都没有。现在改成读返回的 payload，两条路径都有值。
- **会话的 `403` 列出了根本不可能拒绝它的 scope。** 控制台会话只受角色约束，从不受
  scope 约束，所以列出来纯属噪音——在演示账号上还会误导，因为它的 scope 列表里含 `admin`，
  读起来像「我有 admin 却读不了这个」。现在 `have_scopes` 只在 scope 真正起作用时出现。

### 变更

- 新增两条 worker 日志事件：`worker.author_handle.resolved`（带 `cached`）和
  `worker.author_handle.no_id`。此前放弃的那个分支一句话都不打，这正是上面那个 bug
  从外面完全看不见的原因。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.0.3` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.0.3` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-<the commit `v5.0.3` points at — `git rev-list -n1 v5.0.3`>` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.0.3` was never pushed. One value names both published images. `latest`
and `5.0` move with every release — pin a row above instead.

镜像标签不带 `v`：发布时 `docker/metadata-action` 会把它剥掉，`:v5.0.3` 从来没有被
推送过。这一个值同时决定两个已发布镜像的标签。`latest` 和 `5.0`
会随每次发布移动，要钉死请用上面两行之一。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.0.2...v5.0.3
