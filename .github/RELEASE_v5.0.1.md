## What's new

A maintenance release. One thing in it is worth upgrading for on its own: on a
public instance with demo mode on, the console was showing demo visitors the
whole sidebar and answering 403 on most of it.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.0.1 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or run the new installer again and pick Upgrade:

```bash
bash install/install.sh --manage
```

Your data is untouched: the named volumes survive a rebuild, so the identity
pool, the archive, the settings and the API keys stay where they are. No new
migrations in this release.

### Added

- **A guided installer, in both languages.** `install/install.sh` and
  `install/install.zh.sh` work out your distribution, offer the right way to
  install Docker, scale the container limits to the machine, generate `.env`
  with real secrets, and bring the stack up. Run it a second time and it becomes
  the operations menu instead: status, upgrade, passwords, an extra
  administrator, backups, any runtime setting, disk cleanup, stop or uninstall.
- **A live demo.** <https://douyin.wtf> is open to everyone — sign in with the
  prefilled demo account and use the console and the API. 30 requests per 10
  seconds, then a 10-second cooldown.
- **Community files.** `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  issue forms in both languages, a bilingual pull-request template, CODEOWNERS
  and Dependabot. GitHub scored the repository's community profile at 42% before
  this; it is 100% now.
- **Two easter eggs**, which you are invited to find rather than told about.

### Changed

- **The English README is now the default.** `README.md` is English and the
  Chinese one moved to `README.zh-CN.md`. Links to the old path still resolve on
  GitHub.
- **Dependencies.** eslint 10, vite 8, i18next 26, mcp 2.2, redis 8.1,
  sqlalchemy 2.0.52, uvicorn 0.52, Go 1.27 for the downloader image, and eleven
  GitHub Actions. The console's React Compiler lint rules came with eslint 10 and
  found two real defects, both fixed below.
- **Documentation.** Both installation pages now open with the choice between
  Docker and a by-hand install, carry a contents list, and have a section on
  mirrors for installing from mainland China.

### Fixed

- **A demo visitor saw the whole sidebar.** `/auth/me` sat behind a guard whose
  floor was `viewer`, so a demo session was refused its own principal and the
  console could not tell it was a demo. It rendered every page — Identities,
  Proxies, Users, Settings, Backup — each of which then answered 403. Twenty nav
  items where there should have been eleven.
- **The mobile navigation drawer covered the whole screen.** A later rule with
  equal specificity overrode the drawer's width, so the menu hid the page behind
  it and left a column of dead space beside labels a few words long. It is 300px
  or 80vw now, whichever is smaller.
- **About and the sponsor were missing on phones.** Not cut off — absent from the
  markup. The only route to either was typing the URL.
- **The Tools page ran 62px off the side of a phone.** Five tabs in a row that
  does not wrap; the fifth was unreachable and dragged the page into a
  horizontal scroll.
- **A memo in the API keys page never memoised.** It read the clock during render
  and listed the result as a dependency, so every render produced a new value.
- **A temporal dead zone in the downloads page**, where two mutations called a
  helper two hundred lines before it was declared.

### Security

- **`explain` and `identity` are no longer offered to a session that cannot use
  them.** Both hand back the identity's own cookie jar and the signed upstream
  URL, so both are operator-only and always were — the playground was letting a
  demo visitor tick the box and be answered 403. The API's refusal is unchanged;
  what changed is that the control is now disabled, says which role it needs, and
  keeps its value out of the request.

---

## 本次更新

维护版本。其中一项单独就值得升级：在开了演示模式的公开实例上，控制台会把整个侧边栏
展示给演示访客，而其中大部分点进去都是 403。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.0.1 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者再跑一次新的安装脚本，选「升级」：

```bash
bash install/install.zh.sh --manage
```

数据不受影响：命名卷不随容器重建而消失，身份池、归档、设置和 API Key 都在原地。
本次没有新的迁移脚本。

### 新增

- **引导式安装脚本，中英双语。** `install/install.sh` 和 `install/install.zh.sh`
  会识别发行版、给出对应的 Docker 安装方式、按机器大小调好容器上限、生成带真实密钥的
  `.env`，然后把整套跑起来。再跑第二次它就变成运维菜单：状态、升级、改口令、加管理员、
  备份、改任意运行时设置、清理磁盘、停止或卸载。
- **在线演示站。** <https://douyin.wtf> 对所有人开放 —— 用登录页替你填好的演示账号
  登录，即可使用控制台和接口。每 10 秒 30 次请求，超了冷却 10 秒。
- **社区文件。** `CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、中英双语的
  issue 表单、双语 PR 模板、CODEOWNERS 和 Dependabot。此前 GitHub 给这个仓库的社区
  档案完整度打 42 分，现在是 100。
- **两个彩蛋**，请自己找，不剧透。

### 变更

- **英文自述文档成为默认。** `README.md` 现在是英文版，中文版移到
  `README.zh-CN.md`。指向旧路径的链接在 GitHub 上仍然可用。
- **依赖升级。** eslint 10、vite 8、i18next 26、mcp 2.2、redis 8.1、
  sqlalchemy 2.0.52、uvicorn 0.52、下载器镜像的 Go 1.27，以及十一个 GitHub Action。
  eslint 10 带来的 React Compiler 规则查出了两个真缺陷，都在下面修复里。
- **文档。** 两份安装文档现在开头就让你在 Docker 和手动部署之间做选择，配了全文目录，
  并新增了中国大陆用户的换源章节。

### 修复

- **演示访客看到的是整个侧边栏。** `/auth/me` 挂在一个下限为 `viewer` 的守卫上，
  于是演示会话读不到自己的身份，控制台也就不知道自己是个演示。它把每个页面都渲染了
  出来 —— 身份池、代理、用户、设置、备份 —— 而这些点进去全是 403。本该 11 项的导航
  显示了 20 项。
- **移动端导航抽屉占满整屏。** 一条权重相同但位置更靠后的规则盖掉了抽屉宽度，于是菜单
  把它背后的页面完全遮住，而只有几个字的菜单项右侧留着一大片空白。现在是 300px 和
  80vw 取小。
- **手机上看不到「关于」和赞助商。** 不是被截断，是根本没渲染出来，唯一的入口是手敲网址。
- **基础工具页在手机上横向溢出 62px。** 五个标签放在一个不换行的行里，第五个点不到，
  还把整页拖进了横向滚动。
- **API Key 页有个 memo 从来没生效过。** 它在渲染期读时钟并把结果列为依赖，于是每次
  渲染都得到一个新值。
- **下载页存在暂时性死区** —— 两个操作调用了一个在两百行之后才声明的辅助函数。

### 安全

- **`explain` 和 `identity` 不再提供给用不了它们的会话。** 这两个参数都会交回身份自己的
  cookie 和带签名的上游 URL，所以它们一直是 operator 专属 —— 而调试台此前允许演示访客
  勾上它，然后收到 403。接口侧的拒绝没有变；变的是这个控件现在是禁用的、会说明需要什么
  角色，并且不会把值放进请求。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.0.1` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.0.1` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-1b9478a97f89a606a7147fc1acb4461b06dc3af6` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.0.1` was never pushed. One value names both published images. `latest`
and `5.0` move with every release — pin a row above instead.

镜像标签不带 `v`：发布时 `docker/metadata-action` 会把它剥掉，`:v5.0.1` 从来没有被
推送过。这一个值同时决定两个已发布镜像的标签。`latest` 和 `5.0`
会随每次发布移动，要钉死请用上面两行之一。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.0.0...v5.0.1
