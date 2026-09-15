## What's new

**Saved folders, on both platforms.** Douyin and TikTok both let a user file
other people's posts into named folders, and both let each folder be public or
private individually. This release reads all of it: the folders, what is inside
one, who owns it, and whether it is public — a public folder needs no login at
all. TikTok reposts are wired too, and a TikTok collection link pasted into
`/parse` now resolves.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.0 in .env — no `v`, see the tag table at the bottom
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

- **`GET /{platform}/user/collections` — the bookmark folders.** Each entry now
  reports whether it is public, plus the folder's owner. On TikTok you ask about
  an author and a guest identity sees that author's public folders. On Douyin the
  endpoint carries no user id at all and answers only about the session sending
  it, so pin `identity` to an imported identity there; passing `sec_user_id` on
  Douyin is refused rather than quietly ignored, because ignoring it would return
  your own folders under somebody else's name.
- **`GET /{platform}/collection/posts` — what is inside one folder.** Both
  platforms. The request names the folder, not the account, so a folder its owner
  made public is readable with a guest identity and no import at all. A private
  one is refused rather than returned empty.
- **`GET /{platform}/collection` — one folder's own details, by id.** TikTok only.
  Takes just the folder id, which is all a shared link carries, and is the only
  way to find out what an unknown collection id is: name, cover, item count,
  owner, and whether it is public.
- **`GET /{platform}/user/bookmarks` — every post an account saved.** TikTok only,
  and only ever that account's own list, so it needs an imported identity.
- **`GET /{platform}/user/reposts` — posts an author reposted.** TikTok only. A
  public profile tab, so a guest identity reads it. Each entry carries its
  original author rather than the account that reposted it.
- **TikTok collection links are recognized.** Paste
  `https://www.tiktok.com/@name/collection/Title-7685…` into `/parse` and you get
  the folder back. Douyin's `/collection/` links are unchanged and still resolve
  to an author's own series, which is a different thing wearing the same word.

### Changed

- **Playground endpoint names read as a set.** "Mix / playlist posts" did not
  distinguish an author's own series from a viewer's bookmark folders. The four
  are now named against each other: *Author's own series*, *Bookmark folders*,
  *Saved posts (every folder)*, *Saved posts (one folder)*. Endpoint names,
  parameters and URLs are unchanged — this is labels only.
- **Controls that a platform cannot accept are hidden** in the Playground rather
  than submitted and refused, the same way controls your role cannot use already
  were.

### Fixed

- **A page size TikTok refuses is now refused here.** `count` above 35 on a
  TikTok list came back looking like an author with no posts — an ordinary
  absence, indistinguishable from the truth, after spending a pooled identity to
  get it. Values over the platform's real ceiling are rejected before anything
  leaves the process, naming the ceiling. Douyin's 50 is unchanged. If you were
  passing `count=50` to a TikTok endpoint you now get a clear `INVALID_PARAM`
  instead of a silent empty page; lower it to 35.
- **An author's bookmark folders were documented as private.** They are not:
  visibility is per folder, and a guest identity sees the public ones. The old
  wording said a guest gets an empty page, which would have stopped anyone from
  trying. Corrected in the endpoint docs and both locales.
- **Folder covers were dropped.** A collection's cover arrived as `null` for
  every folder because the upstream container is camelCase and the parser read
  the snake_case spelling.

### Security

- **Two test samples named a real account.** The session-check fixtures carried a
  live numeric account id and web id copied from a capture. They are placeholders
  now, matching how every other fixture in the repo already redacted that field.
  Nothing was exploitable — a numeric user id is public — but it identified a
  person and did not need to.

---

## 本次更新

**收藏夹，两个平台都支持了。** 抖音和 TikTok 都允许把别人的作品收进命名的收藏夹，
也都允许逐个把收藏夹设为公开或私密。这个版本把这些全读出来了：收藏夹列表、某个
收藏夹里的内容、归属于谁、是否公开——公开的收藏夹完全不需要登录。TikTok 的转发
列表也一并接上了，收藏夹分享链接丢进 `/parse` 就能解析。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.0 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者重新运行安装脚本并选择「升级」：

```bash
bash install/install.sh --manage
```

数据不受影响：命名卷不随容器重建而消失，身份池、归档、设置和 API Key 都在原地。
这个版本没有新增数据库迁移。

### 新增

- **`GET /{platform}/user/collections` —— 收藏夹列表。** 每一条现在都会说明自己
  是否公开，以及归属于谁。在 TikTok 上你可以问某个作者，游客身份能看到该作者公开
  的收藏夹。在抖音上这个接口里根本没有用户 id，只回答发出请求的那个会话自己的收藏
  夹，所以必须把 `identity` 指向导入的身份；在抖音上传 `sec_user_id` 会被拒绝而不是
  被悄悄忽略——忽略它的话，返回的会是你自己的收藏夹，却挂在别人的名下。
- **`GET /{platform}/collection/posts` —— 某个收藏夹里的内容。** 两个平台都支持。
  请求指名的是收藏夹而不是账号，所以作者设为公开的收藏夹，游客身份就能读，完全不用
  导入身份。私密的会被直接拒绝，而不是返回空页。
- **`GET /{platform}/collection` —— 按 id 查单个收藏夹的信息。** 仅 TikTok。只要
  收藏夹 id——分享链接里也就只有这个——它是唯一能查出一个陌生收藏夹 id 究竟是什么
  的途径：名称、封面、作品数、归属、是否公开。
- **`GET /{platform}/user/bookmarks` —— 账号收藏的全部作品。** 仅 TikTok，且只能读
  账号自己的，所以需要导入身份。
- **`GET /{platform}/user/reposts` —— 作者转发的作品。** 仅 TikTok。这是公开的主页
  tab，游客身份即可读取。每一条携带的是原作者，而不是转发它的那个账号。
- **能识别 TikTok 收藏夹链接了。** 把
  `https://www.tiktok.com/@name/collection/Title-7685…` 丢进 `/parse` 就能拿到这个
  收藏夹。抖音的 `/collection/` 链接行为不变，仍然解析为作者自建的合集——那是同一个
  词底下的另一样东西。

### 变更

- **Playground 的接口名现在是成组可读的。** 原来的「合辑 / 播放列表作品」无法区分
  作者自建的合集和使用者自己的收藏夹。现在四个名字互相对照：*作者自建合集*、
  *收藏夹列表*、*收藏的作品（跨全部收藏夹）*、*收藏的作品（单个收藏夹）*。接口名、
  参数和 URL 都没有变——这次只改了标签。
- **平台不接受的控件会在 Playground 里隐藏**，而不是提交上去再被拒绝，这和原本
  「角色权限不够就隐藏」的做法是同一套。

### 修复

- **TikTok 不接受的分页大小，现在这边也会拒绝。** 在 TikTok 的列表接口上把 `count`
  设到 35 以上，返回的结果看起来就像这个作者没有任何作品——一次普通的「没有内容」，
  和真相无法区分，而且是在消耗掉一个池内身份之后才拿到的。超过平台真实上限的值现在
  在请求离开进程之前就被拒绝，并且会明确告诉你上限是多少。抖音的 50 不受影响。如果
  你原本在 TikTok 接口上传 `count=50`，现在会收到明确的 `INVALID_PARAM` 而不是一个
  静默的空页，把它降到 35 即可。
- **作者的收藏夹被写成了「私密」。** 事实并非如此：公开与否是逐个收藏夹的属性，游客
  身份能看到公开的那些。原来的文案写着游客只会拿到空页，而这种说法会让人根本不去尝试。
  接口文档和中英两份文案都已更正。
- **收藏夹封面丢失。** 每个收藏夹的封面都返回 `null`，因为上游的容器字段是驼峰命名，
  而解析器读的是下划线命名。

### 安全

- **两份测试样本里写着一个真实账号。** 会话检查的样本携带了从抓包里复制来的真实数字
  账号 id 和 web id。现在已改成占位值，与仓库里其他所有样本对这个字段的处理方式一致。
  这不构成可被利用的问题——数字 id 本来就是公开的——但它指向了一个具体的人，而这毫无
  必要。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.0` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.0` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-8a3feca26683778d8c6c885803cb0e11374f3d5e` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.0` was never pushed. One value names both published images. `latest`
and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.0`
这个标签从来没有被推送过。同一个值同时对应两个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。
