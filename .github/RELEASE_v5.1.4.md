## What's new

**Offline transcription.** dtk can now turn a video it has downloaded into text
on your own machine, with FunASR models and no audio leaving the host -
contributed by [@163lico](https://github.com/163lico) in
[#786](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/pull/786). It is
off by default. The release also fixes a Douyin author's feed stopping at page
two and leaving out the newest posts
([#782](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/782),
[#783](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/783)),
adds Douyin's flat bookmarks list
([#790](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/790)),
lets you keep an identity out of rotation
([#789](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/789)),
and says why a proxy failed instead of reporting an internal error
([#781](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/781)).

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.4 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or update the checkout first, then run the installer and pick Upgrade:

```bash
cd /opt/dtk && git pull && bash install/install.sh --manage
```

Three new migrations (transcriptions, identity rotation, the proxy probe error);
the `migrate` step applies them. Your data is untouched.

**If you run the `browser` profile, rebuild browser-rpc as well.** The
CloakBrowser pin moved to 0.6.3, and this API refuses to sign a POST through an
older browser-rpc. This release's installer does it as part of Upgrade, which
is why the checkout is pulled before running it; by hand:

```bash
CLOAKBROWSER_COMMIT=fbf005bc199920b592df17ac4d489324e29d56d8 COMPOSE_ENV_FILES=.env \
  docker compose -p dtk -f docker/compose.yml --profile browser build browser-rpc
docker compose -p dtk -f docker/compose.yml --profile browser up -d browser-rpc
```

### Breaking changes

- **Lists that belong to one account need `identity`.**
  `GET /api/v1/tiktok/user/bookmarks` and `GET /api/v1/douyin/user/collections`
  answer only about the account that sends them. Without `identity`, rotation
  sent them from whichever account it drew, and the shared cache handed that
  account's list to the next caller. They now answer `400 INVALID_PARAM` with
  `details.field` set to `identity`. Pass `identity=<id>` of the logged-in
  identity whose list you want. TikTok's `/user/collections` is an author's
  public folders and is unchanged.
- **Two new error codes where clients used to see others.** A request that
  cannot reach the platform after its retries now fails with
  `502 UPSTREAM_UNREACHABLE` (retryable) instead of `INTERNAL`, and
  `details.proxied` says whether it went through a proxy. A Douyin author's
  later pages with no logged-in identity in rotation fail at once with
  `503 IDENTITY_LOGIN_REQUIRED` (not retryable) instead of spending a guest
  and reporting `UPSTREAM_RISK_CONTROL`. If your client keeps a fixed list of
  codes, add both; the API guide's error table says which to retry.

### Added

- **Offline transcription.** `POST /api/v1/transcriptions` transcribes the
  video of a finished download; the console has a Transcriptions page and
  starts one from Downloads, and MCP has `transcribe_video`. To turn it
  on, pick Manage → 11 in the installer, or follow the transcriber profile in
  the installation guide. It needs the `downloader` profile and about 500 MB
  of models, which the installer downloads and checks for you
  ([how to get them by hand](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/en/18-funasr-models.md)).
  API keys need the `transcription:write` or `transcription:read` scope. The
  image `evil0ctal/douyin_tiktok_download_api-transcriber` is published from
  this release on.
- **Douyin's flat bookmarks list.**
  `GET /api/v1/douyin/user/bookmarks?identity=<id>` reads the videos a
  logged-in account has bookmarked, page by page with `cursor`. It needs a
  logged-in identity named with `identity`, for the reason above. Thanks to
  [@zonglitao-ai](https://github.com/zonglitao-ai) for working out the request.
- **Keep an identity out of rotation.** Switch on "Only for requests that name
  it" in the identity drawer, and that identity is used only by requests that
  pass its `identity=`. To let rotation use it for some endpoints only, call
  `POST /api/v1/admin/identities/{id}/rotation` with `endpoints`. Identities
  you leave alone rotate as before. Requested by
  [@JiuMengDz](https://github.com/JiuMengDz).

### Changed

- **CloakBrowser 0.5.10 → 0.6.3** in `.env.example`. Same Chromium; a launch
  behind a slow proxy no longer stalls every other browser-rpc request for up
  to 20 seconds. Rebuild browser-rpc as shown under Upgrading.

### Fixed

- **A Douyin author's feed pages past page one and includes the newest
  posts.** Douyin serves the full feed only to a logged-in account, and
  rotation never looked at whether an identity was logged in: page two went
  to a guest, came back empty and was reported as risk control. Later pages
  now go to logged-in identities only, and page one prefers one. Import a
  logged-in Douyin identity to get the full feed; without one, page one is
  answered by a guest and marked `meta.guest_fallback`. Reported by
  [@jonysun](https://github.com/jonysun) and
  [@oushiki1219](https://github.com/oushiki1219).
- **A Douyin account with no bookmark folders.** `/api/v1/douyin/user/collections`
  answered `502 UPSTREAM_CHANGED` for an empty list; it answers an empty list.
  Reported by [@zonglitao-ai](https://github.com/zonglitao-ai).
- **The proxies page says why a proxy failed.** The probe's reason was only in
  the log; it is now shown under the badge. SOCKS proxies could not be probed
  at all, and each proxy row counts the identities bound to it. Note that a
  proxy added later does not reach identities created without one: import or
  mint them with the proxy. Reported by [@hy7873](https://github.com/hy7873).
- **Delete on the Downloads page deletes the files.** It called the cancel
  route, which leaves a finished download alone, so it reported success and
  removed nothing. It now calls the new `DELETE /api/v1/downloads/{id}/files`,
  and the rows show as "Cleaned up".
- **The installer with the browser container.** It built browser-rpc without
  a browser, so every install that said yes to it stopped at
  browser-rpc unhealthy. Upgrade from the menu repairs such an install. A
  successful install also exited 1, which broke `curl ... | bash`.
- **`?wait=` answers as soon as the task finishes.** A task that finished in
  milliseconds could still answer up to five seconds later.

---

## 本次更新

**离线转写。** dtk 现在可以把下载好的视频在你自己的机器上转成文字，使用 FunASR 模型，
音频不会离开这台主机——由 [@163lico](https://github.com/163lico) 在
[#786](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/pull/786) 贡献。默认关闭。
这个版本还修复了抖音作者作品翻不过第二页、不返回最新作品的问题
（[#782](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/782)、
[#783](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/783)），
新增了抖音的扁平收藏列表
（[#790](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/790)），
可以让某个身份不参与轮换
（[#789](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/789)），
代理失败时会说明原因，而不是报内部错误
（[#781](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/781)）。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.4 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者先更新代码，再运行安装脚本并选择「升级」：

```bash
cd /opt/dtk && git pull && bash install/install.sh --manage
```

新增三个数据库迁移（转写、身份轮换、代理探测错误），`migrate` 这一步会应用它们。数据不受影响。

**如果你启用了 `browser` profile，还要重新构建 browser-rpc。** CloakBrowser 的固定版本
升到了 0.6.3，而且这个版本的 API 拒绝通过旧版 browser-rpc 给 POST 请求签名。这个版本的
安装脚本会在「升级」时自动处理，所以要先拉代码再运行它；手动的话：

```bash
CLOAKBROWSER_COMMIT=fbf005bc199920b592df17ac4d489324e29d56d8 COMPOSE_ENV_FILES=.env \
  docker compose -p dtk -f docker/compose.yml --profile browser build browser-rpc
docker compose -p dtk -f docker/compose.yml --profile browser up -d browser-rpc
```

### 不兼容变更

- **只属于某个账号的列表需要指定 `identity`。** `GET /api/v1/tiktok/user/bookmarks` 和
  `GET /api/v1/douyin/user/collections` 只返回发请求的那个账号自己的内容。不指定
  `identity` 时，轮换会随便挑一个账号去请求，共享缓存还会把这个账号的列表交给下一个调用者。
  现在它们返回 `400 INVALID_PARAM`，`details.field` 为 `identity`。请传
  `identity=<id>`，指定你要读取的那个已登录身份。TikTok 的 `/user/collections` 是作者公开的
  收藏夹，不受影响。
- **两个新错误码，替代了原来返回的错误码。** 重试之后仍连不上平台的请求，现在返回
  `502 UPSTREAM_UNREACHABLE`（可重试），而不是 `INTERNAL`，`details.proxied` 说明它有没有
  走代理。轮换中没有已登录身份时，抖音作者作品的后续页会直接返回
  `503 IDENTITY_LOGIN_REQUIRED`（不可重试），而不是消耗一个游客身份再报
  `UPSTREAM_RISK_CONTROL`。如果你的客户端维护了固定的错误码列表，把这两个加上；API 指南的
  错误码表写明了哪些可以重试。

### 新增

- **离线转写。** `POST /api/v1/transcriptions` 转写一个已完成下载的视频；控制台新增「视频转写」
  页面，也可以从下载页发起，MCP 新增 `transcribe_video`。开启方式：在安装脚本里选
  「管理 → 11」，或者按安装指南里 transcriber profile 一节操作。它需要 `downloader`
  profile，以及约 500 MB 的模型，安装脚本会帮你下载并校验
  （[手动获取方式](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/zh/18-funasr-models.md)）。
  API Key 需要 `transcription:write` 或 `transcription:read` 权限。镜像
  `evil0ctal/douyin_tiktok_download_api-transcriber` 从这个版本开始发布。
- **抖音扁平收藏列表。** `GET /api/v1/douyin/user/bookmarks?identity=<id>` 读取已登录账号
  收藏的视频，用 `cursor` 翻页。出于上面的原因，它需要用 `identity` 指定一个已登录身份。
  感谢 [@zonglitao-ai](https://github.com/zonglitao-ai) 摸清了这个请求。
- **让身份不参与轮换。** 在身份详情里打开「只服务指定它的请求」，这个身份就只会被带了它的
  `identity=` 的请求使用。如果想让轮换只在某些接口上用它，调用
  `POST /api/v1/admin/identities/{id}/rotation` 并传 `endpoints`。没有设置过的身份照常轮换。
  由 [@JiuMengDz](https://github.com/JiuMengDz) 提出。

### 变更

- **CloakBrowser 0.5.10 → 0.6.3**（`.env.example`）。Chromium 版本不变；在慢代理后面启动
  浏览器时，不会再让 browser-rpc 的其他请求卡住最多 20 秒。按「升级方式」里的命令重新构建
  browser-rpc。

### 修复

- **抖音作者作品可以翻过第一页，并且包含最新作品。** 抖音只把完整的作品列表给已登录账号，
  而轮换从来不看身份是否登录：第二页被分给游客，返回空，再被报成风控。现在后续页只交给已登录
  身份，第一页也优先用已登录身份。导入一个已登录的抖音身份即可拿到完整列表；没有的话，第一页
  由游客返回，并标记 `meta.guest_fallback`。感谢 [@jonysun](https://github.com/jonysun) 和
  [@oushiki1219](https://github.com/oushiki1219) 的反馈。
- **没有收藏夹的抖音账号。** `/api/v1/douyin/user/collections` 对空列表返回
  `502 UPSTREAM_CHANGED`；现在返回空列表。感谢
  [@zonglitao-ai](https://github.com/zonglitao-ai) 的反馈。
- **代理页面会说明代理为什么失败。** 探测失败的原因原来只写在日志里，现在显示在状态标记下方。
  SOCKS 代理之前完全无法探测；每一行也会显示绑定在这个代理上的身份数量。注意：后来添加的代理
  不会作用到创建时没有代理的身份上，需要带着这个代理重新导入或铸造身份。感谢
  [@hy7873](https://github.com/hy7873) 的反馈。
- **下载页的「删除」会真的删除文件。** 它原来调用的是取消接口，而取消接口不会动已完成的下载，
  所以提示成功却什么也没删。现在它调用新的 `DELETE /api/v1/downloads/{id}/files`，这些记录会显示为
  「已清理」。
- **安装脚本启用浏览器容器的问题。** 它构建出的 browser-rpc 里没有浏览器，所以选了浏览器的
  安装都会停在 browser-rpc unhealthy。在菜单里选「升级」可以修复这样的安装。安装成功时脚本还会
  以 1 退出，导致 `curl ... | bash` 判断失败。
- **`?wait=` 在任务完成后立即返回。** 几毫秒就完成的任务，原来可能要再等最多五秒才返回。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.4` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.4` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-<40-char commit>` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.4` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.4`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.3...v5.1.4
