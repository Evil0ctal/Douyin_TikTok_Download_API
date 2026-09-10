# 故障排查

按症状定位，用一条命令或一个控制台页面确认原因，然后修好它。这篇同时收录了本实例可能返回的全部错误码，写明每个码是什么意思、以及重试是否有可能起作用。

下面所有命令都默认在仓库根目录执行。compose 命令一律带上 compose 文件（`-f docker/compose.yml`）——仓库根目录没有 `compose.yml`，少了它 compose 根本找不到要操作的东西——以及项目名（`-p dtk`）。compose 文件里已经写了 `name: dtk`，所以 `-p dtk` 只是把目标写明；但仍然建议保留，因为它同时会覆盖你 shell 里可能已经设好的 `COMPOSE_PROJECT_NAME`。

---

## 先从自检开始

绝大多数「部署好了但拿不到数据」最后都归到四件事之一——网络、代理、cookie、签名——而这四件事从外面一个都看不见。自检会把六层依次走一遍，输出一份报告。

**在控制台：** 打开 `/diagnose`，点 **开始自检**。报告渲染在页面上，整段文本有一个**复制报告**按钮。

**在命令行：**

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
```

两者跑的是同一份代码，区别在于跑在哪里：控制台提交的是后台任务，所以 worker 容器必须在跑才会有结果；CLI 则在你刚启动的这个进程里直接跑完六步。**如果诊断页一直停在「运行中」，说明 worker 没起来**——改用 CLI，然后看 [服务起不来](#服务起不来)。**而如果 `exec api` 本身就失败**——compose 提示服务没在运行，或者根本找不到对应容器——那就是连一个能跑自检的 api 容器都没有，两个入口都帮不上忙：直接去看同一节。

### 六个步骤

| # | 步骤 | 它证明了什么 |
|---|---|---|
| 1 | `components` | 本进程能连上 PostgreSQL、Redis 和 browser-rpc。Postgres 和 Redis 不通是 `fail`；browser-rpc 不通只是 `warn`，因为它是可选的 |
| 2 | `egress` | 本机能**直连**（不走代理）`https://www.douyin.com/` 和 `https://www.tiktok.com/`。用来区分「没网」和「代理坏了」 |
| 3 | `proxies` | 每个已配置的代理是否真的能过流量，以及出口在哪（出口 IP、国家）。最多探测 20 个代理 |
| 4 | `pool` | 有多少身份处于 `active`，以及最后一次成功请求是什么时候 |
| 5 | `signing` | 进程内的签名算法与真实浏览器算出的签名是否一致，按平台分别比对。没配 browser-rpc 时跳过——没有第二个签名器可比 |
| 6 | `smoke` | 一条固定的公开链接，走完整条流水线。这也是初始化向导的最后一步 |

每一步都有一个稳定的 `code`（例如 `pool_below_minimum`）、一句说明，以及——当确实有事可做时——一条建议动作。总结论取所有步骤里最差的那个状态：`pass`、`warn` 或 `fail`。警告是提醒，不是实例坏了：`dtk diagnose` 遇到警告退出码是 0，只有某一步 `fail` 才是 1。

### 报告可以安全粘贴

代理密码、cookie 值、API 密钥和签名参数在报告离开进程之前就被渲染层做了掩码，文本排版时还会再掩码一次。这是刻意的：这份报告存在的意义就是被贴进公开 issue。只作为证据的内容——数据库驱动的报错文本、某一步的原始 detail——保持英文原样，因为那才是维护者能搜索的字符串。

常用的 CLI 参数：

```bash
# 跳过端到端请求（最快，不消耗身份）
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --skip-smoke

# 机器可读
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --json

# 指定平台、指定链接做冒烟测试
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose \
  --platform tiktok --smoke-url 'https://www.tiktok.com/@someone/video/123'
```

---

## 症状索引

| 症状 | 章节 |
|---|---|
| `docker compose up` 立刻退出，或某个容器反复重启 | [服务起不来](#服务起不来) |
| `migrate` 退出码非零，api 一直不启动 | [迁移失败](#迁移失败) |
| 浏览器连不上 `127.0.0.1:8000` | [打不开控制台](#打不开控制台) |
| 初始化链接找不到了，或令牌被拒绝 | [初始化令牌丢了](#初始化令牌丢了) |
| 没人能登进控制台 | [忘记管理员密码](#忘记管理员密码) |
| 密码是对的，却返回 `RATE_LIMITED` | [被登录页锁在外面](#被登录页锁在外面) |
| 每个请求都返回 `IDENTITY_POOL_EXHAUSTED` | [身份池为空](#身份池为空) |
| 身份刚一使用就变成 `cooling` | [身份刚用就进入冷却](#身份刚用就进入冷却) |
| 某个接口返回 `ENDPOINT_CIRCUIT_OPEN` | [接口熔断器打开了](#接口熔断器打开了) |
| 请求要好几秒，或者返回 `202` 而不是数据 | [请求很慢](#请求很慢) |
| 自己的 API 密钥被 `RATE_LIMITED` | [被限流](#被限流) |
| 收到 `401` 或 `403`，分不清是哪种 | [401 还是 403](#401-还是-403) |
| 链接被判为 `INVALID_URL` | [INVALID_URL 背后的八种原因](#invalid_url-背后的八种原因) |
| 浏览器里能打开的作品却 `NOT_FOUND` | [明明存在的作品却返回 NOT_FOUND](#明明存在的作品却返回-not_found) |
| `UPSTREAM_RISK_CONTROL` | [UPSTREAM_RISK_CONTROL](#upstream_risk_control) |
| `SIGNING_FAILED`，或者空响应且没有报错 | [签名错误与签名自检](#签名错误与签名自检) |
| browser-rpc 挂了或者根本没启动 | [浏览器容器不可用](#浏览器容器不可用) |
| 下载失败，或者文件不在 | [下载失败或者文件不见了](#下载失败或者文件不见了) |
| 采集自己停了；收到 `CAPACITY_PAUSED` 告警 | [磁盘满了](#磁盘满了) |
| 某个代理被标成不健康 | [代理探测失败](#代理探测失败) |
| MCP 客户端连不上 `/mcp` | [MCP 客户端连不上](#mcp-客户端连不上) |
| 任务一直 `queued`，或者 `QUEUE_FULL` | [任务一直排队](#任务一直排队) |
| `UPSTREAM_CHANGED` 并指名了某个字段 | [UPSTREAM_CHANGED](#upstream_changed) |

---

## 如何读懂一次失败

本 API 的每一个响应，无论成功还是失败，顶层都是同样这四个键：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "IDENTITY_POOL_EXHAUSTED",
    "message": "No identity is available; the pool is expected to recover in 10 seconds.",
    "retry_after": 10,
    "details": { "endpoint": "douyin.content_detail", "reject_reason": "no_token" }
  },
  "meta": { "request_id": "…" }
}
```

（这里的 `message` 是英文，因为它按实例的默认语言渲染——`api.default_language` 出厂就是 `en`；请求加上 `?lang=zh` 就会渲染成中文。）

排查时真正要看的是三样东西：

- **`error.code`** 是稳定契约。用它做分支、写运维手册、贴进 issue。`error.message` 是本地化文本，绝不要去解析它。
- **`meta.request_id`** 是抓手，但有一点需要说清。随**已完成**结果返回的那个 id——等到了结果的 `?wait=` 调用，或者轮询任务里的 `result_meta`——是 worker 自己的抓取请求 id，也正是写在 `request_log` 那一行上的 id，所以控制台的**日志**页（`/logs`）可以精确定位你手上这次请求——按 `request_id` 过滤。而 `202` 提交响应里的那个 id 是 HTTP 关联 ID（与 `X-Request-ID` 响应头同值）；此时还没有发生任何抓取，所以它在 `request_log` 里没有对应的行。日志页看不到窗口以外的记录（默认 60 分钟，上限 30 天），而行本身在 `retention.request_log_days`（默认 14 天）之后就被删掉了。
- **`retry_after`** 存在时，同样会以 `Retry-After` 响应头发出。

MCP 的工具错误用的是同一个信封，所以一套错误处理同时覆盖两个接口面。

---

## 全部错误码

读自 `src/dtk/core/errors.py`。「可重试」是错误码自己声明的：标 **否** 的码在非重试集合里，MCP 工具也会照此告诉 agent，免得它把预算耗在一个永久性失败上循环。

| 错误码 | HTTP | 可重试 | 含义 |
|---|---|---|---|
| `INVALID_URL` | 400 | 否 | 这个 URL 不是能识别的抖音或 TikTok 链接。`error.details.reason` 说明原因——见[下文](#invalid_url-背后的八种原因) |
| `UNSUPPORTED_CONTENT` | 400 | 否 | 链接或能力是真的，但这个平台适配器不提供（例如抖音的关注/粉丝列表在没有登录态身份时） |
| `INVALID_PARAM` | 400 | 否 | 某个请求参数不合法。`details.field` 指名是哪个 |
| `UNAUTHENTICATED` | 401 | 否 | 没有凭证，或凭证被拒绝 |
| `FORBIDDEN_SCOPE` | 403 | 否 | 凭证有效，但缺少该接口要求的 scope 或角色 |
| `NOT_FOUND` | 404 | 否 | 资源不存在或不可用。对内容类请求，平台的业务错误就变成这个码 |
| `CONTENT_PRIVATE` | 403 | 否 | 内容是私密的，或已被作者删除 |
| `RATE_LIMITED` | 429 | 是 | 触发了防滥用限额。登录锁定也返回这个码 |
| `IDENTITY_POOL_EXHAUSTED` | 503 | 是 | 在等待时限内没能租到身份 |
| `ENDPOINT_CIRCUIT_OPEN` | 503 | 是 | 该接口因多个身份接连失败而被暂停 |
| `UPSTREAM_RISK_CONTROL` | 502 | 是 | 平台拒绝了这个身份，该身份已进入冷却 |
| `UPSTREAM_CHANGED` | 502 | 否 | 平台响应结构和解析器的预期对不上了。`details.path` 指名字段 |
| `SIGNING_FAILED` | 502 | 是 | 没有可用的签名器，或签名器抛错 |
| `TASK_NOT_FOUND` | 404 | 是 | 没有这个 id 的任务，或者结果已过期。整体上标为可重试——但结果一旦过期，重试也拿不回来，重新提交即可 |
| `SETUP_ALREADY_DONE` | 409 | 否 | 本实例已经有管理员账号了 |
| `SETUP_TOKEN_INVALID` | 403 | 否 | 初始化令牌不对或已过期 |
| `NOT_CONFIGURED` | 501 | 否 | 这套部署压根没有那个组件。等多久也不会凭空装出一个浏览器容器 |
| `QUEUE_FULL` | 503 | 是 | 任务队列达到 `sched.queue_max`，或者磁盘满导致新下载被暂停 |
| `DOWNLOADER_UNAVAILABLE` | 503 | 是 | 媒体 sidecar 配了但没应答 |
| `CANCELLED` | 409 | 否 | 有人主动停了这个任务。重试等于无视他 |
| `METHOD_NOT_ALLOWED` | 405 | 否 | 这个路径不接受该 HTTP 方法 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | 否 | 请求体的媒体类型不对 |
| `INTERNAL` | 500 | 是 | 意料之外的失败。看服务端日志；反复出现就值得报 bug |

`NOT_CONFIGURED` 和 `DOWNLOADER_UNAVAILABLE` 长得像，但被刻意分开：前者是这套安装的永久属性（「这里没有下载器」），后者是一个服务挂了（「下载器存在但没应答」）。它们要求调用方做的事正好相反。

---

## 服务起不来

### `refusing to start: DTK_SECRET_KEY is not set`

**含义。** 入口脚本对三种角色——`api`、`worker`、`migrate`——都用 `DTK_SECRET_KEY` 把门，缺失就以 `78`（`EX_CONFIG`：配置错了，重试没用）退出。镜像不带任何默认值，因为一个在所有安装上都相同的密钥等于没有加密：这个密钥加密的是所有存下来的 cookie 和代理凭证。

**确认。**

```bash
docker compose -p dtk -f docker/compose.yml logs api | tail -20
```

**修复。** 在**仓库根目录**的 `.env`（不是 `docker/.env`）里写一个至少 32 字符的密钥：

```bash
echo "DTK_SECRET_KEY=$(openssl rand -base64 48)" >> .env
docker compose -p dtk -f docker/compose.yml up -d
```

**事后更换它的代价。** 已经存下来的凭证是用旧密钥加密的，换钥之后解不开。换钥之后原有身份必须退休、重新铸造或重新导入，代理也要重新录入。见[安全](./15-security.md)。

### `redis: REDIS_PASSWORD is empty; write .env first`

Redis 没有密码就拒绝启动。同一份 `.env` 还需要 `POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`DTK_DATABASE_URL` 和 `DTK_REDIS_URL`；一次生成全部的那段命令见[安装与部署](./02-installation.md)。

### 变量写进了 .env 但容器读不到

compose 读这个文件有两种完全不同的方式，很多人栽在这里：

- **服务变量**来自 `env_file: ../.env`，这个路径由 compose 相对 `docker/compose.yml` 解析——所以尽管项目目录是 `docker/`，用到的仍然是仓库根目录的 `.env`。
- **`compose.yml` 内部的插值**——也就是文件里那些 `${…}` 表达式——**不**读那个文件。它读的是 `docker/.env` 或者你的 shell。涉及六个变量：`DTK_BIND_HOST` 和 `DTK_BIND_PORT`（发布地址，`${DTK_BIND_HOST:-127.0.0.1}:${DTK_BIND_PORT:-8000}:8000`）、`DTK_IMAGE_TAG`（镜像标签）、`DTK_REDIS_MAXMEMORY`（redis 上限），以及 `CLOAKBROWSER_REPO` / `CLOAKBROWSER_COMMIT`（浏览器镜像的构建参数）。完整拆解见[安装与部署](./02-installation.md)。

所以这六个里的任何一个写进根目录的 `.env` 都不会有效果——最常被绊到的是 `DTK_BIND_HOST` 和 `CLOAKBROWSER_COMMIT`。改成在命令行里传，或者让 compose 去读根目录那份：

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
# 或者让 compose 去读根目录那份：
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
```

### 某个容器 unhealthy 依赖它的服务一直不启动

`api` 和 `worker` 都要等 `postgres`（healthy）、`redis`（healthy）和 `migrate`（成功结束）。一个依赖不健康，后面全停。

```bash
docker compose -p dtk -f docker/compose.yml ps
docker compose -p dtk -f docker/compose.yml logs postgres redis migrate
```

api 的健康检查是 `GET /readyz`——就绪而非存活——启动宽限期 40 秒。这也是为什么 `up --wait` 只在 API 真的能服务时才返回。browser-rpc 被刻意排除在就绪判定之外：一个连不上的 RPC 服务绝不该把整个实例摘出负载。

### api 容器反复重启

在怀疑代码之前，先确认它不是被 OOM 杀掉的。两个 Python 服务各限 512 MiB、256 个进程；postgres 2 GiB，redis 512 MiB，browser-rpc 4 GiB 和 4 核，下载器 512 MiB。

```bash
docker compose -p dtk -f docker/compose.yml logs api | grep -i -E 'killed|memory'
docker stats --no-stream
```

这些上限存在的理由是：没有它们，一个把磁盘队列打满的任务会拖慢每一次 Postgres 写入，进而拖慢 api，进而让 `/readyz` 失败，于是 api 被反复重启，而真正的原因照旧在跑。

---

## 迁移失败

### `the timescaledb extension is not available on this server`

**含义。** `request_log`、`identity_events` 和 `content_snapshots` 是超表（hypertable），身份健康度还要读一个连续聚合。原生 PostgreSQL 建不出来，所以第一个迁移在创建任何东西之前就先检查扩展是否可用，给你一句读得懂的报错，而不是一个半成品 schema。

**修复。** 用 `timescale/timescaledb-ha:pg17` 镜像跑 PostgreSQL，`docker/compose.yml` 里就是这么配的。如果你把 `DTK_DATABASE_URL` 指向了自己的 PostgreSQL，要么在那边装上 TimescaleDB，要么改用自带的容器。

### migrate 因为别的原因退出非零

```bash
docker compose -p dtk -f docker/compose.yml logs migrate
```

`migrate` 是一次性容器，没有健康检查：它的退出码**就是**它的健康状态，`service_completed_successfully` 读的就是这个。每次 `up` 都会跑一遍，Alembic 是幂等的，重复执行是安全的。

需要自己控制升级时机、或者恢复了一个更旧 schema 的数据库时，手动跑：

```bash
# 磁盘上的代码期望的版本（不读数据库）
docker compose -p dtk -f docker/compose.yml run --rm --no-deps --entrypoint dtk api migrate --show

# 全部应用
docker compose -p dtk -f docker/compose.yml run --rm migrate
```

单独一个建表容器是刻意设计：api 和 worker 同时启动时并发 `CREATE TABLE` 打架，正是首次启动把自己搞坏的方式。

### 数据库密码里有 `%`

Alembic 把配置放在 ConfigParser 里，而它会对 `%` 做插值。这一点已经处理过了——URL 在进 Alembic 之前会被转义——但如果你在手工拼 `DTK_DATABASE_URL`，请把密码里的特殊字符做百分号编码。用十六进制生成密码（安装文档里就是这么做的）可以一次性绕开这一整类问题，包括 URL 里的 `+` 和 `/`。

---

## 打不开控制台

### 8000 端口连接被拒绝

**含义。** `api` 是唯一发布端口的容器，而 compose 默认把它绑在 `127.0.0.1`。对外提供服务是一个需要你显式去做的动作，前面加 TLS 也是你的事。

**确认。**

```bash
docker compose -p dtk -f docker/compose.yml ps api
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/readyz
```

**修复。** 本机用 `http://127.0.0.1:8000`。要发布到别处：

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

容器内部 api 始终监听 `0.0.0.0`；限制暴露面的是发布地址，不是监听地址。

### `/readyz` 返回 503

就绪判定要求 PostgreSQL 和 Redis 都通，每个探测有 3 秒时限。响应体会说明是哪个组件、为什么失败。browser-rpc 完全不参与就绪判定——一个连不上的 RPC 服务绝不会把实例摘出负载。它改由 `GET /api/v1/system/status` 报告。

### 控制台能打开但所有接口调用都失败

对比 `/healthz`（存活，不碰任何依赖）和 `/readyz`（就绪，会碰依赖）。存活但不就绪，说明进程本身没问题、某个依赖有问题——去看自检第 1 步。

### 某个路径返回控制台 HTML 而不是 JSON

控制台是单页应用，所以任何不是真实文件的路径都会回落到 `index.html`。这个回落会拒绝 `/api`、`/healthz`、`/readyz`、`/swagger`、`/redoc`、`/openapi.json` 和 `/mcp`，所以这些前缀下的笔误会老实地给你 404。其他地方的笔误则会返回控制台外壳加 200——如果你在期待 JSON 的地方拿到了 HTML，先重读一遍自己的路径。

---

## 初始化令牌丢了

**含义。** 全新部署还没有账号，谁先到谁就成了管理员。把门的是一个一次性令牌，它**只存在于容器日志里**：在 `users` 表为空时于启动阶段生成，存在 Redis 里 TTL 24 小时，用常量时间比较，5 次失败后作废，一旦有账号存在就永久关闭。它从不出现在任何 HTTP 响应里，从不进数据库，从不落盘。

**确认与恢复。** 重启 api 会重新播报令牌。未过期的令牌会被复用而不是替换，所以重启不会让你手上的链接失效：

```bash
docker compose -p dtk -f docker/compose.yml restart api
docker compose -p dtk -f docker/compose.yml logs api | grep -A 6 'not initialized'
```

如果令牌已经过期或被作废，同一次重启会铸造一个新的并打印出来。

也可以直接读出存着的令牌，api 忙的时候这比重启快：

```bash
docker compose -p dtk -f docker/compose.yml exec redis \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli GET setup:token'
```

### `SETUP_TOKEN_INVALID`

令牌不对或已过期。`error.details.attempts_remaining` 在倒数；到第 5 次失败时存着的令牌被删除，并记一条 `setup.token_invalidated` 日志。重启 api 换一个新的。

### `SETUP_ALREADY_DONE`

账号已经存在，初始化路径被永久关闭——这个检查在读令牌之前就跑了。正常登录，或者用下面的命令重置密码。

---

## 忘记管理员密码

**含义。** 自部署工具没有找回密码的邮件，也没有客服。恢复路径就是机器上的一个 shell，`dtk user passwd` 存在的意义就是让另一个诚实的答案——「把数据库删了重来」——不必被说出口。

```bash
# 有哪些账号
docker compose -p dtk -f docker/compose.yml exec api dtk user list

# 重置某一个，两次确认，全程不回显
docker compose -p dtk -f docker/compose.yml exec api dtk user passwd admin
```

在没有终端的自动化脚本里，改成从 stdin 读一行：

```bash
printf '%s' "$NEW_PASSWORD" | \
  docker compose -p dtk -f docker/compose.yml exec -T api dtk user passwd admin --stdin
```

密码至少 8 个字符，用 argon2id 哈希。CLI 用的是 OWASP 推荐参数（19 MiB、2 轮迭代、1 条并行通道），控制台和 API 用的是 argon2-cffi 自己的默认值（64 MiB、3 轮迭代、4 条并行通道）。这个差别不要紧，因为参数被编码在每条摘要里：CLI 设的密码可以在控制台验证通过，反之亦然；用旧参数写下的摘要会在下一次成功登录时就地升级。密码本身和它的摘要都不会被打印、回显或写日志。如果一个账号都没有，先建一个：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user create alice --role admin
```

---

## 被登录页锁在外面

**症状。** 密码是对的，控制台返回 `RATE_LIMITED`。

**含义。** 同一个用户名连续 5 次登录失败会锁定该账号 15 分钟。按账号的限制是无条件的，因为它只让攻击者付出他正在猜的那个账号的代价。

**按地址**的限制（20 次失败）只在来源地址确实能标识唯一调用方时才生效——也就是你用 `DTK_FORWARDED_ALLOW_IPS` 声明了受信任的反向代理。没有它时，在 TLS 终结器后面、或者 Docker 发布端口的用户态代理后面，全世界的登录共用同一个 peer 地址，此时强制执行会让一个陌生人反复把唯一的管理员锁在门外。在那种拓扑下计数器照样跑，但只作为告警：在 api 日志里找 `auth.login_spray_suspected`。

另外，60 秒内跨所有账号超过 30 次失败之后，每一次后续尝试都会先等 2 秒再校验密码。那是延迟，不是拒绝。

**修复。** 等满 15 分钟，或者清掉计数器：

```bash
docker compose -p dtk -f docker/compose.yml exec redis \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli DEL login:fail:user:admin'
```

键里的用户名是小写的。从容器自己的环境变量里读密码，可以让它不进入你的 shell 历史和宿主机的进程列表。

---

## 身份池为空

**症状。** 每个请求都返回 `IDENTITY_POOL_EXHAUSTED`（HTTP 503），带一个 `retry_after`。

**含义。** 调度器在 `sched.max_wait_seconds`（默认 10 秒）内没能租到身份。`error.details.reject_reason` 说明是哪一种：

| `reject_reason` | 含义 |
|---|---|
| `no_token` | 身份是有的，但在这个接口上每一个都没配额了 |
| `all_inflight` | 身份是有的，但每一个都正在请求中。每身份并发按设计固定为 1 |
| `wait_timeout` | 到了截止时间两者都没缓过来 |

**确认。** 自检第 4 步会按状态统计身份，或者：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk identity list
```

控制台的**身份**页（`/identities`）展示同样的信息外加健康度，池子卡片会把 `active` 数量与 `pool.min_size` 对比。

**按状态对症下药。**

| 你看到的 | 该做什么 |
|---|---|
| 一个身份都没有 | 铸造几个（需要 browser-rpc），或者在 `/identities` 从自己浏览器导入一份 cookie |
| 有身份但全在 `cooling` | 见[身份刚用就进入冷却](#身份刚用就进入冷却)。冷却到期它们会自己回来 |
| 身份是 `degraded` | 它们撞到了冷却上限。一次成功可以清空连续失败计数，但要等上限冷却到期才重新加入。确认原因已消失的话，可在 `/identities` 手动重置 |
| 身份是 `retired` | 退休会抹掉 cookie，且不可逆。铸造或导入新的 |
| 身份够多，仍然 `no_token` | 你只是问得比配额允许的快——见[请求很慢](#请求很慢) |

**相关配置。** `pool.min_size`（3）是补充器的低水位，`pool.target_size`（8）是补到哪儿为止。补充器每 60 秒跑一次。`pool.max_fail_streak`（3）决定一个身份从什么时候起不再计入池子水位，好让补充器去替换它而不是把一具尸体算进来。

**有一个刻意的行为值得知道：** 当某个平台的**每一个**存活身份都在失败时，补充器会按住水位不再铸造。那是平台级事件，不是池子不够用；在事故当中凭空冒出五个新访客，是这套部署能发出的最响亮的信号。

**短链展开有它自己的一套。** 展开 `v.douyin.com/…` 需要一个出口，而它和别的请求一样要走代理。完全没配代理时，它从本机直接展开；配了代理但全部不健康时，展开会抛 `IDENTITY_POOL_EXHAUSTED`，`details.reason` 为 `no_healthy_egress`——回落到本机地址是唯一会泄漏新东西的答案。

---

## 身份刚用就进入冷却

**症状。** 刚铸造或刚导入的身份发了一两个请求就变成 `cooling`。

**含义。** 冷却是一次 `risk_control` 判定的代价。退避是指数的：`sched.cooldown_base_seconds`（60）按连续失败次数翻倍，再乘以该接口的风险权重（按接口敏感度取 1.0 到 1.8），上限 `sched.cooldown_max_seconds`（21600，六小时）。撞到上限的身份被标为 `degraded` 而不是 `cooling`。

业务错误——被删的作品、私密的主页——不会改变身份的任何状态。这个区分是这套系统做对的最重要的一件事，而做错它，正是过去「一个被删的视频判死一个好 cookie」的原因。

**确认。** **日志**页（`/logs`），按 `outcome = risk_control` 过滤。在风控或网络错误的行上，`error_code` 列装的是触发的**判定规则**，它告诉你这个响应*为什么*被这样读：

| 规则 | 看到了什么 | 通常意味着 |
|---|---|---|
| `body.challenge_marker` | 响应体前 4 KB 里有验证码或验证页标记 | 真正的风控 |
| `signature.refused` | 抖音返回 403，body 是 `Blocked by ArgusSecurityPlugin …` | 是**我们的签名**，不是身份——见[签名错误](#签名错误与签名自检) |
| `signature.rejected` | TikTok 返回 200，带 `tt_orcas_res` 响应头，body 是空壳 | 同上：签名没通过校验 |
| `http.risk_status` | 401、403、405、412、429 或 444 | 平台拒绝了这个调用方 |
| `body.empty` | HTTP 200，body 完全为空 | 最直白的一种拒绝 |
| `payload.withheld` | 200，信封完整，载荷字段存在但为空 | 信封还在，内容被扣下了 |
| `payload.bare_envelope` | 200，body 只有一个状态字段，别的什么都没有 | 一种系统性拒绝，过去会被当成解析成功 |
| `envelope.risk_code` | body 级状态码 `10000` | TikTok 的验证信封 |

**按原因对症下药。**

- **`signature.refused` / `signature.rejected` 占多数。** 身份没问题，签名器有问题。去看[签名错误与签名自检](#签名错误与签名自检)。这里让整池冷却是期望行为——它阻止池子拿着永远不会被接受的签名去砸一个接口——但要修的东西不在身份上。
- **某个代理后面的身份同时全冷却。** 是代理探测器把它们冷却掉的：一个代理在五分钟内出现三次网络错误就被标为不健康，其后面每一个身份进入 900 秒冷却。cookie 没事，出口有事。见[代理探测失败](#代理探测失败)。身份**永远不会**被换绑到另一个代理——那种重组正是设计明确禁止的——所以一个永久死掉的代理，最终意味着它的身份要退休。
- **完全没有代理，而池子很忙。** 所有身份共用本机出口地址。初次试用没问题，负载上来就是真实的关联风险；自检第 3 步会明说这一点。
- **导入的身份指纹对不上。** 用另一个 User-Agent 去使用一份 cookie，比没有身份还弱。导入时请连同它被取出时的那个 User-Agent 一起导入。
- **确实烧掉了的身份。** 退休掉，铸造或导入替代品。

确认原因已经消失时（比如你刚修好的代理），可以在 `/identities` 用该身份的**重置**动作手动放回轮换。退休的身份刻意不可重置：退休已经把会话抹掉了。

---

## 接口熔断器打开了

**症状。** 某一个接口对所有调用方都返回 `ENDPOINT_CIRCUIT_OPEN`（503），带 `retry_after`。其他一切正常。

**含义。** 单个身份被拒绝是常态。**多个不同身份**在同一个接口上接连失败则是另一回事——通常是上游接口变了，或者签名器死了——继续重试只会烧池子。在 300 秒的滚动窗口里，三个条件必须同时成立：

| 条件 | 配置项 | 默认值 |
|---|---|---|
| 风控率超过阈值 | `sched.circuit_risk_threshold` | 0.6 |
| 样本量足够到有意义 | `sched.circuit_min_samples` | 20 |
| 失败跨越了足够多的不同身份 | `sched.circuit_min_identities` | 3 |

第三个条件才是「接口坏了」和「一个身份坏了」的分界线；没有它，一个抖动的身份就能把整个接口熔断掉。

**确认。** **总览**页（`/`）列出每一个已声明的接口——包括没有流量的那些，这是刻意的，因为一个不在看板上的接口，和一个从未被调用过的接口一样看不见——附带熔断状态、观测到的成功率与风控率、样本量、有多少个不同身份撞上风控，以及还有多久重新放行；接口一旦熔断，该页顶部还会有一条横幅写明原因。按接口的健康度**不在**调度器页（`/scheduler`）上，这是刻意的：那一页放的是 `sched.*` 和 `pool.*` 配置。同样的数据在 `GET /api/v1/admin/endpoints/health`。

**它怎么关上。** 自己关。熔断在触发 300 秒之后就不再处于打开状态；Redis 键本身还会多留 60 秒。打开期间每 60 秒放行恰好一个探测请求，第一个成功的探测立刻关闭它。恢复时直接全量放行几乎每次都会再次熔断，所以是一个探测而不是一股洪水。

**修复。** 弄清楚风控率为什么上升——那才是真正的工作，具体在[风控](#upstream_risk_control)和[签名](#签名错误与签名自检)两节。如果原因已经修好、又不想等窗口过去，把两个 Redis 键都清掉（熔断状态本身，以及那个否则会立刻把它重新熔断的观测窗口）：

```bash
docker compose -p dtk -f docker/compose.yml exec redis sh -c \
  'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli DEL \
     sched:circuit:douyin.author_posts sched:window:douyin.author_posts'
```

接口名请严格按总览页上显示的那样替换。

---

## 请求很慢

「慢」有好几种，修法各不相同。

### 返回的是 202 和一个任务 id 而不是数据

那既不是慢也不是错。提交返回 `202`，是因为此刻调度器可能没有空闲身份。要让调用变成同步的，加 `?wait=<秒>`：

```bash
curl -H "X-API-Key: $KEY" \
  'http://127.0.0.1:8000/api/v1/douyin/video?url=https://www.douyin.com/video/123&wait=20'
```

在时限内跑完就给 `200` 和结果。没跑完就给 `202`、任务 id 和 `state: running`——这不是错误，也不丢任何东西；轮询 `GET /api/v1/tasks/{id}` 即可。要求超过 `api.max_wait_seconds`（30）会得到 `400`，是拒绝而不是悄悄截短。

### 每个身份第一次走 browser-rpc 要好几秒

只有在 `signing.mode` 为 `rpc` 或 `auto` 且选中了浏览器时才会这样。签名是在一个加载了该身份自己 cookie 的页面里取的，所以某个身份的第一次调用会启动浏览器、并**经由该身份自己的代理**加载平台页面——不走代理实测约 4 秒。同一身份的后续调用由常驻页面在毫秒级完成。`signing.rpc_timeout_seconds`（60）必须覆盖得住这个冷启动。

默认是 `signing.mode = native`：算法在进程内跑，请求路径上没有浏览器，这笔开销根本不存在。如果你确实要跑 `rpc`，就调大 `DTK_BROWSER_WARM_CONTEXTS` 让更多身份保持常驻。

### 吞吐上限按每身份每接口计算

每一对 `(身份, 接口)` 有一个令牌桶。详情类接口容量 5、每秒补充 0.30；列表类接口容量 3、每秒补充 0.12——突发额度花完之后，大约每 8 秒一个请求，按身份、按接口各算各的。每身份并发按设计固定为 1：真实会话不会并行发 API 请求。

**要扩容就加身份，不是加 worker。** 更多 worker 容器只会排队：

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=4
```

配额按 `(身份, 接口)` 而不是只按身份计，是因为一个身份把全部预算花在最敏感的那一个调用上，读起来就是「这个访客只做一件事，而且一直在做」——这比均匀分布在几个接口上是更强的信号。

### 重复的相同调用反而更慢

它们本该**更快**：完全相同、已在运行中的请求会被合并而不是重复执行，成形后的响应体也有缓存——一个作品 `cache.content_ttl` 1800 秒，一个主页 `cache.author_ttl` 900 秒，任何分页结果 `cache.list_ttl` 300 秒。`?refresh=true` 对这一次调用同时关掉两者，代价是一个身份和一次上游请求，它是用来检查「有没有变化」的，不是每次调用都该带的。

### 什么都慢 包括控制台

先确认 api 容器不是被 CPU 上限压着，以及 Postgres 不是瓶颈。**系统**页（`/system`）展示各组件的探测延迟、数据库总大小和每张表的行数。

---

## 被限流

**症状。** 自己的密钥收到 `RATE_LIMITED`（429），带 `Retry-After`。

**含义。** 固定窗口的防滥用限额，每分钟一个窗口。它不是计量也不是计费：唯一目的是防止一个跑飞的脚本把身份池抽干。限额取密钥自身的 `rate_limit`（如果设了），否则取 `api.default_rate_limit_per_min`（120）。

**确认。** `error.details.limit` 报告实际生效的上限。至于计的是哪个主体，取决于你怎么认证的：API 密钥按密钥计，控制台会话按用户计。

**修复。** 放慢速度；或者在 **API 密钥**页（`/api-keys`）调高该密钥的限额；或者在**设置**里调高 `api.default_rate_limit_per_min`。把限额设为 0 或更小则对该主体关闭限流。

**开放接口有一个注意点。** 通过 `api.public_endpoints` 开放出去的接口，匿名调用方共用同一个用户 id，所以改按 peer 地址计。在 Docker 用户态代理后面所有调用方看起来都是网桥网关，于是它退化成一个共享桶而不是完全没有限制——是更严而不是更松，对一个职责是防滥用的计数器来说方向是对的。

登录失败走的是完全不同的另一套限制：见[被登录页锁在外面](#被登录页锁在外面)。

---

## 401 还是 403

这两个回答的是不同的问题，看一眼错误码就能分清。

| 错误码 | HTTP | 它回答的问题 | 典型原因 |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | *你是谁？*——没有凭证，或凭证被拒绝 | 少了请求头、密钥打错、密钥被撤销、密钥过期、控制台会话过期 |
| `FORBIDDEN_SCOPE` | 403 | *知道你是谁了，你能做这件事吗？* | 密钥缺少 scope，或账号角色不够 |

**401 检查清单。**

- 请求头是 `X-API-Key`，或者 `Authorization: Bearer <key>`。控制台的会话 cookie 是另一套机制。
- 已撤销或超过 `expires_at` 的密钥解析不出任何主体，这是 401，不是 403。
- **给已开放的接口发一个坏凭证，仍然是 401。** 发了被拒绝的密钥的调用方会被明确告知，而不是被悄悄降级成匿名——因为「你的密钥有效但看到的更少」是这两者里更难排查的那一种。

**403 检查清单。**

- scope 有 `douyin:read`、`tiktok:read`、`identity:manage`、`archive:read`、`archive:export`、`media:read`、`media:write`、`admin`。`error.details.required` 会指名接口想要哪一个。
- **管理员拥有的密钥同样受它自己的 scope 约束。** 这是刻意的：自部署实例上几乎每一个密钥都属于管理员用户，如果对管理员短路，`archive:export`——那个能把整个数据库副本交出去的调用——恰恰会在它被写出来针对的那种部署里完全失效。
- 控制台**会话**则是按账号角色约束，而不是按 scope。
- 有几件事同时要 scope 和 operator 角色：`?explain=true` 和用 `?identity=` 指定身份，都需要 `identity:manage` **且**是 operator，因为返回内容里包含 cookie。这类请求会写进审计日志。
- `CONTENT_PRIVATE` 也是 403，但它说的是内容，不是你。

---

## INVALID_URL 背后的八种原因

`error.details.reason` 说明是哪一种。它们的修法不一样。

| `reason` | 发生了什么 | 怎么办 |
|---|---|---|
| `no_url` | 你发过来的内容里没有 URL | 发一条链接，或者包含链接的分享文案 |
| `host_not_allowed` | 这个域名不是抖音或 TikTok 的域名 | 检查拼写。只有 `douyin.com`、`iesdouyin.com`、`amemv.com` 和 `tiktok.com`（及其子域）会被抓取；`javascript:`、`data:`、`file:` 在任何域名检查之前就被拒 |
| `platform_mismatch` | TikTok 链接发到了 `/api/v1/douyin/...`，或者反过来 | 用 `/api/v1/parse`，它按链接自己路由；或者发给正确的平台 |
| `unknown_resource` | 域名对，但路径指向的东西本实例不抓 | 音乐页、话题页、搜索结果页。请发作品或主页链接 |
| `redirect_not_allowed` | 短链跳到了平台之外 | 跳转链离开了白名单。如果你知道那个域名并且信任它，`security.url_allowlist` 可以放行这一跳 |
| `too_many_redirects` | 跳了 5 次还在跳 | 通常是被中间页反复弹的链接。用浏览器打开，拿它最终落地的地址 |
| `unresolved_short_link` | 短链展开之后，结果还是一条短链 | 罕见。用浏览器打开，拿最终 URL |
| `short_link_dead_end` | 短链解析成功了，但落在一个本实例抓不了的页面上 | 见下 |

### 短链解析后落在平台首页

这一条值得单独一段，因为它的报错很容易被误读成是你的错。

一条 `www.tiktok.com/t/<slug>` 链接，被服务端客户端跟随时，会返回 `302` 到 `https://www.tiktok.com/?_r=1`——站点自己的首页。链接没错，粘贴也没错；只是 TikTok 拒绝为一个非浏览器调用方解析它。`details.resolved_to` 会显示它落到了哪里。报错本身也直说了：*the short link resolved to a page this instance cannot fetch; it has probably expired, or the platform will only resolve it in a browser*。

**修复。** 用真正的浏览器打开短链，复制它落地后的完整 URL（`https://www.tiktok.com/@handle/video/<id>`），发这个。没有任何配置需要改。

### 关于抖音两跳分享链接

`v.douyin.com/X` → `iesdouyin.com/share/video/123?…` → `douyin.com/video/123?previous_page=…` 是常见形态，而后两跳规范化之后是同一个 URL。这被当作**已到达**而不是循环——两个规范形式相同的 URL 就是同一个资源。如果你手上有更老的版本，会把这条最常见的分享链接报成「重定向循环」，修的就是这里。

---

## 明明存在的作品却返回 NOT_FOUND

**症状。** 浏览器里能打开的东西，接口返回 `NOT_FOUND`（404）。

**含义。** 对内容类接口，`NOT_FOUND` 是**业务错误**变成的：平台应答了，而它的应答是「对这个调用方来说这东西不在」。它不是在说 id 格式不对，也刻意不是 `UPSTREAM_RISK_CONTROL`——身份不为它背锅，也不会因此被冷却。

**确认。** 在命令行抓同一条链接，看判定结果：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk fetch --raw \
  "https://www.douyin.com/video/7123456789012345678"
```

`dtk fetch` 不需要登录、不需要 API 密钥、不过限流、不走缓存：解析 URL，用一个真实身份发一次签名请求，然后把 HTTP 状态、判定结果（outcome）、命中的规则，以及加了 `--raw` 时平台自己的响应体，一并打出来。最后那一项才是重点：它告诉你平台到底说了什么。

**常见原因。**

| 原始响应体里的样子 | 原因 |
|---|---|
| `filter_detail` 里 `filter_reason: core_dep`，消息为空 | 抖音对一个不存在的 aweme id 的标准答案。检查 id |
| `filter_detail` 里 `filter_reason: status_self_see` | 作者把作品设成了仅自己可见。游客确实抓不到 |
| `statusCode: 10204`，没有 `itemInfo` | TikTok：作品被删了，或者从来不存在。TikTok 自己不区分这两者，所以这里也不区分 |
| `status_code: 2053` | 抖音：aweme 不可用 |
| `status_code: 2` / `error_code: 10201` / `100002` | 一个没人拥有的作者 id 或用户名 |
| 地区限制内容 | 平台会根据请求从哪里出去给出不同答案。换一个出口在别的国家的代理上的身份试试 |

**当内容需要登录才能看时。** 游客身份看不到只有账号才能看的东西。在 `/identities` 从你自己已登录的浏览器导入一份 cookie，然后用 `?identity=<id>` 把请求钉在它上面（需要 `identity:manage` 和 operator 角色）。见[身份与代理](./06-identities-and-proxies.md)。

**当你怀疑这个答案本身就是错的时。** 一个实际上是拒绝、却被判定成业务错误的响应，表现出来就是身份健康却返回 `NOT_FOUND`。`dtk fetch --raw` 是分辨手段：验证码页、空 body、或者光秃秃的 `{"status_code":0}` 都是拒绝而不是回答。带上原始 body 报告它。

---

## UPSTREAM_RISK_CONTROL

**症状。** `UPSTREAM_RISK_CONTROL`（502），`retry_after` 等于 `sched.cooldown_base_seconds`。发出这次请求的身份现在处于冷却。

**它实际的含义。** 这个响应被**判定**成了一次拒绝。判定是一张有序的规则表，不是猜测，触发它的是这些：

- 平台用来拒绝调用方的 HTTP 状态：**401、403、405、412、429、444**（444 是 nginx 不给响应直接关连接，抖音的边缘节点对它不喜欢的请求就是这么干的）。
- 响应体前 4 KB 里的**验证标记**：`verify_center`、`verify_page`、`verifycenter`、`captcha`、`secsdk`、`byted_acrawler`、`slide_verify`、`tiktok-verify-page`。只有当响应**不是**一个干净的成功信封时才扫描——一段恰好含有「captcha」字样的视频简介不是验证码。
- body 级状态 **10000**——TikTok 的验证信封。
- **HTTP 200 且 body 为空**，且该接口没有声明过自己的沉默是有意义的。
- **载荷字段存在但为空**（`aweme_detail`、`aweme_info`、`itemInfo`、`userInfo`、`user_info`、`user`），且没有任何解释。空**列表**被刻意排除在外：空的 `aweme_list` 是分页正常结束，把它当风控会导致每次有人翻到主页最后一页就冷却一个身份。
- **光秃秃的信封**：HTTP 200，body 里只有状态字段、消息和链路追踪碎屑，完全没有载荷。抖音对带非零 `max_cursor` 的 `author_posts` 正是这样应答的——17 字节——而同一个请求带 `max_cursor=0` 会返回几百 KB。
- **平台不接受的签名**——见下一节。它被刻意判为风控，因为请求确实被拒了，但规则名会说明原因在签名器。

**哪些*不会*触发它。** 被删的作品、私密主页、没人拥有的作者 id、HTTP 400/404/410/451、已经测量过自身沉默含义的接口（抖音的 `author_likes` 对私密点赞列表返回零字节），以及平台用 `filter_detail` **解释过**的空载荷。这些都是业务错误，不让身份付任何代价。

**修复。** 先读日志页 `error_code` 列里的规则名，然后：

- 签名类规则 → [签名错误](#签名错误与签名自检)。在身份上怎么折腾都修不好。
- 单个身份上出现真实验证标记 → 让它冷却，它会自己回来。同一个身份反复如此 → 退休它。
- 整池都出现真实验证标记 → 你相对手上的出口问得太快了，或者出口是共享的/已被标记的。加代理、加身份，或者降低对该接口的压力。
- 同一个代理下的全部身份 → [代理探测失败](#代理探测失败)。

---

## 签名错误与签名自检

每一个平台请求都用平台自身算法的纯 Python 重实现来签名。当它漂移时，症状很少是一个干净的报错。

### 它表现出来的三种样子

| 症状 | 是什么 |
|---|---|
| `SIGNING_FAILED`（502） | 完全没有可用签名器，或者签名器抛错。最清楚，也最少见 |
| HTTP 200，body 为空或是空壳，带 `tt_orcas_res` 响应头 | TikTok 在拒绝一个签名没通过校验的请求。判为 `signature.rejected` |
| HTTP 403，body 里有 `Blocked by ArgusSecurityPlugin …` | 抖音在拒绝一个缺失或覆盖不到位的签名。判为 `signature.refused`，body 里会带 `uifid not found`、`signature not found`、`sign invalid`、`sign expired` 之一 |

第二种是代价最高的：一个 200 加空 body，没有任何一点提示「签名」，读起来就像一个死掉的接口。判定表里之所以专门为它写一条规则并起名，就是为了让它不再看起来像被平台封了。

### 用自检第 5 步确认

第 5 步对同一个请求签两次——一次用原生算法，一次经 browser-rpc——然后比对。

| 第 5 步结果 | 含义 | 该做什么 |
|---|---|---|
| `signing_ok` | 原生与浏览器签名一致 | 无需处理 |
| `signing_mismatch` | 原生算法相对平台已经漂移，针对列出的平台 | 这些平台现在改走 browser-rpc 签名。带上报告开一个 issue，好把算法更新掉 |
| `signing_not_comparable` | 浏览器参考实现的是算法的**另一个版本**，结构性比对说明不了任何问题 | **这不是故障。** 原生路径不受影响，仍然照常服务请求 |
| `signing_no_browser_rpc` | 没配 browser-rpc，因此没有第二个签名器 | 想要这项检查就设 `DTK_BROWSER_RPC_URL`。它的价值在于抢在风控率之前发现算法过期 |

`signing_not_comparable` 最容易被误读：「无法比对」读起来像失败，但它不是。

### 手工复现一个签名

`POST /api/v1/tools/sign` 为一个 URL 计算签名。它是纯算术——什么都不抓、不消耗身份——结果只取决于你传进去的东西。`stages` 会按层拆开，说明哪一层贡献了哪些参数、以及那一层有没有真的跑。

签名端和发送端有三样东西必须对齐，否则无论签名多正确平台都会拒绝：

- **User-Agent。** 两个平台都把它哈希进签名。响应会回显实际使用的那一个；请原样发送。
- **TLS 指纹。** TikTok 会检查它与 User-Agent 是否一致，所以 Chrome 的 UA 必须走 Chrome 的 TLS profile。裸 `curl` 无论签名多对都会被拒。
- **一份 cookie。** 签名正确但没有 cookie 依然什么都拿不到：平台应答的是一个*会话*。`POST /api/v1/tools/identity` 可以铸一份（需要 browser-rpc）。在抖音上，cookie 还是 `x-secsdk-web-signature` 的计算对象——没有 visitor id，`headers` 会返回空，受签名保护的接口会点名 `uifid` 拒绝你。

`POST /api/v1/tools/decode` 反过来读一个签名参数，用来核对别人给你的签名到底编了什么。两者都在控制台的**工具**页（`/tools`）上；见[调试台与工具](./07-playground-and-tools.md)。

### 真正在扛流量的是哪个签名器

`signing.mode` 决定：`native`（默认，进程内，请求路径上没有浏览器）、`rpc`（驱动真实浏览器）、`auto`（平台自家 SDK 会签的接口优先走 rpc，其余走 native）。`signing.fallback_enabled`（默认开）允许首选签名器失败时改用另一个——**想知道到底是谁在扛你的流量，就把它关掉**，因为悄悄切换正是一个坏掉的签名器继续看起来很健康的方式：它的流量转移到了另一个上，成功率一点没掉。

每一行 `request_log` 的 `signer` 列记录本次是 `native` 还是 `browser`。

---

## 浏览器容器不可用

**症状。** 自检第 1 步报 `warn`：*postgres and redis are reachable; browser-rpc is not*。或者铸造身份失败并返回 `NOT_CONFIGURED`。

**这是一种受支持的部署形态，不是降级形态。** browser-rpc 在一个 compose profile 后面，是整套栈里最重的容器——预热状态实测常驻 2.57 GiB、占用六个核的 Chromium。一个自己手工导入 cookie 的实例根本不需要它。

### 没有它还能用什么

| 能用 | 不能用 |
|---|---|
| 全部读取接口，用原生签名 | 自动铸造身份 |
| 手工导入的 cookie | `POST /api/v1/tools/identity`（返回 `NOT_CONFIGURED`） |
| 整个控制台、API、MCP、CLI | 自检第 5 步（跳过：没有可比对的对象） |
| 就绪判定——RPC 连不上从不把实例摘出负载 | `signing.mode` 为 `rpc` 或 `auto` 时的浏览器签名兜底 |

### 分清是没配还是挂了

`NOT_CONFIGURED` 表示 `DTK_BROWSER_RPC_URL` 没设——这套安装从来就没有浏览器。组件检查报超时或连接错误，则表示服务配了但挂了。

```bash
docker compose -p dtk -f docker/compose.yml --profile browser ps
docker compose -p dtk -f docker/compose.yml logs browser-rpc | tail -40
```

### 启动它

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT=f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

### 它报告健康却铸造不出身份

即使浏览器后端启动失败，这个服务照样会应答 `/rpc/health`——这是刻意的，为的是给你一个原因而不是一个重启循环。所以 compose 的健康检查读的是 body 里的 `status` 字段，而不是满足于一个 200。最常见的原因是构建镜像时没给 `CLOAKBROWSER_COMMIT`。

### Chromium 崩溃 报错 Target crashed 看起来像被平台封了

驱动自己带了 `--disable-dev-shm-usage`，所以每个渲染进程的共享内存落在容器的 `/tmp` tmpfs 上，而不是 `/dev/shm`。每个常驻上下文占着大约 290 MB 的「已删除但仍打开」的文件——`du` 看不见，`df` 看得见。tmpfs 因此被设成 3 GB。粗略的上限是 `300M × DTK_BROWSER_WARM_CONTEXTS × 2 个平台`，再加上一次进行中的铸造。如果你调大了 `DTK_BROWSER_WARM_CONTEXTS`，就把 tmpfs 一起调大——并且记住那是内存预算，不是空闲磁盘。

### Chromium 与模拟指纹的版本漂移

系统页把 browser-rpc 运行的 Chromium 主版本号和 `wreq` 模拟 profile 的主版本号并排展示。并排是为了让漂移一直可见，而不是几周后从上升的风控率里才被发现。

---

## 下载失败或者文件不见了

媒体下载是一个可选的 sidecar。只要元数据的实例，一个字节的视频都不用存。

### `POST /api/v1/downloads` 返回 `NOT_CONFIGURED`（501）

要么 `DTK_DOWNLOADER_URL` 没设，要么设置里的 `media.enabled` 是关的。报错会说明是哪一个。

```bash
echo 'DTK_DOWNLOADER_URL=http://downloader:9100' >> .env
docker compose -p dtk -f docker/compose.yml --profile downloader up -d --build
```

### `DOWNLOADER_UNAVAILABLE`（503）

sidecar 配了但没应答。这次失败的调用没有改变任何东西。

```bash
docker compose -p dtk -f docker/compose.yml --profile downloader ps
docker compose -p dtk -f docker/compose.yml logs downloader | tail -40
```

这个容器是一个 scratch 镜像加一个静态 Go 二进制——没有 shell，没有包管理器——所以它的健康检查是二进制自己探自己。

### 下载返回 `QUEUE_FULL`，`Retry-After` 是 300 秒

磁盘越过了 `capacity.hard_stop_percent`，新下载被暂停。见[磁盘满了](#磁盘满了)。

### 下载结束状态是 `partial`

`partial` 是一个独立状态，因为一个包含多个文件的作品可能落地一部分、丢掉另一部分——那既不是 `done` 也不是 `failed`。在 `/downloads` 看这条下载的行，确认缺的是哪个文件、为什么缺。常见原因：某个文件超过了 `media.max_file_bytes`（默认 512 MiB，按实际写入的字节数计，而不是按服务器声称的大小），或者某个镜像地址在传输中途过期了。

### 文件本来在，现在没了

有两种机制会删除媒体，而且它们都会告知你：

- **体积上限。** `media.max_bytes`（默认 2 GiB）。超过之后，最旧的**未置顶**下载会被删除直到卷回到线下，并发出 `MEDIA_EVICTED` 告警说明具体删了什么——那条告警是你唯一会收到的通知。在控制台把一条下载置顶就能永久豁免它。`media.max_bytes = 0` 完全关闭清理。
- **除此之外没有别的。** 保留策略不删媒体，容量守卫更是什么都不删。

### 一条下载永远卡在 `queued` 或 `running`

维护巡检每 300 秒跑一次，把创建超过 7200 秒仍处于活动状态的下载结算为 `failed`，错误信息是 *the worker never reported an outcome for this download*。已经落地的文件原地保留。如果它一直不被结算，说明 worker 没在跑。

### 传输还没开始链接就过期了

签名的 CDN 链接几小时内就会失效。`media.mirror_max_age_seconds`（600）规定归档里的媒体链接最多可以多旧，超过就重新解析作品拿新链接；`0` 表示总是重新解析。

### API 不肯把文件给我

这是下载器的设计：它只负责把字节落到磁盘上，不做中继。API 以只读方式挂载媒体卷，并在 `media:read` scope 后面把已存文件交给控制台；下载器本身从不向调用方中继媒体。见[下载、素材库与监控](./08-downloads-and-library.md)。

---

## 磁盘满了

**症状。** 后台采集自己停了。新下载返回 `QUEUE_FULL`。收到 `CAPACITY_WARNING` 或 `CAPACITY_PAUSED` 告警。

**含义。** 容量守卫用 `shutil.disk_usage` 测量数据库路径和媒体路径的剩余空间——这是唯一把 WAL、索引、chunk 开销、其他容器以及所有共用该卷的东西都算进去的数字，这些在按表求和里一个都看不见。它取**最满**的那个卷，而不是平均值：先满的那块盘才是会把事情搞坏的那块，把它和一个空卷平均掉，正是守卫报告一切正常而 Postgres 正在写失败的方式。

| 状态 | 阈值 | 行为 |
|---|---|---|
| `ok` | 低于 `capacity.warn_percent`（80） | 无 |
| `warn` | 达到或超过 80% | 发 `CAPACITY_WARNING` 告警。行为不变 |
| `full` | 达到或超过 `capacity.hard_stop_percent`（92） | 后台采集和**新的**下载任务停下。发 `CAPACITY_PAUSED` 告警 |

**交互式读取永远不会被暂停，也永远不会删除任何东西。** 把磁盘满变成「我的 API 挂了」，比它要预防的那个故障更糟；而为了腾空间去删用户的归档比这两者都更糟——归档存在的意义恰恰是比平台活得更久。

**确认。** **系统**页展示数据库总大小和每张表的行数。`GET /api/v1/system/status` 返回的是同一组数字——`storage.db_size_bytes`、每张表的 `storage.rows`，以及 `storage.identities`。每张表在磁盘上占用的字节数目前没有暴露。

**按能腾出多少排序的修法。**

1. **已存媒体。** 通常是最大头。调低 `media.max_bytes` 让清理回收，或者在 `/downloads` 删掉不需要的下载。
2. **原始归档。** `archive.store_raw`（默认关）会为每一个对象保留平台未经处理的原始载荷。它是本实例可以*选择*存储的最大单项。如果开着而你并不需要，关掉它。
3. **请求日志保留期。** `retention.request_log_days`（14）。调低之后下一次维护巡检会回收 chunk。
4. **归档的作品。** 归档不会被自动清理，`retention.content_days` 也帮不上忙：这个 key 虽然声明了，但**没有任何代码读它**，设成什么都不会有效果（见[配置参考](./03-configuration.md)）。要腾空间就去[资料库](./08-downloads-and-library.md)里删掉你不要的作品。
5. **加盘。** 当归档本来就是你想要的东西时，这才是诚实的答案。

---

## 代理探测失败

**症状。** 某个代理在 `/proxies` 上被标为不健康，或者自检第 3 步报 `proxies_partial` / `proxies_all_failed`。

**含义。** 探测器每 300 秒扫一轮（同一个代理两次探测之间不短于 60 秒）。一个代理在五分钟内出现三次网络错误就被标为不健康，其后面每一个身份进入 900 秒冷却。身份永远不会被换绑到别的代理。

**直接测一个代理。**

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk proxy test '<proxy-id>'
```

它会报告出口 IP、国家、时区、延迟，失败时给出 detail，并把健康与地理结果写回该行（传 `--no-write` 则只探测不记录）。id 可以用表格里显示的短前缀。控制台在 `/proxies` 上每一行都有同样的按钮。

**读懂 detail。**

| detail | 原因 |
|---|---|
| `ConnectTimeout`、`ConnectionError`、`ProxyConnectionError` | 出口挂了，或凭证不对，或供应商不允许你这台机器的地址 |
| `ImportError` | SOCKS 代理但没装 httpx 的对应可选依赖 |
| `ValueError` | 代理 URL 本身写法不可用 |
| `probe returned no JSON` / `no JSON object` | 探测 URL 不是你以为的那个服务，或者被门户页面劫持了 |
| 请求日志里出现 HTTP 407 | 代理自己在拒绝：出口配置错误，或者订阅到期了。它被计为网络错误以便触发代理健康探测，绝不会被当成「内容不存在」 |

**修复。** 改正凭证，或者换掉这个代理。绑在一个永久死掉的代理上的身份无法恢复——退休它们，铸造或导入绑到可用出口上的替代品。

**出口国家变了这件事，即使代理还能用也值得知道。** 出口地址决定了铸造身份时该声称的地区，而一个德国出口报告 `Asia/Shanghai` 是白送出去的破绽。

**完全不配代理**是允许的，自检会把它报成警告而不是失败：所有身份共用本机出口 IP。初次试用没问题，池子忙起来就是关联风险。

---

## MCP 客户端连不上

MCP 服务器挂在 API 进程的 `/mcp` 上（streamable-http），也可以用 stdio 方式运行。

| 症状 | 原因 | 怎么办 |
|---|---|---|
| `GET /mcp` 返回 404 或跳转异常 | Mount 只匹配它**下面**的路径 | 端点是 `/mcp/`。裸路径会返回 `307` 跳到它并保留方法和 body，多数客户端会跟随——但请直接配 `/mcp/` |
| `401 UNAUTHENTICATED` | 没带 API 密钥，或密钥被拒绝 | 发 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。**控制台会话 cookie 在这里永远不被接受**——cookie 是给浏览器用的，接受它会让任何能从那个浏览器里发请求的东西都能碰到 MCP 端点 |
| `401` 且提示 *this endpoint accepts API keys only* | 你用的是控制台会话 | 在 `/api-keys` 铸一个 API 密钥 |
| 连接时 `403 FORBIDDEN_SCOPE` | 密钥完全没有任何平台读取 scope | 给它 `douyin:read` 或 `tiktok:read` |
| 某次工具调用 `403 FORBIDDEN_SCOPE` | 密钥能读一个平台，而这次调用点的是另一个 | 传输层只能检查密钥能读*某个*平台；具体平台在 JSON-RPC body 里，所以按调用再检查一次。与 REST 同一套规则，这是刻意的 |
| `429 RATE_LIMITED` | 与 REST 同一套防滥用限额 | 见[被限流](#被限流) |
| api 扩容之后会话断掉 | 会话默认是无状态的，好让 API 水平扩展 | 无需处理；钉住会话的话，第二个进程一起来就会坏 |

MCP 的失败使用与 REST 相同的 `{"success": false, "error": {"code": …}}` 信封，401 会带 `WWW-Authenticate: Bearer realm="dtk"`。

**用 stdio**，给本地 agent 用时直接跑服务器：

```bash
python -m dtk.mcp
```

它自己管理数据库、Redis 和配置的生命周期，所以需要与容器同样的那套 `DTK_*` 环境变量。注意在 stdio 模式下 stdout **就是** JSON-RPC 通道——日志在任何东西写日志之前就被移到了 stderr，往 stdout 里多写一行就会把流搞坏。

**工具集是八个，而且会一直是八个。** 每多一个工具，agent 的选择准确率都会可测量地下降。这里在结构上就没有身份、cookie 或代理相关的工具——见 [MCP 与 AI 客户端](./12-mcp.md)。

---

## 任务一直排队

**症状。** `GET /api/v1/tasks/{id}` 一直报 `queued`。诊断页永远跑不完。

**含义。** 没有任何东西在消费队列。控制台触发的作业——诊断、铸造、身份测试、代理测试、备份、恢复、告警测试——全都在 worker 里跑。

```bash
docker compose -p dtk -f docker/compose.yml ps worker
docker compose -p dtk -f docker/compose.yml logs worker | tail -40
```

worker 没有 HTTP 端口，所以它的健康检查断言的是解释器能跑、并且 Redis——租约、预算和队列都在那里——可达。一个连不上 Redis 的 worker 什么活都干不了。

**卡住的任务会自己恢复。** 一个开始后 900 秒仍处于 `running` 的任务被视为孤儿并重新入队，每轮最多 200 个，免得一个病态的积压被一次性推回队列。

### `QUEUE_FULL`（503）

队列达到了 `sched.queue_max`（500）。报错会说明当前深度和上限，`Retry-After` 取 `sched.max_wait_seconds`。丢负载好过接下没人处理得完的活：让调用方干等着，比告诉他晚点再来更糟。

运维触发的维护作业不受这个上限约束——在一个饱和的实例上你永远能跑一次诊断。媒体下载则被刻意排除在豁免之外。

**修复。** 加 worker，或者少提交，或者在队列确实在排空、只是需要更多余量时调高 `sched.queue_max`。吞吐的瓶颈是身份池，不是 worker 数量。

### 结果过期了

一个明确成功过的任务返回 `TASK_NOT_FOUND`，意味着结果被清掉了：`retention.task_result_hours` 是 24 小时。任务行本身活 `retention.task_days`（90 天）。重新提交即可——重试拿不回一个已过期的结果。

---

## UPSTREAM_CHANGED

**症状。** `UPSTREAM_CHANGED`（502），`details.path` 指名了一个字段，报错请你上报。

**含义。** 平台应答了，响应被判定为可用，然后解析器发现响应的结构不再是它预期的样子。它被刻意标为不可重试：再怎么重试都不会改变平台的响应格式，修法是改代码。

**确认，并让上报变得有用。**

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk fetch --raw "<url>"
```

这条命令会打印解析错误并指名缺失的路径，旁边就是平台自己的载荷。这一对东西正好告诉维护者：平台是把字段改名了，还是干脆不返回了——`--raw` 就是为此存在的。

**有一种情况长得像它但不是。** 一种系统性拒绝会以「格式完整但内容为空」的形式抵达解析器，过去就表现为 `UPSTREAM_CHANGED`，把运维送去上报一个任何解析器改动都修不好的「解析器 bug」。现在 `payload.bare_envelope` 这条判定规则会抓住这种形态，改判为风控。如果你看到 `UPSTREAM_CHANGED` 伴随 `http_status: 200` 和一个极小的响应，先看原始 body，别急着认定是解析器的问题。

---

## 报告问题时请附上什么

1. **诊断报告。** `dtk diagnose`，或者 `/diagnose` 上的复制按钮。它在源头就做了脱敏，这正是「把这个贴进 issue」这句建议安全的原因。
2. **失败响应里的 `error.code` 和 `meta.request_id`。**
3. **`dtk fetch --raw` 的输出**，如果问题局限在某一条链接或某一个接口上。
4. **版本与 commit。** 在 `/system` 和**关于**页（`/about`）上展示，`dtk --version` 也会打印。
5. **你启用了哪些 compose profile**——纯净、`--profile browser`、`--profile downloader`。

不要粘贴 `.env`、cookie、带凭证的代理 URL 或 API 密钥。报告和 CLI 已经把这些做了掩码；你从别处复制来的内容没有。

---

## 延伸阅读

- [运维](./10-operations.md) —— 本页反复指向的那些控制台页面、告警、备份与保留策略
- [CLI 参考](./13-cli.md) —— 本页用到的每条命令的完整参数：`dtk diagnose`、`dtk user passwd`、`dtk migrate`、`dtk fetch`、`dtk identity`、`dtk proxy`
- [配置参考](./03-configuration.md) —— 这里提到的每一个配置项，含默认值与作用域
- [核心概念](./04-concepts.md) —— 身份、判定结果、调度器与熔断器的原理，而不是排查
- [身份与代理](./06-identities-and-proxies.md) —— 铸造、导入、指定身份、退休
- [REST API 指南](./11-api.md) —— 响应信封、`?wait=`、`?refresh=`、`?explain=`、scope
- [安全](./15-security.md) —— 主密钥、各种白名单、什么会离开本实例
- [常见问题与术语表](./17-faq.md) —— 全文使用的词汇
