## What's new

**Short links now work on the per-platform endpoints, not only on
`/api/v1/parse`.** `GET /api/v1/douyin/video?url=https://v.douyin.com/...` was
accepted and then failed with "missing required parameter(s) for
douyin.content_detail: content_id", although the endpoint's own documentation
listed `v.douyin.com` links as supported
([#767](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/767)).
Comments, comment replies and every author endpoint failed the same way, on
both platforms. The release also carries this month's dependency updates,
among them a PyJWT update that clears thirteen advisories an image scanner
may be reporting; dtk itself never loads PyJWT.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.3 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or run the installer again and pick Upgrade:

```bash
bash install/install.sh --manage
```

Your data is untouched. No new migrations in this release.

### Changed

- **Dependencies updated.** On the server: SQLAlchemy 2.1, uvicorn 0.54,
  Alembic 1.20 and wreq 0.12.3. In the console: React 19.3, react-i18next 17
  and TanStack Query 5.104. Nothing to do: the images carry them, and a source
  checkout picks them up with `uv sync` and `npm ci`.

### Fixed

- **A link in `url=` on the per-platform endpoints is followed when it has to
  be.** A short link (`v.douyin.com/…`, `vm.tiktok.com/…`) sent to
  `/api/v1/{platform}/video`, its `comments` and `comments/replies`, or any
  author endpoint is now expanded by the worker, through the same egress as
  `/api/v1/parse`, and the post or author behind it is fetched. A link to the
  wrong kind of thing, such as a profile link sent to `/video`, now fails with
  `INVALID_PARAM` and `details.reason` set to `wrong_resource`, with
  `details.resource` and `details.expected` saying what the link was and what
  the endpoint wanted, instead of reporting a missing id. Nothing to do.
  Reported by [@duiaic](https://github.com/duiaic) — thank you.

### Security

- **PyJWT 2.13.0 → 2.15.0.** Clears thirteen advisories against PyJWT, among
  them GHSA-ffc3-869f-jxw9 (critical) and five rated high. dtk is not affected
  by any of them: PyJWT arrives as a dependency of the `mcp` package, which uses
  it only in its OAuth client code, and neither the API, the worker nor the MCP
  server loads it. Upgrading stops image scanners from flagging it; there is
  nothing else to do.

---

## 本次更新

**短链接现在在分平台接口上也能用了，不再只限于 `/api/v1/parse`。**
`GET /api/v1/douyin/video?url=https://v.douyin.com/...` 会被接受，然后失败，报
"missing required parameter(s) for douyin.content_detail: content_id"——而这个接口
自己的文档写着支持 `v.douyin.com` 链接
（[#767](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/767)）。
评论、评论回复和所有作者接口在两个平台上都以同样的方式失败。这个版本还带上了本月的
依赖更新，其中 PyJWT 的更新会消除镜像扫描器可能报出的十三条安全公告；dtk 本身从不加载
PyJWT。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.3 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者重新运行安装脚本并选择「升级」：

```bash
bash install/install.sh --manage
```

数据不受影响。这个版本没有新增数据库迁移。

### 变更

- **依赖更新。** 服务端：SQLAlchemy 2.1、uvicorn 0.54、Alembic 1.20 和 wreq 0.12.3。
  控制台：React 19.3、react-i18next 17 和 TanStack Query 5.104。不用做任何事：镜像里
  已经带上了；源码部署运行 `uv sync` 和 `npm ci` 即可。

### 修复

- **分平台接口 `url=` 里的链接，需要跟随时会被跟随。** 发给 `/api/v1/{platform}/video`、
  它的 `comments` 和 `comments/replies`，或任何作者接口的短链接（`v.douyin.com/…`、
  `vm.tiktok.com/…`），现在由 worker 展开，走的出口和 `/api/v1/parse` 相同，然后取回它
  背后的作品或作者。指向错误类型的链接，比如把主页链接发给 `/video`，现在返回
  `INVALID_PARAM`，`details.reason` 为 `wrong_resource`，并由 `details.resource` 和
  `details.expected` 写明链接是什么、接口要的是什么，而不是报缺少 id。不用做任何事。
  感谢 [@duiaic](https://github.com/duiaic) 的反馈。

### 安全

- **PyJWT 2.13.0 → 2.15.0。** 消除了针对 PyJWT 的十三条安全公告，其中
  GHSA-ffc3-869f-jxw9 为严重（critical），另有五条为高危。dtk 不受其中任何一条影响：
  PyJWT 是作为 `mcp` 包的依赖被装进来的，`mcp` 只在它的 OAuth 客户端代码里用到它，而
  API、worker 和 MCP 服务端都不会加载它。升级之后镜像扫描器就不会再报它；除此之外不用
  做任何事。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.3` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.3` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-<40-char commit>` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.3` was never pushed. One value names both published images. `latest`
and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.3`
这个标签从来没有被推送过。同一个值同时对应两个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.2...v5.1.3
