## What's new

**TikTok lists and search that get past the posts TikTok will not play.** One
post sent without a media address used to turn a whole page of an author's posts
or a search into a 502. Those posts are TikTok Shop videos, whose media TikTok's
web API gives to nobody, guest or signed in; they now come back with their
details and no streams, and asking for one directly says so with its own error
code. Page two of a TikTok post search, empty until now, pages again. Also: the
console's single-post form takes the share text the apps copy, retention stops
rewriting the same finished tasks on every pass, and A-Bogus signing is about 9%
faster.

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

It checks out `v5.1.11`, pulls 5.1.11, backs the database up, migrates, restarts
and writes `DTK_IMAGE_TAG=5.1.11` into `.env` itself; there is nothing to edit.
CloakBrowser and the browser image are unchanged.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit; a pinned one becomes 5.1.11 - no `v`, see the tag table
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

No new migrations, no new settings, and API keys need nothing. Your data is
untouched.

### Added

- **`MEDIA_WITHHELD`** (403, not retryable): the post was read in full, but the
  platform gives out no media for it - today, a TikTok Shop video. Asking for
  such a post's details, or downloading it, fails with this instead of
  `UPSTREAM_CHANGED`. If your client handles error codes, treat it like
  `CONTENT_PRIVATE`: the post exists, and no retry or login gets its media.

### Changed

- **A TikTok list can hold posts without media.** Author posts, hashtag, music,
  related posts and post search return a TikTok Shop video with its details,
  `media.video: null` and no streams, instead of failing the page. If you
  download from a list, skip the posts whose media is empty. A page on which no
  post has media at all still answers `UPSTREAM_CHANGED`: that is the platform
  changing its format, not a few posts.
- **The TikTok post search cursor carries a search id**, as in
  `20:2026110807205434CD61E448A521C8F318`. Pass it back as you got it and nothing
  changes for you; if your code read it as a number, stop - the cursor has always
  been documented as opaque. A bare offset is still accepted, and TikTok answers
  it with an empty page, as before.
- **A-Bogus signing is about 9% faster.** SM3's message expansion was rewritten;
  the signatures are byte for byte the same. Nothing to do. Thanks to
  [@dexhunter](https://github.com/dexhunter) (#821).

### Fixed

- **A TikTok author's posts or a post search failed with 502 `UPSTREAM_CHANGED`**
  when one post on the page had no media address, and every other post on the
  page was lost with it (#819). See Changed. Thanks to
  [@talaon2](https://github.com/talaon2) and [@codersjj](https://github.com/codersjj).
- **Page two of a TikTok post search was always empty.** TikTok answers a later
  page only to a request that names the search it continues, and no request did;
  the cursor carries it now. Searching posts still needs a signed-in TikTok
  identity. Thanks to [@codersjj](https://github.com/codersjj).
- **A TikTok Shop video's details were a 502** asking you to report a bug, and
  downloading one failed with "retry the download", which never helps. Both say
  `MEDIA_WITHHELD` now.
- **The console's single-post form refused share text** - what the Douyin and
  TikTok apps copy - with a bare `INVALID_PARAM`, because a short link has to be
  followed before it names a post (#823). The form follows it first now, as the
  batch tab does, and so do the comments mode and watching a post. A profile's
  short link still has to be pasted in full. Thanks to
  [@FreeLi2023](https://github.com/FreeLi2023).
- **Retention rewrote the same finished tasks on every pass** (#822). Clearing a
  task's payload stored a JSON `null`, which the next pass matched again: one
  instance's 291-row task table had taken 813,455 updates. The first pass after
  upgrading clears the existing rows one last time, and later passes report 0.
  Nothing to do. Thanks to [@languang666](https://github.com/languang666).

---

## 本次更新

**TikTok 列表和搜索不再被平台不给播放的作品拖垮。** 以前一页作者作品或搜索结果里只要有一条没有媒体地址，整页就返回 502。这些作品是 TikTok Shop 带货视频，TikTok 网页接口对谁都不给它们的媒体，游客和已登录身份都一样；现在它们会带着作品信息、不带视频流照常返回，单独请求这类作品时则会返回专门的错误码说明原因。TikTok 作品搜索的第二页一直是空的，现在可以正常翻页了。另外：控制台的单个作品表单能接受 App 复制出来的分享文案，数据保留任务不再每轮都重写同一批已完成任务，A-Bogus 签名快了约 9%。

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

它会 checkout `v5.1.11`、拉取 5.1.11、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.11` 写进 `.env`，
不需要手改任何东西。CloakBrowser 和浏览器镜像都没有变化。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.11 —— 不带 v，见文末标签表
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

没有新的迁移，没有新的设置，API Key 也不用动。数据不受影响。

### 新增

- **`MEDIA_WITHHELD`**（403，不可重试）：作品已经完整读到，但平台不提供它的媒体——目前就是 TikTok Shop 带货视频。请求这类作品的详情或下载它时，会返回这个错误码，不再是 `UPSTREAM_CHANGED`。如果你的客户端按错误码处理，把它当成 `CONTENT_PRIVATE` 对待即可：作品存在，重试或导入登录态都拿不到媒体。

### 变更

- **TikTok 列表里可能出现没有媒体的作品。** 作者作品、话题、音乐、相关推荐和作品搜索会把 TikTok Shop 带货视频连同作品信息一起返回，`media.video` 为 `null`、没有视频流，而不是让整页失败。如果你从列表里下载，跳过媒体为空的作品即可。如果一页里所有作品都没有媒体，仍然返回 `UPSTREAM_CHANGED`：那说明是平台改了格式，而不是个别作品的问题。
- **TikTok 作品搜索的 cursor 带上了搜索 ID**，形如 `20:2026110807205434CD61E448A521C8F318`。原样传回去就行，你那边什么都不用改；如果你的代码把它当数字处理，请改掉——文档一直写的是 cursor 不透明。纯数字的 offset 仍然接受，但 TikTok 对它返回的还是空页，和以前一样。
- **A-Bogus 签名快了约 9%。** 重写了 SM3 的消息扩展，签名结果逐字节不变，不需要做任何事。感谢 [@dexhunter](https://github.com/dexhunter)（#821）。

### 修复

- **TikTok 作者作品和作品搜索报 502 `UPSTREAM_CHANGED`**：页里只要有一条作品没有媒体地址，整页都会失败，其他作品也一起拿不到（#819）。见「变更」。感谢 [@talaon2](https://github.com/talaon2) 和 [@codersjj](https://github.com/codersjj)。
- **TikTok 作品搜索的第二页总是空的。** TikTok 只给带上了"接着哪次搜索"的请求返回后续页，而我们的请求从来没带；现在 cursor 会把它带上。搜索作品仍然需要已登录的 TikTok 身份。感谢 [@codersjj](https://github.com/codersjj)。
- **TikTok Shop 带货视频的详情返回 502**，还让你反馈 bug；下载这类视频失败时提示"请重试"，而重试从来没用。现在两处都会返回 `MEDIA_WITHHELD`。
- **控制台的单个作品表单不接受分享文案**——也就是抖音和 TikTok App 复制出来的那段文字——只给一个光秃秃的 `INVALID_PARAM`，因为短链要先跟随跳转才知道指向哪个作品（#823）。现在表单会先展开短链，和批量作品标签页一样；评论模式和关注单个作品也同样支持短链了。主页的短链仍然需要粘贴完整地址。感谢 [@FreeLi2023](https://github.com/FreeLi2023)。
- **数据保留任务每一轮都在重写同一批已完成任务**（#822）。清空任务结果时写入的是 JSON 的 `null`，下一轮又会匹配到它们：有一个实例 291 行的任务表累计被更新了 813,455 次。升级后的第一轮会把现有的这些行最后清理一次，之后每轮都报 0，不需要做任何事。感谢 [@languang666](https://github.com/languang666)。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.11` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.11` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-SHA_FILLED_IN_AFTER_TAGGING` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.11` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.11`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.10...v5.1.11
