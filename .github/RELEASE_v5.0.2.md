## What's new

Four fixes, all of them things you could hit on a normal afternoon. Three
came out of using the live demo rather than reading the code.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.0.2 in .env — no `v`, see the tag table at the bottom
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

### Added

- **`Author.sec_uid`.** The id this project's own author endpoints key on. On
  Douyin it is the same value as `uid`; on TikTok the two differ, and that
  difference is the reason the field exists. If you read an author out of any
  response and mean to pass it back, read this field.

### Fixed

- **A TikTok profile link could not reach that author's posts.** Two things
  were wrong at once. The parser threw TikTok's `secUid` away, so nothing the
  API returned could be fed back into its own author endpoints — you got a
  numeric id and an `@handle`, and `author_posts`, `author_likes`, `followers`
  and `following` refuse both. And a profile link carries only the handle, which
  those endpoints refused by name rather than resolving. The handle is now
  looked up for you, behind `author_profile`'s cache, so a second page of the
  same author costs nothing. Douyin was never affected: its profile links
  contain the `sec_user_id` literally.
- **An author download could only ever go to Douyin.** On the Downloads page the
  platform menu was disabled for the whole of author mode, on the reasoning that
  an author is named by an id rather than by digits. But a bare author id is the
  same `MS4wLjABAAAA…` shape on both platforms and says nothing about which one
  it came from, so the code read that menu — while it sat greyed out at its
  default. TikTok was unreachable and the one control that could have said
  otherwise would not open.

### Changed

- **A `403` now says what it wants and what you sent.** Eleven places raise
  `FORBIDDEN_SCOPE` and they disagreed: most named what the endpoint requires,
  one named only your own role, and the key was spelled `required` in some and
  `required_role` in others. `details` is now one shape everywhere —
  `required_scopes` or `required_roles`, plus `have_role`, `have_scopes` and
  `via`. Read `via` first: it says whether this caller is judged by its scopes
  (`api_key`) or by its role (`session`), and an administrator's key is still
  bounded by its own scopes.

  **If you match on these keys, they changed.** `required` is now
  `required_scopes`, and `required_role` is now `required_roles` and holds a
  list.

- **The message on that `403` stopped claiming an API key you may not have.** One
  catalogue entry served all eleven refusals and read "This API key lacks the
  scope required by this endpoint" — wrong for a console session, which has no
  API key, and wrong for the role gates, which are not scopes. It is neutral and
  true now, and points at `details` for the part that varies.

---

## 本次更新

四个修复，都是正常用一下午就可能撞上的。其中三个是在演示站上用出来的，不是读代码读出来的。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.0.2 —— 不带 v，见文末标签表
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

### 新增

- **`Author.sec_uid`。** 本项目自己的作者接口认的那个 id。抖音上它和 `uid` 同值；
  TikTok 上两者不同，而这个差异正是它存在的理由。**从任何响应里读出作者、还打算拿
  回去再请求的话，读这个字段。**

### 修复

- **TikTok 的主页链接取不到该作者的作品。** 两处同时出错。解析器把 TikTok 的
  `secUid` 丢掉了，于是这个 API 返回的任何东西都喂不回它自己的作者接口 —— 你拿到
  的是一个数字 id 和一个 `@handle`，而 `author_posts`、`author_likes`、`followers`、
  `following` 两个都不收。同时主页链接里只有 handle，这些接口按名字拒绝它，而不是
  去解析。现在 handle 会替你查一次，走 `author_profile` 自己的缓存，所以同一作者的
  第二页不花钱。抖音从来不受影响：它的主页链接字面就带着 `sec_user_id`。
- **作者作品的下载只能下到抖音。** 下载页的平台下拉在整个「作者」模式下都是禁用的，
  理由是「作者是用 id 指定的，不是数字」。但裸的作者 id 在两个平台上是同一个
  `MS4wLjABAAAA…` 形状，说不出自己来自哪边，所以代码其实要读这个菜单 —— 而它正灰着，
  停在默认值上。TikTok 根本到不了，唯一能改它的控件点不开。

### 变更

- **403 现在会说清「要什么」和「你给了什么」。** 有 11 处会抛 `FORBIDDEN_SCOPE`，
  而它们各说各的：多数说接口需要什么，有一处只说你自己是什么角色，键名还有
  `required` 和 `required_role` 两种拼法。现在 `details` 全站一个形状 ——
  `required_scopes` 或 `required_roles`，外加 `have_role`、`have_scopes` 和 `via`。
  **先看 `via`**：它说明这个调用方是按 scope 判定（`api_key`）还是按角色判定
  （`session`），而管理员的 key 同样受它自己的 scope 约束。

  **如果你的代码匹配这些键，它们变了。** `required` 改成 `required_scopes`，
  `required_role` 改成 `required_roles` 且值是列表。

- **403 的文案不再断言你有一把可能并不存在的 API Key。** 一条文案服务全部 11 种拒绝，
  写的是「该 API Key 缺少调用此接口所需的权限范围」—— 对用会话 cookie 的控制台用户
  是错的（他根本没有 API Key），对角色关卡也是错的（那不是 scope）。现在它中立且真实，
  会变的部分交给 `details`。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.0.2` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.0.2` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-<the commit `v5.0.2` points at — `git rev-list -n1 v5.0.2`>` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.0.2` was never pushed. One value names both published images. `latest`
and `5.0` move with every release — pin a row above instead.

镜像标签不带 `v`：发布时 `docker/metadata-action` 会把它剥掉，`:v5.0.2` 从来没有被
推送过。这一个值同时决定两个已发布镜像的标签。`latest` 和 `5.0`
会随每次发布移动，要钉死请用上面两行之一。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.0.1...v5.0.2
