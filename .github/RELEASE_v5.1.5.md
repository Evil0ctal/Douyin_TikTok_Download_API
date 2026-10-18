## What's new

**A security fix for instances with more than one account.** A signed-in
`viewer` could read the cookie jar stored with an operator's explained request,
read what an operator's requests through a named identity returned, and mint a
guest identity through the browser - all things the role documentation reserves
for operators. If every account on your instance is an admin or an operator,
nothing was exposed; upgrade anyway. This release
otherwise matches [5.1.4](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/tag/v5.1.4):
**coming from 5.1.3, read those notes first** - they carry a breaking change,
three migrations and a browser-rpc rebuild.

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.5 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or update the checkout first, then run the installer and pick Upgrade:

```bash
cd /opt/dtk && git pull && bash install/install.sh --manage
```

No new migrations since 5.1.4. Your data is untouched.

### Changed

- **`POST /api/v1/tools/identity` needs the operator role.** It needed
  `identity:manage` before, which a viewer's console session passed. Viewers
  now get `403 FORBIDDEN_SCOPE`. Operators, admins and `identity:manage` keys
  owned by them are unaffected.
- **Reading a task back needs the role that could have started it.**
  `GET /api/v1/tasks/{task_id}`, its event stream, cancelling it and MCP's
  `get_task_result` checked only the task's scope. Now a task sent with
  `identity=` needs what `identity=` needs (`identity:manage` and the operator
  role); identity, proxy, self-check and notification jobs need an operator;
  backups and restores need an admin, so an operator can no longer read or
  cancel a restore. Anything a viewer could start - reads, downloads,
  transcriptions, library jobs - is unchanged.
- **Dependencies updated:** Mako 1.4.2 on the server and source-map-js 1.2.2
  in the console build. Nothing to do: the images carry them.

### Security

- **A viewer could read an identity's cookie jar.** `?explain=true` stores the
  outgoing request, cookie jar included, with the task result. Asking for it
  needs `identity:manage` and the operator role, but reading the task back
  through `GET /api/v1/tasks/{task_id}` checked only the scope, which every
  console session passes - so a viewer could read the jar of any explained
  task, finding task ids in the request log. The explanation is now stripped
  for every reader below operator. Affected: 5.0.0 to 5.1.4, on an instance
  with a viewer account where an operator or admin used explain. Task results
  are kept for `retention.task_result_hours` (24 by default), so only jars
  explained within that window were readable. **If you have viewers you do not
  fully trust and have used explain, treat the identities you explained with
  as exposed:** retire them and import fresh ones, and log out of those
  sessions on the platform.
- **A viewer could mint a guest identity.** `POST /api/v1/tools/identity`
  drove the browser container and returned a new guest jar to any console
  session. It is now gated like minting into the pool. No existing credential
  was exposed by this one; it spent browser time.
- **A viewer could read results that belong to an operator.** Found while
  fixing the first: reading any task back checked its scope alone. A task
  sent with `identity=` holds what that logged-in account sees - its
  bookmarks, likes and private folders - and a maintenance job's result holds
  things like a proxy's exit address. Neither holds a cookie jar. Same
  versions and the same 24-hour window as above; nothing to do beyond
  upgrading.

Reported privately by [@Sazidul0](https://github.com/Sazidul0) - thank you.

---

## 本次更新

**一个安全修复，影响有多个账号的实例。** 已登录的 `viewer` 能读到 operator 做 explain
请求时存下来的 cookie jar，能读到 operator 通过指定身份发出的请求的结果，还能通过浏览器
铸造游客身份——角色文档里这些都只属于 operator。如果你实例上所有账号都是 admin 或 operator，就没有东西被泄露；仍然建议升级。
除此之外这个版本和 [5.1.4](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/tag/v5.1.4)
相同：**从 5.1.3 升级的话请先读那份说明**——里面有一处不兼容变更、三个数据库迁移，
以及需要重新构建 browser-rpc。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.5 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者先更新代码，再运行安装脚本并选择「升级」：

```bash
cd /opt/dtk && git pull && bash install/install.sh --manage
```

相比 5.1.4 没有新的数据库迁移。数据不受影响。

### 变更

- **`POST /api/v1/tools/identity` 需要 operator 角色。** 原来只要求 `identity:manage`，
  而 viewer 的控制台会话能通过这一项。现在 viewer 会得到 `403 FORBIDDEN_SCOPE`。operator、
  admin，以及他们名下带 `identity:manage` 的密钥都不受影响。
- **读回一个任务，需要有资格发起它的角色。** `GET /api/v1/tasks/{task_id}`、它的事件流、
  取消它，以及 MCP 的 `get_task_result`，原来只检查任务的权限范围。现在带 `identity=` 发出的
  任务需要和 `identity=` 同样的条件（`identity:manage` 加 operator 角色）；身份、代理、自检和
  通知类任务需要 operator；备份和恢复需要 admin，所以 operator 不能再读取或取消恢复任务。
  viewer 本来就能发起的任务——读取、下载、转写、资料库任务——不受影响。
- **依赖更新：** 服务端 Mako 1.4.2，控制台构建用的 source-map-js 1.2.2。不用做任何事：
  镜像里已经带上了。

### 安全

- **viewer 能读到身份的 cookie jar。** `?explain=true` 会把发出去的请求（包括 cookie jar）
  和任务结果存在一起。发起它需要 `identity:manage` 加 operator 角色，但通过
  `GET /api/v1/tasks/{task_id}` 读回任务时只检查了权限范围，而每个控制台会话都能通过这一项——
  所以 viewer 能读到任何一次 explain 任务里的 jar，任务 id 可以从请求日志里找到。现在对低于
  operator 的读者一律剥掉这部分内容。受影响版本：5.0.0 到 5.1.4，前提是实例上有 viewer 账号，
  并且有 operator 或 admin 用过 explain。任务结果保留 `retention.task_result_hours` 小时
  （默认 24），所以只有在这个时间窗口内被 explain 过的 jar 是可读的。**如果你有不完全信任的
  viewer，并且用过 explain，请把 explain 时用到的身份视为已泄露：**退休它们、重新导入新的，
  并在平台上登出那些会话。
- **viewer 能铸造游客身份。** `POST /api/v1/tools/identity` 会为任何控制台会话驱动浏览器容器
  并返回一份新的游客 jar。现在它和铸造进身份池走同一道关卡。这一项没有泄露任何已有凭据，
  消耗的是浏览器资源。
- **viewer 能读到属于 operator 的结果。** 修第一项时发现的：读回任何任务都只检查权限范围。
  带 `identity=` 发出的任务里，是那个登录账号能看到的内容——收藏、喜欢、私密收藏夹；维护类
  任务的结果里有代理的出口地址之类的信息。两者都不含 cookie jar。受影响版本和 24 小时窗口
  同上；升级即可，不需要其他操作。

感谢 [@Sazidul0](https://github.com/Sazidul0) 的私下报告。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.5` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.5` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-<40-char commit>` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.5` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.5`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.4...v5.1.5
